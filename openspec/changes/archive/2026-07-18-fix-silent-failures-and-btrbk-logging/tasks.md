## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fix/silent-failures-and-btrbk-logging`
- [x] 1.2 Run the full test suite to establish a passing baseline: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Silent Failure Fixes (P0)

### 2.1 backup_failed WARNING in Core._backup_target

- [x] 2.1.1 Add `logger.warning` before `backup_failed = True` at `qsnap/core/__init__.py` line ~2519, including VM name, target path, count of failed snapshots, and per-snapshot error messages
- [x] 2.1.2 Reference spec: `core-orchestrator/spec.md` — "backup_failed WARNING in Core._backup_target"

### 2.2 Fix "Post-commit chain verification passed" indent

- [x] 2.2.1 Move `logger.info("Post-commit chain verification passed")` one indent level right — inside the `else:` block where `chain_length_before is not None`, at `qsnap/core/__init__.py` line ~2165
- [x] 2.2.2 Verify the log is NOT emitted when `chain_length_before is None`
- [x] 2.2.3 Reference spec: `core-orchestrator/spec.md` — "Post-commit chain verification after blockcommit"

### 2.3 WARNING logs in FileCopyBackupProvider.transfer_missing

- [x] 2.3.1 Add `logger.warning` before `BackupResult(success=False)` in rsync failure path (`qsnap/modules/backup/file_copy.py`, ~line 164)
- [x] 2.3.2 Add `logger.warning` before `BackupResult(success=False)` in rebase-to-FULL failure path (~line 253)
- [x] 2.3.3 Add `logger.warning` before `BackupResult(success=False)` in fallback rebase failure path (~line 298)
- [x] 2.3.4 Add `logger.warning` before `BackupResult(success=False)` in JSON decode failure path (~line 310)
- [x] 2.3.5 Add `logger.warning` before `BackupResult(success=False)` in verify_backup failure path (~line 331)
- [x] 2.3.6 Reference spec: `backup-provider/spec.md` — "Rebase error handling" and "verify_backup failure logging" and "rsync failure logging"

## 3. BitmapBackupProvider Parity (P1)

### 3.1 Domjobabort in BitmapBackupProvider.transfer_missing

- [x] 3.1.1 Add `virsh domjobabort --domain <vm_name>` in the `finally` block of `BitmapBackupProvider.transfer_missing()` in `qsnap/modules/backup/bitmap.py`, before socket cleanup. Use 30-second timeout. On failure, log WARNING but continue.
- [x] 3.1.2 Reference spec: `backup-provider/spec.md` — "BitmapBackupProvider domjobabort after NBD incremental transfer"

### 3.2 IStateManager injection in BitmapBackupProvider

- [x] 3.2.1 Add `state: IStateManager | None = None` parameter to `BitmapBackupProvider.__init__()` in `qsnap/modules/backup/bitmap.py`
- [x] 3.2.2 In `create_full_backup()`, after successful FULL creation and atomic rename, call `self._state.record_full_backup(...)` when `self._state is not None`
- [x] 3.2.3 Reference spec: `backup-provider/spec.md` — "BitmapBackupProvider accepts IStateManager"

### 3.3 Factory passes IStateManager to BitmapBackupProvider

- [x] 3.3.1 In `DefaultFactory.create_backup_provider()` at `qsnap/factory/default.py`, pass `self._state` as `state=` parameter when constructing `BitmapBackupProvider`
- [x] 3.3.2 Reference spec: `backup-provider/spec.md` — "Factory passes IStateManager to BitmapBackupProvider"

## 4. ActionRecord & Audit Trail (New Capability)

### 4.1 ActionRecord dataclass
- [x] 4.1.1 Create `ActionRecord` frozen dataclass in `qsnap/models/results.py` with fields: `action: str`, `vm_name: str`, `name: str`, `path: Path`, `size: int = 0`, `duration: float = 0.0`, `error: str | None = None`

- [x] 4.1.2 Add `actions: list[ActionRecord] = field(default_factory=list)` to `PipelineResult`

- [x] 4.1.3 Reference spec: `action-audit-trail/spec.md` — "ActionRecord dataclass"
### 4.2 ActionRecord accumulation in Core

