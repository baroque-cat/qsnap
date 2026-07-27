## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fix-broken-backing-chain`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Phase 1 — Fix Key Mismatch (B1, CRITICAL)

**Spec:** `specs/state-management/spec.md` — MODIFIED: "IStateManager tracks incremental-to-FULL dependencies", ADDED: "Legacy dependency key migration on load"
**Design:** D3 — Key normalization at lookup, not at storage

- [x] 2.1 Add key normalization in `JsonStateManager.get_incremental_dependencies()` (`qsnap/state/json_manager.py:441`): strip `.qcow2` extension from `full_name` parameter before lookup, since storage uses stem form
- [x] 2.2 Add key normalization in `JsonStateManager.remove_incremental_dependency()` (`qsnap/state/json_manager.py:458`): same normalization as 2.1
- [x] 2.3 Add key normalization in `JsonStateManager.remove_all_incremental_dependencies()`: same normalization as 2.1
- [x] 2.4 Add legacy key migration in `JsonStateManager._load_dependencies()` (`qsnap/state/json_manager.py:409`): after loading JSON, iterate keys in each target's deps dict; any key ending in `.qcow2` SHALL be renamed to its stem form, preserving the value list
- [x] 2.5 Add the same key normalization to `InMemoryStateManager` in `tests/mocks/mock_state.py` for `get_incremental_dependencies`, `remove_incremental_dependency`, and `remove_all_incremental_dependencies` — test parity with production

## 3. Phase 2 — Fix Cascade Deletion of Incrementals (B2, CRITICAL)

**Spec:** `specs/cascade-deletion/spec.md` — MODIFIED: "Core prevents deletion of FULLs with active dependents", "Cascade deletion of orphaned incrementals", "State cleanup when incremental backup is deleted", ADDED: "Reverse backing-chain dependency map"
**Design:** D2 — Reverse dependency map built once per cleanup cycle, D5 — Ghost retention extended to incrementals

- [x] 3.1 Add `_build_backing_refs()` method to `Core` (`qsnap/core/__init__.py`, before `_cleanup_backups` at line 3526): scans all backups via `qemu-img info --output=json`, extracts `backing-filename`, resolves relative paths to absolute, returns `dict[str, list[str]]` mapping `{backing_path → [dependent_name, ...]}`
- [x] 3.2 Call `_build_backing_refs(backups)` at the start of `_cleanup_backups()` (after `keep_set` and `to_delete` are computed, before the deletion loop)
- [x] 3.3 Rewrite the else-branch of `_cleanup_backups()` (`qsnap/core/__init__.py:3661-3676`): check `backing_refs` for dependents of the incremental being deleted; if any dependent is in `keep_set`, ghost-retain (skip deletion, log INFO); if no dependents in keep-set, delete the incremental, cascade-delete orphaned dependents not in keep-set, and call `_resolve_chain_full_anchor()` + `remove_incremental_dependency()` to clean state (fixes B4)

## 4. Phase 3 — Fix `_copy_dirty_blocks` Previous Selection (B3, HIGH)

**Spec:** `specs/nbd-dirty-block-transfer/spec.md` — MODIFIED: "Dirty-block copy loop replaces qemu-img convert for incrementals", ADDED: "Backing-chain validation method for backup files"
**Design:** D4 — Walk backwards through backups for valid `previous`

- [x] 4.1 Add `_validate_backing_chain()` method to `BitmapBackupProvider` (`qsnap/modules/backup/bitmap.py`): runs `qemu-img info --force-share --backing-chain --output=json <path>` via `IShell.run()`, returns `True` if command succeeds, `False` otherwise. Standalone files (FULLs) are always valid.
- [x] 4.2 Replace the `previous` selection logic in `_copy_dirty_blocks()` (`qsnap/modules/backup/bitmap.py:1147-1173`): walk backwards through `backups` (sorted ascending), check `test -f` for file existence, then for non-FULL files call `_validate_backing_chain()`; skip broken-chain files with a WARNING log; select the first valid file as `previous`; if no valid non-FULL found, fall back to FULL; if no valid backup at all, return `_CopyResult` with error directing user to `qsnap check --deep` and `qsnap reconcile`

