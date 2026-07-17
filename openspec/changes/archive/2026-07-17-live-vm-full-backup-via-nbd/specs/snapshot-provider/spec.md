## MODIFIED Requirements

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
