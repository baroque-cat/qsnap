# QA Strategy & Test Plan

## Coverage Map

### Modified Requirement: Post-commit chain length verification

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| chain-integrity-verification | Post-commit chain length verification | Chain shortened as expected | `tests/core/test_pipeline.py` | `test_post_commit_chain_shortened_as_expected` | `post-commit-chain-tests` |
| chain-integrity-verification | Post-commit chain length verification | Chain shortened with intermediate file removal | `tests/core/test_pipeline.py` | `test_post_commit_chain_shortened_intermediate_removal` | `post-commit-chain-tests` |
| chain-integrity-verification | Post-commit chain length verification | Chain length unchanged — CRITICAL | `tests/core/test_pipeline.py` | `test_post_commit_chain_length_unchanged_critical` | `post-commit-chain-tests` |
| chain-integrity-verification | Post-commit chain length verification | Post-commit measurement fails — snapshots preserved | `tests/core/test_pipeline.py` | `test_post_commit_measurement_fails_graceful` | `post-commit-chain-tests` |
| chain-integrity-verification | Post-commit chain length verification | Pre-commit chain length unavailable — skip post-commit | `tests/core/test_pipeline.py` | `test_post_commit_skipped_when_pre_commit_unavailable` | `post-commit-chain-tests` |

### Ancillary: API shape verification (use_base_image removal)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| chain-integrity-verification | Post-commit chain length verification | `_get_chain_length()` no longer accepts `use_base_image` | `tests/core/test_pipeline.py` | `test_get_chain_length_no_use_base_image_param` | `post-commit-chain-tests` |

### New fixture assets

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| chain-integrity-verification | Post-commit chain length verification | Fixture JSON for 7-entry pre-commit backing chain | `tests/fixtures/shell_outputs/backing_chain_7_entries.json` | (static data) | `post-commit-fixtures` |
| chain-integrity-verification | Post-commit chain length verification | Fixture JSON for 6-entry post-commit backing chain | `tests/fixtures/shell_outputs/backing_chain_6_entries.json` | (static data) | `post-commit-fixtures` |
| chain-integrity-verification | Post-commit chain length verification | Fixture JSON for 3-entry post-commit chain (intermediate removal) | `tests/fixtures/shell_outputs/backing_chain_3_entries.json` | (static data) | `post-commit-fixtures` |

## Delegation Groups

Non-overlapping groups for parallel execution. Each test file belongs to exactly one group.

### Group: `post-commit-chain-tests`

**Scope:** `tests/core/test_pipeline.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | `test_post_commit_chain_shortened_as_expected` | MODIFY (remake from scratch — old version REMOVED) |
| `tests/core/test_pipeline.py` | `test_post_commit_chain_shortened_intermediate_removal` | NEW |
| `tests/core/test_pipeline.py` | `test_post_commit_chain_length_unchanged_critical` | MODIFY (remake from scratch — old version REMOVED) |
| `tests/core/test_pipeline.py` | `test_post_commit_measurement_fails_graceful` | NEW |
| `tests/core/test_pipeline.py` | `test_post_commit_skipped_when_pre_commit_unavailable` | NEW |
| `tests/core/test_pipeline.py` | `test_get_chain_length_no_use_base_image_param` | NEW |

### Group: `post-commit-fixtures`

**Scope:** `tests/fixtures/shell_outputs/`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/fixtures/shell_outputs/backing_chain_7_entries.json` | Pre-commit chain with 7 entries (snap6..base) | NEW |
| `tests/fixtures/shell_outputs/backing_chain_6_entries.json` | Post-commit chain with 6 entries (snap5..base) | NEW |
| `tests/fixtures/shell_outputs/backing_chain_3_entries.json` | Post-commit chain with 3 entries (intermediate removal) | NEW |

## Test Modifications

Existing tests that need updating or removal. Reference: spec scenario or design decision.

