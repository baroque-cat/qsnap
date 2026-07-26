## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b onchange-self-healing`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. IStateManager: New ABC Methods

Reference: `specs/state-management/spec.md`, `specs/state-reconciliation/spec.md`

- [x] 2.1 Add `clear_last_backup_allocation(target_path: str) -> bool` abstract method to `IStateManager` ABC in `interfaces/state.py`
- [x] 2.2 Add `remove_all_incremental_dependencies(target_path: str, full_name: str) -> int` abstract method to `IStateManager` ABC in `interfaces/state.py`
- [x] 2.3 Implement `clear_last_backup_allocation` in `JsonStateManager` (`state/json_manager.py`) — load `_target_state.json`, delete key, atomic save
- [x] 2.4 Implement `remove_all_incremental_dependencies` in `JsonStateManager` (`state/json_manager.py`) — load `_dependencies.json`, delete full_name key, atomic save, return count
- [x] 2.5 Implement both methods in `InMemoryStateManager` (`tests/mocks/mock_state.py`) — dict-based, mirror JsonStateManager semantics

## 3. Core: Replace Onchange Gate (Approach B)

Reference: `specs/change-detection/spec.md` (MODIFIED: Backup-side onchange gate), `design.md` Decision 1

- [x] 3.1 Replace `_should_backup_onchange()` in `core/__init__.py:2760-2782` with Approach B: call `self._factory.create_backup_provider(vm_config, target)`, call `provider.list(target)`, compare snapshot names in state against backup names on target
- [x] 3.2 Remove the `set_last_backup_allocation` call block at `core/__init__.py:2989-3001` (dead code — gate no longer uses `last_backup_allocation`)
- [x] 3.3 Update log message from "unchanged (allocation %d == last backup) — skipping" to "no new snapshots — skipping transfer"

## 4. Core: Cascade Cleanup at Phantom Detection

Reference: `specs/core-orchestrator/spec.md` (MODIFIED: Phantom FULL detection with cascade cleanup)

- [x] 4.1 Extend phantom FULL detection loop in `_backup_target()` (`core/__init__.py:2816-2833`): after `remove_full_backup()`, call `remove_all_incremental_dependencies(target_path, full_name)` and log count
- [x] 4.2 After the phantom loop, if `not filtered_fulls and all_fulls`: call `clear_last_backup_allocation(target_path)` and log INFO

## 5. Core: Startup State Validation

Reference: `specs/startup-state-validation/spec.md`, `specs/core-orchestrator/spec.md` (Startup state validation in pipeline)

- [x] 5.1 Add `_validate_state_at_startup(vm_config: VMConfig) -> None` private method to Core: iterate targets, check phantom FULLs with cascade cleanup, clear stale baselines. Non-fatal (log warnings, never raise).
- [x] 5.2 Call `_validate_state_at_startup()` from `_execute_pipeline()` before `_execute_snapshot_steps()` (`core/__init__.py:1786`)
- [x] 5.3 Call `_validate_state_at_startup()` from `_execute_backup_steps()` before the target loop (`core/__init__.py:2664`)

## 6. Core: Reconcile Public Method

Reference: `specs/state-reconciliation/spec.md`, `specs/state-consistency-check/spec.md`

- [x] 6.1 Add `ReconcileResult` frozen dataclass to `models/results.py` with fields: vm_name, phantom_snapshots_removed, phantom_fulls_removed, stale_deps_removed, baselines_cleared, orphan_checkpoints_deleted, orphan_files_removed, errors
- [x] 6.2 Add `reconcile(vm_filter: str | None = None) -> dict[str, ReconcileResult]` public method to Core: for each VM, (1) remove phantom snapshots, (2) remove phantom FULLs with cascade, (3) clear stale baselines, (4) remove stale deps, (5) auto-delete orphan checkpoints via `_detect_orphan_checkpoints(auto_cleanup=True)`
- [x] 6.3 Support `dry_run` mode in `reconcile()`: when `self._dry_run` is True, report what would be fixed without making changes

## 7. Core: Orphan Checkpoint Auto-Cleanup

Reference: `specs/state-consistency-check/spec.md` (Orphan checkpoint auto-cleanup parameter)

- [x] 7.1 Add `auto_cleanup: bool = False` keyword parameter to `_detect_orphan_checkpoints()` (`core/__init__.py:1285-1332`)
- [x] 7.2 When `auto_cleanup=True` and orphans detected: execute `virsh checkpoint-delete --metadata --domain <vm> <checkpoint>` via `self._shell.run()` for each orphan. Log INFO on success, WARNING on failure. Non-fatal.

## 8. Core: Gate/Retention Separation

Reference: `specs/change-detection/spec.md` (ADDED: Onchange gate and retention separation)

- [x] 8.1 Restructure `_backup_target()` (`core/__init__.py:2794-3005`): replace early `return False` in onchange gate with `skip_transfer = True` flag
- [x] 8.2 Wrap bucket FULL check and `transfer_missing()` in `if not skip_transfer:` block
- [x] 8.3 Move retention evaluation (`_evaluate_backup_retention`) and cleanup (`_cleanup_backups`) outside the `if not skip_transfer:` block so they always run

