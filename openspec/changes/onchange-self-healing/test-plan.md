# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `state-management` | Clear last backup allocation | Clear existing baseline | `tests/state/test_manager.py` | `test_clear_backup_allocation_existing` | `state-methods` |
| `state-management` | Clear last backup allocation | Clear non-existent baseline | `tests/state/test_manager.py` | `test_clear_backup_allocation_nonexistent` | `state-methods` |
| `state-management` | Remove all incremental dependencies | Remove all dependencies for existing FULL | `tests/state/test_manager.py` | `test_remove_all_incremental_deps_existing` | `state-methods` |
| `state-management` | Remove all incremental dependencies | Remove dependencies for non-existent FULL | `tests/state/test_manager.py` | `test_remove_all_incremental_deps_nonexistent` | `state-methods` |
| `state-management` | IStateManager implementations must implement new methods | JsonStateManager implements clear_last_backup_allocation | `tests/state/test_manager.py` | `test_json_clear_last_backup_allocation_atomic` | `state-methods` |
| `state-management` | IStateManager implementations must implement new methods | InMemoryStateManager implements clear_last_backup_allocation | `tests/state/test_manager.py` | `test_inmemory_clear_last_backup_allocation` | `state-methods` |
| `change-detection` | Backup-side onchange gate | Gate passes when new snapshots exist on target | `tests/core/test_pipeline.py` | `test_onchange_approach_b_new_snapshot_on_target` | `core-gate` |
| `change-detection` | Backup-side onchange gate | Gate skips when all snapshots already backed up | `tests/core/test_pipeline.py` | `test_onchange_approach_b_all_backed_up` | `core-gate` |
| `change-detection` | Backup-side onchange gate | Gate passes on first backup to target | `tests/core/test_pipeline.py` | `test_onchange_approach_b_first_backup` | `core-gate` |
| `change-detection` | Backup-side onchange gate | Gate works independently of snapshot_create mode | `tests/core/test_pipeline.py` | `test_onchange_approach_b_always_snapshot_mode` | `core-gate` |
| `change-detection` | Backup-side onchange gate | Gate works for standalone qsnap backup | `tests/core/test_pipeline.py` | `test_onchange_approach_b_standalone_backup` | `core-gate` |
| `change-detection` | Backup-side onchange gate | Gate does not use last_backup_allocation | `tests/core/test_pipeline.py` | `test_onchange_approach_b_no_allocation_access` | `core-gate` |
| `change-detection` | Onchange gate and retention separation | Retention runs when gate skips transfer | `tests/core/test_pipeline.py` | `test_onchange_skip_runs_retention` | `core-gate` |
| `change-detection` | Onchange gate and retention separation | Transfer skipped but retention cleans expired backups | `tests/core/test_pipeline.py` | `test_onchange_skip_cleans_expired_backups` | `core-gate` |
| `startup-state-validation` | Startup state validation before onchange gate | Startup validation cleans phantom FULLs | `tests/core/test_pipeline.py` | `test_startup_validation_cleans_phantom_fulls` | `core-runtime` |
| `startup-state-validation` | Startup state validation before onchange gate | Startup validation clears stale baseline when no FULLs remain | `tests/core/test_pipeline.py` | `test_startup_validation_clears_baseline_after_phantom` | `core-runtime` |
| `startup-state-validation` | Startup state validation before onchange gate | Startup validation clears stale baseline when no FULLs in state | `tests/core/test_pipeline.py` | `test_startup_validation_clears_baseline_no_fulls` | `core-runtime` |
| `startup-state-validation` | Startup state validation before onchange gate | Startup validation is non-fatal | `tests/core/test_pipeline.py` | `test_startup_validation_non_fatal_on_corrupt_state` | `core-runtime` |
| `startup-state-validation` | Startup state validation before onchange gate | Startup validation runs for standalone backup | `tests/core/test_pipeline.py` | `test_startup_validation_runs_for_standalone_backup` | `core-runtime` |
| `startup-state-validation` | Startup state validation before onchange gate | Startup validation does not delete checkpoints | `tests/core/test_pipeline.py` | `test_startup_validation_no_checkpoint_deletion` | `core-runtime` |
| `core-orchestrator` | Phantom FULL detection with cascade cleanup | Phantom FULL triggers cascade dependency cleanup | `tests/core/test_pipeline.py` | `test_phantom_full_cascade_dep_cleanup` | `core-runtime` |
| `core-orchestrator` | Phantom FULL detection with cascade cleanup | Last phantom FULL clears baseline | `tests/core/test_pipeline.py` | `test_phantom_last_full_clears_baseline` | `core-runtime` |
| `core-orchestrator` | Phantom FULL detection with cascade cleanup | Phantom FULL with remaining valid FULLs does not clear baseline | `tests/core/test_pipeline.py` | `test_phantom_full_keeps_baseline_with_remaining` | `core-runtime` |
| `core-orchestrator` | Backup target pipeline with gate/retention separation | Gate skip does not block retention | `tests/core/test_pipeline.py` | `test_gate_skip_retention_still_runs` | `core-gate` |
| `core-orchestrator` | Startup state validation in pipeline | Pipeline calls startup validation | `tests/core/test_pipeline.py` | `test_pipeline_calls_startup_validation_before_steps` | `core-runtime` |
| `core-orchestrator` | Startup state validation in pipeline | Standalone backup calls startup validation | `tests/core/test_pipeline.py` | `test_standalone_backup_calls_startup_validation` | `core-runtime` |
| `state-reconciliation` | Reconcile command actively repairs state | Reconcile removes phantom FULLs with cascade cleanup | `tests/core/test_pipeline.py` | `test_reconcile_removes_phantom_fulls` | `core-reconcile` |
| `state-reconciliation` | Reconcile command actively repairs state | Reconcile clears stale last_backup_allocation | `tests/core/test_pipeline.py` | `test_reconcile_clears_stale_baseline` | `core-reconcile` |
| `state-reconciliation` | Reconcile command actively repairs state | Reconcile removes phantom snapshots | `tests/core/test_pipeline.py` | `test_reconcile_removes_phantom_snapshots` | `core-reconcile` |
| `state-reconciliation` | Reconcile command actively repairs state | Reconcile removes stale incremental dependencies | `tests/core/test_pipeline.py` | `test_reconcile_removes_stale_deps` | `core-reconcile` |
| `state-reconciliation` | Reconcile command actively repairs state | Reconcile deletes orphaned checkpoints | `tests/core/test_pipeline.py` | `test_reconcile_deletes_orphan_checkpoints` | `core-reconcile` |
| `state-reconciliation` | Reconcile command actively repairs state | Reconcile dry-run mode | `tests/core/test_pipeline.py` | `test_reconcile_dry_run_no_mutations` | `core-reconcile` |
| `state-reconciliation` | Reconcile command actively repairs state | Reconcile returns structured result | `tests/core/test_pipeline.py` | `test_reconcile_returns_structured_result` | `core-reconcile` |
| `state-reconciliation` | Reconcile command actively repairs state | Reconcile with VM filter | `tests/core/test_pipeline.py` | `test_reconcile_vm_filter` | `core-reconcile` |
| `state-reconciliation` | ReconcileResult dataclass | ReconcileResult is frozen | `tests/models/test_results.py` | `test_reconcile_result_is_frozen` | `models` |
| `cli-interface` | Reconcile CLI subcommand | Reconcile command dispatches to Core | `tests/cli/test_commands.py` | `test_handle_reconcile_dispatches_to_core` | `cli-reconcile` |
| `cli-interface` | Reconcile CLI subcommand | Reconcile with dry-run | `tests/cli/test_commands.py` | `test_handle_reconcile_dry_run` | `cli-reconcile` |
| `cli-interface` | Reconcile CLI subcommand | Reconcile with VM filter | `tests/cli/test_commands.py` | `test_handle_reconcile_vm_filter` | `cli-reconcile` |
| `cli-interface` | Reconcile CLI subcommand | Reconcile exit code | `tests/cli/test_commands.py` | `test_handle_reconcile_exit_code` | `cli-reconcile` |
| `cli-interface` | Reconcile CLI subcommand | Reconcile in dispatch map | `tests/cli/test_app.py` | `test_reconcile_dispatch_map_entry` | `cli-reconcile` |
| `state-consistency-check` | Orphan checkpoint auto-cleanup parameter | Auto-cleanup disabled by default | `tests/core/test_state_check.py` | `test_detect_orphan_checkpoints_no_auto_cleanup_by_default` | `core-runtime` |
| `state-consistency-check` | Orphan checkpoint auto-cleanup parameter | Auto-cleanup enabled deletes orphans | `tests/core/test_state_check.py` | `test_detect_orphan_checkpoints_auto_cleanup_deletes` | `core-runtime` |
| `state-consistency-check` | Orphan checkpoint auto-cleanup parameter | Auto-cleanup failure is non-fatal | `tests/core/test_state_check.py` | `test_detect_orphan_checkpoints_auto_cleanup_non_fatal` | `core-runtime` |
| `state-consistency-check` | Reconcile uses auto-cleanup for orphan checkpoints | Reconcile auto-deletes orphan checkpoints | `tests/core/test_pipeline.py` | `test_reconcile_auto_deletes_orphan_checkpoints` | `core-reconcile` |
| `state-consistency-check` | Startup validation does NOT auto-delete checkpoints | Startup validation leaves orphan checkpoints | `tests/core/test_pipeline.py` | `test_startup_validation_no_checkpoint_deletion` | `core-runtime` |
| `state-management` | ReconcileResult dataclass fields | ReconcileResult is frozen | `tests/models/test_results.py` | `test_reconcile_result_is_frozen` | `models` |
| — | Integration (plan.md Phase 7) | test_onchange_approach_b_gate | `tests/integration/test_onchange.py` | `test_onchange_approach_b_gate` | `integration-onchange` |
| — | Integration (plan.md Phase 7) | test_onchange_manual_deletion_recovery | `tests/integration/test_onchange.py` | `test_onchange_manual_deletion_recovery` | `integration-onchange` |
| — | Integration (plan.md Phase 7) | test_reconcile_command | `tests/integration/test_reconcile.py` | `test_reconcile_command` | `integration-reconcile` |
| — | Integration (plan.md Phase 7) | test_startup_validation | `tests/integration/test_startup_validation.py` | `test_startup_validation` | `integration-startup` |
| — | Integration (plan.md Phase 7) | test_retention_runs_on_skip | `tests/integration/test_onchange.py` | `test_retention_runs_on_skip` | `integration-onchange` |
| — | Contract (TESTING.md) | IStateManager contract for clear_last_backup_allocation | `tests/interfaces/test_state_manager.py` | `test_istate_manager_clear_allocation_abstract` | `interfaces` |
| — | Contract (TESTING.md) | IStateManager contract for remove_all_incremental_dependencies | `tests/interfaces/test_state_manager.py` | `test_istate_manager_remove_all_deps_abstract` | `interfaces` |
| — | Contract (TESTING.md) | InMemoryStateManager implements clear_last_backup_allocation | `tests/interfaces/test_state_manager.py` | `test_inmemory_implements_clear_allocation` | `interfaces` |
| — | Contract (TESTING.md) | JsonStateManager implements clear_last_backup_allocation | `tests/interfaces/test_state_manager.py` | `test_json_implements_clear_allocation` | `interfaces` |
| — | Contract (TESTING.md) | InMemoryStateManager implements remove_all_incremental_dependencies | `tests/interfaces/test_state_manager.py` | `test_inmemory_implements_remove_all_deps` | `interfaces` |
| — | Contract (TESTING.md) | JsonStateManager implements remove_all_incremental_dependencies | `tests/interfaces/test_state_manager.py` | `test_json_implements_remove_all_deps` | `interfaces` |
| — | Mock (TESTING.md) | InMemoryStateManager has clear_last_backup_allocation | `tests/mocks/mock_state.py` | (implementation — validated by contract tests) | `interfaces` |
| — | Mock (TESTING.md) | InMemoryStateManager has remove_all_incremental_dependencies | `tests/mocks/mock_state.py` | (implementation — validated by contract tests) | `interfaces` |

