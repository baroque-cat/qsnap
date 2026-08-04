# Restore Command

## Purpose

Replaces a stopped VM's disk with a flattened standalone qcow2 created from the named snapshot or backup. The disk is resolved from the snapshot/backup name — only that disk is touched. Performs full state cleanup and best-effort libvirt checkpoint cleanup.

## Requirements

### Requirement: Restore command replaces the resolved disk atomically
The `qsnap restore <name> [vm]` command SHALL replace a stopped VM's disk with a flattened standalone qcow2 created from the named snapshot or backup. The disk SHALL be resolved from `SnapshotInfo.disk` (or `parse_disk_from_snapshot_name` as fallback) — there is no `--disk` CLI flag. The command SHALL:

1. Resolve the snapshot/backup via `Core._resolve_snapshot()`
2. Resolve the disk: `disk = snapshot_info.disk or parse_disk_from_snapshot_name(name)`
3. Resolve `disk_cfg = vm_config.get_disk(disk)` and `base_image = disk_cfg.base_image`
4. Verify the VM is stopped via `is_vm_running()` — abort with error if running
5. Pre-verify source chain integrity via `scan_backing_chain()` — abort if broken
6. Create standalone image at temporary path `<snapshot_dir>/<vm>.<disk>.restored.qcow2.tmp` via `qemu-img convert --force-share -O qcow2`
7. Atomically replace ONLY that disk's base image: `os.replace(<tmp>, base_image)`
8. Delete old snapshot overlay files for THAT DISK only (skip `snap.disk != disk`)
9. Strip `<backingStore>` elements AND update `<source file>` only in the `<disk target='{disk}'>` element of domain XML
10. Reset all VM state via `IStateManager.reset_vm_state(vm_name)` and `IStateManager.reset_target_state(target_path)` for each target
11. Best-effort cleanup of libvirt checkpoints: `virsh checkpoint-delete --domain <vm> --metadata <checkpoint>` for each `qsnap-*` checkpoint

The command SHALL accept `--dry-run` and `--yes` flags. Without `--yes`, the command SHALL prompt for confirmation.

#### Scenario: Restore from snapshot replaces the resolved disk
- **WHEN** `qsnap restore myvm.20260701T120000_a1b2c3` is executed and the snapshot's `disk` field is `"vda"`
- **THEN** `disk_cfg = vm_config.get_disk("vda")` and `base_image = disk_cfg.base_image` are used
- **THEN** the temp file is `{snapshot_dir}/myvm.vda.restored.qcow2.tmp`
- **THEN** only `disk_cfg.base_image` is replaced via `os.replace()`
- **THEN** only snapshot overlays with `snap.disk == "vda"` are deleted
- **THEN** only the `<disk target='vda'>` XML element is updated
- **AND** `RestoreResult(disk="vda")` is returned

#### Scenario: Restore from backup with disk in filename
- **WHEN** `qsnap restore vm.FULL.20260701T000000_a1b2c3` is executed and the backup name contains a disk identifier
- **THEN** the disk is resolved from the backup `SnapshotInfo.disk` or parsed from the filename
- **THEN** the same single-disk replacement occurs for that disk only

#### Scenario: Restore aborts on running VM
- **WHEN** `qsnap restore myvm.20260701T120000_a1b2c3` is executed and `is_vm_running()` returns True
- **THEN** `RestoreResult(success=False, error="VM must be stopped for restore", disk=disk)` is returned
- **AND** no files are modified

#### Scenario: Restore aborts on broken source chain
- **WHEN** `qsnap restore myvm.20260701T120000_a1b2c3` is executed and `scan_backing_chain()` reports a broken chain
- **THEN** `RestoreResult(success=False, error="Source backing chain is broken: <details>", disk=disk)` is returned
- **AND** no files are modified

#### Scenario: Restore aborts when disk cannot be determined
- **WHEN** `qsnap restore ambiguous_snapshot` is executed and neither `SnapshotInfo.disk` nor `parse_disk_from_snapshot_name()` yields a disk
- **THEN** `RestoreResult(success=False, error="Cannot determine disk for snapshot: ambiguous_snapshot")` is returned

#### Scenario: Restore aborts when disk is not in VM config
- **WHEN** the resolved disk target does not exist in `vm_config.disks`
- **THEN** `RestoreResult(success=False, error="Disk 'vdz' not configured for VM myvm")` is returned

#### Scenario: Restore with --dry-run shows planned actions
- **WHEN** `qsnap restore myvm.20260701T120000_a1b2c3 --dry-run` is executed
- **THEN** planned actions are logged at INFO level with the resolved disk target
- **AND** no files are modified, no state is reset, no XML is changed