## 5. Phase 4 — Fix State Cleanup in Reconcile (B4, MEDIUM)

**Spec:** `specs/state-reconciliation/spec.md` — MODIFIED: "Reconcile removes orphan files on target"

- [x] 5.1 In `Core.reconcile()` step 6 (orphan file cleanup, `qsnap/core/__init__.py:1594-1603`): after `provider.delete(backup)`, call `_resolve_chain_full_anchor(backup.path)` and if an anchor is found, call `self._state.remove_incremental_dependency(target_path, backup.name, anchor)` to clean the stale dependency record

## 6. Phase 5 — Enhance `check --state` with Backing Chain Validation (B5, MEDIUM)

**Spec:** `specs/state-consistency-check/spec.md` — ADDED: "Broken backing chain detection in check --state"

- [x] 6.1 Add `broken_chains: list[str]` field to `StateCheckResult` dataclass in `qsnap/models/results.py` (default: empty list via `field(default_factory=list)`)
- [x] 6.2 Add broken-chain detection in `Core.check_state()` (`qsnap/core/__init__.py`, after stale_deps check at line 1249): for each target, list backups via `provider.list(target)`, for each non-FULL backup run `qemu-img info --force-share --backing-chain --output=json` via `IShell.run()`, if command fails add to `broken_chains` list; if `broken_chains` is non-empty, append `"broken_chains"` to `status_parts`
- [x] 6.3 Update CLI handler `_state_check_to_rows()` in `qsnap/cli/commands.py` to display `broken_chains` in the output table

## 7. Phase 6 — Enhance `reconcile` with Broken Chain Detection (B6, MEDIUM)

**Spec:** `specs/state-reconciliation/spec.md` — ADDED: "Broken backing chain detection in reconcile"

- [x] 7.1 Add `broken_chains: list[str]` field to `ReconcileResult` dataclass in `qsnap/models/results.py` (default: empty list via `field(default_factory=list)`)
- [x] 7.2 In `Core.reconcile()` step 6 (`qsnap/core/__init__.py:1555-1612`), before the orphan classification loop: for each non-FULL backup in `backups_on_disk`, run `qemu-img info --force-share --backing-chain --output=json` via `IShell.run()`; if command fails, add backup name to `broken_chains` list and log a WARNING; the file then proceeds through normal orphan classification (if not tracked in state, it is deleted)
- [x] 7.3 Update CLI handler `_reconcile_to_rows()` in `qsnap/cli/commands.py` to display `broken_chains` in the output table

## 8. Lint & Type Check

- [x] 8.1 Run `ruff check qsnap/` — fix any lint errors
- [x] 8.2 Run `ruff format qsnap/` — ensure formatting is consistent
- [x] 8.3 Run `pyright qsnap/` — fix any type errors (strict mode)

## 9. Testing

**CRITICAL INSTRUCTION FOR THE PROGRAMMER AGENT:** When delegating ANY test group to a @Mr.Tester subagent, you MUST pass the following document to each tester along with their task:

> **`/home/openuser/vm/qsnap/TESTING.md`** — This document describes the complete test architecture, categories, and rules for qsnap. The tester MUST read and follow its conventions exactly: test directory structure mirrors production, mock strategy (custom mock classes implementing ABCs, NO pytest-mock), test markers (unit, mock, contract, integration, stress, e2e), fixtures (mock_shell, mock_state, mock_config, mock_factory, make_vm_config, make_target, frozen_clock, cli_app), and running commands.

> **`/home/openuser/vm/qsnap/AGENTS.md`** — This document describes the project architecture, patterns, anti-patterns, and naming conventions. The tester MUST follow these when writing test code.