## Delegation Groups

### Group: `state-methods`

**Scope:** `tests/state/test_manager.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/state/test_manager.py` | 6 | ADD (6 new tests) |

New tests: `test_clear_backup_allocation_existing`, `test_clear_backup_allocation_nonexistent`, `test_remove_all_incremental_deps_existing`, `test_remove_all_incremental_deps_nonexistent`, `test_json_clear_last_backup_allocation_atomic`, `test_inmemory_clear_last_backup_allocation`.

### Group: `core-gate`

**Scope:** `tests/core/test_pipeline.py` (onchange gate section, lines 4260–4580) + new tests at end of file

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | 10 | MODIFY (6 old tests) + ADD (4 new tests) |

Existing tests modified: `test_onchange_backup_first_run_proceeds`, `test_onchange_backup_no_change_skipped`, `test_onchange_backup_allocation_grew_proceeds`, `test_onchange_always_mode_backup_gate_bypassed`, `test_onchange_no_snapshots_skipped`, `test_onchange_baseline_updated_after_successful_transfer`, `test_onchange_baseline_not_updated_on_failure`.

New tests: `test_onchange_approach_b_new_snapshot_on_target`, `test_onchange_approach_b_all_backed_up`, `test_onchange_approach_b_first_backup`, `test_onchange_approach_b_always_snapshot_mode`, `test_onchange_approach_b_standalone_backup`, `test_onchange_approach_b_no_allocation_access`, `test_onchange_skip_runs_retention`, `test_gate_skip_retention_still_runs`, `test_onchange_skip_cleans_expired_backups`.

