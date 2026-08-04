## MODIFIED Requirements

### Requirement: Restore command replaces the resolved disk atomically
The `qsnap restore <name> [vm]` command SHALL replace a stopped VM's disk with a flattened standalone qcow2 created from the named snapshot or backup. The disk SHALL be resolved from `SnapshotInfo.disk` (or `parse_disk_from_snapshot_name` as fallback) — there is no `--disk` CLI flag. The command SHALL:

1. Resolve the snapshot/backup via `Core._resolve_snapshot()`
2. Resolve the disk: `disk = snapshot_info.disk or parse_disk_from_snapshot_name(name)`
3. Resolve `disk_cfg = vm_config.get_disk(disk)` and `base_image = disk_cfg.base_image`
4. Verify the VM is stopped via `is_vm_running()` — abort with error if running
5. Pre-verify source chain integrity via `scan_backing_chain()` — abort if broken
6. Create standalone image at temporary path `<snapshot_dir>/<vm>.<disk>.restored.qcow2.tmp` via the shared standalone-image-conversion helper (`convert_with_retry`, which runs `qemu-img convert --force-share -O qcow2` with retry on retryable errors and best-effort partial-file cleanup)
7. Verify the temporary image via `verify_standalone_image()` (M1 virtual-size equality with the source chain, M2 `qemu-img check`) BEFORE replacement — on verification failure remove the temp file and abort; the base image SHALL NOT be replaced with an unverified image
8. Atomically replace ONLY that disk's base image: `os.replace(<tmp>, base_image)`
9. Delete old snapshot overlay files for THAT DISK only (skip `snap.disk != disk`)
10. Strip `<backingStore>` elements AND update `<source file>` only in the `<disk target='{disk}'>` element of domain XML
11. Reset ONLY the restored disk's state: `IStateManager.reset_vm_disk_state(vm_name, disk)` and `IStateManager.reset_target_disk_state(target_path, vm_name, disk)` for each target. State of other disks of the VM and records of other VMs sharing a target SHALL NOT be touched.
12. Best-effort cleanup of libvirt checkpoints of the restored disk ONLY: checkpoint names follow `qsnap-{target_hash}-{disk}-{timestamp}-{hex}`; only checkpoints whose third dash-separated segment equals the restored disk SHALL be deleted via `virsh checkpoint-delete --domain <vm> --metadata <checkpoint>`. Checkpoint names without a disk segment (legacy format) SHALL NOT be deleted and SHALL be logged at WARNING level.

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

#### Scenario: Restore verifies the temp image before replacing the base
- **WHEN** conversion of the temp image completes
- **THEN** `verify_standalone_image()` runs M1 and M2 against the temp file BEFORE `os.replace()`
- **AND** on verification failure the temp file is removed, the base image is untouched, and a failed `RestoreResult` is returned

#### Scenario: Restore cleans up only the restored disk's checkpoints
- **WHEN** restore completes the disk replacement for disk `vda` and libvirt holds checkpoints `qsnap-abc123-vda-20260701T120000-a1b2c3` and `qsnap-abc123-vdb-20260701T120000-d4e5f6`
- **THEN** only the `vda` checkpoint is deleted via `virsh checkpoint-delete --domain <vm> --metadata <checkpoint>`
- **AND** the `vdb` checkpoint remains
- **AND** checkpoint deletion failures are logged at WARNING level and do NOT block the operation

#### Scenario: Restore skips legacy checkpoints without a disk segment
- **WHEN** libvirt holds a checkpoint named `qsnap-abc123-20260701T120000-a1b2c3` (no disk segment)
- **THEN** the checkpoint is NOT deleted
- **AND** a WARNING log message names the skipped checkpoint

#### Scenario: Restore resets only the restored disk's state
- **WHEN** restore of disk `vda` completes successfully
- **THEN** `IStateManager.reset_vm_disk_state(vm_name, "vda")` is called (clears only `vda` snapshots, `vda` allocation baseline, `vda` deferred operations)
- **AND** `IStateManager.reset_target_disk_state(target_path, vm_name, "vda")` is called for each target
- **AND** `reset_vm_state()` and `reset_target_state()` are NOT called

#### Scenario: Restore leaves other disks and other VMs intact
- **WHEN** restore of `myvm` disk `vda` completes and the VM also has disk `vdb` with snapshots and FULL records, and another VM `othervm` shares a target
- **THEN** `myvm` `vdb` snapshots, allocation baseline, and deferred operations remain in state
- **AND** `myvm` `vdb` FULL records and dependencies on the shared target remain
- **AND** `othervm` FULL records and dependencies on the shared target remain

#### Scenario: Restore from nonexistent snapshot
- **WHEN** `qsnap restore nonexistent-snap` is executed
- **THEN** exit code is 1 and an error message is printed

### Requirement: Core.restore method
`Core` SHALL provide a `restore(name: str, vm_filter: str | None = None) -> RestoreResult` method. It SHALL resolve the disk from `SnapshotInfo.disk` (or `parse_disk_from_snapshot_name` as fallback), replace only that disk's `base_image` (after verifying the converted temp image), update only that disk's `<disk>` element in domain XML, delete only that disk's snapshot overlays, reset only that disk's state via `reset_vm_disk_state()` and `reset_target_disk_state()`, and clean up only that disk's checkpoints. The `target_dir` parameter is REMOVED — the result is written to `disk_cfg.base_image`.

#### Scenario: Restore from snapshot identifies disk
- **WHEN** `core.restore("vm.20250101T1200")` is called and the snapshot exists with `disk="vda"`
- **THEN** the VM's `vda` disk base_image is replaced; other disks are untouched

#### Scenario: Restore from backup identifies disk
- **WHEN** `core.restore("vm.20250101T1200")` is called and the snapshot is found on a backup target
- **THEN** the disk is resolved from the backup's `SnapshotInfo.disk` and only that disk is restored

#### Scenario: Restore fails on running VM
- **WHEN** `core.restore("vm.20250101T1200")` is called and `is_vm_running()` returns True
- **THEN** `RestoreResult(success=False, error="VM must be stopped for restore")` is returned
