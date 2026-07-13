# Snapshot Provider

## Purpose

External disk-only snapshot creation, listing, and deletion via `virsh snapshot-create-as` and `qemu-img info`.
Provides the `ISnapshotProvider` interface — the primary mechanism for creating qcow2 external snapshots of QEMU/KVM VMs.

## Requirements

### Requirement: External disk-only snapshot creation

The system SHALL create external disk-only snapshots via `virsh snapshot-create-as` with flags `--disk-only --atomic --no-metadata`. After snapshot creation, the system SHALL determine the allocation-size of the new image via `qemu-img info --output=json`.

#### Scenario: Successful snapshot creation

- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **THEN** the module returns `SnapshotResult(success=True, new_allocation=<parsed actual-size>)`
- **AND** `new_allocation` equals the `actual-size` value from `qemu-img info --output=json`

#### Scenario: virsh command fails

- **WHEN** `virsh snapshot-create-as` returns a non-zero exit code
- **THEN** the module returns `SnapshotResult(success=False, error=<stderr from virsh>)`

#### Scenario: virsh command times out

- **WHEN** `virsh snapshot-create-as` exceeds the timeout (120 seconds)
- **THEN** the module returns `SnapshotResult(success=False)` with error containing "timed out"

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
