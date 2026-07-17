## Why

qsnap cannot create FULL backups for running VMs. `FileCopyBackupProvider.create_full_backup()` runs `qemu-img convert` directly on the most-recent snapshot, which IS the active layer the live QEMU process is writing to. QEMU holds an exclusive write lock on that file, so `qemu-img convert` fails with `Failed to get shared "write" lock`. This recurs on EVERY FULL creation (first run + every bucket boundary), not just the first run. The dry-run mode masks the problem entirely — it skips both environment validation and FULL backup creation, so operators discover the failure only in production. Additionally, five other `qemu-img` call sites are missing `--force-share` on active-layer files, causing the same lock-conflict class of bug in snapshot creation, change detection, integrity checks, and fork.

## What Changes

- **NBD-based FULL backup for live VMs**: `FileCopyBackupProvider.create_full_backup()` detects whether the VM is running. If running, it uses the libvirt pull-model NBD API (`virsh backup-begin` + `qemu-img convert -n nbd:unix:<socket>`) to export a frozen point-in-time view of the disk — the same mechanism `BitmapBackupProvider` already uses for incremental transfers. If the VM is stopped, it falls back to the existing direct `qemu-img convert` path. No checkpoint is created in file-copy mode (NBD is used only for the frozen view, not for dirty-block tracking).
- **`BitmapBackupProvider.create_full_backup()` implemented**: overrides the current `NotImplementedError` so bucket-driven FULL creation no longer crashes the pipeline for bitmap-mode targets. Uses the same NBD full-export path (no `--incremental` flag).
- **`--force-share` added to all metadata-only `qemu-img` calls on active layers**: fixes 5 confirmed bugs where `qemu-img info`, `qemu-img map`, or `qemu-img info --backing-chain` run on the active layer without `--force-share`, causing lock conflicts on running VMs. Affected sites: snapshot creation post-info, allocation-map change detection, integrity check listing, fork chain estimation, and fork convert. Data-copying operations (`qemu-img convert`, `qemu-img compare`) are NOT given `--force-share` — they use NBD instead, because `--force-share` on data-copying operations produces silently corrupted backups.
- **Dry-run enhanced**: dry-run mode now (a) runs environment validation (`_validate_environment()`) and reports broken checks instead of skipping them, (b) logs whether a FULL backup WOULD be created and via which method (NBD vs direct convert), and (c) attempts lock-conflict detection by checking VM running state. This makes dry-run a truthful preview of real-run behavior.
- **README updated**: documents the NBD-based FULL backup mechanism, the `--force-share` safety classification (safe for metadata, dangerous for data-copy), the dry-run behavior, and the operational requirement that the `qsnap` user must have libvirt access (group membership or polkit).

## Capabilities

### New Capabilities

- `live-vm-full-backup`: NBD-based FULL backup creation for running VMs. Covers VM running-state detection, NBD export lifecycle for FULL backups (no checkpoint tracking), fallback to direct convert when VM is stopped, and the shared NBD helper used by both `FileCopyBackupProvider` and `BitmapBackupProvider`.

### Modified Capabilities

