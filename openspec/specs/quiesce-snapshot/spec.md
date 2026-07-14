# Quiesce Snapshot

## Purpose

Filesystem-consistent snapshots via qemu-guest-agent — uses virsh --quiesce flag to freeze guest filesystems during snapshot creation.

## Requirements

### Requirement: VMConfig snapshot_quiesce field

`VMConfig` SHALL gain a `snapshot_quiesce: bool` field with default `False`. When `True`, `ExternalSnapshotProvider.create()` SHALL pass `--quiesce` to `virsh snapshot-create-as`.

#### Scenario: Quiesce enabled

- **WHEN** `vm_config.snapshot_quiesce == True`
- **THEN** `virsh snapshot-create-as --quiesce` is called

#### Scenario: Quiesce disabled (default)

- **WHEN** `vm_config.snapshot_quiesce` is unset or `False`
- **THEN** `virsh snapshot-create-as` is called without `--quiesce`

### Requirement: Quiesce failure handling

When `--quiesce` is used and the guest agent is not installed or not responding, `virsh snapshot-create-as` SHALL return a non-zero exit code. The provider SHALL return `SnapshotResult(success=False, error=...)` with the virsh error. It SHALL NOT silently fall back to a non-quiesced snapshot — application consistency is a hard requirement.

#### Scenario: Guest agent not installed

- **WHEN** `--quiesce` is passed but the VM has no qemu-guest-agent
- **THEN** `SnapshotResult(success=False, error=<virsh error about guest agent>)` is returned

#### Scenario: Quiesce snapshot timeout

- **WHEN** timeout for quiesce snapshot is extended to 180 seconds (vs. standard 120)
- **AND** the operation still exceeds this timeout
- **THEN** `SnapshotResult(success=False, error="timed out")` is returned