### Group: `core-runtime`

**Scope:** `tests/core/test_pipeline.py`, `tests/core/test_state_check.py`, `tests/core/test_full_verification_pipeline.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | 12 | ADD (12 new tests for startup validation + phantom cascade + pipeline ordering) |
| `tests/core/test_state_check.py` | 3 | ADD (3 new tests for orphan checkpoint auto-cleanup parameter) |
| `tests/core/test_full_verification_pipeline.py` | 2 | MODIFY (2 old phantom tests to verify cascade cleanup) |

New pipeline tests: `test_startup_validation_cleans_phantom_fulls`, `test_startup_validation_clears_baseline_after_phantom`, `test_startup_validation_clears_baseline_no_fulls`, `test_startup_validation_non_fatal_on_corrupt_state`, `test_startup_validation_runs_for_standalone_backup`, `test_startup_validation_no_checkpoint_deletion`, `test_phantom_full_cascade_dep_cleanup`, `test_phantom_last_full_clears_baseline`, `test_phantom_full_keeps_baseline_with_remaining`, `test_pipeline_calls_startup_validation_before_steps`, `test_standalone_backup_calls_startup_validation`, `test_gate_skip_retention_still_runs`.

New state_check tests: `test_detect_orphan_checkpoints_no_auto_cleanup_by_default`, `test_detect_orphan_checkpoints_auto_cleanup_deletes`, `test_detect_orphan_checkpoints_auto_cleanup_non_fatal`.

Modified phantom tests: `test_phantom_full_detected_removed_from_state`, `test_all_fulls_exist_no_phantom_cleanup`.

### Group: `core-reconcile`

**Scope:** `tests/core/test_pipeline.py` (new reconcile section)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | 9 | ADD (9 new tests for Core.reconcile()) |

New tests: `test_reconcile_removes_phantom_fulls`, `test_reconcile_clears_stale_baseline`, `test_reconcile_removes_phantom_snapshots`, `test_reconcile_removes_stale_deps`, `test_reconcile_deletes_orphan_checkpoints`, `test_reconcile_dry_run_no_mutations`, `test_reconcile_returns_structured_result`, `test_reconcile_vm_filter`, `test_reconcile_auto_deletes_orphan_checkpoints`.

### Group: `cli-reconcile`

**Scope:** `tests/cli/test_commands.py`, `tests/cli/test_app.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/cli/test_commands.py` | 4 | ADD (4 new tests for handle_reconcile) |
| `tests/cli/test_app.py` | 1 | ADD (1 new test for dispatch map) |

New tests: `test_handle_reconcile_dispatches_to_core`, `test_handle_reconcile_dry_run`, `test_handle_reconcile_vm_filter`, `test_handle_reconcile_exit_code`, `test_reconcile_dispatch_map_entry`.

### Group: `models`

**Scope:** `tests/models/test_results.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/models/test_results.py` | 1 | ADD (1 new test for ReconcileResult) |

New test: `test_reconcile_result_is_frozen`.

### Group: `interfaces`

**Scope:** `tests/interfaces/test_state_manager.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_state_manager.py` | 6 | ADD (6 new contract tests) + MODIFY (update _MissingBackupAlloc class) |

New tests: `test_istate_manager_clear_allocation_abstract`, `test_istate_manager_remove_all_deps_abstract`, `test_inmemory_implements_clear_allocation`, `test_json_implements_clear_allocation`, `test_inmemory_implements_remove_all_deps`, `test_json_implements_remove_all_deps`.

Modification: The `_MissingBackupAlloc` inner class used in `test_istate_manager_backup_allocation_methods_abstract` must be updated to also omit `clear_last_backup_allocation` and `remove_all_incremental_dependencies`, and the assertion must verify both new methods are abstract.

### Group: `integration-onchange`

**Scope:** `tests/integration/test_onchange.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_onchange.py` | 5 | MODIFY (2 old tests) + ADD (3 new tests) |

**CRITICAL integration tests from plan.md Phase 7:**

1. `test_onchange_approach_b_gate` (NEW) — Tests Approach B gate: first backup creates FULL, second with no new snapshots skips, third with new snapshot creates incremental. Verify caplog contains "no new snapshots — skipping" and does NOT contain old "unchanged (allocation ... == ...)" message.

2. `test_onchange_manual_deletion_recovery` (NEW) — Tests that after manually deleting all backup files and checkpoints from target, the system self-heals: startup validation detects phantom FULLs, clears stale baselines, creates new FULL. Verify caplog contains "phantom FULL" and "cleared last_backup_allocation". **This is the KEY test.**

3. `test_retention_runs_on_skip` (NEW) — Tests that when the onchange gate skips transfer, retention + cleanup still run and delete expired backups.

Modified integration tests:

4. `test_onchange_skips_when_unchanged` (MODIFY) — Remove the old allocation-comparison assertion. Replace with Approach B assertions: verify gate skips when all snapshots already on target. Verify log message is "no new snapshots — skipping" not "unchanged (allocation ... == ...)".

5. `test_onchange_proceeds_when_changed` (MODIFY) — Remove the old allocation-comparison logic. Replace with Approach B assertion: create a second snapshot (new name not on target) → gate proceeds → incremental backup is created.

### Group: `integration-reconcile`

**Scope:** `tests/integration/test_reconcile.py` (new file)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_reconcile.py` | 1 | ADD (new file, 1 test) |

