# Quiesce Snapshot

## Purpose

Filesystem-consistent snapshots via qemu-guest-agent — uses `virsh --quiesce` flag to freeze guest filesystems during snapshot creation. Quiesce is VM-wide, so it is applied only to the first disk's snapshot when creating per-disk snapshots.

## Requirements

### Requirement: VMConfig snapshot_quiesce field
`VMConfig` SHALL have a `snapshot_quiesce: bool` field with default `False`. When `True`, Core SHALL pass `quiesce=True` only for the first disk's snapshot to avoid repeated guest-agent freezes.

#### Scenario: Quiesce enabled for first disk only
- **WHEN** `vm_config.snapshot_quiesce == True` and snapshots are created for disks `["vda", "vdb"]`
- **THEN** `ExternalSnapshotProvider.create()` is called with `quiesce=True` for `vda` (index 0)
- **AND** `ExternalSnapshotProvider.create()` is called with `quiesce=False` for `vdb` (index 1)

#### Scenario: Quiesce disabled (default)
- **WHEN** `vm_config.snapshot_quiesce` is unset or `False`
- **THEN** `virsh snapshot-create-as` is called without `--quiesce` for all disks

### Requirement: Quiesce failure handling
When `--quiesce` is used and the guest agent is not installed or not responding, `virsh snapshot-create-as` SHALL return a non-zero exit code. The provider SHALL return `SnapshotResult(success=False, error=...)` with the virsh error. It SHALL NOT silently fall back to a non-quiesced snapshot — application consistency is a hard requirement.

#### Scenario: Guest agent not installed
- **WHEN** `--quiesce` is passed but the VM has no qemu-guest-agent
- **THEN** `SnapshotResult(success=False, error=<virsh error about guest agent>)` is returned

#### Scenario: Quiesce snapshot timeout
- **WHEN** timeout for quiesce snapshot is extended to 180 seconds (vs. standard 120)
- **AND** the operation still exceeds this timeout
- **THEN** `SnapshotResult(success=False, error="timed out")` is returned

### Requirement: ExternalSnapshotProvider.create accepts quiesce parameter
`ExternalSnapshotProvider.create(vm_config, snapshot_name, disk, snapshot_path, quiesce=False)` SHALL accept an optional `quiesce: bool` parameter. When `True`, SHALL append `--quiesce` to the `virsh snapshot-create-as` command.

#### Scenario: Snapshot with quiesce enabled
- **WHEN** `provider.create(vm_config, name, "vda", path, quiesce=True)` is called
- **THEN** `virsh snapshot-create-as --quiesce` is executed

#### Scenario: Snapshot without quiesce (default)
- **WHEN** `provider.create(vm_config, name, "vda", path)` is called without quiesce
- **THEN** `virsh snapshot-create-as` is executed without `--quiesce`
