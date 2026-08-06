# Snapshot Provider

## Purpose

External disk-only snapshot creation, listing, and deletion via `virsh snapshot-create-as` and `qemu-img info`. Snapshots are per-disk — each configured disk gets its own snapshot file. Listing scans all disks' backing chains and tags each `SnapshotInfo` with its disk.

## Requirements

### Requirement: Per-disk external snapshot creation
The system SHALL create external disk-only snapshots per-disk via `virsh snapshot-create-as` with flags `--disk-only --atomic --no-metadata` and `--diskspec {disk},file={path},snapshot=external`. The method SHALL accept parameters `(vm_config, snapshot_name, disk, snapshot_path, quiesce=False)`. After creation, the system SHALL determine the allocation-size of the new image via `qemu-img info --force-share --output=json`. The `--force-share` flag is REQUIRED because the newly created snapshot file IS the active layer — the running VM holds an exclusive write lock on it.

After `virsh snapshot-create-as` returns exit code 0, the method SHALL perform post-creation validation:

1. **File existence**: `test -f <snapshot_path>` — verify the file landed on disk
2. **qcow2 metadata** (from `qemu-img info`): verify `format == "qcow2"`, check `virtual-size` matches the base image, verify `actual-size` is reasonable for an overlay (≤ 50% of `virtual-size`), confirm `incompatible-features` does not contain `"corrupt"`, and verify `backing-filename` points to the previous active layer
3. **libvirt pivot**: `virsh domblklist --domain <vm>` — verify source path = snapshot_path

#### Scenario: Successful snapshot creation with validation
- **WHEN** `create(vm_config, name, "vda", path, quiesce=False)` is called
- **AND** `virsh snapshot-create-as` returns exit code 0
- **AND** the snapshot file exists on disk (`test -f` succeeds)
- **AND** `qemu-img info` reports format `"qcow2"`, matching `virtual-size`, reasonable `actual-size`, no corrupt bit, correct `backing-filename`
- **AND** `virsh domblklist` shows the snapshot path as the active source for `vda`
- **THEN** the module returns `SnapshotResult(success=True, new_allocation=<parsed actual-size>)`

#### Scenario: virsh command fails
- **WHEN** `virsh snapshot-create-as` returns a non-zero exit code
- **THEN** the module returns `SnapshotResult(success=False, error=<stderr from virsh>)`

#### Scenario: virsh command times out
- **WHEN** `virsh snapshot-create-as` exceeds the timeout (120 seconds, 180 for quiesce)
- **THEN** the module returns `SnapshotResult(success=False)` with error containing "timed out"

#### Scenario: Post-snapshot qemu-img info uses --force-share on running VM
- **WHEN** a snapshot is created on a running VM
- **THEN** the subsequent `qemu-img info` command includes `--force-share`
- **AND** the command succeeds despite the VM holding a write lock on the new active layer

#### Scenario: Validation fails — file missing despite virsh success
- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** `test -f <snapshot_path>` fails (file does not exist)
- **THEN** `SnapshotResult(success=False, error="snapshot file not found on disk after virsh success")` is returned

#### Scenario: Validation fails — wrong backing-filename
- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** `qemu-img info` reports `backing-filename` pointing to a file that is NOT the previous active layer
- **THEN** `SnapshotResult(success=False, error="backing-filename mismatch")` is returned

#### Scenario: Validation fails — corrupt bit set
- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** `qemu-img info` reports `incompatible-features` containing `"corrupt"`
- **THEN** `SnapshotResult(success=False, error="snapshot has corrupt bit set")` is returned

#### Scenario: Validation fails — virtual-size mismatch
- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** the new snapshot's `virtual-size` differs from the previous active layer's `virtual-size`
- **THEN** `SnapshotResult(success=False, error="virtual-size mismatch")` is returned

#### Scenario: Validation fails — unreasonable actual-size
- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** the new snapshot's `actual-size` exceeds 50% of `virtual-size` (indicating a full copy, not an overlay)
- **THEN** `SnapshotResult(success=False, error="actual-size ... is unreasonable for a new overlay")` is returned

#### Scenario: Validation fails — libvirt pivot not confirmed
- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** `virsh domblklist` still shows the previous active layer (not the new snapshot path) for the disk
- **THEN** `SnapshotResult(success=False, error="libvirt pivot not confirmed")` is returned

