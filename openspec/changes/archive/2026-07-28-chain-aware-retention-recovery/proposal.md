## Why

Three confirmed production bugs caused cascading backup chain destruction: (1) `_cleanup_backups()` ghost-retains an element but forgets this during cascade-deletion of the next element, breaking chains for keep-set items; (2) `transfer_missing()` uses the newest checkpoint as baseline for ALL snapshots, even those taken before the checkpoint was created, causing 15 GiB transfers instead of small deltas; (3) `_delete_checkpoint_best_effort()` uses `--metadata` flag, leaving QEMU dirty bitmaps that collide on retry with "Bitmap already exists". The root cause is that backup retention operates per-item on a structure (qcow2 backing chains) that has no merge operation — you can never delete from the middle without breaking the chain.

## What Changes

- **Per-chain backup retention**: Replace per-item retention + ghost-retention cascade-deletion with per-chain retention. Core groups backups by chain (FULL + its incrementals), evaluates retention at chain level, and keeps/deletes entire chains atomically. Ghost-retention is eliminated — the problem is removed, not patched.
- **Snapshot oldest-prefix retention**: Core post-processes snapshot retention results to only remove a contiguous oldest prefix. Middle snapshots marked for removal are moved to keep (chain gap fillers).
- **Auto-recovery at startup**: `_validate_state_at_startup()` detects broken backup chains and auto-deletes broken files (they're useless without backing). Forces FULL creation if no valid FULL remains. This is mandatory — per-chain grouping cannot resolve broken-chain files.
- **Checkpoint lifecycle fixes**: `_delete_checkpoint_best_effort()` uses full `checkpoint-delete` (not `--metadata` only) with fallback. Checkpoint names get UUID suffix to prevent collisions. "Bitmap already exists" errors are caught and retried with force-cleanup.
- **Temporal mismatch detection**: `transfer_missing()` skips snapshots whose timestamp predates the newest checkpoint's creation time. Size-based sanity check warns when transferred size exceeds 10x snapshot allocation.
- **Blockcommit recovery**: When pre-commit chain verification fails, partial blockcommit is attempted for snapshots before the break point. Stuck snapshots after the break are auto-rebased via `qemu-img rebase -u` to skip the missing file. Stale state entries are cleaned.
- **Post-cleanup chain verification**: After `_cleanup_backups()`, all keep-set items are verified to have intact backing chains. Broken chains are logged CRITICAL.
- **Retention engine unchanged**: `TimeBasedRetention.evaluate()` remains a pure function. All chain-aware logic is in Core pre/post-processing.
- **Config unchanged**: Same parameters (`target_preserve`, `target_preserve_min`, etc.). Only semantics change (per-chain for targets, oldest-prefix for snapshots).

## Capabilities

### New Capabilities

- `per-chain-retention`: Core pre/post-processing that groups backups by chain (FULL + incrementals), creates chain-level RetentionItems, evaluates retention at chain level, and expands results to individual items. Eliminates ghost-retention and cascade-deletion.
- `snapshot-oldest-prefix`: Core post-processing that clips snapshot retention remove-list to a contiguous oldest prefix. Middle snapshots marked for removal become chain gap fillers (moved to keep).
- `blockcommit-recovery`: Partial blockcommit + auto-rebase when pre-commit chain verification fails. Splits to_merge into committable (before break) and stuck (after break), rebases stuck snapshots to skip missing file, and continues blockcommit.
- `auto-recovery`: Automatic detection and recovery of broken backup chains at pipeline startup. Detects broken chains via `qemu-img info --backing-chain`, deletes broken files, cleans state, and forces FULL creation if no valid FULL remains.

### Modified Capabilities

- `cascade-deletion`: Replace cascade-deletion and ghost-retention with per-chain retention. The entire cascade-deletion mechanism (backing_refs, ghost-retained check, cascade-delete orphans) is removed. Cleanup deletes entire chains atomically.
- `nbd-bitmap-backup`: Checkpoint deletion changes from `--metadata` only to full `checkpoint-delete` with fallback. Checkpoint names get UUID suffix. "Bitmap already exists" errors are caught and retried with force-cleanup. Temporal mismatch detection skips snapshots predating the newest checkpoint.
- `chain-integrity-verification`: Post-cleanup chain verification added (verify all keep-set items have intact chains after cleanup). `ChainVerifyResult` extended with `broken_file` field for blockcommit recovery.
- `startup-state-validation`: Extended to detect and auto-recover broken backup chains on targets. Deletes broken-chain files, cleans state dependencies, and forces FULL creation when no valid FULL remains.

## Impact

- **Core** (`qsnap/core/__init__.py`): Major changes to `_evaluate_backup_retention()`, `_cleanup_backups()`, `_evaluate_snapshot_retention()`, `_validate_state_at_startup()`, `_blockcommit_snapshots()`, `_verify_backing_chain()`. New methods: `_group_backups_by_chain()`, `_split_at_break()`, `_auto_rebase_stuck()`, `_force_full_targets` set.
- **BitmapBackupProvider** (`qsnap/modules/backup/bitmap.py`): Changes to `_delete_checkpoint_best_effort()`, `_new_checkpoint_name()`, `transfer_missing()`. New methods: `_force_cleanup_checkpoints()`.
- **Models** (`qsnap/models/results.py`): `ChainVerifyResult` extended with `broken_file` field.
- **Retention engine** (`qsnap/retention/time_based.py`): NO CHANGES — stays pure function.
- **Config** (`qsnap/models/config.py`, `qsnap/config/facade.py`): NO CHANGES — same parameters, different semantics.
- **Interfaces** (`qsnap/interfaces/`): NO BREAKING CHANGES to ABCs. All changes are in Core private methods and BitmapBackupProvider internals.
- **State** (`qsnap/state/json_manager.py`): No schema changes. Existing `remove_incremental_dependency` and `remove_all_incremental_dependencies` methods used as-is.
- **Tests**: New unit tests for per-chain retention, oldest-prefix, checkpoint collision, temporal mismatch. New integration tests for auto-recovery, blockcommit recovery, production incident reproduction. Existing cascade-deletion tests need updating (ghost-retention tests removed).
