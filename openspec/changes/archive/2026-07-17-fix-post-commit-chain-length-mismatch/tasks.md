## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fix/post-commit-chain-length-mismatch`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Remove `use_base_image` parameter from `_get_chain_length()`

Ref: design Decision 3, delta spec REMOVED Requirement "Post-commit verification queries base image".

- [x] 2.1 In `qsnap/core/__init__.py`, remove the `use_base_image` parameter from `_get_chain_length()` method signature (line 1988). Remove the conditional branch that sets `active_path = vm_config.base_image` when `use_base_image=True` (lines 2001-2002). The method now ALWAYS queries the most recent snapshot from state, falling back to `vm_config.base_image` if no snapshots exist.
- [x] 2.2 Remove the `/usr/bin/bash` docstring paragraph about `use_base_image` (lines 1992-1996) and replace with a simpler docstring describing the single-path behavior.

## 3. Fix post-commit chain measurement in `_blockcommit_snapshots()`

Ref: design Decision 1 (use snapshots from updated IStateManager), design Decision 2 (accept actual post-commit chain length).

- [x] 3.1 After a successful blockcommit (after `result.success` check at line 2118), move the `chain_length_after` measurement to occur AFTER the merged snapshots are removed from `IStateManager`. The post-commit call becomes `_get_chain_length(vm_config)` (no `use_base_image`).
- [x] 3.2 Replace the exact-match comparison `chain_length_after != expected_length` (line 2129) with a directional check: `chain_length_after >= chain_length_before`. If `chain_length_after` is not `None` and `chain_length_before` is not `None` and `chain_length_after >= chain_length_before`, log CRITICAL. This correctly flags silent blockcommit failure while accepting any actual reduction (including intermediate file removal by `virsh --delete`).
- [x] 3.3 When `chain_length_after` is `None` (measurement failed), log a WARNING instead of CRITICAL: "Post-commit chain measurement failed for VM %s (blockcommit itself succeeded)". Do NOT block state cleanup — the blockcommit succeeded.
- [x] 3.4 When `chain_length_before` is `None` (pre-commit measurement failed), log INFO: "Pre-commit chain length unavailable — skipping post-commit verification for VM %s". Do NOT measure `chain_length_after`.
- [x] 3.5 Verify that the CRITICAL log message still includes snapshot paths for manual recovery (line 2137 format preserved).
- [x] 3.6 Verify that when `chain_length_after >= chain_length_before`, snapshots are NOT removed from state (the `return` at line 2140 is preserved).

## 4. Update spec delta sync

- [x] 4.1 Run `openspec sync specs --change "fix-post-commit-chain-length-mismatch"` to apply the delta spec to `openspec/specs/chain-integrity-verification/spec.md`. Verify the base spec reflects: (a) MODIFIED Requirement "Post-commit chain length verification" now references "current active layer" instead of "base image", (b) REMOVED Requirement "Post-commit verification queries base image" is gone, (c) new scenarios are present.

## 5. Create fixture files

Ref: test-plan.md Delegation Group `post-commit-fixtures`.

- [x] 5.1 Create `tests/fixtures/shell_outputs/backing_chain_7_entries.json` — 7-entry backing chain fixture (snap6 → snap5 → snap4 → snap3 → snap2 → snap1 → base). Follow format of existing `backing_chain_intact.json` (use `"image"` key for legacy compatibility). All entries have `"format": "qcow2"`, consistent `"backing-filename"` references.
- [x] 5.2 Create `tests/fixtures/shell_outputs/backing_chain_6_entries.json` — 6-entry backing chain fixture (snap5 → snap4 → snap3 → snap2 → snap1 → base). Same format.
- [x] 5.3 Create `tests/fixtures/shell_outputs/backing_chain_3_entries.json` — 3-entry backing chain fixture (snap2 → snap1 → base). Same format.

## 6. Remove stale tests

Ref: test-plan.md Test Modifications section (3 stale tests to REMOVE).

- [x] 6.1 Remove `test_post_commit_chain_shortened_as_expected` from `tests/core/test_pipeline.py` (line ~1801). This test mocked `_get_chain_length` with `patch.object(core, "_get_chain_length", side_effect=[5, 4])`.
- [x] 6.2 Remove `test_post_commit_chain_length_unchanged_critical` from `tests/core/test_pipeline.py` (line ~1840). This test mocked `_get_chain_length` with `patch.object(core, "_get_chain_length", return_value=5)`.
- [x] 6.3 Remove `test_post_commit_verification_fails_snapshots_preserved` from `tests/core/test_pipeline.py` (line ~1882). This test mocked `_get_chain_length` with `patch.object(core, "_get_chain_length", side_effect=[3, 4])`.

