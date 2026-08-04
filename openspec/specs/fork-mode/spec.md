# Fork Mode

## Purpose

One-command creation of a standalone qcow2 file from any qsnap-managed snapshot or backup. Uses `qemu-img convert --force-share -O qcow2` to flatten the backing chain into a single standalone file. The disk is resolved from the snapshot/backup name. No XML manipulation, VM definition, or libvirt management is performed.

## Requirements

### Requirement: qsnap fork command creates independent qcow2 from snapshot or backup
`qsnap fork <name> --output <path> [vm]` SHALL locate the named snapshot or backup via `Core._resolve_snapshot()` and create a standalone qcow2 file at the specified output path. The command SHALL NOT perform XML manipulation, VM definition, or any libvirt management operations.

The standalone qcow2 creation SHALL use `qemu-img convert --force-share -O qcow2 <source_path> <output_path>` for all sources (snapshots and backups). The `--force-share` flag is required because the source snapshot may be the active layer of a running VM with an exclusive write lock.

The chain-size estimation step (`qemu-img info --backing-chain --force-share`) SHALL log the expected size before conversion.

#### Scenario: Fork creates standalone writable qcow2 from snapshot
- **WHEN** `qsnap fork myvm.20260701T120000_a1b2c3 --output /var/lib/libvirt/images/myvm-clone.qcow2` is executed
- **THEN** `qemu-img convert --force-share -O qcow2 <source> /var/lib/libvirt/images/myvm-clone.qcow2` is executed
- **THEN** the resulting file has NO backing file (`qemu-img info` shows `backing file: <none>`)
- **AND** no `virsh dumpxml`, `virsh define`, or XML manipulation is performed

#### Scenario: Fork creates standalone qcow2 from backup target
- **WHEN** `qsnap fork backup.20260701T1200 --output /var/lib/libvirt/images/recovered.qcow2` is executed
- **THEN** the backup file is resolved via `_resolve_snapshot()` (same as `qsnap restore`)
- **THEN** `qemu-img convert --force-share -O qcow2 <target_path> /var/lib/libvirt/images/recovered.qcow2` is executed
- **THEN** the resulting file has NO backing file

#### Scenario: Fork from incremental backup flattens chain
- **WHEN** `qsnap fork vm.20260715T120000_a1b2c3 --output /tmp/recovered.qcow2` is executed and the backup is an incremental
- **THEN** `qemu-img convert` flattens the entire backing chain (FULL + increments) into a standalone qcow2

#### Scenario: Fork logs estimated size before converting
- **WHEN** `qsnap fork ...` is executed
- **THEN** an INFO log message shows the estimated chain size before conversion begins
- **AND** `qemu-img info --backing-chain --force-share` is used for estimation

#### Scenario: Fork fails on nonexistent snapshot
- **WHEN** `qsnap fork nonexistent --output /tmp/test.qcow2` is executed
- **THEN** exit code is 1 and an error message is printed

### Requirement: Core.fork method
`Core` SHALL provide a `fork(name: str, output_path: Path, vm_filter: str | None = None) -> RestoreResult` method. It SHALL reuse `Core._resolve_snapshot()` for snapshot/backup resolution, estimate chain size, then create the standalone qcow2 via `qemu-img convert --force-share -O qcow2`. It SHALL NOT perform XML manipulation or VM definition. The returned `RestoreResult` SHALL include `disk` from `snapshot_info.disk`.

#### Scenario: fork returns RestoreResult on success
- **WHEN** `core.fork("myvm.20260701T120000_a1b2c3", Path("/var/lib/libvirt/images/clone.qcow2"))` completes
- **THEN** returns `RestoreResult(success=True, snapshot_name="myvm.20260701T120000_a1b2c3", restored_path=Path("/var/lib/libvirt/images/clone.qcow2"), chain_files=[restored_path], error=None, disk="vda")`

#### Scenario: fork fails on nonexistent snapshot
- **WHEN** `core.fork("nonexistent-snap", Path("/tmp/test.qcow2"))` is called
- **THEN** returns `RestoreResult(success=False, error="Snapshot not found: nonexistent-snap")`

#### Scenario: fork does not touch XML or state
- **WHEN** `core.fork(...)` completes successfully
- **THEN** no `virsh dumpxml`, `virsh define`, or `IStateManager` mutation occurs