| File | Change | Reason |
|---|---|---|
| `tests/core/test_pipeline.py` | **REMOVE** `test_post_commit_chain_shortened_as_expected` (line ~1801) | Mocks `_get_chain_length` with `patch.object(core, "_get_chain_length", side_effect=[5, 4])`. OBSOLETE — the `use_base_image` parameter is removed from `_get_chain_length()` (design Decision 3). Replaced by NEW fixture-based test of same name that drives `qemu-img info --backing-chain` via `MockShell` with distinct pre/post paths. |
| `tests/core/test_pipeline.py` | **REMOVE** `test_post_commit_chain_length_unchanged_critical` (line ~1840) | Mocks `_get_chain_length` with `patch.object(core, "_get_chain_length", return_value=5)`. OBSOLETE — relies on the `use_base_image` parameter that no longer exists. Replaced by NEW fixture-based test that simulates a silent blockcommit failure where the post-commit `qemu-img info --backing-chain` returns the same chain length as before. |
| `tests/core/test_pipeline.py` | **REMOVE** `test_post_commit_verification_fails_snapshots_preserved` (line ~1882) | Mocks `_get_chain_length` with `patch.object(core, "_get_chain_length", side_effect=[3, 4])`. OBSOLETE — the "chain length increased" assertion path is superseded. In the new design (Decision 2), the comparison is `chain_length_after < chain_length_before`; an increase triggers CRITICAL via the same "unchanged" path. Replaced by `test_post_commit_chain_length_unchanged_critical` which covers this case. |
| `tests/core/test_pipeline.py` | **NEW** `test_post_commit_chain_shortened_as_expected` | Covers delta spec scenario "Chain shortened as expected". Uses two `MockShell.expect()` registrations keyed to different snapshot paths (pre-commit: snap6.qcow2 → 7-entry fixture; post-commit: snap5.qcow2 → 6-entry fixture). Retention removes snap6, state cleanup drops it, post-commit query then hits snap5. Asserts no CRITICAL log. |
| `tests/core/test_pipeline.py` | **NEW** `test_post_commit_chain_shortened_intermediate_removal` | Covers delta spec scenario "Chain shortened with intermediate file removal". Pre-commit chain: 7 entries (snap6.qcow2). Post-commit: after state cleanup removes snap6, query goes to snap2.qcow2 → 3-entry fixture (snap2, snap1, base). Asserts verification passes because `3 < 7`. |
| `tests/core/test_pipeline.py` | **NEW** `test_post_commit_chain_length_unchanged_critical` | Covers delta spec scenario "Chain length unchanged — CRITICAL". Pre-commit chain: 7 entries (snap6.qcow2). Post-commit: after state cleanup removes snap4, query still hits snap6.qcow2 → same 7-entry fixture (simulates silent blockcommit failure). Asserts CRITICAL log emitted with snapshot paths for manual recovery. |
| `tests/core/test_pipeline.py` | **NEW** `test_post_commit_measurement_fails_graceful` | Covers delta spec scenario "Post-commit measurement fails — snapshots preserved". Pre-commit: `qemu-img info` succeeds (7 entries). Post-commit: MockShell returns failure for the post-commit path. Asserts `chain_length_after` is `None`, WARNING log emitted, blockcommit result is still considered successful. |
| `tests/core/test_pipeline.py` | **NEW** `test_post_commit_skipped_when_pre_commit_unavailable` | Covers delta spec scenario "Pre-commit chain length unavailable — skip post-commit". Pre-commit: MockShell returns failure for the active layer `qemu-img info` → `chain_length_before = None`. Asserts post-commit check skipped, INFO log emitted. |
| `tests/core/test_pipeline.py` | **NEW** `test_get_chain_length_no_use_base_image_param` | Validates that `Core._get_chain_length()` no longer accepts a `use_base_image` keyword argument. Uses `make_vm_config`, `mock_state`, `mock_shell`. Calls `core._get_chain_length(vm_config)` and asserts it queries the most recent snapshot (or base image when no snapshots exist). Also asserts `TypeError` is raised if `use_base_image` is passed. |
| `tests/fixtures/shell_outputs/backing_chain_7_entries.json` | **NEW** | 7-entry backing chain fixture: snap6 → snap5 → snap4 → snap3 → snap2 → snap1 → base. Format matches existing `backing_chain_intact.json` (legacy `"image"` key). |
| `tests/fixtures/shell_outputs/backing_chain_6_entries.json` | **NEW** | 6-entry backing chain fixture: snap5 → snap4 → snap3 → snap2 → snap1 → base. Used for post-commit when snap6 was merged and removed from state. |
| `tests/fixtures/shell_outputs/backing_chain_3_entries.json` | **NEW** | 3-entry backing chain fixture: snap2 → snap1 → base. Used for the intermediate-file-removal post-commit scenario where virsh --delete removed intermediate snap3-5. |
| `tests/core/test_pipeline.py` | **NEW** helper `_add_snapshots_6_for_chain(state, vm_name)` | Adds 6 snapshots (snap1–snap6) to match the 7-entry fixture chain. Used by all new post-commit tests. Snapshot paths follow the existing convention in `_add_snapshots_for_chain`. |

