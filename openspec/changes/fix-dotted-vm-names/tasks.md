## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fix-dotted-vm-names`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e" --tb=short`

## 2. Interface Change — Add `vm_name` Parameter (Prong 1)

Reference: `specs/backup-provider/spec.md`, `specs/live-vm-full-backup/spec.md`, `design.md` Decision 1 & 2

- [x] 2.1 Update `IBackupProvider.create_full_backup()` signature in `qsnap/interfaces/backup.py`: add `vm_name: str` as the first positional parameter (before `source_snapshot`). Update the docstring to explain that `vm_name` is the full, untruncated VM name passed from Core's `vm_config.name`.
- [x] 2.2 Update `FileCopyBackupProvider.create_full_backup()` in `qsnap/modules/backup/file_copy.py`: add `vm_name: str` as first parameter. DELETE line 347 (`vm_name = source_snapshot.name.split(".")[0]`). The method now uses the `vm_name` parameter directly for `is_vm_running()`, `nbd_full_export()`, and `full_name` generation.
- [x] 2.3 Update `BitmapBackupProvider.create_full_backup()` in `qsnap/modules/backup/bitmap.py`: add `vm_name: str` as first parameter. DELETE line 290 (`vm_name = source_snapshot.name.split(".")[0]`).
- [x] 2.4 Update the internal call in `FileCopyBackupProvider.transfer_missing()` at `qsnap/modules/backup/file_copy.py:99`: add `vm_config.name` as the first argument to `self.create_full_backup()`.
- [x] 2.5 Update the Core call site in `Core._backup_target()` at `qsnap/core/__init__.py:2363`: add `vm_config.name` as the first argument to `provider.create_full_backup()`.
- [x] 2.6 Update mock implementations in `tests/mocks/mock_modules.py`: add `vm_name: str` as first parameter to both `MockBackupProvider.create_full_backup()` (line 97) and `MockBitmapBackupProvider.create_full_backup()` (line 157).
- [x] 2.7 Run `poetry run pyright qsnap/` to verify the type checker catches any missed call sites.

## 3. Fix `parse_timestamp()` (Prong 2)

Reference: `specs/parsing-utils/spec.md`, `design.md` Decision 3

- [x] 3.1 Rewrite `parse_timestamp()` in `qsnap/utils/parsing.py`: replace the `split(".")[-1]` + `strptime("%Y%m%dT%H%M%S")` logic with regex-based extraction. Use `re.search()` with three patterns in order of specificity: (1) `r"(\d{8}T\d{6}[+-]\d{4})"` for long-iso, (2) `r"(\d{8}T\d{4})"` for long, (3) `r"(\d{8})"` for short. Try `strptime` with the matching format. Keep mtime and `datetime.now()` fallbacks. Update the docstring.
- [x] 3.2 Verify the fix manually: `python -c "from qsnap.utils.parsing import parse_timestamp; from pathlib import Path; print(parse_timestamp('3.Projects_opencode.20260717T0431_vda', Path('/fake')))"` should print `2026-07-17 04:31:00`.

## 4. Testing

Reference: `test-plan.md` Delegation Groups section

**CRITICAL — TEST ORCHESTRATION PROTOCOL:**

The implementing agent (Mr. Programmer) MUST delegate ALL test work to specialized @Mr.Tester subagents. The implementing agent MUST NOT write tests directly.

For EACH delegation group listed below, the implementing agent MUST:
1. Launch one @Mr.Tester subagent with the group's scope, scenario list, and instructions
2. **MANDATORY**: Pass the full content of `TESTING.md` (located at `/home/openuser/vm/qsnap/TESTING.md`) to EACH @Mr.Tester subagent. This document defines the testing paradigm, directory structure, mock strategy, contract test rules, and test categories that ALL test agents MUST follow. Without this document, the tester cannot produce conforming tests.
3. Launch ALL groups IN PARALLEL (single message, multiple tool calls)
4. After all testers return: fix any reported source-level bugs, then re-delegate affected groups
5. Repeat until all groups pass

