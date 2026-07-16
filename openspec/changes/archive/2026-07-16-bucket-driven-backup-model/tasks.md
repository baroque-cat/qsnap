## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b bucket-driven-backup-model`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Config Model & Parsing (Foundation)

- [x] 2.1 In `qsnap/models/config.py`: Remove `full_every: str = "0d"` from `TargetConfig`. Rename `full_compress: bool = False` to `compress: bool = True`. Add `copy_base: bool = False` to `TargetConfig`. Add `compress: bool = True` to `GlobalConfig`. (specs: config-model)
- [x] 2.2 In `qsnap/config/facade.py`: Remove parsing of `full_every`. Rename `full_compress` parsing to `compress` with inheritance global→target. Add parsing of `copy_base` (default False). Add `compress` to GlobalConfig parsing. If `full_every` found in TOML, log deprecation WARNING and ignore. If `full_compress` found and `compress` not set, map `full_compress`→`compress` with WARNING. (specs: config-parsing)
- [x] 2.3 In `qsnap/config/facade.py`: Add validation — if `target_preserve` is not None and not "latest" and all bucket counts are 0 and `target_preserve_min` is not "all", raise `ConfigError`. (specs: config-parsing)
- [x] 2.4 Update `qsnap.toml.example` (if exists) or create one: remove `full_every` and `full_compress`, add `compress` and `copy_base`. (specs: config-parsing)

## 3. State Manager (Multi-FULL + Dependency Tracking)

- [x] 3.1 In `qsnap/interfaces/state.py` (IStateManager): Add `get_full_backups(target_path: str) -> list[FullBackupInfo]`, `record_full_backup(target_path: str, name: str, timestamp: datetime, bucket_level: str) -> None`, `record_incremental_dependency(target_path: str, incremental_name: str, full_name: str) -> None`, `get_incremental_dependencies(target_path: str, full_name: str) -> list[str]`. (specs: cascade-deletion)
- [x] 3.2 In `qsnap/state/json_manager.py`: Change `_full_backups.json` format from `dict[str, dict]` (single FULL) to `dict[str, list[dict]]` (multiple FULLs). Implement auto-migration on load: if value is dict (not list), wrap in single-element list. Implement `record_full_backup` (append, not overwrite). Implement `get_full_backups` (return list). (specs: cascade-deletion)
- [x] 3.3 In `qsnap/state/json_manager.py`: Add `_dependencies.json` (or extend state file) for incremental→FULL dependency tracking. Implement `record_incremental_dependency` and `get_incremental_dependencies`. (specs: cascade-deletion)
- [x] 3.4 Update `FullBackupInfo` dataclass: add `bucket_level: str` field. (specs: cascade-deletion)

## 4. Backup Provider (rsync + FULL + copy_base + Dependencies)

- [x] 4.1 In `qsnap/modules/backup/file_copy.py` `transfer_missing()`: Remove `cp` fallback and `use_rsync`/`rsync_available` flags. Always use `rsync`. When `rate_limit == "no"`: `rsync --partial <src> <dst>`. When `rate_limit != "no"`: `rsync --bwlimit=<kib> --partial <src> <dst>`. (specs: backup-provider, design D3)
- [x] 4.2 In `qsnap/modules/backup/file_copy.py` `transfer_missing()`: Add `copy_base` check — when `target.copy_base == False` (default), never copy `base.qcow2`. When target is empty (no existing backups), trigger `create_full_backup()` for the first snapshot instead of rsync. (specs: backup-provider, design D4)
- [x] 4.3 In `qsnap/modules/backup/file_copy.py` `_find_full_anchor()`: Replace mtime-based selection with timestamp-from-filename parsing. Parse `*.FULL.*.qcow2` filenames to extract date, return the most recent by date. (specs: periodic-full-backup)
- [x] 4.4 In `qsnap/modules/backup/file_copy.py` `transfer_missing()`: After rebase, call `state.record_incremental_dependency(target_path, incremental_name, full_name)`. Use the specific FULL the incremental was rebased to (from state), not just the newest. (specs: periodic-full-backup, cascade-deletion)
- [x] 4.5 In `qsnap/modules/backup/file_copy.py` `create_full_backup()`: Add `bucket_level: str = "monthly"` parameter. After creation, call `state.record_full_backup(target_path, name, timestamp, bucket_level)`. (specs: periodic-full-backup)
- [x] 4.6 In `qsnap/modules/backup/file_copy.py` `delete()`: Before deleting a FULL, check dependencies via `state.get_incremental_dependencies()`. If dependents exist in keep-set, skip deletion (ghost retention). If no dependents, delete FULL + cascade-delete orphaned incrementals. (specs: cascade-deletion)