**CRITICAL integration test from plan.md Phase 7:**

4. `test_reconcile_command` (NEW) — Tests that `core.reconcile()` removes phantom FULLs from state, cleans dependencies, clears baselines, and deletes orphan checkpoints after manual file deletion. Steps: create VM, make FULL + incremental, delete FULL file from target, run `core.reconcile()`, verify phantom FULL removed from state, dependencies cleaned, `last_backup_allocation` cleared, orphan checkpoint deleted.

### Group: `integration-startup`

**Scope:** `tests/integration/test_startup_validation.py` (new file)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_startup_validation.py` | 1 | ADD (new file, 1 test) |

**CRITICAL integration test from plan.md Phase 7:**

5. `test_startup_validation` (NEW) — Tests that startup validation detects phantom FULLs before the onchange gate runs, ensuring the gate sees correct state and a new FULL is created. Steps: create VM, make FULL backup, delete FULL file from target, run `core.run()` (full pipeline), verify startup validation detects phantom FULL, verify `_should_backup_onchange` sees correct state (no FULL → gate passes), verify new FULL is created.

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/core/test_pipeline.py:4263—4295` (`test_onchange_backup_first_run_proceeds`) | Replace allocation-comparison assertion with Approach B assertion: mock `provider.list()` to return empty list, assert gate returns True (first backup). Remove `get_last_backup_allocation` mock usage. | Old test validates broken allocation comparison logic. |
| `tests/core/test_pipeline.py:4298—4344` (`test_onchange_backup_no_change_skipped`) | Replace allocation-comparison assertion with Approach B: mock `provider.list()` to return snapshot names matching state, assert gate returns False (all backed up). Remove `set_last_backup_allocation` call in test setup. | Old test validates broken allocation comparison logic. |
| `tests/core/test_pipeline.py:4347—4393` (`test_onchange_backup_allocation_grew_proceeds`) | Replace allocation-grew assertion with Approach B: mock `provider.list()` to miss one snapshot, assert gate returns True. | Old test validates allocation growth, new test validates snapshot presence check. |
| `tests/core/test_pipeline.py:4396—4436` (`test_always_mode_backup_gate_bypassed`) | Remove `set_last_backup_allocation` call; keep `_should_backup_onchange` spy but update expected behavior — it may or may not be called depending on new gate location. | The gate behavior changes, but always mode should still bypass gate-related checks. |
| `tests/core/test_pipeline.py:4439—4476` (`test_onchange_no_snapshots_skipped`) | Keep test but add verification that empty snapshots → gate returns False → no `provider.list()` call made. | Core logic preserved: empty snapshots = skip, but note that gate+retention separation means `_backup_target` may still run retention. |
| `tests/core/test_pipeline.py:4479—4519` (`test_onchange_baseline_updated_after_successful_transfer`) | **DELETE entire test** or repurpose to verify `set_last_backup_allocation` is NOT called after successful backup under Approach B (spec: "Gate does not use last_backup_allocation"). | The gate no longer sets or reads `last_backup_allocation`. This test validates the OLD behavior. |
| `tests/core/test_pipeline.py:4522—4579` (`test_onchange_baseline_not_updated_on_failure`) | **DELETE entire test** or repurpose to verify `set_last_backup_allocation` is NOT called on failure (redundant: baseline not used, but verify non-regression). | Same reason — `set_last_backup_allocation` is no longer part of the onchange flow. |
| `tests/core/test_full_verification_pipeline.py:1113—1159` (`test_phantom_full_detected_removed_from_state`) | Add assertions that `remove_all_incremental_dependencies` and `clear_last_backup_allocation` are called when the phantom FULL is the last one. Verify cascade cleanup. | Phantom detection now includes cascade cleanup per design.md Decision 2. |
| `tests/core/test_full_verification_pipeline.py:1164—1175` (`test_all_fulls_exist_no_phantom_cleanup`) | Add assertion that `remove_all_incremental_dependencies` is NOT called when all FULLs exist on disk. | Ensure no false-positive cascade cleanup. |
| `tests/integration/test_onchange.py:91—173` (`test_onchange_skips_when_unchanged`) | Replace the test to use Approach B logic: (1) first backup proceeds because target is empty, (2) second backup skips because snapshot already on target, (3) verify caplog "no new snapshots — skipping" and absence of "unchanged (allocation...)". Replace `get_last_backup_allocation`/`set_last_backup_allocation` checks with `provider.list()`-based verification. | Integration test must validate the new Approach B gate, not the broken allocation comparison. |
| `tests/integration/test_onchange.py:176—280` (`test_onchange_proceeds_when_changed`) | Replace with Approach B: (1) first backup creates FULL, (2) create new snapshot → gate detects new snapshot not on target → backup proceeds, (3) verify incremental backup file exists on target. Remove allocation-based comparisons entirely. | Integration test must validate Approach B (snapshot presence check), not allocation comparisons. |
| `tests/interfaces/test_state_manager.py:189—211` (`_MissingBackupAlloc` and `test_istate_manager_backup_allocation_methods_abstract`) | Update `_MissingBackupAlloc` inner class to also omit `clear_last_backup_allocation` and `remove_all_incremental_dependencies`. Add assertion that both new methods are in `IStateManager.__abstractmethods__`. | Contract test must enforce that new IStateManager methods are abstract. |
| `tests/mocks/mock_state.py:170—178` | Add `clear_last_backup_allocation` and `remove_all_incremental_dependencies` method implementations to `InMemoryStateManager`. | Every IStateManager implementation must provide the two new methods per the ABC contract. |