### Requirement: Multi-disk snapshot listing via backing chain
The system SHALL obtain the list of existing snapshots by iterating all configured disks in `vm_config.disks`, resolving each disk's active path via `virsh domblklist`, scanning each disk's backing chain via `qemu-img info --backing-chain --output=json`, and building `SnapshotInfo` for every chain element except the base image. Each `SnapshotInfo` SHALL be tagged with its `disk` target. Results from all disks SHALL be merged into a single flat list sorted by timestamp.

#### Scenario: Multi-disk backing chains with snapshots
- **WHEN** VM has disks `vda` (3-element chain: base ← snap1 ← snap2) and `vdb` (2-element chain: base ← snap3)
- **THEN** `list()` returns a flat list of 3 `SnapshotInfo` (snap1, snap2 tagged `disk="vda"`; snap3 tagged `disk="vdb"`)
- **AND** snapshots are sorted oldest-first

#### Scenario: No snapshots exist (fresh VM)
- **WHEN** no disk has a chain longer than 1 element
- **THEN** `list()` returns an empty list

#### Scenario: Disk not found in domblklist
- **WHEN** a configured disk's target is not present in `virsh domblklist` output
- **THEN** a WARNING is logged and the disk's chain is skipped

### Requirement: Snapshot file deletion
The system SHALL delete a snapshot `.qcow2` file via `rm -f`. The method accepts a `SnapshotInfo` and returns a `ShellResult`.

#### Scenario: Successful file deletion
- **WHEN** `rm -f <snapshot.path>` completes successfully
- **THEN** the module returns `ShellResult(success=True)`

#### Scenario: File does not exist
- **WHEN** the snapshot file does not exist
- **THEN** `rm -f` returns success (idempotent operation)
- **AND** the module returns `ShellResult(success=True)`

### Requirement: Snapshot creation retry on lock conflict
`ExternalSnapshotProvider.create()` and `ExternalSnapshotProvider.create_multi()` SHALL retry `virsh snapshot-create-as` up to 3 total attempts (1 initial + 2 retries) when the error message contains "cannot acquire state change lock". Retry backoff SHALL be exponential: 2 seconds, then 4 seconds. Non-lock errors SHALL NOT be retried. For `create_multi()` the retry loop SHALL wrap the entire batch call (all `--diskspec` arguments in one command), never individual disks.

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
- **WHEN** `create_multi` is called for disks `vda` and `vdb` and the first virsh attempt fails with lock conflict
- **THEN** the ENTIRE batch call (both `--diskspec` arguments) is retried as one unit

### Requirement: Batch multi-disk snapshot creation via create_multi
`ISnapshotProvider` SHALL provide a method `create_multi(vm_config: VMConfig, specs: Sequence[SnapshotSpec], quiesce: bool) -> list[SnapshotResult]` where `SnapshotSpec` is a frozen dataclass with fields `disk: str`, `name: str`, `path: Path`. `ExternalSnapshotProvider.create_multi` SHALL create ALL given disks' snapshots with ONE `virsh snapshot-create-as` call containing one `--diskspec {disk},file={path},snapshot=external` argument per spec plus the flags `--disk-only --atomic --no-metadata`, and `--quiesce` when `quiesce=True`. The single call SHALL be wrapped by the same lock-conflict retry loop as `create()`. After virsh returns exit code 0, the provider SHALL validate every spec's file with the same post-creation checks used by `create()` (existence, qcow2 metadata, virtual-size, actual-size ≤ 50% of virtual-size, corrupt bit, backing-filename) and SHALL perform ONE `virsh domblklist` pivot check covering all disks. The returned list SHALL contain one `SnapshotResult` per spec, in spec order. A single-disk VM is the degenerate case of this method (one `--diskspec`). The single-disk `create()` method SHALL remain available unchanged for compatibility and tests.

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
- **WHEN** the batch call exceeds its timeout (180s with quiesce; otherwise `120 + 30 × (N − 1)` seconds for N disks)
- **THEN** every `SnapshotResult` has `success=False` with an error containing "timed out"

### Requirement: Batch leftover cleanup on failure
When `create_multi` fails (virsh error, timeout, or any file failing validation), the provider SHALL best-effort remove the batch's snapshot files it created (via `rm -f`) before returning. Files that cannot be removed SHALL be left for the next run's pre-flight orphan detection. The provider SHALL NOT record or mutate any state.

#### Scenario: Validation failure removes created files
- **WHEN** virsh succeeds but one file fails validation
- **THEN** the provider best-effort `rm -f` removes all batch files
- **AND** returns the per-spec results with the failing one marked unsuccessful
