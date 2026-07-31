# Restore Command

## Purpose

Replaces a stopped VM's disk with a flattened standalone qcow2 created from the named snapshot or backup. Performs full state cleanup (snapshots, FULLs, dependencies, baselines) and best-effort libvirt checkpoint cleanup after disk replacement.

## Requirements

### Requirement: Restore command copies backup chain to target directory
The `qsnap restore <name> [vm]` command SHALL replace a stopped VM's disk with a flattened standalone qcow2 created from the named snapshot or backup. The command SHALL:

1. Resolve the snapshot/backup via `Core._resolve_snapshot()`
2. Verify the VM is stopped via `is_vm_running()` — abort with error if running
3. Pre-verify source chain integrity via `scan_backing_chain()` — abort if broken
4. Create standalone image at temporary path via `qemu-img convert --force-share -O qcow2 <source> <snapshot_dir>/<vm>.restored.qcow2.tmp`
5. Atomically replace base image FIRST: `os.replace(<tmp>, <vm_config.base_image>)`
6. Delete old snapshot overlay files from `snapshot_dir` (best-effort AFTER atomic replace)
7. Strip `<backingStore>` elements AND update `<source file>` in domain XML in a single `virsh dumpxml` / XML modification / `virsh define` pass
8. Reset all VM state via `IStateManager.reset_vm_state(vm_name)` and `IStateManager.reset_target_state(target_path)` for each target
9. Best-effort cleanup of libvirt checkpoints: `virsh checkpoint-delete --domain <vm> --metadata <checkpoint>` for each `qsnap-*` checkpoint

The command SHALL accept `--dry-run` (show what would be done without executing) and `--yes` (skip confirmation prompt) flags. Without `--yes`, the command SHALL prompt for confirmation before performing destructive operations.

#### Scenario: Restore from snapshot replaces VM disk
- **WHEN** `qsnap restore myvm.20260701T120000_a1b2c3` is executed and the VM is stopped
- **THEN** `qemu-img convert --force-share -O qcow2 <snapshot_path> <snapshot_dir>/myvm.restored.qcow2.tmp` is executed
- **THEN** the temporary file atomically replaces `vm_config.base_image` via `os.replace()`
- **THEN** old snapshot overlay files in `snapshot_dir` are deleted (best-effort)
- **THEN** domain XML is updated in a single pass: `<backingStore>` elements removed, `<source file>` updated to the restored image
- **THEN** `virsh define` is called with the modified XML
- **AND** all VM state is reset (snapshots, allocation, deferred ops, FULLs, deps, baselines)

#### Scenario: Restore from backup target replaces VM disk
- **WHEN** `qsnap restore vm.FULL.20260701T000000_a1b2c3` is executed and the backup exists on a target
- **THEN** `qemu-img convert -O qcow2 <target_path> <snapshot_dir>/myvm.restored.qcow2.tmp` is executed (no `--force-share` needed)
- **THEN** the same disk replacement, XML update, and state cleanup occurs

#### Scenario: Restore aborts on running VM
- **WHEN** `qsnap restore myvm.20260701T120000_a1b2c3` is executed and `is_vm_running()` returns True
- **THEN** `RestoreResult(success=False, error="VM must be stopped for restore")` is returned
- **AND** no files are modified

#### Scenario: Restore aborts on broken source chain
- **WHEN** `qsnap restore myvm.20260701T120000_a1b2c3` is executed and `scan_backing_chain()` reports a broken chain
- **THEN** `RestoreResult(success=False, error="Source backing chain is broken: <details>")` is returned
- **AND** no files are modified

#### Scenario: Restore with --dry-run shows planned actions
- **WHEN** `qsnap restore myvm.20260701T120000_a1b2c3 --dry-run` is executed
- **THEN** planned actions are logged at INFO level
- **AND** no files are modified, no state is reset, no XML is changed

#### Scenario: Restore with --yes skips confirmation
- **WHEN** `qsnap restore myvm.20260701T120000_a1b2c3 --yes` is executed
- **THEN** no confirmation prompt is displayed
- **AND** the restore proceeds immediately

