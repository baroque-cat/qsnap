## ADDED Requirements

### Requirement: Quiesce support in snapshot creation
`ExternalSnapshotProvider.create()` SHALL accept an optional `quiesce: bool = False` parameter. When `True`, SHALL pass `--quiesce` to `virsh snapshot-create-as`. Timeout SHALL be extended to 180 seconds for quiesce operations.

#### Scenario: Snapshot with quiesce enabled
- **WHEN** `provider.create(vm_config, name, disk, path, quiesce=True)` is called
- **THEN** `virsh snapshot-create-as --quiesce` is executed

#### Scenario: Snapshot without quiesce (default)
- **WHEN** `provider.create(vm_config, name, disk, path)` is called without quiesce
- **THEN** `virsh snapshot-create-as` is executed without `--quiesce`

## MODIFIED Requirements

### Requirement: External disk-only snapshot creation
The system SHALL create external disk-only snapshots via `virsh snapshot-create-as` with flags `--disk-only --atomic --no-metadata`. After snapshot creation, the system SHALL determine the allocation-size of the new image via `qemu-img info --output=json`. The method SHALL accept an optional `quiesce: bool = False` parameter to request guest-agent filesystem freeze.

#### Scenario: Successful snapshot creation
- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **THEN** the module returns `SnapshotResult(success=True, new_allocation=<parsed actual-size>)`
- **AND** `new_allocation` equals the `actual-size` value from `qemu-img info --output=json`

#### Scenario: virsh command fails
- **WHEN** `virsh snapshot-create-as` returns a non-zero exit code
- **THEN** the module returns `SnapshotResult(success=False, error=<stderr from virsh>)`

#### Scenario: virsh command times out
- **WHEN** `virsh snapshot-create-as` exceeds the timeout (120 seconds, 180 for quiesce)
- **THEN** the module returns `SnapshotResult(success=False)` with error containing "timed out"