## Risks & Edge Cases

Sources: design.md Risks section and additional edge cases identified during test planning.

### From design.md

| Risk | Test Coverage |
|---|---|
| Post-commit `qemu-img info` fails because the active layer is still locked by QEMU | `test_post_commit_measurement_fails_graceful` — MockShell returns failure for post-commit call; asserts WARNING log, blockcommit result still considered successful |
| Merged snapshots removed from state BEFORE post-commit measurement (if VM crashes between state removal and measurement) | Not directly testable in unit tests (requires crash simulation). Mitigation: the next pipeline run will see correct state. Documented as acceptable risk per design.md. |
| State removal before measurement makes pre/post comparison asymmetric | `test_post_commit_chain_shortened_as_expected` — validates that asymmetric comparison is correct: pre-commit queries snap6 (7 entries), post-commit queries snap5 (6 entries), and `6 < 7` passes. |

### Additional edge cases

| Edge Case | Test Coverage |
|---|---|
| No snapshots in state → `_get_chain_length` falls back to base image | `test_get_chain_length_no_use_base_image_param` — state empty, assert query hits `vm_config.base_image` |
| Active layer path no longer exists (deleted by virsh --delete) → `_get_chain_length` returns None | `test_post_commit_measurement_fails_graceful` — MockShell returns failure for the post-commit path, covers the "file gone" case |
| `chain_verify_after_commit = False` → post-commit verification skipped entirely | Covered by existing `test_chain_verify_disabled_skips_pre_commit_check` (line 1927) — add assertion that post-commit check is also skipped when `chain_verify_after_commit=False` |
| `chain_verify_before_commit = True, chain_verify_after_commit = True` → both checks run | `test_post_commit_chain_shortened_as_expected` (with `chain_verify_before_commit=True`) — new test variant that verifies pre + post checks run in sequence |
| Backing chain contains different-formatted entries (QEMU 11.0+ `"filename"` keys) → post-commit measurement still correct | `test_post_commit_chain_shortened_as_expected` + new-format fixture (`"filename"` keys with `"children"` arrays) — `_get_chain_length` parses both legacy and new formats via the same JSON path resolution |
| State has snapshots but the most recent one was removed by retention → active layer changes | All new tests use this pattern — retention removes a snapshot, state cleanup changes the most-recent snapshot |

## Test Implementation Notes

### MockShell pattern for post-commit tests

All new post-commit tests use the same pattern established by existing pre-commit chain tests (e.g., `test_chain_verify_intact_chain_blockcommit_proceeds`):

1. Seed state with 6 snapshots via `_add_snapshots_6_for_chain(mock_state, "testvm")`
2. Configure MockShell with `expect("qemu-img info.*--backing-chain")` for each distinct path:
   - Pre-commit path (most recent snapshot before state cleanup) → 7-entry fixture
   - Post-commit path (most recent surviving snapshot after state cleanup) → 6/3-entry fixture
3. Call `core._blockcommit_snapshots(vm, retention)`
4. Assert on logs and state

The key design decision: retention removes the **most recent** snapshot (snap6) so that the pre-commit and post-commit `qemu-img info` calls hit **different file paths** (snap6.qcow2 vs snap5.qcow2). This lets MockShell distinguish the two calls via path-specific expectation matching.

### New fixture files

```
tests/fixtures/shell_outputs/backing_chain_7_entries.json  — snap6, snap5, snap4, snap3, snap2, snap1, base
tests/fixtures/shell_outputs/backing_chain_6_entries.json  — snap5, snap4, snap3, snap2, snap1, base
tests/fixtures/shell_outputs/backing_chain_3_entries.json  — snap2, snap1, base
```

Format matches existing `backing_chain_intact.json`: use `"image"` key (legacy QEMU), all `"format": "qcow2"`, consistent `"backing-filename"` references. Second set of fixtures using `"filename"` keys (QEMU 11.0+) can be added later for new-format parsing coverage — not strictly needed for post-commit verification since `_get_chain_length` only counts entries and does not validate backing-filename consistency.

### No `remove_snapshot` needed on IStateManager

The new tests manipulate `InMemoryStateManager` directly to simulate post-blockcommit state. The production implementation will clear and re-record snapshots or use an internal helper — the test plan does not prescribe the mechanism, only the observable behavior: after a successful blockcommit, `_get_chain_length(vm_config)` must return the chain length from the most recent surviving snapshot.