## Risks & Edge Cases

- **Double `provider.list(target)` call:** Approach B calls `provider.list()` in `_should_backup_onchange()`, then `transfer_missing()` calls it again internally. For 10-20 backup files (< 1 second overhead), this is acceptable. → Unit test `test_onchange_approach_b_new_snapshot_on_target` verifies gate works correctly with `provider.list()`; integration test `test_onchange_approach_b_gate` verifies real-world overhead is acceptable. No separate dedup test needed at this stage.

- **`qsnap backup` standalone:** Startup validation must also run in `_execute_backup_steps` for standalone backup invocations. → Unit test `test_standalone_backup_calls_startup_validation` verifies this; integration test `test_startup_validation` verifies it with real virsh.

- **Concurrent runs:** Locking (`/run/qsnap.lock`) prevents parallel runs. Reconcile also uses locking. → Existing `test_concurrent.py` covers lockfile behavior. No new test needed, but `Core.reconcile()` must call `lock_manager.acquire()`. Add assertion in `test_reconcile_removes_phantom_fulls` that reconcile acquires the lock.

- **Dry-run mode:** `reconcile --dry-run` must not mutate state. → Unit test `test_reconcile_dry_run_no_mutations` verifies state files are unchanged; integration test for dry-run is covered by `test_reconcile_command` with dry-run assertions verified.

