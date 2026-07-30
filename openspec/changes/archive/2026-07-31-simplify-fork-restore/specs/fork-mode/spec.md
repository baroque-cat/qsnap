## MODIFIED Requirements

### Requirement: qsnap fork command creates independent VM from snapshot
`qsnap fork <name> --output <path> [vm]` SHALL locate the named snapshot or backup (via `IStateManager` and backup providers, reusing `Core._resolve_snapshot()` resolution logic) and create a standalone qcow2 file at the specified output path. The command SHALL NOT perform XML manipulation, VM definition, or any libvirt management operations — creating a VM from the resulting image is the operator's responsibility.

The standalone qcow2 creation SHALL use `qemu-img convert --force-share -O qcow2 <source_path> <output_path>` for all sources (snapshots and backups). The `--force-share` flag is required because the source snapshot may be the active layer of a running VM with an exclusive write lock. NBD pull-model SHALL NOT be used — direct file read with `--force-share` is sufficient for all cases.

For snapshots that are the active layer of a running VM, `--force-share` allows reading while the VM writes. The resulting image MAY be inconsistent. Operators requiring consistency SHALL stop the VM or fork a previous (non-active) snapshot.

The chain-size estimation step (`qemu-img info --backing-chain --force-share`) SHALL log the expected size before conversion.

#### Scenario: Fork creates standalone writable qcow2 from snapshot
- **WHEN** `qsnap fork myvm.20260701T120000_a1b2c3 --output /var/lib/libvirt/images/myvm-clone.qcow2` is executed
- **THEN** `qemu-img convert --force-share -O qcow2 /source/snapshots/myvm.20260701T120000_a1b2c3.qcow2 /var/lib/libvirt/images/myvm-clone.qcow2` is executed
- **THEN** the resulting file has NO backing file (`qemu-img info` shows `backing file: <none>`)
- **THEN** the file is writable
- **AND** no `virsh dumpxml`, `virsh define`, or XML manipulation is performed

#### Scenario: Fork creates standalone qcow2 from backup target
- **WHEN** `qsnap fork backup.20260701T1200 --output /var/lib/libvirt/images/recovered.qcow2` is executed and the backup exists on a backup target
- **THEN** the backup file is resolved via backup provider listing (same as `qsnap restore`)
- **THEN** `qemu-img convert -O qcow2 <target_path> /var/lib/libvirt/images/recovered.qcow2` is executed (no `--force-share` needed for target files)
- **THEN** the resulting file has NO backing file

#### Scenario: Fork from incremental backup flattens chain
- **WHEN** `qsnap fork vm.20260715T120000_a1b2c3 --output /tmp/recovered.qcow2` is executed and the backup is an incremental (has backing dependencies on the target)
- **THEN** `qemu-img convert` flattens the entire backing chain (FULL + increments) into a standalone qcow2

#### Scenario: Fork logs estimated size before converting
- **WHEN** `qsnap fork ...` is executed
- **THEN** an INFO log message shows the estimated chain size before conversion begins
- **AND** `qemu-img info --backing-chain --force-share` is used for estimation

#### Scenario: Fork fails on nonexistent snapshot
- **WHEN** `qsnap fork nonexistent --output /tmp/test.qcow2` is executed
- **THEN** exit code is 1 and an error message is printed

### Requirement: Core.fork method
`Core` SHALL provide a `fork(name: str, output_path: Path, vm_filter: str | None = None) -> RestoreResult` method. It SHALL reuse `Core._resolve_snapshot()` for snapshot/backup resolution, then create the standalone qcow2 via `IShell.run()` with `qemu-img convert --force-share -O qcow2`. It SHALL NOT perform XML manipulation or VM definition.

#### Scenario: fork returns RestoreResult on success
- **WHEN** `core.fork("myvm.20260701T120000_a1b2c3", Path("/var/lib/libvirt/images/myvm-clone.qcow2"))` completes
- **THEN** returns `RestoreResult(success=True, snapshot_name="myvm.20260701T120000_a1b2c3", restored_path=Path("/var/lib/libvirt/images/myvm-clone.qcow2"), chain_files=[restored_path], error=None)`

#### Scenario: fork fails on nonexistent snapshot
- **WHEN** `core.fork("nonexistent-snap", Path("/tmp/test.qcow2"))` is called
- **THEN** returns `RestoreResult(success=False, error="Snapshot not found: nonexistent-snap")`

## REMOVED Requirements

### Requirement: Fork defines new libvirt VM
**Reason**: Fork is simplified to standalone image creation only. VM definition is the operator's responsibility.
**Migration**: Use `virsh define` manually after `qsnap fork` to create a VM from the resulting standalone qcow2.

### Requirement: Fork generates unique VM UUID
**Reason**: Fork no longer creates VMs or manipulates XML. UUID generation is unnecessary.
**Migration**: Generate UUIDs manually when defining the new VM via `virsh define`.

### Requirement: Fork with --add-to-config
**Reason**: Fork no longer manages VM configuration. The `--add-to-config` flag is removed.
**Migration**: Manually add a `[[vm]]` block to the qsnap config file after fork.

### Requirement: qsnap deploy command deploys backup as VM
**Reason**: `deploy` was a thin wrapper around `fork()`. With fork simplified, deploy is redundant.
**Migration**: Use `qsnap fork <backup_name> --output <path>` directly.
