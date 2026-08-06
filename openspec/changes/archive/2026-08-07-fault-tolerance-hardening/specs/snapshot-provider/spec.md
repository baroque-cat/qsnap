# Snapshot Provider — Delta

## ADDED Requirements

### Requirement: Batch multi-disk snapshot creation via create_multi

`ISnapshotProvider` SHALL provide a method
`create_multi(vm_config: VMConfig, specs: Sequence[SnapshotSpec], quiesce: bool) -> list[SnapshotResult]`
where `SnapshotSpec` is a frozen dataclass with fields `disk: str`, `name: str`,
`path: Path`. `ExternalSnapshotProvider.create_multi` SHALL create ALL given disks'
snapshots with ONE `virsh snapshot-create-as` call containing one
`--diskspec {disk},file={path},snapshot=external` argument per spec plus the flags
`--disk-only --atomic --no-metadata`, and `--quiesce` when `quiesce=True`. The single
call SHALL be wrapped by the same lock-conflict retry loop as `create()`. After virsh
returns exit code 0, the provider SHALL validate every spec's file with the same
post-creation checks used by `create()` (existence, qcow2 metadata, virtual-size,
actual-size ≤ 50% of virtual-size, corrupt bit, backing-filename) and SHALL perform ONE
`virsh domblklist` pivot check covering all disks. The returned list SHALL contain one
`SnapshotResult` per spec, in spec order. A single-disk VM is the degenerate case of
this method (one `--diskspec`). The single-disk `create()` method SHALL remain available
unchanged for compatibility and tests.

#### Scenario: Two-disk batch created with one virsh call

- **WHEN** `create_multi(vm_config, [spec_vda, spec_vdb], quiesce=True)` is called
- **THEN** exactly ONE `virsh snapshot-create-as` command is executed
- **AND** the command contains `--diskspec vda,file=<path_vda>,snapshot=external`
- **AND** the command contains `--diskspec vdb,file=<path_vdb>,snapshot=external`
- **AND** the command contains `--disk-only --atomic --no-metadata --quiesce`
- **AND** the result list has two successful `SnapshotResult` entries in spec order

#### Scenario: Single-disk degenerate case

- **WHEN** `create_multi(vm_config, [spec_vda], quiesce=False)` is called
- **THEN** exactly ONE `virsh snapshot-create-as` command is executed with one `--diskspec`
- **AND** the command does NOT contain `--quiesce`
- **AND** the result list has one successful `SnapshotResult`

#### Scenario: One file fails validation — whole batch reported failed

- **WHEN** virsh returns exit code 0 but `vdb`'s file fails the backing-filename check
- **THEN** the `SnapshotResult` for `vdb` has `success=False` with a descriptive error
- **AND** the caller treats the batch as failed (all-or-nothing state recording is Core's duty)

#### Scenario: virsh failure fails the whole batch

- **WHEN** the single `virsh snapshot-create-as` call returns a non-zero exit code
- **THEN** every `SnapshotResult` in the returned list has `success=False`
- **AND** the error carries the virsh stderr

#### Scenario: Batch timeout

- **WHEN** the batch call exceeds its timeout (180s with quiesce; otherwise
  `120 + 30 × (N − 1)` seconds for N disks)
- **THEN** every `SnapshotResult` has `success=False` with an error containing "timed out"

### Requirement: Batch leftover cleanup on failure

When `create_multi` fails (virsh error, timeout, or any file failing validation), the
provider SHALL best-effort remove the batch's snapshot files it created (via `rm -f`)
before returning. Files that cannot be removed SHALL be left for the next run's
pre-flight orphan detection. The provider SHALL NOT record or mutate any state.

#### Scenario: Validation failure removes created files

- **WHEN** virsh succeeds but one file fails validation
- **THEN** the provider best-effort `rm -f` removes all batch files
- **AND** returns the per-spec results with the failing one marked unsuccessful

## MODIFIED Requirements

### Requirement: Snapshot creation retry on lock conflict
`ExternalSnapshotProvider.create()` and `ExternalSnapshotProvider.create_multi()` SHALL
retry `virsh snapshot-create-as` up to 3 total attempts (1 initial + 2 retries) when the
error message contains "cannot acquire state change lock". Retry backoff SHALL be
exponential: 2 seconds, then 4 seconds. Non-lock errors SHALL NOT be retried. For
`create_multi()` the retry loop SHALL wrap the entire batch call (all `--diskspec`
arguments in one command), never individual disks.

#### Scenario: Lock conflict resolved on retry
- **WHEN** the first `virsh snapshot-create-as` attempt fails with "cannot acquire state change lock"
- **AND** the second attempt (after 2s backoff) succeeds
- **THEN** the module returns `SnapshotResult(success=True)`

#### Scenario: Lock conflict exhausted
- **WHEN** all 3 attempts fail with "cannot acquire state change lock"
- **THEN** the module returns `SnapshotResult(success=False)`

#### Scenario: Non-lock error not retried
- **WHEN** `virsh snapshot-create-as` fails with "domain not found"
- **THEN** the module returns `SnapshotResult(success=False)` without retrying

#### Scenario: Batch lock retry wraps the whole call
- **WHEN** `create_multi` is called for disks `vda`, `vdb` and the first attempt fails
  with "cannot acquire state change lock"
- **THEN** the retry re-executes the single batch command containing BOTH `--diskspec`
  arguments
- **AND** no per-disk virsh calls are made