- **Corrupt state file during startup validation:** `_validate_state_at_startup()` must not raise exceptions on corrupt JSON. → Unit test `test_startup_validation_non_fatal_on_corrupt_state` covers this edge case.

- **`backup_create = "always"` + `snapshot_create = "always"`**: Gate not invoked for `backup_create = "always"`, behavior unchanged. → Existing test `test_always_mode_backup_gate_bypassed` ensures gate code is not reached.

- **Orphan checkpoint auto-cleanup deletes active checkpoints:** Auto-cleanup only runs in `reconcile()` (explicit user action), not at startup. → Unit test `test_startup_validation_no_checkpoint_deletion` verifies this; integration test `test_startup_validation` confirms no auto-deletion during pipeline.

- **Retention deletes backups that were previously "frozen" by gate skip:** Gate/retention separation changes behavior — retention now always runs. → Unit test `test_onchange_skip_runs_retention` + integration test `test_retention_runs_on_skip`.

- **Phantom FULL with remaining valid FULLs must NOT clear baseline:** Only the last phantom FULL clears `last_backup_allocation`. → Unit test `test_phantom_full_keeps_baseline_with_remaining` covers this.

- **`remove_all_incremental_dependencies` on non-existent FULL returns 0:** No file modification, no error. → Unit test `test_remove_all_incremental_deps_nonexistent`.

- **`clear_last_backup_allocation` on non-existent target returns False:** → Unit test `test_clear_backup_allocation_nonexistent`.

- **Atomicity of `clear_last_backup_allocation` in JsonStateManager:** Must write to `.tmp`, then `os.replace`. → Unit test `test_json_clear_last_backup_allocation_atomic`.