## 7. Testing — Delegate to @Mr.Tester

**CRITICAL — TEST DELEGATION PROTOCOL:**
When delegating test tasks to @Mr.Tester sub-agents, the programmer agent MUST pass the following documents to EACH tester:

1. **`/home/openuser/vm/qsnap/TESTING.md`** — the full testing philosophy, directory structure conventions, mock strategy rules (custom mock classes, NO pytest-mock, contract test parametrization), fixture patterns, and test categories. Every tester must understand and follow these conventions.
2. The specific test requirements from `test-plan.md` for their assigned group (scenario list, expected test names, file paths, approach notes).

Failure to provide TESTING.md will result in tests that violate project conventions (e.g., using pytest-mock mocks instead of MockShell fixtures, or writing tests in the wrong directory).

**Delegation groups (from test-plan.md):**

- [x] 7.1 Read `test-plan.md` Delegation Groups section thoroughly before delegating.

- [x] 7.2 Delegate group `post-commit-fixtures` to @Mr.Tester (scope: `tests/fixtures/shell_outputs/`).
  **Task for @Mr.Tester:** Create 3 JSON fixture files (`backing_chain_7_entries.json`, `backing_chain_6_entries.json`, `backing_chain_3_entries.json`) following the format of existing `backing_chain_intact.json`. Each must be a valid JSON array of qcow2 image entries with `"image"`, `"format": "qcow2"`, and correct `"backing-filename"` references. Must pass `poetry run ruff check tests/fixtures/`.
  **MUST pass:** `TESTING.md` and fixture path conventions from the test-plan.

- [x] 7.3 Delegate group `post-commit-chain-tests` to @Mr.Tester (scope: `tests/core/test_pipeline.py`).
  **Task for @Mr.Tester:** Write 6 tests for `tests/core/test_pipeline.py` according to the Coverage Map in test-plan.md:
  - `test_post_commit_chain_shortened_as_expected` (MODIFY — rebuild from scratch)
  - `test_post_commit_chain_shortened_intermediate_removal` (NEW)
  - `test_post_commit_chain_length_unchanged_critical` (MODIFY — rebuild from scratch)
  - `test_post_commit_measurement_fails_graceful` (NEW)
  - `test_post_commit_skipped_when_pre_commit_unavailable` (NEW)
  - `test_get_chain_length_no_use_base_image_param` (NEW)

  Also write the helper function `_add_snapshots_6_for_chain(state, vm_name)` that seeds `InMemoryStateManager` with 6 snapshots.

  All tests MUST use MockShell with `.expect().returns()` pattern (NOT `patch.object` or `pytest-mock`). Refer to existing `test_chain_verify_intact_chain_blockcommit_proceeds` for the MockShell pattern. Each test must follow the Approach described in test-plan.md §Test Implementation Notes.
  **MUST pass:** `TESTING.md`, the full test-plan.md Coverage Map, and Test Implementation Notes.

- [x] 7.4 Launch both @Mr.Tester sub-agents IN PARALLEL (single message with two `task` tool calls).

- [x] 7.5 Review @Mr.Tester reports. For any reported source-level bugs, fix them in the source code.

- [x] 7.6 Re-delegate any groups affected by source fixes.

- [x] 7.7 Run all tests: `poetry run pytest tests/core/test_pipeline.py tests/fixtures/ -v`. Verify all new/modified tests pass.

- [x] 7.8 Run the full test suite to verify no regressions: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 8. Verification & Cleanup

- [x] 8.1 Run linter: `poetry run ruff check qsnap/ tests/`
- [x] 8.2 Run type checker: `poetry run pyright qsnap/`
- [x] 8.3 Run formatter: `poetry run ruff format qsnap/ tests/`
- [x] 8.4 Verify that `_get_chain_length()` no longer accepts `use_base_image` — grep for all call sites
- [x] 8.5 Run `openspec status --change "fix-post-commit-chain-length-mismatch"` and confirm all artifacts are done
