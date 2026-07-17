# Snapshot Provider

## Purpose

External disk-only snapshot creation, listing, and deletion via `virsh snapshot-create-as` and `qemu-img info`.
Provides the `ISnapshotProvider` interface — the primary mechanism for creating qcow2 external snapshots of QEMU/KVM VMs.

## Requirements

### Requirement: External disk-only snapshot creation
The system SHALL create external disk-only snapshots via `virsh snapshot-create-as` with flags `--disk-only --atomic --no-metadata`. After snapshot creation, the system SHALL determine the allocation-size of the new image via `qemu-img info --force-share --output=json`. The `--force-share` flag is REQUIRED because the newly created snapshot file IS the active layer — the running VM holds an exclusive write lock on it. Without `--force-share`, `qemu-img info` fails with a lock error. The method SHALL accept an optional `quiesce: bool = False` parameter to request guest-agent filesystem freeze.

#### Scenario: Successful snapshot creation
- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **THEN** the module returns `SnapshotResult(success=True, new_allocation=<parsed actual-size>)`
- **AND** `new_allocation` equals the `actual-size` value from `qemu-img info --force-share --output=json`

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

#### Scenario: Post-snapshot qemu-img info without --force-share fails (regression guard)
- **WHEN** a snapshot is created on a running VM
- **AND** the `qemu-img info` command does NOT include `--force-share`
- **THEN** the command fails with "Failed to get shared lock" or similar lock error
- **AND** `SnapshotResult(success=False)` would be returned (this scenario documents the bug being fixed)

### Requirement: Snapshot listing via backing chain

The system SHALL obtain the list of existing snapshots via `qemu-img info --backing-chain --output=json` on the active VM disk image. For each chain element (except the base image itself) the system SHALL create a `SnapshotInfo` with name, path, timestamp, and allocation.

#### Scenario: Backing chain with snapshots

- **WHEN** the active image has a backing chain of 3 elements (base ← snap1 ← snap2)
- **THEN** `list()` returns a list of 2 `SnapshotInfo` (for snap1 and snap2)
- **AND** snapshots are sorted oldest-first

#### Scenario: No snapshots exist (fresh VM)

- **WHEN** the active image has no backing chain (only base)
- **THEN** `list()` returns an empty list

### Requirement: Snapshot file deletion

The system SHALL delete a snapshot `.qcow2` file via `rm -f`. The method accepts a `SnapshotInfo` and returns a `ShellResult`.

#### Scenario: Successful file deletion

- **WHEN** `rm -f <snapshot.path>` completes successfully
- **THEN** the module returns `ShellResult(success=True)`

#### Scenario: File does not exist

- **WHEN** the snapshot file does not exist
- **THEN** `rm -f` returns success (idempotent operation)
- **AND** the module returns `ShellResult(success=True)`

### Requirement: Quiesce support in snapshot creation
`ExternalSnapshotProvider.create()` SHALL accept an optional `quiesce: bool = False` parameter. When `True`, SHALL pass `--quiesce` to `virsh snapshot-create-as`. Timeout SHALL be extended to 180 seconds for quiesce operations.

#### Scenario: Snapshot with quiesce enabled
- **WHEN** `provider.create(vm_config, name, disk, path, quiesce=True)` is called
- **THEN** `virsh snapshot-create-as --quiesce` is executed

#### Scenario: Snapshot without quiesce (default)
- **WHEN** `provider.create(vm_config, name, disk, path)` is called without quiesce
- **THEN** `virsh snapshot-create-as` is executed without `--quiesce`

### Requirement: Snapshot creation retry on lock conflict

`ExternalSnapshotProvider.create()` SHALL retry `virsh snapshot-create-as` up to 3 total attempts (1 initial + 2 retries) when the error message contains "cannot acquire state change lock". Retry backoff SHALL be exponential: 2 seconds, then 4 seconds. Non-lock errors SHALL NOT be retried.
