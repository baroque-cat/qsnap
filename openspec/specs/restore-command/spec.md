# Restore Command

## Purpose

Replaces a stopped VM's disk with a flattened standalone qcow2 created from the named snapshot or backup. The disk is resolved from the snapshot/backup name — only that disk is touched. Performs full state cleanup and best-effort libvirt checkpoint cleanup.
## Requirements
### Requirement: Restore point selection policy

`restore --at <timestamp>` SHALL select the FIRST restore point whose freeze timestamp is greater than or equal to the requested timestamp (superset policy — the closest point from above), searching per disk within the target chains of the VM. The restore point actually used SHALL always be logged explicitly (requested timestamp vs selected point and backup file). When no restore point satisfies the condition, the command SHALL fail with a failed `RestoreResult` listing the available points. A delta restores the state of its chain (FULL + deltas up to and including the selected point).

#### Scenario: First point above the requested timestamp is used

- **WHEN** target points exist at 03:00 and 04:00 and `restore --at 03:30` is requested
- **THEN** the 04:00 chain terminus is selected
- **AND** the log states the requested timestamp and the actually used point

#### Scenario: Exact match is used when present

- **WHEN** a restore point exists exactly at the requested timestamp
- **THEN** that point is selected

#### Scenario: No satisfying point fails with available list

- **WHEN** `restore --at <ts>` is requested and every restore point is older than `<ts>` beyond the newest point
- **THEN** `RestoreResult(success=False)` is returned
- **AND** the error lists the available restore points

### Requirement: Restore points listing

`qsnap list restore-points <vm>` SHALL enumerate real restore points per target and disk per the `restore-points-listing` capability, giving operators visibility into actual coverage before choosing `--at`.

#### Scenario: Operator inspects points before restore

- **WHEN** `qsnap list restore-points myvm` is executed
- **THEN** all FULL anchors and delta points are listed per target and disk with timestamps

### Requirement: Restore command replaces the resolved disk atomically
The `qsnap restore <name> [vm]` and `qsnap restore --at <timestamp> [vm]` commands SHALL replace a stopped VM's disk with a flattened standalone qcow2 created from the resolved restore source. Resolution order: with `--at`, the restore point is selected per the restore point selection policy (first point ≥ ts) from the target chains; with `<name>`, the name is resolved via `Core._resolve_snapshot()` exactly as today (local snapshot state or backup target files — legacy compatibility shim), and a resolved backup name maps to its chain. The disk SHALL be resolved from the backup/snapshot `disk` field (or `parse_disk_from_snapshot_name` as fallback) — there is no `--disk` CLI flag. The command SHALL:

1. Resolve the restore source: `--at` via point selection, or the name via `Core._resolve_snapshot()`
2. Resolve the disk: `disk = info.disk or parse_disk_from_snapshot_name(name)`
3. Resolve `disk_cfg = vm_config.get_disk(disk)` and `base_image = disk_cfg.base_image`
4. Verify the VM is stopped via `is_vm_running()` — abort with error if running
5. Pre-verify source chain integrity via `scan_backing_chain()` — abort if broken
6. Create standalone image at temporary path `<snapshot_dir>/<vm>.<disk>.restored.qcow2.tmp` via the shared standalone-image-conversion helper (`convert_with_retry`)
7. Verify the temporary image via `verify_standalone_image()` (M1 virtual-size equality with the source chain, M2 `qemu-img check`) BEFORE replacement — on verification failure remove the temp file and abort; the base image SHALL NOT be replaced with an unverified image
8. Atomically replace ONLY that disk's base image: `os.replace(<tmp>, base_image)`
9. Delete old snapshot overlay files for THAT DISK only (skip `snap.disk != disk`)
10. Strip `<backingStore>` elements AND update `<source file>` only in the `<disk target='{disk}'>` element of domain XML
11. Reset ONLY the restored disk's state: `IStateManager.reset_vm_disk_state(vm_name, disk)` and `IStateManager.reset_target_disk_state(target_path, vm_name, disk)` for each target. State of other disks of the VM and records of other VMs sharing a target SHALL NOT be touched.
12. Best-effort cleanup of libvirt checkpoints of the restored disk ONLY: only checkpoints whose disk segment equals the restored disk SHALL be deleted via `virsh checkpoint-delete --domain <vm> --metadata <checkpoint>`. Checkpoint names without a disk segment (legacy format) SHALL NOT be deleted and SHALL be logged at WARNING level.

The command SHALL accept `--dry-run` and `--yes` flags. Without `--yes`, the command SHALL prompt for confirmation. When `--at` selects a point different from the requested timestamp, the selected point SHALL be logged before confirmation.

#### Scenario: Restore from snapshot replaces the resolved disk
- **WHEN** `qsnap restore myvm.20260701T120000_a1b2c3` is executed and the snapshot's `disk` field is `"vda"`
- **THEN** `disk_cfg = vm_config.get_disk("vda")` and `base_image = disk_cfg.base_image` are used
- **AND** only `disk_cfg.base_image` is replaced via `os.replace()`
- **AND** only snapshot overlays with `snap.disk == "vda"` are deleted
- **AND** only the `<disk target='vda'>` XML element is updated
- **AND** `RestoreResult(disk="vda")` is returned

#### Scenario: Restore from backup with disk in filename
- **WHEN** `qsnap restore vm.FULL.20260701T000000_vda_a1b2c3` is executed and the backup name contains a disk identifier
- **THEN** the disk is resolved from the backup `disk` field or parsed from the filename
- **AND** the same single-disk replacement occurs for that disk only

