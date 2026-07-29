## Why

The `check` and `reconcile` commands were designed before the count-based chain paradigm replaced the old bucket-based system. The current `check` only verifies backing-chain exit codes without cross-referencing state, disk, or domain XML — it cannot distinguish legitimate deletions (blockcommit, retention) from actual data loss. The current `reconcile` deletes orphan files instead of supplementing state from reality, does not refresh stale domain XML, and performs unsafe auto-rebase on broken chains. Additionally, the None defaults for retention parameters resolve to 0/1, causing extremely aggressive behavior (near-daily FULL creation, single-snapshot retention). Post-creation validation for snapshots is absent — the system trusts virsh exit code 0 without verifying the file landed on disk, the backing chain is correct, or libvirt pivoted the active layer.

## What Changes

- **BREAKING**: Refactor `Core.check()` to perform triple-source verification (state JSON ↔ disk files ↔ domain XML) for both snapshots and targets, replacing the current exit-code-only shallow check
- **BREAKING**: Refactor `Core.reconcile()` to supplement state from reality (when disk + XML agree but state is stale) instead of deleting orphan files, add `_refresh_domain_backing_store()` call for stale domain XML, and stop performing unsafe auto-rebase on broken chains
- Add post-creation validation to `ExternalSnapshotProvider.create()`: verify file exists, qcow2 format, backing-filename points to previous active layer, corrupt bit not set, and `virsh domblklist` confirms libvirt pivot
- Add post-transfer validation to `BitmapBackupProvider`: verify chain-to-FULL traversability after incremental transfer, verify checkpoint existence after both FULL and incremental creation
- Fix `Core._deep_check_file()` to check `errors` + `leaks` + `corruptions` (currently only checks `corruptions`), and increase timeout from 60s to 7200s
- Change default retention values: `snapshot_chain_length=24`, `target_chain_length=168`, `target_keep_generations=2` (currently all `None` → resolve to 0/1)
- Add new `ReconcileResult` fields: `state_supplemented`, `xml_refreshed`, `allocation_fixed`

## Capabilities

### New Capabilities

- `post-creation-validation`: Validation checks performed immediately after snapshot creation (`virsh snapshot-create-as`) and backup transfer (`transfer_missing()`, `create_full_backup()`) to verify the operation succeeded at the disk, qcow2 metadata, and libvirt XML levels — before recording state
- `triple-source-check`: The refactored `check` command that cross-references three sources of truth (qsnap state JSON, disk qcow2 files, libvirt domain XML) to detect phantoms, orphans, stale XML references, and broken chains — while correctly ignoring legitimate deletions from blockcommit and retention

### Modified Capabilities

- `state-reconciliation`: Reconcile now supplements state from disk+XML reality (instead of deleting orphan files), calls `_refresh_domain_backing_store()` for stale domain XML, and no longer performs unsafe auto-rebase on broken chains (only logs CRITICAL)
- `deep-verification-circuit`: Deep check now verifies `errors` + `leaks` + `corruptions` (not just `corruptions`), uses 7200s timeout (was 60s), and integrates with the triple-source-check paradigm
- `config-model`: Default values change from `None` to `snapshot_chain_length=24`, `target_chain_length=168`, `target_keep_generations=2`
- `snapshot-provider`: `ExternalSnapshotProvider.create()` gains post-creation validation steps (file existence, format, backing-filename, corrupt bit, domblklist pivot confirmation)
- `backup-provider`: `BitmapBackupProvider` gains post-transfer validation (chain-to-FULL traversability, checkpoint existence)
- `chain-integrity-verification`: The `check` command now uses full JSON parsing of `qemu-img info --backing-chain` (backing-filename consistency, cycle detection) instead of only checking exit codes

## Impact

- **Core (`qsnap/core/__init__.py`)**: Major refactor of `check()`, `check_state()`, `reconcile()`, `_deep_check_file()`; moderate changes to `_validate_state_at_startup()` to align with new check paradigm
- **Snapshot module (`qsnap/modules/snapshot/external.py`)**: Add validation steps to `create()` method (~20 lines)
- **Backup module (`qsnap/modules/backup/bitmap.py`)**: Add chain-to-FULL and checkpoint verification after `transfer_missing()` and `create_full_backup()` (~15 lines)
- **Config model (`qsnap/models/config.py`)**: Change 3 default values in `GlobalConfig`
- **Result types (`qsnap/models/results.py`)**: Add new fields to `ReconcileResult` (`state_supplemented`, `xml_refreshed`, `allocation_fixed`)
- **Tests**: New unit tests in `tests/core/` (check + reconcile for snapshots and targets), new integration tests in `tests/integration/` (real VM verification, `_refresh_domain_backing_store()` testing, post-creation validation), mock infrastructure additions (configurable `MockRetentionEngine`, `MockChangeDetector`, domain XML fixtures)
- **No ABC interface changes**: `ISnapshotProvider`, `IBackupProvider`, `IStateManager` interfaces remain unchanged — all changes are internal to implementations and Core