- [x] 4.2.1 Add `self._actions: list[ActionRecord]` to Core, cleared at start of `_run_pipeline()`
- [x] 4.2.2 Append `ActionRecord(action="snapshot_create", ...)` after successful `record_snapshot()` in `_create_snapshot()`
- [x] 4.2.3 Append `ActionRecord(action="snapshot_delete", ...)` after successful `remove_snapshot()` in `_blockcommit_snapshots()`
- [x] 4.2.4 Append `ActionRecord(action="backup_transfer", ...)` after successful transfers in `_backup_target()`
- [x] 4.2.5 Append `ActionRecord(action="backup_full", ...)` after successful FULL creation in `_backup_target()`
- [x] 4.2.6 Append `ActionRecord(action="backup_delete", ...)` after successful `provider.delete()` in `_cleanup_backups()`
- [x] 4.2.7 Append `ActionRecord(action="error", ...)` for any pipeline step failure
- [x] 4.2.8 Populate `PipelineResult.actions` from `self._actions` at end of `_run_pipeline()`
- [x] 4.2.9 Reference spec: `action-audit-trail/spec.md` — "ActionRecord accumulation in Core" and "PipelineResult carries actions"

## 5. Per-Operation INFO Logging (btrbk-style)

- [x] 5.1 Add `logger.info("[snapshot] %s: created %s (%d B)", vm_name, name, alloc)` after snapshot creation in `Core._create_snapshot()`
- [x] 5.2 Add `logger.info("[blockcommit] %s: merged %d snapshot(s) — %s", vm_name, count, names)` after blockcommit in `Core._blockcommit_snapshots()`
- [x] 5.3 Add `logger.info("[backup] %s: transferred %s → %s (%d B in %.1fs, %.1f MiB/s)", ...)` for each successful incremental transfer in `Core._backup_target()`
- [x] 5.4 Add `logger.info("[backup] %s: created FULL %s (%d B)", ...)` for successful FULL creation
- [x] 5.5 Add `logger.info("[delete] %s: removed backup %s from %s", ...)` for each deleted backup in `Core._cleanup_backups()`
- [x] 5.6 Add `logger.info("[delete] %s: ghost-retained FULL %s (%d dependent(s) in keep-set)", ...)` for ghost retention
- [x] 5.7 Reference spec: `core-orchestrator/spec.md` — "Per-operation INFO logging in Core"

## 6. Summary Table (New Capability)

### 6.1 Summary formatter

- [x] 6.1.1 Create `qsnap/cli/summary.py` with pure function `format_summary(result: PipelineResult) -> str`
- [x] 6.1.2 Format header: `qsnap Backup Summary (version X.Y.Z)`, date, config path
- [x] 6.1.3 Format legend with symbols: `+++` created snapshot, `---` deleted snapshot, `>>>` transferred incremental, `***` created FULL, `!!!` ERROR
- [x] 6.1.4 Format per-VM blocks: group `ActionRecord` by `vm_name`, sort by pipeline order
- [x] 6.1.5 For dry-run: add `Dryrun: YES` to header and disclaimer footer
- [x] 6.1.6 No imports from `qsnap.modules`, `qsnap.config`, `qsnap.retention`, or `qsnap.state`
- [x] 6.1.7 Reference spec: `backup-summary/spec.md`

### 6.2 CLI integration

- [x] 6.2.1 In `qsnap/cli/commands.py:_format_pipeline_result()`, after computing exit code, call `format_summary(result)` and print to stdout
- [x] 6.2.2 Reference spec: `cli-interface/spec.md` — "Summary output after run command" and "Summary printed after successful run"

## 7. Transaction Log (New Capability)

### 7.1 GlobalConfig field

- [x] 7.1.1 Add `transaction_log: str | None = None` to `GlobalConfig` in `qsnap/models/config.py`
- [x] 7.1.2 Add parsing in `qsnap/config/facade.py` (optional, absolute path string)

### 7.2 TransactionWriter utility

- [x] 7.2.1 Create `qsnap/utils/transaction.py` with `TransactionWriter` class
- [x] 7.2.2 Implement `static write(path: Path, record: ActionRecord) -> None` — appends one line in btrbk-compatible format: `localtime type status target_url source_url parent_url`
- [x] 7.2.3 Map `ActionRecord.action` values to btrbk-compatible `type` field: `snapshot_create` → `snapshot`, `snapshot_delete` → `delete_snapshot`, `backup_transfer` → `backup`, `backup_full` → `backup_full`, `backup_delete` → `delete_backup`, `error` → `ERROR` status
- [x] 7.2.4 No import from `qsnap.core`, `qsnap.config`, or `qsnap.modules`
- [x] 7.2.5 Reference spec: `transaction-log/spec.md`

### 7.3 Core integration