## 5. Core Orchestrator (Bucket-Driven FULL + Cascade + Size Estimation)

- [x] 5.1 In `qsnap/core/__init__.py`: Remove `_should_create_full()` method. Add `_should_create_bucket_full(target, policy, last_full, snapshot_ts) -> tuple[bool, str]` — identify highest active bucket (yearly→hourly), check if snapshot is in new period of that bucket. (specs: periodic-full-backup, design D1)
- [x] 5.2 In `qsnap/core/__init__.py` `_backup_target()`: Replace `_should_create_full()` call with `_should_create_bucket_full()`. Pass parsed `RetentionPolicy`. On `should_create=True`, call `provider.create_full_backup(snapshot, target, compress=target.compress, bucket_level=level)`. Then continue with incremental transfer. (specs: periodic-full-backup)
- [x] 5.3 In `qsnap/core/__init__.py` `_cleanup_backups()`: Before deleting any backup, check if it's a FULL (name matches `*.FULL.*`). If FULL, check `state.get_incremental_dependencies()`. If any dependent is in keep-set, remove FULL from delete list (ghost retention). Log ghost retention. After deleting a FULL, cascade-delete orphaned incrementals not in keep-set. Add dry-run log: `[dry-run] Would delete backup: {name}`. (specs: cascade-deletion, design D2)
- [x] 5.4 In `qsnap/core/__init__.py` `_validate_environment()`: Change rsync check from soft WARNING to hard ERROR. Always run `which rsync` (remove conditional on `rate_limit`). If rsync not found, return error and abort pipeline. (specs: env-validation, design D3)
- [x] 5.5 In `qsnap/core/__init__.py`: Add `_log_size_estimate(vm_config, target)` method — get base image actual-size via `qemu-img info`, get avg incremental size from state history, compute projected size from retention policy, get current target size via `du -sh`, log at INFO. Call in `_backup_target()` before transfer (even in dry-run). (specs: size-estimation, design D5)
- [x] 5.6 In `qsnap/core/__init__.py`: Add `estimate(vm_filter=None)` method — same as `schedule_summary()` but with real size projections. (specs: size-estimation)
- [x] 5.7 In `qsnap/core/__init__.py` `schedule_summary()`: Add real size projections — base image actual-size, avg incremental size, projected FULL count, projected total size, current target size. (specs: schedule-summary)

## 6. CLI (estimate command)

- [x] 6.1 In `qsnap/cli/app.py`: Add `estimate` subcommand to argparser with optional VM positional argument. (specs: cli-interface)
- [x] 6.2 In `qsnap/cli/commands.py`: Add `handle_estimate()` handler — calls `core.estimate(vm_filter)`, formats and prints output. (specs: cli-interface)

## 7. README & Documentation

- [x] 7.1 In `README.md` Configuration Reference: Remove `full_every` and `full_compress` from Target Keys table. Add `compress` (default `true`) and `copy_base` (default `false`). Add `compress` to Global Keys table.
- [x] 7.2 In `README.md` Full Backups section: Complete rewrite — describe bucket-driven FULL creation (not `full_every`). Add table showing how highest active bucket determines FULL frequency. Describe cascade deletion. Remove `full_every` config examples.
- [x] 7.3 In `README.md` Example Configurations: Remove `full_every = "14d"` and `full_every = "7d"` from examples. Replace with `compress = true` where appropriate. Update comments.
- [x] 7.4 In `README.md` Requirements: Add `rsync` to the list of system requirements (was optional, now hard requirement).
- [x] 7.5 In `README.md`: Add new "Size Estimation" section — describe automatic logging on every run, `qsnap estimate` command, example output.
- [x] 7.6 In `README.md` Restore section: Update to mention FULL anchor chain structure on target.
- [x] 7.7 In `AGENTS.md`: Update Pipeline description to mention bucket-driven FULL creation and cascade deletion in Step 5.

## 8. Testing

**CRITICAL — TEST DELEGATION PROTOCOL:**

The implementing agent (@Mr.Programmer) MUST delegate ALL test writing to @Mr.Tester subagents. The implementing agent MUST NOT write tests directly. For EACH delegation group below, the implementing agent MUST:

1. Launch a @Mr.Tester subagent with the group's scope, scenario list, and test file paths
2. **MANDATORY**: Pass the file `/home/openuser/vm/qsnap/TESTING.md` to each @Mr.Tester subagent. This document defines the testing paradigm, directory structure, test categories, and rules that ALL tests must follow. The @Mr.Tester subagent MUST read and internalize TESTING.md before writing any tests.
3. Instruct each @Mr.Tester: "Read TESTING.md at /home/openuser/vm/qsnap/TESTING.md first. It defines the testing paradigm: tests mirror production hierarchy, every ABC gets a mock, Core tested with MockFactory (zero real virsh), each module tested in isolation with mocked IShell, retention engine tested with fixed timestamps. Follow its directory structure and rules exactly."
4. Launch ALL groups IN PARALLEL (single message, multiple @Mr.Tester calls)
5. After all testers return: fix any reported source-level bugs, re-delegate affected groups
6. Repeat until all groups pass

- [x] 8.1 Read `test-plan.md` Delegation Groups section for full scope and scenario details
- [x] 8.2 Delegate group `config-model-unit` to @Mr.Tester (scope: `tests/config/test_model.py`) — MUST pass TESTING.md
- [x] 8.3 Delegate group `config-parsing-unit` to @Mr.Tester (scope: `tests/config/test_parser.py`, `tests/config/test_facade.py`) — MUST pass TESTING.md
- [x] 8.4 Delegate group `backup-provider-unit` to @Mr.Tester (scope: `tests/modules/backup/test_copy.py`) — MUST pass TESTING.md
- [x] 8.5 Delegate group `state-manager-unit` to @Mr.Tester (scope: `tests/state/test_manager.py`) — MUST pass TESTING.md
- [x] 8.6 Delegate group `core-bucket-unit` to @Mr.Tester (scope: `tests/core/test_engine.py`, `tests/core/test_pipeline.py` bucket scenarios) — MUST pass TESTING.md
- [x] 8.7 Delegate group `core-cascade-unit` to @Mr.Tester (scope: `tests/core/test_pipeline.py` cascade scenarios) — MUST pass TESTING.md
- [x] 8.8 Delegate group `core-validation-unit` to @Mr.Tester (scope: `tests/core/test_validation.py`) — MUST pass TESTING.md
- [x] 8.9 Delegate group `core-schedule-unit` to @Mr.Tester (scope: `tests/core/test_schedule_summary.py`) — MUST pass TESTING.md
- [x] 8.10 Delegate group `retention-engine-unit` to @Mr.Tester (scope: `tests/modules/retention/test_time_based.py`) — MUST pass TESTING.md
- [x] 8.11 Delegate group `cli-interface-unit` to @Mr.Tester (scope: `tests/cli/test_commands.py`, `tests/cli/test_app.py`) — MUST pass TESTING.md
- [x] 8.12 Delegate group `interfaces-contract` to @Mr.Tester (scope: `tests/interfaces/test_state_manager.py`, `tests/interfaces/test_backup_provider.py`) — MUST pass TESTING.md
- [x] 8.13 Delegate group `mocks-unit` to @Mr.Tester (scope: `tests/mocks/mock_state.py`, `tests/mocks/test_mock_state.py`) — MUST pass TESTING.md
- [x] 8.14 Delegate group `conftest-fixtures` to @Mr.Tester (scope: `tests/conftest.py`, `tests/fixtures/configs/`) — MUST pass TESTING.md
- [x] 8.15 Review all @Mr.Tester reports and fix any source-level bugs discovered
- [x] 8.16 Re-delegate any groups affected by source fixes (re-pass TESTING.md to each)
- [x] 8.17 Verify all groups pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 8.18 Verify coverage matches test-plan.md: every spec scenario has a corresponding test

## 9. Final Verification

- [x] 9.1 Run full test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 9.2 Verify config with `full_every` logs deprecation warning and continues
- [x] 9.3 Verify config with `full_compress` maps to `compress` with warning
- [x] 9.4 Verify config with all-zero buckets and `preserve_min != "all"` raises ConfigError
- [x] 9.5 Verify `qsnap estimate` command works
- [x] 9.6 Verify README.md has no references to `full_every` or `full_compress`
- [x] 9.7 Verify `rsync` is listed in README Requirements section
- [x] 9.8 Run `openspec validate --change bucket-driven-backup-model` to verify change artifacts