- `backup-provider`: `FileCopyBackupProvider.create_full_backup()` now branches on VM running state (NBD vs direct convert). `BitmapBackupProvider.create_full_backup()` no longer raises `NotImplementedError` — implemented via NBD full export.
- `periodic-full-backup`: `_should_create_bucket_full()` and the `_backup_target()` FULL creation path now work for both running and stopped VMs. The `most_recent` snapshot selection is unchanged — NBD exports the current disk state, which may be slightly newer than the last snapshot (acceptable for FULL anchors).
- `nbd-bitmap-backup`: `BitmapBackupProvider.create_full_backup()` implemented. The NBD full-export path (no `--incremental`) is reused for bucket-driven FULLs. No checkpoint is created for FULL-only exports in file-copy mode.
- `env-validation`: dry-run mode now executes `_validate_environment()` and reports broken checks as warnings (non-fatal in dry-run). Currently dry-run skips validation entirely (`if not self._dry_run`).
- `snapshot-provider`: `ExternalSnapshotProvider.create()` post-snapshot `qemu-img info` now uses `--force-share` — fixes lock conflict on the newly created active layer.
- `map-change-detection`: `MapChangeDetector.has_changed()` now uses `--force-share` on `qemu-img map` — fixes lock conflict on the active disk.
- `chain-integrity-verification`: `Core.check_integrity()` and `Core._deep_check_file()` now use `--force-share` on `qemu-img info`/`qemu-img check` for active-layer snapshots.
- `fork-mode`: `Core.fork()` now uses `--force-share` on `qemu-img info --backing-chain` and `qemu-img convert` now branches on VM running state (NBD vs direct convert) — same hybrid approach as FULL backup.
- `backup-verification`: `verify_backup()` source-side `qemu-img info` and `qemu-img compare` now use `--force-share` when the source may be the active layer. `qemu-img compare` with `--force-share` is flagged as a known risk (metadata verification is the recommended tier for live sources).
- `shell-abstraction`: documents the `--force-share` safety classification — safe for metadata-only operations, dangerous for data-copying operations. No `IShell` interface change.
- `cli-interface`: dry-run output now includes validation results and FULL-backup-would-be-created indicators.
- `size-estimation`: dry-run now logs whether a FULL would be created and the estimated transfer method (NBD vs direct).

## Impact

**Affected source modules:**
- `qsnap/modules/backup/file_copy.py` — `create_full_backup()` hybrid NBD/direct path; `transfer_missing()` rebase info `--force-share`
- `qsnap/modules/backup/bitmap.py` — `create_full_backup()` implemented via NBD full export
- `qsnap/modules/backup/verification.py` — `--force-share` on source-side `qemu-img info` and `qemu-img compare`
- `qsnap/modules/snapshot/external.py` — `--force-share` on post-snapshot `qemu-img info`
- `qsnap/modules/change/map_detector.py` — `--force-share` on `qemu-img map`
- `qsnap/core/__init__.py` — dry-run validation un-skip; `--force-share` on `check_integrity`, `_deep_check_file`, `fork()` chain info and convert; `_backup_target()` FULL creation path; `_log_size_estimate()` dry-run FULL indicator
- `qsnap/interfaces/backup.py` — no interface change (same signature, new behavior)
- `qsnap/factory/default.py` — `BitmapBackupProvider` may now receive `IStateManager` for FULL tracking (design decision)

**Affected tests:**
- `tests/modules/backup/test_copy.py` — new NBD FULL backup tests
- `tests/modules/backup/test_bitmap.py` — `create_full_backup()` no longer raises
- `tests/modules/snapshot/test_external.py` — `--force-share` on post-snapshot info
- `tests/modules/change/test_map_detector.py` — `--force-share` on map
- `tests/core/test_validation.py` — dry-run now validates
- `tests/core/test_full_anchor.py` — FULL creation for running VMs
- `tests/core/test_fork.py` — `--force-share` and NBD branch
- `tests/core/test_pipeline.py` — dry-run FULL indicator
- `tests/interfaces/test_backup_provider.py` — contract test for `create_full_backup` on both providers

**Affected documentation:**
- `README.md` — NBD FULL backup section, `--force-share` safety table, dry-run behavior, libvirt permissions
- `qsnap.toml.example` — no new config fields (NBD is automatic, not configurable)

**Dependencies:** No new PyPI dependencies (NBD via `virsh backup-begin` + `qemu-img convert -n nbd:`, both already used). Requires libvirt >= 6.0 for `backup-begin` (already enforced by `BitmapBackupProvider`).

**Migration:** No state schema change. No config change. No breaking interface change. Existing `FileCopyBackupProvider` behavior for stopped VMs is unchanged. The NBD path is a transparent enhancement for running VMs.
