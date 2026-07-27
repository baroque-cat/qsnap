## Why

Incremental backups in qsnap can develop broken backing chains when retention deletes an intermediate incremental that another incremental depends on as its backing file. The root cause is a cascade-deletion gap: `_cleanup_backups()` only checks dependencies for FULLs (ghost retention), but deletes non-FULL incrementals without verifying whether other incrementals chain to them. A compounding key-mismatch bug in `IStateManager` makes `reconcile` classify ALL incrementals as orphans (data loss risk), while `check --state` reports "ok" for broken chains because it only checks file existence, never backing-chain integrity. This makes the system unable to recover from broken chains without manual intervention.

## What Changes

- **B1 — Key mismatch fix**: Normalize `full_name` keys in `JsonStateManager.get_incremental_dependencies`, `remove_incremental_dependency`, and `remove_all_incremental_dependencies` to accept both stem (`vm.FULL.20260727`) and extended (`vm.FULL.20260727.qcow2`) forms. Add migration in `_load_dependencies` to normalize legacy `.qcow2` keys to stem format on load.
- **B2 — Cascade deletion for incrementals**: Extend `_cleanup_backups()` else-branch to check whether the incremental being deleted is the backing file for any other backup in the keep-set (ghost retention for incrementals). Build a reverse backing-chain dependency map (`_build_backing_refs`) via `qemu-img info` before the deletion loop. Cascade-delete orphaned dependents not in keep-set. Clean up `IStateManager` dependency records on deletion.
- **B3 — Previous selection validation**: Replace `backups[-1]` selection in `BitmapBackupProvider._copy_dirty_blocks()` with a backwards walk that validates backing-chain integrity via `qemu-img info --backing-chain` before using a file as `previous`. Skip broken-chain files and fall back to the last available valid backup (or FULL).
- **B4 — State cleanup on incremental deletion**: Call `remove_incremental_dependency` when an incremental is deleted by retention (currently only called during FULL cascade deletion and reconcile). Also add cleanup in reconcile orphan-file deletion.
- **B5 — `check --state` backing chain validation**: Add a `broken_chains` check category to `Core.check_state()` that runs `qemu-img info --backing-chain` on each non-FULL backup file. Add `broken_chains` field to `StateCheckResult`.
- **B6 — Reconcile broken chain detection**: Add broken-chain detection in reconcile step 6 before orphan classification, with warning logs. Add `broken_chains` field to `ReconcileResult`.

## Capabilities

### New Capabilities

_(None — all changes extend existing capabilities.)_

### Modified Capabilities

- `cascade-deletion`: Extend ghost retention and cascade deletion from FULL-only to also cover non-FULL incrementals. The else-branch of `_cleanup_backups()` must check backing-chain dependencies before deleting an incremental.
- `state-management`: Normalize `full_name` keys in dependency lookup/removal methods to accept both stem and `.qcow2`-extended forms. Add legacy key migration on load.
- `nbd-dirty-block-transfer`: Validate backing-chain integrity of the selected `previous` backup in `_copy_dirty_blocks()` before creating a delta. Walk backwards through `backups` to find the newest valid (non-broken-chain) file.
- `state-consistency-check`: Add `broken_chains` check category to `check_state()` that validates backing-chain integrity of backup files via `qemu-img info --backing-chain`.
- `state-reconciliation`: Add broken-chain detection before orphan classification. Clean up `IStateManager` dependency records when deleting orphan files.

## Impact

- **Core** (`qsnap/core/__init__.py`): New `_build_backing_refs()` method; rewritten `_cleanup_backups()` else-branch; enhanced `check_state()` with `broken_chains` category; enhanced `reconcile()` with broken-chain detection and dependency cleanup.
- **BitmapBackupProvider** (`qsnap/modules/backup/bitmap.py`): New `_validate_backing_chain()` method; rewritten `previous` selection logic in `_copy_dirty_blocks()`.
- **JsonStateManager** (`qsnap/state/json_manager.py`): Key normalization in `get_incremental_dependencies`, `remove_incremental_dependency`, `remove_all_incremental_dependencies`; migration in `_load_dependencies`.
- **InMemoryStateManager** (`tests/mocks/mock_state.py`): Same key normalization for test parity.
- **Models** (`qsnap/models/results.py`): New `broken_chains` field on `StateCheckResult` and `ReconcileResult`.
- **No ABC interface changes** — all fixes are within existing method signatures. No `IStateManager` or `IBackupProvider` interface modifications.
- **No breaking changes** — key normalization is backward-compatible (accepts both forms). Legacy `_dependencies.json` files are migrated on load.
- **Tests**: New unit tests for each fix; new integration test for full pipeline broken-chain recovery.