#### Scenario: Restore --at selects the first point above the timestamp
- **WHEN** `qsnap restore --at 2026-08-08T03:30:00 myvm` is executed and target points exist at 03:00 and 04:00 for disk `vda`
- **THEN** the 04:00 chain is restored for `vda`
- **AND** the log states requested 03:30 and actually used 04:00

#### Scenario: Restore --at with legacy snapshot name shim
- **WHEN** `qsnap restore myvm.20260807T152956_vda_ec1148` names a legacy backup file whose timestamp parses to T
- **THEN** resolution behaves as `--at T` over the target chains when the name is a backup, or restores the local snapshot chain when the name is a local snapshot

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
`Core` SHALL provide a `restore(name: str | None = None, at: datetime | None = None, vm_filter: str | None = None) -> RestoreResult` method. Exactly one of `name` or `at` SHALL be provided. With `at`, Core SHALL select the first restore point ≥ `at` per the restore point selection policy and log the actually used point. With `name`, Core SHALL resolve via `_resolve_snapshot()` (local snapshot state or backup target files; legacy compatibility). Core SHALL resolve the disk from the `disk` field (or `parse_disk_from_snapshot_name` as fallback), replace only that disk's `base_image` (after verifying the converted temp image), update only that disk's `<disk>` element in domain XML, delete only that disk's snapshot overlays, reset only that disk's state via `reset_vm_disk_state()` and `reset_target_disk_state()`, and clean up only that disk's checkpoints. The `target_dir` parameter is REMOVED — the result is written to `disk_cfg.base_image`.

#### Scenario: Restore from snapshot identifies disk
- **WHEN** `core.restore(name="vm.20250101T1200")` is called and the snapshot exists with `disk="vda"`
- **THEN** the VM's `vda` disk base_image is replaced; other disks are untouched

#### Scenario: Restore from backup identifies disk
- **WHEN** `core.restore(name="vm.20250101T1200")` is called and the name is found on a backup target
- **THEN** the disk is resolved from the backup's `disk` field and only that disk is restored

#### Scenario: Restore --at selects point and logs it
- **WHEN** `core.restore(at=datetime(2026, 8, 8, 3, 30))` is called and points exist at 03:00 and 04:00
- **THEN** the 04:00 chain is restored
- **AND** the actually used point is logged

#### Scenario: Restore fails on running VM
- **WHEN** `core.restore(name="vm.20250101T1200")` is called and `is_vm_running()` returns True
- **THEN** `RestoreResult(success=False, error="VM must be stopped for restore")` is returned

#### Scenario: Restore fails when neither name nor at given
- **WHEN** `core.restore()` is called with both `name` and `at` as `None`
- **THEN** `RestoreResult(success=False, error="Either name or --at must be provided")` is returned

### Requirement: Snapshot resolution exposes shared primitives for fork
`Core` SHALL provide a `_resolve_snapshot(snapshot_name: str, vm_filter: str | None = None) -> tuple[SnapshotInfo, VMConfig]` method that locates a snapshot by name across all sources (IStateManager and backup providers) and returns both the `SnapshotInfo` and the `VMConfig`. The `SnapshotInfo` carries a `disk` field identifying which disk it belongs to. This method SHALL be used internally by both `restore()` and `fork()`.

Two-layer failure contract: `_resolve_snapshot()` is the low-level primitive and SHALL raise `FileNotFoundError("Snapshot not found: {name}")` when the snapshot exists in neither source (or the `vm_filter` excludes every owner). The public commands `restore()` and `fork()` SHALL catch that exception and return `RestoreResult(success=False, error="Snapshot not found: {name}")` — they never raise for expected failures (Result-object convention). Both spec statements ("raises" for the primitive, "returns failed result" for the commands) describe different layers of the same contract.

#### Scenario: _resolve_snapshot finds snapshot in state
- **WHEN** `_resolve_snapshot("myvm.20260701T1200")` is called and the snapshot exists in IStateManager
- **THEN** returns `(SnapshotInfo(name="myvm.20260701T1200", disk="vda", ...), VMConfig(name="myvm", ...))`

#### Scenario: _resolve_snapshot finds snapshot in backup
- **WHEN** `_resolve_snapshot("vm.FULL.20260701T000000_a1b2c3")` is called and the snapshot exists on a backup target
- **THEN** returns `(SnapshotInfo(name="vm.FULL.20260701T000000_a1b2c3", disk="vda", ...), VMConfig(...))`

#### Scenario: _resolve_snapshot raises on not found
- **WHEN** `_resolve_snapshot("nonexistent")` is called
- **THEN** raises `FileNotFoundError` with message `"Snapshot not found: nonexistent"`

#### Scenario: restore and fork convert the raised error into a failed result
- **WHEN** `restore("nonexistent")` or `fork("nonexistent", out)` is called and `_resolve_snapshot` raises `FileNotFoundError`
- **THEN** the command catches it and returns `RestoreResult(success=False, error="Snapshot not found: nonexistent")`
- **AND** no exception propagates to the CLI layer

### Requirement: RestoreResult type
The system SHALL provide a `RestoreResult` frozen dataclass with fields: `success: bool`, `snapshot_name: str`, `restored_path: Path`, `chain_files: list[Path]`, `error: str | None`, `disk: str | None`. The `disk` field SHALL identify the disk target (e.g. `"vda"`) that was restored.

#### Scenario: Successful restore result
- **WHEN** restore completes successfully for disk `vda`
- **THEN** `RestoreResult(success=True, restored_path=Path(".../disk.qcow2"), chain_files=[...], disk="vda")` is returned

#### Scenario: Failed restore result still carries disk
- **WHEN** restore fails after resolving the disk
- **THEN** the `RestoreResult` SHALL include the `disk` field for diagnostic context