#### Scenario: Restore prompts for confirmation without --yes
- **WHEN** `qsnap restore myvm.20260701T120000_a1b2c3` is executed without `--yes`
- **THEN** a confirmation prompt is displayed warning about destructive operation
- **AND** the operator must confirm before proceeding

#### Scenario: Restore performs best-effort checkpoint cleanup
- **WHEN** restore completes the disk replacement and state reset
- **THEN** all libvirt checkpoints with `qsnap-` prefix are deleted via `virsh checkpoint-delete --domain <vm> --metadata <checkpoint>`
- **AND** checkpoint deletion failures are logged at WARNING level
- **AND** checkpoint failures do NOT block the restore operation

#### Scenario: Restore resets all VM state
- **WHEN** restore completes successfully
- **THEN** `IStateManager.reset_vm_state(vm_name)` is called (clears snapshots, last_allocation, deferred_operations)
- **AND** `IStateManager.reset_target_state(target_path)` is called for each target (clears full_backups, incremental_dependencies, last_backup_allocation)
- **AND** the next `qsnap run` will create a new snapshot and force a new FULL backup

#### Scenario: Restore from nonexistent snapshot
- **WHEN** `qsnap restore nonexistent-snap` is executed
- **THEN** exit code is 1 and an error message is printed

### Requirement: Core.restore method
`Core` SHALL provide a `restore(name: str, vm_filter: str | None = None) -> RestoreResult` method. It SHALL search snapshots and backups across all configured VMs for the named snapshot via `_resolve_snapshot()`. It SHALL verify the VM is stopped, pre-verify chain integrity, create a standalone image, replace the VM's disk, update domain XML, reset state, and cleanup checkpoints. The `target_dir` parameter is REMOVED — the result is written to `vm_config.base_image`.

#### Scenario: Restore from snapshot
- **WHEN** `core.restore("vm.20250101T1200")` is called and the snapshot exists in `IStateManager` records
- **THEN** the VM's disk is replaced with a flattened standalone qcow2 from the snapshot

#### Scenario: Restore from backup
- **WHEN** `core.restore("vm.20250101T1200")` is called and the snapshot is found on a backup target
- **THEN** the VM's disk is replaced with a flattened standalone qcow2 from the backup

#### Scenario: Restore fails on running VM
- **WHEN** `core.restore("vm.20250101T1200")` is called and `is_vm_running()` returns True
- **THEN** `RestoreResult(success=False, error="VM must be stopped for restore")` is returned

### Requirement: Snapshot resolution exposes shared primitives for fork
`Core` SHALL provide a `_resolve_snapshot(snapshot_name: str, vm_filter: str | None = None) -> tuple[SnapshotInfo, VMConfig]` method that locates a snapshot by name across all sources (IStateManager and backup providers) and returns both the `SnapshotInfo` and the `VMConfig` it belongs to. This method SHALL be used internally by both `restore()` and `fork()`.

#### Scenario: _resolve_snapshot finds snapshot in state
- **WHEN** `_resolve_snapshot("myvm.20260701T1200")` is called and the snapshot exists in IStateManager
- **THEN** returns `(SnapshotInfo(name="myvm.20260701T1200", ...), VMConfig(name="myvm", ...))`

#### Scenario: _resolve_snapshot finds snapshot in backup
- **WHEN** `_resolve_snapshot("vm.FULL.20260701T000000_a1b2c3")` is called and the snapshot exists on a backup target
- **THEN** returns `(SnapshotInfo(name="vm.FULL.20260701T000000_a1b2c3", ...), VMConfig(...))`

#### Scenario: _resolve_snapshot raises on not found
- **WHEN** `_resolve_snapshot("nonexistent")` is called
- **THEN** raises `FileNotFoundError` with message "Snapshot not found: nonexistent"

### Requirement: RestoreResult type
The system SHALL provide a `RestoreResult` frozen dataclass with fields: `success: bool`, `snapshot_name: str`, `restored_path: Path`, `chain_files: list[Path]`, `error: str | None`.

#### Scenario: Successful restore result
- **WHEN** restore completes successfully
- **THEN** `RestoreResult(success=True, chain_files=[...])` is returned with all copied file paths