## 9. CLI: Reconcile Subcommand

Reference: `specs/cli-interface/spec.md`

- [x] 9.1 Add `"reconcile": commands.handle_reconcile` to `_DISPATCH` map in `cli/app.py:29-41`
- [x] 9.2 Add `reconcile` subparser to argument parser in `cli/app.py`: positional `vm_filter` (optional), `--dry-run` flag, `--format` choice
- [x] 9.3 Add `handle_reconcile(core, args) -> int` handler to `cli/commands.py`: set `core.dry_run` if `--dry-run`, call `core.reconcile(vm_filter)`, format results, return 0 or 1

## 10. Testing

Reference: `test-plan.md` Delegation Groups section

**CRITICAL INSTRUCTION FOR THE IMPLEMENTING AGENT:** When delegating test groups to @Mr.Tester subagents, you MUST pass the document `/home/openuser/vm/qsnap/TESTING.md` to EACH @Mr.Tester subagent. This document defines the test architecture, categories, rules, fixtures, and the testing paradigm that ALL tests must follow. Without this document, the tester cannot write tests that conform to the project's testing standards. Include in each delegation prompt: "Read `/home/openuser/vm/qsnap/TESTING.md` for the test architecture and paradigm before writing any tests."

- [x] 10.1 Read `test-plan.md` Delegation Groups section
- [x] 10.2 Delegate group `state-methods` to @Mr.Tester (scope: `tests/state/test_manager.py` — 6 new tests for clear_last_backup_allocation and remove_all_incremental_dependencies)
- [x] 10.3 Delegate group `core-gate` to @Mr.Tester (scope: `tests/core/test_pipeline.py` onchange gate section — 6 modified + 4 new tests for Approach B gate and gate/retention separation)
- [x] 10.4 Delegate group `core-runtime` to @Mr.Tester (scope: `tests/core/test_pipeline.py`, `tests/core/test_state_check.py`, `tests/core/test_full_verification_pipeline.py` — 12 new + 3 new + 2 modified tests for startup validation, phantom cascade, orphan auto-cleanup)
- [x] 10.5 Delegate group `core-reconcile` to @Mr.Tester (scope: `tests/core/test_pipeline.py` reconcile section — 9 new tests for Core.reconcile())
- [x] 10.6 Delegate group `cli-reconcile` to @Mr.Tester (scope: `tests/cli/test_commands.py`, `tests/cli/test_app.py` — 5 new tests for reconcile CLI)
- [x] 10.7 Delegate group `models` to @Mr.Tester (scope: `tests/models/test_results.py` — 1 new test for ReconcileResult)
- [x] 10.8 Delegate group `interfaces` to @Mr.Tester (scope: `tests/interfaces/test_state_manager.py` — 6 new contract tests + 1 modified for new IStateManager methods)
- [x] 10.9 Delegate group `integration-onchange` to @Mr.Tester (scope: `tests/integration/test_onchange.py` — 2 modified + 3 new integration tests including test_onchange_approach_b_gate, test_onchange_manual_deletion_recovery, test_retention_runs_on_skip)
- [x] 10.10 Delegate group `integration-reconcile` to @Mr.Tester (scope: `tests/integration/test_reconcile.py` new file — 1 new integration test: test_reconcile_command)
- [x] 10.11 Delegate group `integration-startup` to @Mr.Tester (scope: `tests/integration/test_startup_validation.py` new file — 1 new integration test: test_startup_validation)
- [x] 10.12 Review @Mr.Tester reports and fix any source-level bugs discovered
- [x] 10.13 Re-delegate any groups affected by source fixes
- [x] 10.14 Verify all groups pass and coverage matches `test-plan.md`

<!--
  TEST ORCHESTRATION PROTOCOL (followed by the apply phase agent):

  1. Read test-plan.md → Delegation Groups section
  2. For EACH group listed, launch one @Mr.Tester subagent with:
     - The group's scope (file paths)
     - The group's scenario list from Coverage Map
     - Instruction: "Read /home/openuser/vm/qsnap/TESTING.md for the test architecture and paradigm before writing any tests. Write or fix ONLY these specific tests. Report source bugs, don't fix them."
  3. Launch ALL groups IN PARALLEL (single message)
  4. After all testers return: fix any reported source bugs, re-delegate affected groups
  5. Repeat until all groups pass
-->

## 11. Final Verification

- [x] 11.1 Run full unit test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` — **1291 passed, 49 deselected** (baseline was 1240 passed, 44 deselected; +51 new tests, 0 regressions)
- [x] 11.2 Run integration tests: `poetry run pytest tests/integration/ -m integration` — **45 collected** (cannot execute without libvirt daemon; all collect without errors)
- [x] 11.3 Verify no regressions in existing functionality — **0 failures**, all 1291 unit tests pass
- [x] 11.4 Run `openspec validate --change onchange-self-healing` to verify spec compliance — **Change 'onchange-self-healing' is valid**
