## Why

The system currently has multiple silent failure paths — backup transfer failures produce exit code 10 without any WARNING in logs, a false "passed" message logs after skipped post-commit cleanup, and `BitmapBackupProvider` lacks abort/state-recording parity with `FileCopyBackupProvider`. Operators have no visibility into what the pipeline actually did: which snapshots were created, which were blockcommitted, which backups were transferred or deleted. The `dry-run` mode prints minimal output. Fixing these gaps is urgent because every systemd timer run currently produces a confusing "Failed with result 'exit-code'" log despite snapshots and blockcommit succeeding.

## What Changes

### Bug fixes (P0)
- Add `logger.warning` at the `backup_failed = True` assignment in `_backup_target()` — currently silent
- Fix indent of `"Post-commit chain verification passed"` log — currently fires even when cleanup was skipped
- Add `logger.warning` in five silent failure paths of `FileCopyBackupProvider.transfer_missing()`

### BitmapBackupProvider parity (P1)
- Add `virsh domjobabort` in `BitmapBackupProvider.transfer_missing()` finally block (mirrors what is already done in `nbd_full_export()`)
- Add `IStateManager` injection to `BitmapBackupProvider` and call `record_full_backup()` after successful FULL creation

### btrbk-style audit trail (new capability)
- Introduce `ActionRecord` frozen dataclass — accumulated by Core during pipeline, carried in `PipelineResult`
- Per-operation INFO messages to stderr: `[snapshot] created:`, `[blockcommit] merged:`, `[backup] transferred:`, `[delete] removed:`
- Summary table on stdout after each run: legend with `+++`/`---`/`>>>`/`***`/`!!!` symbols, per-VM blocks listing created/deleted/transferred items
- Dry-run mode prints predicted actions (what WOULD happen) using the same format with `Dryrun: YES` header
- Optional structured transaction log file (btrbk-compatible format: `timestamp type status target source parent`)

### CLI changes
- `_format_pipeline_result()` prints the summary table from `PipelineResult.actions`
- `dry-run` mode shows predicted actions via retention evaluation + bucket strategy (no filesystem mutations)

## Capabilities

### New Capabilities
- `action-audit-trail`: `ActionRecord` frozen dataclass, Core accumulation in `self._actions`, carried in `PipelineResult.actions` — consumed by CLI summary formatter and optional transaction log writer
- `backup-summary`: stdout summary table with legend symbols (`+++` created snapshot, `---` deleted snapshot, `>>>` incremental backup, `***` FULL backup, `!!!` error), per-VM blocks, dry-run variant with `Dryrun: YES` header and footer note
- `transaction-log`: optional structured log file in btrbk-compatible format (`localtime type status target_url source_url parent_url`), controlled by `GlobalConfig.transaction_log` field

### Modified Capabilities
- `backup-provider`: **ADDED** requirement — `BitmapBackupProvider.transfer_missing()` must call `virsh domjobabort` in finally block (mirrors `nbd_full_export`). **ADDED** requirement — `BitmapBackupProvider` must accept optional `IStateManager` and call `record_full_backup()` after successful FULL creation. **ADDED** requirement — `FileCopyBackupProvider.transfer_missing()` must emit `logger.warning` before returning `BackupResult(success=False)` in rebase/verify failure paths.
- `core-orchestrator`: **MODIFIED** — `_backup_target()` must emit `logger.warning` when `backup_failed = True` at line 2519 (currently silent). **MODIFIED** — `"Post-commit chain verification passed"` log must be at inner `else:` indent level, not outer `if`. **ADDED** — Core must accumulate `ActionRecord` instances in `self._actions` during pipeline execution and attach them to `PipelineResult`.
- `cli-interface`: **ADDED** requirement — `_format_pipeline_result()` must call summary formatter with `PipelineResult.actions` and print the table. **MODIFIED** — `dry-run` mode must show predicted actions in summary format.

## Impact

- **Modified files**: `qsnap/core/__init__.py` (~80 lines: B1/B2 fixes + ActionRecord accumulation + per-op INFO logs), `qsnap/modules/backup/bitmap.py` (~20 lines: domjobabort + IStateManager), `qsnap/modules/backup/file_copy.py` (~25 lines: 5 WARNING logs), `qsnap/factory/default.py` (~5 lines: pass IStateManager), `qsnap/cli/commands.py` (~15 lines: summary dispatch), `qsnap/models/results.py` (~15 lines: ActionRecord + PipelineResult.actions)
- **New files**: `qsnap/cli/summary.py` (~120 lines: pure formatter), `qsnap/utils/transaction.py` (~60 lines: stateless transaction log writer)
- **New config field**: `GlobalConfig.transaction_log: str | None` (optional path)
- **No breaking API changes**: All interface changes are additive (new optional parameter on `IBackupProvider`, new field on `PipelineResult`)
- **State migration**: None (new `transaction_log` config field has safe default `None`)
- **New tests**: ~25 unit tests (action audit trail, summary formatter, transaction writer, silent-failure logging, bitmap parity), ~4 contract test updates (BitmapBackupProvider IStateManager)