#### Scenario: Restore with --yes skips confirmation
- **WHEN** `qsnap restore myvm.20260701T120000_a1b2c3 --yes` is executed
- **THEN** no confirmation prompt is displayed
- **AND** the restore proceeds immediately

#### Scenario: Restore prompts for confirmation without --yes
- **WHEN** `qsnap restore myvm.20260701T120000_a1b2c3` is executed without `--yes`
- **THEN** a confirmation prompt is displayed
- **AND** the operator must confirm before proceeding

#### Scenario: Restore performs best-effort checkpoint cleanup
- **WHEN** restore completes the disk replacement and state reset
- **THEN** all libvirt checkpoints with `qsnap-` prefix are deleted via `virsh checkpoint-delete --domain <vm> --metadata <checkpoint>`
- **AND** checkpoint deletion failures are logged at WARNING level and do NOT block the operation

#### Scenario: Restore resets all VM state
- **WHEN** restore completes successfully
- **THEN** `IStateManager.reset_vm_state(vm_name)` is called (clears snapshots, last_allocation, deferred_operations)
- **AND** `IStateManager.reset_target_state(target_path)` is called for each target (clears full_backups, incremental_dependencies, last_backup_allocation)

#### Scenario: Restore from nonexistent snapshot
- **WHEN** `qsnap restore nonexistent-snap` is executed
- **THEN** exit code is 1 and an error message is printed

### Requirement: Core.restore method
`Core` SHALL provide a `restore(name: str, vm_filter: str | None = None) -> RestoreResult` method. It SHALL resolve the disk from `SnapshotInfo.disk` (or `parse_disk_from_snapshot_name` as fallback), replace only that disk's `base_image`, update only that disk's `<disk>` element in domain XML, delete only that disk's snapshot overlays, reset state, and clean up checkpoints. The `target_dir` parameter is REMOVED — the result is written to `disk_cfg.base_image`.

#### Scenario: Restore from snapshot identifies disk
- **WHEN** `core.restore("vm.20250101T1200")` is called and the snapshot exists with `disk="vda"`
- **THEN** the VM's `vda` disk base_image is replaced; other disks are untouched

#### Scenario: Restore from backup identifies disk
- **WHEN** `core.restore("vm.20250101T1200")` is called and the snapshot is found on a backup target
- **THEN** the disk is resolved from the backup's `SnapshotInfo.disk` and only that disk is restored

#### Scenario: Restore fails on running VM
- **WHEN** `core.restore("vm.20250101T1200")` is called and `is_vm_running()` returns True
- **THEN** `RestoreResult(success=False, error="VM must be stopped for restore")` is returned

### Requirement: Snapshot resolution exposes shared primitives for fork
`Core` SHALL provide a `_resolve_snapshot(snapshot_name: str, vm_filter: str | None = None) -> tuple[SnapshotInfo, VMConfig]` method that locates a snapshot by name across all sources (IStateManager and backup providers) and returns both the `SnapshotInfo` and the `VMConfig`. The `SnapshotInfo` carries a `disk` field identifying which disk it belongs to. This method SHALL be used internally by both `restore()` and `fork()`.

#### Scenario: _resolve_snapshot finds snapshot in state
- **WHEN** `_resolve_snapshot("myvm.20260701T1200")` is called and the snapshot exists in IStateManager
- **THEN** returns `(SnapshotInfo(name="myvm.20260701T1200", disk="vda", ...), VMConfig(name="myvm", ...))`

#### Scenario: _resolve_snapshot finds snapshot in backup
- **WHEN** `_resolve_snapshot("vm.FULL.20260701T000000_a1b2c3")` is called and the snapshot exists on a backup target
- **THEN** returns `(SnapshotInfo(name="vm.FULL.20260701T000000_a1b2c3", disk="vda", ...), VMConfig(...))`

#### Scenario: _resolve_snapshot raises on not found
- **WHEN** `_resolve_snapshot("nonexistent")` is called
- **THEN** raises `FileNotFoundError` with message `"Snapshot not found: nonexistent"`

### Requirement: RestoreResult type
The system SHALL provide a `RestoreResult` frozen dataclass with fields: `success: bool`, `snapshot_name: str`, `restored_path: Path`, `chain_files: list[Path]`, `error: str | None`, `disk: str | None`. The `disk` field SHALL identify the disk target (e.g. `"vda"`) that was restored.

#### Scenario: Successful restore result
- **WHEN** restore completes successfully for disk `vda`
- **THEN** `RestoreResult(success=True, restored_path=Path(".../disk.qcow2"), chain_files=[...], disk="vda")` is returned

#### Scenario: Failed restore result still carries disk
- **WHEN** restore fails after resolving the disk
- **THEN** the `RestoreResult` SHALL include the `disk` field for diagnostic context