- [x] 7.3.1 In `_run_pipeline()`, after populating `PipelineResult.actions`, if `global_cfg.transaction_log` is set, write each `ActionRecord` via `TransactionWriter.write()`
- [x] 7.3.2 Append final `finished success` line after writing all action records
- [x] 7.3.3 Skip transaction log writing in dry-run mode

## 8. Testing

**IMPORTANT — Test Orchestration Protocol:**

The lead programmer agent implementing these tasks MUST follow this protocol for testing:

1. Read `test-plan.md` Delegation Groups section
2. For EACH group listed below, launch one `@Mr.Tester` subagent IN PARALLEL (all in a single message)
3. Each `@Mr.Tester` subagent MUST receive:
   - The group's scope (file paths from test-plan.md)
   - The group's scenario list from the Coverage Map
   - A copy of `TESTING.md` describing the test architecture, paradigm, and mock strategy
   - Instruction: "Write or fix ONLY these specific tests. Report source bugs found, do NOT fix them yourself."
4. After ALL testers return: review reports, fix any reported source-level bugs
5. Re-delegate any groups affected by source fixes
6. Repeat until all groups pass

### 8.1 Read test-plan and launch parallel test delegation

- [x] 8.1.1 Read `test-plan.md` Delegation Groups section
- [x] 8.1.2 Delegate group `models-action-record` to @Mr.Tester (scope: `tests/models/test_results.py` — NEW tests for ActionRecord)
- [x] 8.1.3 Delegate group `core-audit-trail` to @Mr.Tester (scope: `tests/core/test_engine.py` — NEW tests for action accumulation)
- [x] 8.1.4 Delegate group `core-pipeline` to @Mr.Tester (scope: `tests/core/test_pipeline.py` — NEW + MODIFY tests for backup_failed WARNING, indent fix, and per-op logging)
- [x] 8.1.5 Delegate group `backup-filecopy-silent-failures` to @Mr.Tester (scope: `tests/modules/backup/test_copy.py` — NEW + MODIFY tests for silent failure WARNING logs)
- [x] 8.1.6 Delegate group `backup-bitmap-enhancements` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap.py` — NEW tests for domjobabort + IStateManager)
- [x] 8.1.7 Delegate group `factory-bitmap-state` to @Mr.Tester (scope: `tests/factory/test_default.py` — NEW + MODIFY tests for IStateManager injection)
- [x] 8.1.8 Delegate group `cli-summary-output` to @Mr.Tester (scope: `tests/cli/test_summary.py` — NEW file, all summary formatter tests)
- [x] 8.1.9 Delegate group `cli-commands-summary` to @Mr.Tester (scope: `tests/cli/test_commands.py` — NEW + MODIFY tests for summary dispatch)
- [x] 8.1.10 Delegate group `cli-thin-layer` to @Mr.Tester (scope: `tests/cli/test_thin_layer.py` — NEW + MODIFY tests for summary.py import check)
- [x] 8.1.11 Delegate group `utils-transaction-log` to @Mr.Tester (scope: `tests/utils/test_transaction.py` — NEW file, all TransactionWriter tests)
- [x] 8.1.12 Delegate group `interfaces-backup-contract` to @Mr.Tester (scope: `tests/interfaces/test_backup_provider.py` — MODIFY: add BitmapBackupProvider with state kwarg)
- [x] 8.1.13 Delegate group `config-transaction-log` to @Mr.Tester (scope: `tests/config/test_model.py` — NEW tests for transaction_log config field)

### 8.2 Review and verification

- [x] 8.2.1 Review all @Mr.Tester reports and fix any source-level bugs discovered
- [x] 8.2.2 Re-delegate any groups affected by source fixes
- [x] 8.2.3 Verify all groups pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 8.2.4 Verify coverage matches test-plan.md: all spec scenarios have corresponding tests

## 9. Final Verification

- [x] 9.1 Run ruff lint: `poetry run ruff check qsnap/` — 5 pre-existing errors only (SIM108, F841, SIM105 in files not modified by this change); all new files clean
- [x] 9.2 Run ruff format check: `poetry run ruff format --check qsnap/`
- [x] 9.3 Run pyright: `poetry run pyright` — 16 pre-existing errors (reportUnknownVariableType on list fields, reportPrivateUsage); no new errors in new files
- [x] 9.4 Run full test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` — **990 passed, 0 failed** (up from 924 baseline, +66 new tests)
- [x] 9.5 Manual smoke test: deferred to operator (requires live libvirt environment)
