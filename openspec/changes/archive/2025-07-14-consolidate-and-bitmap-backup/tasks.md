## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b feat/consolidate-and-bitmap-backup`
- [x] 1.2 Run the full test suite to establish a passing baseline: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`
- [x] 1.3 Verify `poetry run ruff check qsnap/` passes with no errors

## 2. Shared Parsing Utilities

- [x] 2.1 Create `qsnap/utils/parsing.py` with `parse_domblklist_path(stdout: str) -> str` — extract source path (last column) from first data row, raise `ValueError` on empty
- [x] 2.2 Add `parse_domblklist_target(stdout: str) -> str` — extract target device name (first column)
- [x] 2.3 Add `parse_domblklist_disks(stdout: str) -> list[tuple[str, str]]` — return list of `(target, source_path)` tuples for all disks
- [x] 2.4 Add `parse_timestamp(name: str, filepath: Path) -> datetime` — parse `%Y%m%dT%H%M%S` from filename suffix, fallback to `filepath.stat().st_mtime`, then `datetime.now()`
- [x] 2.5 Refactor `qsnap/modules/snapshot/external.py`: remove `_parse_domblklist_path` and `_parse_timestamp`, import from `qsnap.utils.parsing`
- [x] 2.6 Refactor `qsnap/modules/change/allocation_detector.py`: remove `_parse_domblklist_path`, import from `qsnap.utils.parsing`
- [x] 2.7 Refactor `qsnap/modules/backup/file_copy.py`: remove `_parse_timestamp`, import from `qsnap.utils.parsing`

## 3. Bug Fixes — P1

- [x] 3.1 Fix hardcoded `disk = "vda"` in `Core._create_snapshot()` (file: `qsnap/core/__init__.py`, line ~278): resolve active disk via `virsh domblklist` using `parse_domblklist_disks` from parsing utils
- [x] 3.2 Implement multi-disk snapshot naming: `{vm_name}.{timestamp}_{disk}.qcow2` per disk (design D2)
- [x] 3.3 Fix `FileCopyBackupProvider.transfer_missing()` (file: `qsnap/modules/backup/file_copy.py`, line ~104): replace bare `pass` in `except (json.JSONDecodeError, KeyError, TypeError)` with `return BackupResult(success=False, error=...)`. The `cp` step already returned success; the rebase failure now properly surfaces.
- [x] 3.4 Wire `EXIT_BACKUP_ABORT` (exit code 10) into `PipelineResult`: add `backup_failed: bool` field to `VMRunResult`; in `Core._backup_target()`, mark it `True` when any `BackupResult` is `success=False`; in `cli/app.py`, return `EXIT_BACKUP_ABORT` if any `VMRunResult.backup_failed` is True

## 4. Config Model Extensions

- [x] 4.1 Add `incremental_mode: str = "file-copy"` field to `TargetConfig` in `qsnap/models/config.py`. Accepted values: `"file-copy"`, `"bitmap"`
- [x] 4.2 Add optional `disks: list[str] | None = None` field to `VMConfig` in `qsnap/models/config.py`. When `None`, Core auto-discovers all disks
- [x] 4.3 Update `ConfigFacade._build_target()` to parse optional `incremental_mode` from TOML target sections
- [x] 4.4 Update `ConfigFacade._build_vm()` to parse optional `disks` array from TOML VM sections

## 5. BitmapBackupProvider

- [x] 5.1 Create `qsnap/modules/backup/bitmap.py` with `BitmapBackupProvider(IShell)` implementing `IBackupProvider`
- [x] 5.2 Implement `transfer_missing()` with dirty bitmap extraction pipeline (design D3): (1) `virsh checkpoint-create-as`, (2) `qemu-img convert --bitmap`, (3) `virsh checkpoint-delete --metadata` on success
- [x] 5.3 Handle first backup (no prior checkpoint): full `qemu-img convert` without `--bitmap`, then create checkpoint for next run
- [x] 5.4 Handle transfer failure: preserve checkpoint, return `BackupResult(success=False)`
- [x] 5.5 Implement `list()` — scan target directory for `*.qcow2` files, same as `FileCopyBackupProvider.list()`
- [x] 5.6 Implement `delete()` — `rm -f` on target file, same as `FileCopyBackupProvider.delete()`
- [x] 5.7 Implement `list_checkpoints(vm_name: str) -> list[str]` — filter `virsh checkpoint-list --name` output by `qsnap-` prefix
- [x] 5.8 Add QEMU version check in constructor: parse `qemu-img --version`, reject if < 5.1 with clear error message
- [x] 5.9 Update `DefaultFactory.create_backup_provider()` in `qsnap/factory/default.py`: return `BitmapBackupProvider(self._shell)` when `target.incremental_mode == "bitmap"`, `FileCopyBackupProvider(self._shell)` otherwise. On `BitmapBackupProvider` construction failure (old QEMU), log a warning and fall back to `FileCopyBackupProvider`

## 6. Multi-Disk and Ondemand Support

- [x] 6.1 Modify `Core._create_snapshot()` to iterate all disks (auto-discovered via `parse_domblklist_disks` or from `VMConfig.disks`), creating one snapshot per disk
- [x] 6.2 Modify `SnapshotResult` collection in `Core._create_snapshot()`: if vda succeeds but vdb fails, log the vdb error and continue pipeline (design D2)
- [x] 6.3 Extend `IChangeDetector.has_changed()` signature to accept optional `disk: str` parameter. Update `AllocationSizeDetector` to resolve the specified disk's path via domblklist when provided
- [x] 6.4 Implement `snapshot_create = "ondemand"`: in `Core._execute_snapshot_steps()`, before creating snapshot, check if at least one `target.path` is a reachable directory. If none, log INFO and skip snapshot creation

## 7. Restore Command

- [x] 7.1 Add `RestoreResult` frozen dataclass to `qsnap/models/results.py`: `success: bool`, `snapshot_name: str`, `restored_path: Path`, `chain_files: list[Path]`, `error: str | None`
- [x] 7.2 Implement `Core.restore(snapshot_name: str, target_dir: Path, vm_filter: str | None = None) -> RestoreResult`: search snapshots in IStateManager and backups on targets; copy chain files to `target_dir`; run `qemu-img rebase -u -b ./basename.qcow2` on each; return `RestoreResult`
- [x] 7.3 Add `restore` subcommand to CLI argparser in `qsnap/cli/app.py`: positional args `SNAPSHOT_NAME` (required) and `TARGET_DIR` (required), optional `VM` filter
- [x] 7.4 Add `handle_restore` handler in `qsnap/cli/commands.py`: validate target_dir exists, call `Core.restore()`, format and print `RestoreResult`

## 8. check --deep and print_schedule Extension

- [x] 8.1 Extend `Core.check()` signature to `check(vm_filter=None, deep=False)`. When `deep=True`, execute `qemu-img check --output=json` on each file, parse `corruptions` count, mark as broken if > 0
- [x] 8.2 Add `--deep` flag to `check` subcommand in `qsnap/cli/app.py`
- [x] 8.3 Extend `Core.print_schedule()` to evaluate backup retention for each target (currently only evaluates snapshot retention). Return per-target `RetentionResult` alongside snapshot results

## 9. Missing Fixtures

- [x] 9.1 Create `tests/fixtures/timestamps/daily_set.json` — 28 items, 2 per day over 14 days
- [x] 9.2 Create `tests/fixtures/timestamps/mixed_set.json` — 19 items, irregular intervals over 7 days
- [x] 9.3 Verify existing retention tests pass with new fixtures: `poetry run pytest tests/modules/retention/test_time_based.py -v`

---

## 10. Testing

**CRITICAL — Test Orchestration Protocol.** All testing is delegated to @Mr.Tester subagents. The implementation agent MUST:

1. Read `test-plan.md` → Delegation Groups section to identify all 6 groups and their scopes.
2. Launch ALL groups IN PARALLEL (single message with multiple @Mr.Tester task calls).
3. For EACH @Mr.Tester subagent, pass as context:
   - The group's scope files and scenario list from Coverage Map
   - **`TESTING.md`** (file at `/home/openuser/vm/qsnap/TESTING.md`) — the complete testing paradigm document that describes: test file hierarchy, mock conventions, contract test rules, fixture locations, run commands, and the "Adding a New Module" checklist. This document MUST be included in every @Mr.Tester task so they understand the project's test architecture.
4. Each @Mr.Tester MUST receive a self-contained task with exact file paths and scenarios; no cross-references.
5. @Mr.Tester subagents write or fix ONLY the specified test files. If they discover source bugs, they report them — they do NOT fix source code.
6. After all testers return: review reports, fix any reported source bugs, re-delegate affected groups.
7. Repeat until all groups pass.

- [x] 10.1 Read `test-plan.md` Delegation Groups section fully to understand all 6 groups
- [x] 10.2 Delegate group `snapshot-bitmap-unit` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap.py`, `tests/modules/backup/test_copy.py`, `tests/modules/snapshot/test_external.py`, `tests/utils/test_parsing.py`, `tests/modules/change/test_allocation.py`). Include `TESTING.md` in the task context.
- [x] 10.3 Delegate group `core-orchestrator-unit` to @Mr.Tester (scope: `tests/core/test_engine.py`, `tests/core/test_pipeline.py`, `tests/core/test_list_commands.py`, `tests/models/test_results.py`). Include `TESTING.md` in the task context.
- [x] 10.4 Delegate group `config-parsing-unit` to @Mr.Tester (scope: `tests/config/test_model.py`, `tests/factory/test_default.py`). Include `TESTING.md` in the task context.
- [x] 10.5 Delegate group `cli-unit` to @Mr.Tester (scope: `tests/cli/test_commands.py`, `tests/cli/test_app.py`). Include `TESTING.md` in the task context.
- [x] 10.6 Delegate group `contracts-mocks` to @Mr.Tester (scope: `tests/interfaces/test_backup_provider.py`, `tests/interfaces/test_change_detector.py`, `tests/mocks/mock_modules.py`, `tests/mocks/mock_factory.py`, `tests/mocks/test_mock_factory.py`). Include `TESTING.md` in the task context.
- [x] 10.7 Delegate group `fixtures-missing` to @Mr.Tester (scope: `tests/fixtures/timestamps/daily_set.json`, `tests/fixtures/timestamps/mixed_set.json`, `tests/modules/retention/test_time_based.py`). Include `TESTING.md` in the task context.
- [x] 10.8 Review all @Mr.Tester reports and fix any source-level bugs discovered during testing
- [x] 10.9 Re-delegate any groups affected by source fixes
- [x] 10.10 Run full test suite and verify all groups pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`
- [x] 10.11 Run coverage report: `poetry run pytest tests/ --cov=qsnap --cov-report=term --cov-report=html`
- [x] 10.12 Verify `poetry run ruff check qsnap/` passes with no errors
- [x] 10.13 Verify `poetry run pyright qsnap/` passes with no errors

<!--
  TEST ORCHESTRATION PROTOCOL (executed by the implementation agent during apply phase):

  1. Read test-plan.md → Delegation Groups section to get all 6 group names and scopes.

  2. For EACH of the 6 groups, launch one @Mr.Tester subagent with:
     - **TESTING.md** — the full project testing paradigm (file at /home/openuser/vm/qsnap/TESTING.md, already read into context). This is MANDATORY for every @Mr.Tester task. It defines:
       * Test file hierarchy mirroring production
       * Mock conventions: MockShell (expectation-based), InMemoryStateManager, MockVMModuleFactory
       * Contract test rules: parametrize over all implementations, verify ABC compliance
       * Run commands: `poetry run pytest tests/ -m "not integration..."`, coverage, etc.
       * "Adding a New Module" checklist: create test → update mock factory → add to contract parametrization → add config fixture → verify
     - The group's scope (file paths) from the test-plan
     - The group's scenario list from the Coverage Map
     - Instruction: "Write or fix ONLY the specified test files. Do NOT modify source code. If you find a source-level bug, report it in your response. Use the project's TESTING.md conventions."

  3. Launch ALL 6 groups IN PARALLEL (single message with 6 task tool calls).

  4. After ALL testers return: collect their reports. If any reported source-level bugs, fix them in source files, then re-delegate the affected group(s) to @Mr.Tester with updated code.

  5. Repeat until ALL groups pass.

  6. Final verification: run full test suite + ruff + pyright.
-->