The `TESTING.md` document content to pass to each tester (read from `/home/openuser/vm/qsnap/TESTING.md`):
- Testing philosophy: tests mirror production hierarchy, factory injection, result objects, isolated dependencies
- Directory structure: tests/ mirrors qsnap/ exactly
- Test categories: unit (zero I/O, mocked IShell), mock (isinstance checks), contract (parametrized over all implementations), integration (real libvirt)
- Mock strategy: custom mock classes implementing ABCs, NO pytest-mock, `unittest.mock.patch` only for spying
- Fixtures: mock_shell, mock_state, mock_config, mock_factory, make_vm_config, make_target, frozen_clock
- Test markers: unit, mock, contract, integration, stress, e2e — use `--strict-markers`
- Running: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

- [x] 4.1 Read `test-plan.md` Delegation Groups section and `TESTING.md`
- [x] 4.2 Delegate group `mock-backup-provider` to @Mr.Tester (scope: `tests/mocks/mock_modules.py` — 2 mock `create_full_backup()` implementations need `vm_name: str` param added). **Pass TESTING.md to the tester.** This group MUST be done first — it unblocks all other groups.
- [x] 4.3 Delegate group `file-copy-unit` to @Mr.Tester (scope: `tests/modules/backup/test_copy.py` — ~20 call sites need `vm_name` added + 3 NEW tests for dotted VM name scenarios). **Pass TESTING.md to the tester.**
- [x] 4.4 Delegate group `bitmap-unit` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap.py` — ~12 call sites need `vm_name` added + 1 NEW test for dotted VM name). **Pass TESTING.md to the tester.**
- [x] 4.5 Delegate group `parsing-utils-unit` to @Mr.Tester (scope: `tests/utils/test_parsing.py` — fix existing test using wrong format + 6 NEW tests for all timestamp formats and dotted names). **Pass TESTING.md to the tester.**
- [x] 4.6 Delegate group `core-unit` to @Mr.Tester (scope: `tests/core/test_pipeline.py` — 5 spy sites need signature match verification + 1 NEW test for `vm_config.name` passing). **Pass TESTING.md to the tester.**
- [x] 4.7 Delegate group `interface-unit` to @Mr.Tester (scope: `tests/interfaces/test_backup_provider.py` — contract test signature verification + call site updates). **Pass TESTING.md to the tester.**
- [x] 4.8 Delegate group `integration` to @Mr.Tester (scope: `tests/integration/test_nbd_full_backup.py` — 6 call sites need `vm_name` added). **Pass TESTING.md to the tester.**
- [x] 4.9 Review all @Mr.Tester reports and fix any source-level bugs discovered
- [x] 4.10 Re-delegate any groups affected by source fixes (pass TESTING.md again)
- [x] 4.11 Verify all groups pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e" --tb=short`
- [x] 4.12 Verify coverage matches `test-plan.md`: every spec scenario has a corresponding test

## 5. Linting & Type Check

- [x] 5.1 Run ruff: `poetry run ruff check qsnap/ tests/`
- [x] 5.2 Run ruff format: `poetry run ruff format --check qsnap/ tests/`
- [x] 5.3 Run pyright strict: `poetry run pyright qsnap/`
- [x] 5.4 Fix any issues found by linters or type checker

## 6. Final Verification

- [x] 6.1 Run full test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 6.2 Run with random order: `poetry run pytest tests/ -m "not integration and not stress and not e2e" --random-order`
- [x] 6.3 Run coverage: `poetry run pytest tests/ --cov=qsnap --cov-report=html`
- [x] 6.4 Manual dry-run verification: `poetry run qsnap --dry-run run` — should show `method=NBD, VM=running` for all 3 dotted VMs
- [x] 6.5 Commit all changes with a descriptive message
