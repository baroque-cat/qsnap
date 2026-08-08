# Tasks: fix-full-backup-state-extension

## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fix-full-backup-state-extension`
- [x] 1.2 Verify all existing tests pass before starting: run the full test suite (`poetry run pytest tests/ -m "not integration and not stress and not e2e"`)

## 2. State Manager — Name Normalization (design D2, D3; specs/state-management)

- [x] 2.1 Add private helper `_to_extended_name(name: str) -> str` to `JsonStateManager` in `qsnap/state/json_manager.py`: returns `name` unchanged if it already ends with `.qcow2`, otherwise appends `.qcow2`. Inverse counterpart of the existing `_normalize_full_name` (which strips the extension for the `_dependencies.json` world). Sphinx-style docstring.
- [x] 2.2 Make `record_full_backup()` (json_manager.py:478-497) defensive: normalize `name` via `_to_extended_name` before storing; derive `"path"` from the NORMALIZED name (`str(Path(target_path) / normalized_name)`). `set_last_full_backup` inherits the behavior via delegation — no separate change needed there, but verify.
- [x] 2.3 Add idempotent load-time migration to `_load_full_backups()` (json_manager.py:359-421): for every entry, normalize `name` and `path` fields INDEPENDENTLY (per-field guard: append `.qcow2` only when the respective field lacks it; rebuild `path` as `str(Path(target_path) / normalized_name)` when the stored path is stem-based). This normalization MUST run BEFORE the existing dedup pass so stem/extended twin entries collapse into one. Persist the normalized data back to disk (same write-back pattern as the existing dedup migration).
- [x] 2.4 Make `remove_full_backup()` (json_manager.py:579-588) name-format tolerant: normalize the incoming `name` argument via `_to_extended_name` before the exact-match filter, so stem callers (`Core._cleanup_backups`, core:5681, passes `BackupInfo.name` = stem) and extended callers both match. Do NOT change any call site.
- [x] 2.5 Verify no other reader of `_full_backups.json` needs adjustment: `get_full_backups`, `get_last_full_backup`, `reset_target_disk_state` consume entries via `_load_full_backups` and therefore inherit the migration.

## 3. Core — Record FULL With Extension (design D1; specs/periodic-full-backup)

- [x] 3.1 In `qsnap/core/__init__.py:5262` (`Core._backup_target`), change the state recording call to pass `f"{result.snapshot_name}.qcow2"` instead of the bare stem `result.snapshot_name`. This restores the pre-0811599 contract. Do NOT change the provider contract: `BackupResult.snapshot_name` remains a stem (design D5).
- [x] 3.2 Confirm the sibling call `record_incremental_dependency` (core:5269-5277) is untouched — the `_dependencies.json` world stays stem-keyed by design.

## 4. Mock Parity (design D4)

- [x] 4.1 Update `InMemoryStateManager.record_full_backup` in `tests/mocks/mock_state.py:144-161` to mirror the production contract: normalize `name` to extended form and derive `path` from the normalized name. The mock currently mirrors the bug, which is why unit tests missed the regression.
- [x] 4.2 Update `InMemoryStateManager.remove_full_backup` to the same tolerant lookup as production (design D4 — contract tests are parametrized over both implementations and must observe identical behavior).

## 5. Latent Bug Discovered During Test Planning

- [x] 5.1 Fix swapped argument order in `tests/core/test_reconcile.py:193`: `record_full_backup("vda", str(target.path), full_name, datetime.now())` must be `record_full_backup(str(target.path), full_name, datetime.now(), disk)` per the `IStateManager` signature. The test currently passes only because the garbage record lands under a target it never reads.

## 6. Testing

<!--
  MANDATORY DELEGATION RULE (product-owner requirement):
  The lead programmer agent MUST delegate test work to specialized tester agents
  (@Mr.Tester), one per delegation group below, launched IN PARALLEL (single message).
  When delegating, the lead programmer agent is REQUIRED to pass to EVERY tester:
    1. The group's scope and scenario list from test-plan.md (Coverage Map rows).
    2. The file /home/openuser/vm/qsnap/TESTING.md — the project's testing
       philosophy, directory layout, categories, markers, mock strategy and
       paradigm. Every tester MUST read it before writing or modifying any test.
    3. Instruction: "Write or fix ONLY the tests in your group. Report source
       bugs, do not fix them."

  TEST ORCHESTRATION PROTOCOL:
  1. Read test-plan.md → Delegation Groups section
  2. For EACH group listed, launch one @Mr.Tester subagent with the group's
     scope (file paths), its Coverage Map scenarios, TESTING.md, and the
     instruction above
  3. Launch ALL groups IN PARALLEL (single message)
  4. After all testers return: fix any reported source bugs, re-delegate
     affected groups
  5. Repeat until all groups pass
-->

- [x] 6.1 Read `test-plan.md` Delegation Groups and Coverage Map sections
- [x] 6.2 Delegate group `state-unit` to @Mr.Tester (scope: `tests/state/test_manager.py`; includes rewrites of the 11 stem-encoding tests listed in test-plan.md "Tests to Delete (Refactoring)") — pass TESTING.md
- [x] 6.3 Delegate group `state-contract` to @Mr.Tester (scope: `tests/interfaces/test_state_manager.py`; add tolerant-lookup and normalization contract scenarios parametrized over JsonStateManager + InMemoryStateManager) — pass TESTING.md
- [x] 6.4 Delegate group `mock-parity` to @Mr.Tester (scope: `tests/mocks/mock_state.py` verification + `tests/mocks/test_mock_state.py`) — pass TESTING.md
- [x] 6.5 Delegate group `core-unit` to @Mr.Tester (scope: `tests/core/test_full_backup_state_extension.py` [NEW], `tests/core/test_state_check.py`, `tests/core/test_dry_run_prediction.py`) — pass TESTING.md
- [x] 6.6 Delegate group `integration-workaround-cleanup` to @Mr.Tester (scope: `tests/integration/test_check_targets.py`, `test_preserve_min.py`, `test_coverage_gaps.py`, `test_dry_run.py`, `test_rollback_retry.py`; delete `_normalize_full_state`, `_align_recorded_full_with_disk` and all re-record workaround blocks per test-plan.md deletion list; convert call sites into corrected-behavior assertions) — pass TESTING.md
- [x] 6.7 Delegate group `integration-behavior` to @Mr.Tester (scope: `tests/integration/test_reconcile.py`, `test_startup_validation.py`, `test_reconcile_targets.py`, `test_count_based_full.py`; strengthen assertions for the corrected behavior: recorded name ends with `.qcow2`, `full.path` exists on disk, no phantom FULLs reported, second run creates a delta) — pass TESTING.md
- [x] 6.8 Review all @Mr.Tester reports and fix any source-level bugs discovered
- [x] 6.9 Re-delegate any groups affected by source fixes (again with TESTING.md attached)
- [x] 6.10 Verify all groups pass and coverage matches `test-plan.md`

## 7. Final Verification

- [x] 7.1 Run the full non-integration suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` — all green
- [x] 7.2 Run lint and types: `poetry run ruff check qsnap/ tests/` and `poetry run ruff format --check qsnap/ tests/` and `poetry run pyright qsnap/`
- [x] 7.3 Run integration groups if a libvirt test environment is available; otherwise document the skip
- [x] 7.4 Validate the change artifacts: `openspec validate fix-full-backup-state-extension`
- [x] 7.5 Manual smoke verification per design.md migration plan: on a disposable VM, run backup twice — first run records a FULL with `.qcow2` name, `qsnap check` reports no phantom FULLs, second run creates a delta (not a new FULL)
