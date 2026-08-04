## MODIFIED Requirements

### Requirement: Core.fork method
`Core` SHALL provide a `fork(name: str, output_path: Path, vm_filter: str | None = None) -> RestoreResult` method. It SHALL:
1. Resolve the snapshot/backup via `_resolve_snapshot()` which searches `IStateManager` and backup providers.
2. Estimate total chain size via `qemu-img info --force-share --backing-chain --output=json` and log the estimate.
3. If `self._dry_run` is True: log the planned conversion (source, output path, estimated size) at INFO level and return `RestoreResult(success=True)` WITHOUT executing any conversion or creating any file.
4. Execute the conversion via the shared standalone-image-conversion helper `convert_with_retry()` (`qemu-img convert --force-share -O qcow2 <source_path> <output_path>` with retry on retryable errors and best-effort partial-file cleanup).
5. Verify the output via `verify_standalone_image()` (M1 virtual-size equality with the source chain, M2 `qemu-img check`). On verification failure remove the output file and return a failed `RestoreResult`.
6. No XML manipulation, VM definition, or libvirt management is performed — creating a VM from the resulting image is the operator's responsibility.
7. No NBD pull-model is used — direct file read with `--force-share` is sufficient for all cases.

The `--as-vm`, `--storage`, and `--add-to-config` flags are REMOVED. The `deploy` subcommand is REMOVED. The `_append_vm_to_config()` method is REMOVED.

#### Scenario: fork from snapshot creates standalone qcow2
- **WHEN** `core.fork("myvm.20260701T1200", Path("/tmp/standalone.qcow2"))` is called
- **THEN** `qemu-img convert --force-share -O qcow2` is executed
- **AND** returns `RestoreResult(success=True, restored_path=Path("/tmp/standalone.qcow2"), chain_files=[Path("/tmp/standalone.qcow2")])`
- **AND** no `virsh dumpxml`, `virsh define`, or XML manipulation is performed

#### Scenario: fork from incremental backup flattens chain
- **WHEN** `core.fork("myvm.20260702T130000_def456", Path("/tmp/restored.qcow2"))` is called with an incremental backup
- **THEN** `qemu-img convert --force-share -O qcow2` flattens the entire backing chain (FULL + all dependents) into a single standalone file

#### Scenario: fork dry-run creates no file
- **WHEN** `core.fork("myvm.20260701T1200", Path("/tmp/standalone.qcow2"))` is called with `core.dry_run = True`
- **THEN** the chain-size estimate still runs (read-only)
- **AND** the planned conversion is logged at INFO level
- **AND** no `qemu-img convert` is executed and no output file exists afterwards
- **AND** returns `RestoreResult(success=True)`

#### Scenario: fork verifies output and removes it on verification failure
- **WHEN** conversion succeeds but `verify_standalone_image()` fails
- **THEN** the output file is removed
- **AND** returns `RestoreResult(success=False, error=<verification error>)`

### Requirement: Core.restore method
`Core` SHALL provide a `restore(name: str, vm_filter: str | None = None) -> RestoreResult` method. The `target_dir` parameter is REMOVED. Multi-disk: the restored disk is resolved from the snapshot record (`SnapshotInfo.disk`, falling back to `parse_disk_from_snapshot_name(name)`), and the result is written to THAT disk's base image (`vm_config.get_disk(disk).base_image`) — other disks of the VM are not touched. It SHALL:
1. Resolve the snapshot/backup via `_resolve_snapshot()` and determine the target disk.
2. Verify the VM is stopped via `is_vm_running()` — abort with error `"VM must be stopped for restore"` if running.
3. Pre-verify source chain integrity via `scan_backing_chain()` — abort if broken.
4. Create a standalone image at `<snapshot_dir>/<vm_name>.<disk>.restored.qcow2.tmp` via the shared helper `convert_with_retry()` (`qemu-img convert --force-share -O qcow2` with retry on retryable errors and partial-file cleanup).
5. Verify the temp image via `verify_standalone_image()` (M1 + M2) BEFORE replacement — on failure remove the temp file and abort without touching the base image.
6. Delete old snapshot overlay files of the restored disk only from `snapshot_dir` (best-effort, WARNING on failures; snapshots of other disks are kept).
7. Atomically replace the disk's base image via `os.replace(tmp_path, base_image)`.
8. Strip `<backingStore>` and update `<source file>` ONLY on the `<disk>` element whose `<target dev>` equals the restored disk, then `virsh define`.
9. Reset ONLY the restored disk's state via `IStateManager.reset_vm_disk_state(vm_name, disk)` and `IStateManager.reset_target_disk_state(target_path, vm_name, disk)` for each target — state of other disks and records of other VMs sharing a target are untouched.
10. Perform best-effort libvirt checkpoint cleanup for the restored disk ONLY: checkpoint names follow `qsnap-{target_hash}-{disk}-{timestamp}-{hex}`; only checkpoints whose third dash-separated segment equals the restored disk are deleted via `virsh checkpoint-delete --metadata`; legacy names without a disk segment are skipped with a WARNING.

The CLI SHALL offer `--dry-run` (log planned actions, execute nothing) and `--yes` (skip confirmation prompt). Without `--yes` and without `--dry-run`, the CLI SHALL prompt the operator for confirmation.

#### Scenario: restore from snapshot replaces VM disk
- **WHEN** `core.restore("myvm.20260701T1200")` is called on a stopped VM
- **THEN** `qemu-img convert --force-share -O qcow2` is executed to a temp file
- **AND** the temp file passes `verify_standalone_image()` before replacement
- **AND** `os.replace(tmp, base_image)` atomically replaces the base image
- **AND** domain XML is updated and redefined
- **AND** `reset_vm_disk_state(vm_name, disk)` and `reset_target_disk_state(target_path, vm_name, disk)` are called for each target

#### Scenario: restore aborts on running VM
- **WHEN** `core.restore("myvm.20260701T1200")` is called and the VM is running
- **THEN** returns `RestoreResult(success=False, error="VM must be stopped for restore")`

#### Scenario: restore aborts on broken source chain
- **WHEN** `core.restore("myvm.20260701T1200")` is called and `scan_backing_chain()` returns broken
- **THEN** returns `RestoreResult(success=False, error="Source backing chain is broken: ...")`

#### Scenario: restore aborts when temp image verification fails
- **WHEN** conversion succeeds but `verify_standalone_image()` fails on the temp file
- **THEN** the temp file is removed and the base image is NOT replaced
- **AND** returns `RestoreResult(success=False, error=<verification error>)`

#### Scenario: restore dry-run shows planned actions
- **WHEN** `core.restore("myvm.20260701T1200")` is called in dry-run mode
- **THEN** all planned actions are logged at INFO level
- **AND** no `qemu-img convert`, `os.replace`, or `virsh define` is executed
- **AND** returns `RestoreResult(success=True)`

#### Scenario: restore keeps other disks' state and checkpoints
- **WHEN** `core.restore(...)` completes for disk `vda` of a VM that also has disk `vdb`
- **THEN** `vdb` snapshots, allocation baseline, deferred operations, FULL records, and dependencies remain in state
- **AND** `vdb` checkpoints remain in libvirt