> **Integration tests:** The environment has FULL access to a real libvirt daemon and QEMU. Disposable test VMs can be created via the existing `tests/integration/conftest.py` fixture pattern. Integration tests MUST be marked with `@pytest.mark.integration` and placed in `tests/integration/`. The tester should carefully study the integration test task and use real `virsh`/`qemu-img` calls where appropriate.

Read `test-plan.md` Delegation Groups section. Launch @Mr.Tester subagents IN PARALLEL (all in one message).

- [x] 9.1 Read `test-plan.md` Delegation Groups section
- [x] 9.2 Delegate group `state-unit` to @Mr.Tester (scope: `tests/state/test_manager.py` — 7 scenarios, MODIFY: add 4 new tests for key normalization and legacy migration). Pass TESTING.md and AGENTS.md to the tester.
- [x] 9.3 Delegate group `cascade-unit` to @Mr.Tester (scope: `tests/core/test_pipeline.py` — 10 scenarios, MODIFY: add 6 new tests for incremental ghost retention, reverse dep map, state cleanup). Pass TESTING.md and AGENTS.md to the tester.
- [x] 9.4 Delegate group `bitmap-unit` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap.py` — 9 scenarios, MODIFY: add 9 new tests for backing-chain validation, broken-chain walk, retryable errors). Pass TESTING.md and AGENTS.md to the tester.
- [x] 9.5 Delegate group `state-check-unit` to @Mr.Tester (scope: `tests/core/test_state_check.py` — 3 scenarios, MODIFY: add 3 new tests for broken chain detection in `check --state`). Pass TESTING.md and AGENTS.md to the tester.
- [x] 9.6 Delegate group `reconcile-unit` to @Mr.Tester (scope: `tests/core/test_reconcile.py` — 7 scenarios, NEW: create file with 7 tests for orphan detection, broken chains in reconcile, dry-run). Pass TESTING.md and AGENTS.md to the tester.
- [x] 9.7 Delegate group `integration` to @Mr.Tester (scope: `tests/integration/test_broken_chain.py` — 4 scenarios, NEW: create file with 4 integration tests using real libvirt/qemu for broken-chain recovery, ghost retention, check --state, and reconcile). Pass TESTING.md and AGENTS.md to the tester. Emphasize: full libvirt and qemu access is available; the tester should carefully study the integration test requirements and use real VMs.
- [x] 9.8 Review @Mr.Tester reports and fix any source-level bugs discovered
- [x] 9.9 Re-delegate any groups affected by source fixes
- [x] 9.10 Verify all groups pass and coverage matches `test-plan.md`

<!--
  TEST ORCHESTRATION PROTOCOL (followed by the apply phase agent):

  1. Read test-plan.md → Delegation Groups section
  2. For EACH group listed, launch one @Mr.Tester subagent with:
     - The group's scope (file paths)
     - The group's scenario list from Coverage Map
     - The document `/home/openuser/vm/qsnap/TESTING.md` — the tester MUST read this and follow its conventions
     - The document `/home/openuser/vm/qsnap/AGENTS.md` — the tester MUST follow project architecture and naming conventions
     - Instruction: "Write or fix ONLY these specific tests. Report source bugs, don't fix them."
     - For integration groups: "Full libvirt and qemu access is available. Use real virsh/qemu-img calls. Mark tests with @pytest.mark.integration."
  3. Launch ALL groups IN PARALLEL (single message)
  4. After all testers return: fix any reported source bugs, re-delegate affected groups
  5. Repeat until all groups pass
-->

## 10. Final Verification

- [x] 10.1 Run full unit test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 10.2 Run integration tests: `poetry run pytest tests/integration/ -m integration`
- [x] 10.3 Run lint: `ruff check qsnap/ tests/`
- [x] 10.4 Run type check: `pyright qsnap/`
- [x] 10.5 Verify no regressions: compare test coverage before and after
