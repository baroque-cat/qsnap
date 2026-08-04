## MODIFIED Requirements

### Requirement: qsnap fork command creates independent qcow2 from snapshot or backup
`qsnap fork <name> --output <path> [vm]` SHALL locate the named snapshot or backup via `Core._resolve_snapshot()` and create a standalone qcow2 file at the specified output path. The command SHALL NOT perform XML manipulation, VM definition, or any libvirt management operations.

The standalone qcow2 creation SHALL use the shared standalone-image-conversion helpers (`convert_with_retry`, which executes `qemu-img convert --force-share -O qcow2 <source_path> <output_path>` with retry on retryable errors and best-effort removal of the partial output file on failure) for all sources (snapshots and backups). The `--force-share` flag is required because the source snapshot may be the active layer of a running VM with an exclusive write lock.

After a successful conversion the output SHALL be verified via `verify_standalone_image()` (M1 virtual-size equality with the source chain, M2 `qemu-img check`). On verification failure the output file SHALL be removed and the command SHALL return a failed result.

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

#### Scenario: Fork verifies the converted output
- **WHEN** `qsnap fork ... --output /tmp/out.qcow2` completes conversion successfully
- **THEN** `verify_standalone_image()` runs M1 (virtual-size equality with the source chain) and M2 (`qemu-img check`) against the output
- **AND** the command succeeds only if both checks pass

#### Scenario: Fork removes output when verification fails
- **WHEN** conversion succeeds but `verify_standalone_image()` reports a failure
- **THEN** the output file is removed
- **AND** the command returns a failed result with the verification error

#### Scenario: Fork removes partial output when conversion fails
- **WHEN** `qemu-img convert` fails after writing a partial output file and retries are exhausted
- **THEN** the partial output file is removed best-effort
- **AND** no partial file remains at the output path

### Requirement: Core.fork method
`Core` SHALL provide a `fork(name: str, output_path: Path, vm_filter: str | None = None) -> RestoreResult` method. It SHALL reuse `Core._resolve_snapshot()` for snapshot/backup resolution, estimate chain size, then create the standalone qcow2 via the shared standalone-image-conversion helpers (`convert_with_retry` followed by `verify_standalone_image`), consulting `self._dry_run` after the read-only chain-size estimate. It SHALL NOT perform XML manipulation or VM definition. The returned `RestoreResult` SHALL include `disk` from `snapshot_info.disk`.

#### Scenario: fork returns RestoreResult on success
- **WHEN** `core.fork("myvm.20260701T120000_a1b2c3", Path("/var/lib/libvirt/images/clone.qcow2"))` completes
- **THEN** returns `RestoreResult(success=True, snapshot_name="myvm.20260701T120000_a1b2c3", restored_path=Path("/var/lib/libvirt/images/clone.qcow2"), chain_files=[restored_path], error=None, disk="vda")`

#### Scenario: fork fails on nonexistent snapshot
- **WHEN** `core.fork("nonexistent-snap", Path("/tmp/test.qcow2"))` is called
- **THEN** returns `RestoreResult(success=False, error="Snapshot not found: nonexistent-snap")`

#### Scenario: fork does not touch XML or state
- **WHEN** `core.fork(...)` completes successfully
- **THEN** no `virsh dumpxml`, `virsh define`, or `IStateManager` mutation occurs

#### Scenario: fork dry-run logs the plan and creates no file
- **WHEN** `core.fork("myvm.20260701T120000_a1b2c3", Path("/tmp/clone.qcow2"))` is called with `core.dry_run = True`
- **THEN** the read-only chain-size estimate (`qemu-img info --backing-chain --force-share`) still runs
- **AND** an INFO log message states the planned conversion with source, output path, and estimated size
- **AND** no `qemu-img convert` is executed and no output file is created
- **AND** returns `RestoreResult(success=True)`

## ADDED Requirements

### Requirement: Fork accepts a local dry-run flag
The `fork` subcommand SHALL accept a local `--dry-run` flag in addition to the global `--dry-run` / `-n` flag. When either is active, the CLI handler SHALL ensure `core.dry_run = True` before calling `Core.fork()`. The local flag SHALL be declared with `default=argparse.SUPPRESS` so that the global flag's value is not silently overridden when the local flag is absent (same pattern as the `reconcile` subcommand).

#### Scenario: Local --dry-run activates fork dry-run
- **WHEN** `qsnap fork myvm.20260701T120000_a1b2c3 --output /tmp/clone.qcow2 --dry-run` is executed
- **THEN** `core.dry_run` is `True` when `Core.fork()` runs
- **AND** no output file is created

#### Scenario: Global -n activates fork dry-run
- **WHEN** `qsnap -n fork myvm.20260701T120000_a1b2c3 --output /tmp/clone.qcow2` is executed
- **THEN** `core.dry_run` is `True` when `Core.fork()` runs
- **AND** no output file is created

#### Scenario: Fork without any dry-run flag converts normally
- **WHEN** `qsnap fork myvm.20260701T120000_a1b2c3 --output /tmp/clone.qcow2` is executed without dry-run flags
- **THEN** `core.dry_run` is `False` and the conversion executes
