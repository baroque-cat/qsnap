## Why

Final spec ↔ code synchronization for files not touched by `vm-level-isolation` and `fix-dry-run-mutations`. The code is correct in all cases — the specs drifted. This change is spec-only: no code changes.

## What Changes

- **F1 `result-types`**: `SnapshotResult` gains the `disk: str | None = None` field in the spec (code has it since the dry-run prediction work).
- **F2 `cascade-deletion`**: `FullBackupInfo`/`record_full_backup` parameter documented as `disk`, not the obsolete `bucket_level`.
- **F3 `transaction-log`**: new requirement documenting the `ActionRecord.action` → log `type` mapping (`_TYPE_MAP`: snapshot_create→snapshot, snapshot_delete→delete_snapshot, backup_transfer→backup, backup_full→backup_full, backup_delete→delete_backup), the `error` type for error records, and the `unknown` fallback.
- **F4 `list-commands`**: new `stats` requirement (columns `vm, snapshots, snapshot_size, backups, backup_size`; sources `list_snapshots`+`list_backups`; scope limited to VMs configured in TOML); `list_backups` requirement clarifies the config-driven scope and the empty shape `{vm_name: []}`.
- **F5 `fork-mode`**: `Core.fork` gains `[vm]` filter scenarios (non-matching filter → `Snapshot not found`; disambiguation of identical names across VMs) and the dry-run failure contract (unresolvable snapshot → `RestoreResult(success=False)` even in dry-run).
- **F6 `restore-command`**: the `_resolve_snapshot` requirement documents the two-layer contract explicitly — the primitive raises `FileNotFoundError`; `restore()`/`fork()` catch it and return a failed `RestoreResult`. This resolves the apparent contradiction between restore-command ("raises") and fork-mode ("returns failed result"): both are correct at different layers.

## Capabilities

### Modified Capabilities

- `result-types`, `cascade-deletion`, `transaction-log`, `list-commands`, `fork-mode`, `restore-command`.

## Impact

- **Code**: none (specs catch up to existing tested behavior).
- **Tests**: none required — behavior already covered (`tests/cli/test_commands.py` stats tests, `tests/core/test_fork.py`, `tests/core/test_restore.py`, `tests/utils/test_transaction.py`).
