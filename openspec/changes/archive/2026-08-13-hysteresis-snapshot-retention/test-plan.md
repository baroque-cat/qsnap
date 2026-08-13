# QA Strategy & Test Plan

Change: `hysteresis-snapshot-retention` — hysteresis snapshot retention (threshold H / floor L, persisted `collapse_in_progress` phase, `max_commits_per_run` cap), shared pure block-job classifier with target-name probing (fail-open backup gate), and partial-prefix commit reconciliation.

Conventions honored (TESTING.md): tests mirror the production hierarchy; unit tests are zero-I/O with mocked `IShell`; Core tests use `MockVMModuleFactory` + `InMemoryStateManager`; every ABC addition gets mock + contract coverage; integration is `@pytest.mark.integration`, stress `@pytest.mark.stress`, e2e `@pytest.mark.e2e`; all new files registered under their owning module mirror path. No source or test code is modified by this plan — this is planning only.

Scenario inventory: **70 spec scenarios** traced (16 hysteresis-retention, 6 count-based-retention, 10 snapshot-preserve-min, 6 config-model, 5 core-orchestrator, 4 state-management, 7 blockjob-protocol, 3 backup-provider, 3 dry-run-prediction, 10 commit-reconciliation). Rows marked *(existing)* trace already-passing tests that must remain green; rows marked *(modify)* are existing tests whose assertions change; everything else is NEW.

---

## Coverage Map

### Spec: hysteresis-retention (16 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| hysteresis-retention | Hysteresis retention mode selection | Default mode is hysteresis | `tests/core/test_hysteresis_retention.py` | `test_default_hysteresis_mode_no_phase_state_written` | retention-unit |
| hysteresis-retention | Hysteresis retention mode selection | Hysteresis mode reinterprets the knobs | `tests/core/test_hysteresis_retention.py` | `test_hysteresis_mode_interprets_chain_length_as_threshold_floor` | retention-unit |
| hysteresis-retention | Hysteresis validation | Floor above threshold is rejected | `tests/config/test_facade.py` | `test_hysteresis_floor_above_threshold_rejected_names_both_values` | state-config-unit |
| hysteresis-retention | Hysteresis validation | Zero floor is rejected | `tests/config/test_facade.py` | `test_hysteresis_zero_floor_rejected` | state-config-unit |
| hysteresis-retention | Grow phase below the trigger threshold | Chain at threshold does not commit | `tests/core/test_hysteresis_retention.py` | `test_chain_at_threshold_commits_nothing` | retention-unit |
| hysteresis-retention | Grow phase below the trigger threshold | Growth accumulates without commits | `tests/core/test_hysteresis_retention.py` | `test_growth_phase_accumulates_without_commits` | retention-unit |
| hysteresis-retention | Collapse trigger and floor | Trigger fires above threshold | `tests/core/test_hysteresis_retention.py` | `test_trigger_marks_oldest_n_minus_l_before_cap` | retention-unit |
| hysteresis-retention | Collapse trigger and floor | Floor snapshots are never committed | `tests/core/test_hysteresis_retention.py` | `test_floor_snapshots_never_in_remove_set` | retention-unit |
| hysteresis-retention | Persisted collapse phase | Phase survives a capped run | `tests/core/test_hysteresis_retention.py` | `test_phase_persists_after_capped_run_continues_next_run` | retention-unit |
| hysteresis-retention | Persisted collapse phase | Phase cleared at the floor | `tests/core/test_hysteresis_retention.py` | `test_phase_cleared_when_floor_reached` | retention-unit |
| hysteresis-retention | Persisted collapse phase | Phase set before irreversible work | `tests/core/test_hysteresis_retention.py` | `test_phase_persisted_before_first_blockcommit` | retention-unit |
| hysteresis-retention | Persisted collapse phase | Defensive clear after external shrink | `tests/core/test_hysteresis_retention.py` | `test_defensive_phase_clear_on_external_shrink` | retention-unit |
| hysteresis-retention | Per-run commit cap | Cap truncates a large collapse | `tests/core/test_hysteresis_retention.py` | `test_cap_truncates_collapse_keeps_oldest` | retention-unit |
| hysteresis-retention | Per-run commit cap | Cap zero means unlimited | `tests/core/test_hysteresis_retention.py` | `test_cap_zero_unlimited` | retention-unit |
| hysteresis-retention | Per-run commit cap | Cap never breaks the floor | `tests/core/test_hysteresis_retention.py` | `test_cap_never_breaks_floor` | retention-unit |
| hysteresis-retention | Hysteresis observability | Trigger logs the collapse start | `tests/core/test_hysteresis_retention.py` | `test_trigger_logs_collapse_start_info` | retention-unit |

### Spec: count-based-retention (6 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| count-based-retention | Count-based retention policy | Snapshot chain length triggers blockcommit (steady mode) | `tests/core/test_preserve.py` | `test_steady_mode_cap_truncates_remove_list` *(new)* | retention-unit |
| count-based-retention | Count-based retention policy | Snapshot chain length not exceeded | `tests/modules/retention/test_time_based.py` | `test_snapshot_count_within_chain_length_keeps_all` *(existing)* | trace-only |
| count-based-retention | Count-based retention policy | Hysteresis mode defers to the hysteresis capability | `tests/core/test_hysteresis_retention.py` | `test_hysteresis_uses_threshold_floor_phase_not_steady_rule` | retention-unit |
| count-based-retention | Count-based retention policy | Target chain length triggers new FULL | `tests/core/test_full_anchor.py` | `test_incremental_count_exceeds_chain_length_triggers_full` *(existing)* | trace-only |
| count-based-retention | Count-based retention policy | Target keep generations limits chains | `tests/modules/retention/test_time_based.py` | `test_keep_generations_limits_chains` *(existing)* | trace-only |
| count-based-retention | Count-based retention policy | First backup to target creates FULL | `tests/core/test_pipeline.py` | `test_first_backup_creates_full_regardless_of_chain_length` *(existing)* | core-pipeline-unit |

### Spec: snapshot-preserve-min (10 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| snapshot-preserve-min | Per-disk preserve_min filter | preserve_min inactive when explicitly zero | `tests/core/test_preserve.py` | `test_preserve_min_inactive_explicit_zero` *(existing)* | retention-unit |
| snapshot-preserve-min | Per-disk preserve_min filter | default preserve_min 48 keeps newest 48 | `tests/core/test_preserve.py` | `test_default_preserve_min_48_keeps_newest_48` *(modify: pin `max_commits_per_run=0`)* | retention-unit |
| snapshot-preserve-min | Per-disk preserve_min filter | default floor dominates chain_length | `tests/core/test_preserve.py` | `test_default_floor_dominates_chain_length` *(existing)* | retention-unit |
| snapshot-preserve-min | Per-disk preserve_min filter | preserve_min preserves newest snapshots of a disk | `tests/core/test_preserve.py` | `test_preserve_min_trim_excess_from_newest` *(existing)* | retention-unit |
| snapshot-preserve-min | Per-disk preserve_min filter | preserve_min does not trigger when remove is small | `tests/core/test_preserve.py` | `test_preserve_min_no_trim_when_within_limit` *(modify: pin `max_commits_per_run=0`)* | retention-unit |
| snapshot-preserve-min | Per-disk preserve_min filter | preserve_min equals total snapshots for a disk | `tests/core/test_preserve.py` | `test_preserve_min_equals_total_no_blockcommit` *(existing)* | retention-unit |
| snapshot-preserve-min | Per-disk preserve_min filter | preserve_min greater than total snapshots | `tests/core/test_preserve.py` | `test_preserve_min_exceeds_total_no_blockcommit` *(existing)* | retention-unit |
| snapshot-preserve-min | Per-disk preserve_min filter | preserve_min applied after oldest-prefix within a single disk | `tests/core/test_preserve.py` | `test_preserve_min_applied_after_oldest_prefix` *(existing)* | retention-unit |
| snapshot-preserve-min | Per-disk preserve_min filter | Each disk applies preserve_min independently | `tests/core/test_preserve.py` | `test_multidisk_preserve_min_independent` *(existing)* | retention-unit |
| snapshot-preserve-min | Per-disk preserve_min filter | Hysteresis collapse respects the floor | `tests/core/test_hysteresis_retention.py` | `test_hysteresis_collapse_respects_floor` | retention-unit |

### Spec: config-model (6 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| config-model | snapshot_retention_mode option | Default mode is hysteresis | `tests/config/test_facade.py` | `test_snapshot_retention_mode_default_hysteresis` | state-config-unit |
| config-model | snapshot_retention_mode option | VM override wins | `tests/config/test_resolver.py` | `test_snapshot_retention_mode_vm_override_wins` | state-config-unit |
| config-model | snapshot_retention_mode option | Invalid mode value rejected | `tests/config/test_facade.py` | `test_snapshot_retention_mode_invalid_value_rejected` | state-config-unit |
| config-model | snapshot_retention_mode option | Invalid hysteresis bounds rejected | `tests/config/test_facade.py` | `test_hysteresis_invalid_bounds_rejected_naming_values` | state-config-unit |
| config-model | max_commits_per_run option | Default cap | `tests/config/test_model.py` | `test_global_config_max_commits_per_run_default_12` | state-config-unit |
| config-model | max_commits_per_run option | Negative value rejected | `tests/config/test_facade.py` | `test_max_commits_per_run_negative_rejected` | state-config-unit |

### Spec: core-orchestrator (5 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| core-orchestrator | Hysteresis retention evaluation flow | Steady mode untouched | `tests/core/test_hysteresis_retention.py` | `test_steady_mode_branch_identical_to_legacy` | retention-unit |
| core-orchestrator | Hysteresis retention evaluation flow | Hysteresis collapse evaluation | `tests/core/test_hysteresis_retention.py` | `test_collapse_evaluation_engine_floor_and_cap` | retention-unit |
| core-orchestrator | Hysteresis retention evaluation flow | Below threshold with inactive phase | `tests/core/test_hysteresis_retention.py` | `test_below_threshold_inactive_phase_no_phase_write` | retention-unit |
| core-orchestrator | Collapse phase completion handling | Floor reached clears the phase | `tests/core/test_hysteresis_retention.py` | `test_collapse_complete_info_logged_at_floor` | retention-unit |
| core-orchestrator | Collapse phase completion handling | Cap reached keeps the phase | `tests/core/test_hysteresis_retention.py` | `test_cap_reached_keeps_phase_logs_continuation` | retention-unit |

### Spec: state-management (4 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| state-management | collapse_in_progress phase key | Missing key reads as empty | `tests/state/test_manager.py` | `test_collapse_in_progress_missing_key_reads_empty` | state-config-unit |
| state-management | collapse_in_progress phase key | Marker survives atomic write | `tests/state/test_manager.py` | `test_collapse_in_progress_round_trip_atomic` | state-config-unit |
| state-management | collapse_in_progress phase key | Reset clears the marker | `tests/state/test_manager.py` | `test_reset_vm_state_clears_collapse_in_progress` (+ companion `test_reset_vm_disk_state_removes_one_disk`) | state-config-unit |
| state-management | collapse_in_progress phase key | Old code tolerates the new key | `tests/state/test_manager.py` | `test_collapse_in_progress_key_tolerated_on_load` | state-config-unit |

### Spec: blockjob-protocol (7 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| blockjob-protocol | Shared block-job probe | No job reported | `tests/utils/test_blockjob.py` | `test_classify_no_current_job_returns_none` | utils-classifier-unit |
| blockjob-protocol | Shared block-job probe | Active job reported | `tests/utils/test_blockjob.py` | `test_classify_active_job_output_returns_active` | utils-classifier-unit |
| blockjob-protocol | Shared block-job probe | Probe call fails | `tests/core/test_engine.py` | `test_probe_call_failure_returns_error` | core-pipeline-unit |
| blockjob-protocol | Shared block-job probe | Probe addresses the disk by target name | `tests/core/test_engine.py` | `test_probe_command_uses_target_name_only` | core-pipeline-unit |
| blockjob-protocol | Shared block-job probe | External-snapshot domain resolves the target probe | `tests/integration/test_blockcommit_defer.py` | `test_external_snapshot_chain_probe_resolves_by_target_name` | integration |
| blockjob-protocol | Shared block-job probe | Backup path defers on active job | `tests/modules/backup/test_bitmap.py` | `test_active_blockjob_defers_run_backup` *(modify: assert `--path vda`)* | backup-probe-unit |
| blockjob-protocol | Shared block-job probe | Backup path proceeds on probe error with warning | `tests/modules/backup/test_bitmap.py` | `test_probe_error_logs_warning_and_proceeds` | backup-probe-unit |

### Spec: backup-provider (3 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-provider | Pre-backup probe target-name / fail-open | Active block job defers the disk backup | `tests/modules/backup/test_bitmap.py` | `test_active_blockjob_defers_run_backup` *(modify)* | backup-probe-unit |
| backup-provider | Pre-backup probe target-name / fail-open | Probe addressed by target device name resolves on external chains | `tests/integration/test_blockcommit_defer.py` | `test_external_snapshot_chain_probe_resolves_by_target_name` | integration |
| backup-provider | Pre-backup probe target-name / fail-open | Probe failure logs a warning and proceeds | `tests/modules/backup/test_bitmap.py` | `test_probe_unclassifiable_and_timeout_warns_proceeds` | backup-probe-unit |

### Spec: dry-run-prediction (3 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| dry-run-prediction | Hysteresis retention prediction in dry-run | Grow phase predicts no commits | `tests/core/test_dry_run_prediction.py` | `test_hysteresis_grow_phase_predicts_no_commits` | core-pipeline-unit |
| dry-run-prediction | Hysteresis retention prediction in dry-run | Collapse prediction is capped and names the oldest snapshots | `tests/core/test_dry_run_prediction.py` | `test_hysteresis_collapse_prediction_capped_oldest` | core-pipeline-unit |
| dry-run-prediction | Hysteresis retention prediction in dry-run | Persisted collapse phase drives prediction below the trigger threshold | `tests/core/test_dry_run_prediction.py` | `test_hysteresis_phase_drives_prediction_below_threshold` | core-pipeline-unit |

### Spec: commit-reconciliation (10 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| commit-reconciliation | Post-unknown reconciliation protocol | Late success detected after client timeout | `tests/core/test_pipeline.py` | `test_reconcile_late_success_after_timeout` *(existing)* | core-pipeline-unit |
| commit-reconciliation | Post-unknown reconciliation protocol | Job still active after timeout | `tests/core/test_pipeline.py` | `test_reconcile_job_active_after_timeout` *(existing)* | core-pipeline-unit |
| commit-reconciliation | Post-unknown reconciliation protocol | Dead job with no effect | `tests/core/test_pipeline.py` | `test_reconcile_dead_job_no_effect` *(existing)* | core-pipeline-unit |
| commit-reconciliation | Post-unknown reconciliation protocol | Contradictory evidence is inconclusive | `tests/core/test_pipeline.py` | `test_reconcile_partial_prefix_chain_delta_disagrees_inconclusive` | core-pipeline-unit |
| commit-reconciliation | Post-unknown reconciliation protocol | Probe failure is inconclusive | `tests/core/test_pipeline.py` | `test_reconcile_probe_failure_inconclusive` *(existing)* | core-pipeline-unit |
| commit-reconciliation | Post-unknown reconciliation protocol | Partial oldest prefix verified after multi-snapshot timeout | `tests/core/test_pipeline.py` | `test_reconcile_partial_oldest_prefix_late_success` | core-pipeline-unit |
| commit-reconciliation | Post-unknown reconciliation protocol | Prefix size disagreeing with chain delta is inconclusive | `tests/core/test_pipeline.py` | `test_reconcile_prefix_size_disagrees_with_delta_inconclusive` | core-pipeline-unit |
| commit-reconciliation | Post-unknown reconciliation protocol | Non-contiguous deletion pattern is inconclusive | `tests/core/test_pipeline.py` | `test_reconcile_contradictory_evidence_inconclusive` *(existing — update docstring to "non-contiguous pattern")* | core-pipeline-unit |
| commit-reconciliation | Late-success state convergence | State synced after late success | `tests/core/test_pipeline.py` | `test_late_success_converges_state_continues` *(existing)* | core-pipeline-unit |
| commit-reconciliation | Late-success state convergence | Partial late success converges the prefix and keeps the suffix | `tests/core/test_pipeline.py` | `test_partial_late_success_rewrites_intent_to_suffix` | core-pipeline-unit |

**Trace total:** 70 rows. 63 rows are owned by grouped files; 3 rows reference unchanged existing tests in `tests/modules/retention/test_time_based.py` and `tests/core/test_full_anchor.py` (group `trace-only`); 4 additional rows live in files that already have work assigned (the `*(existing)*` reconciliation rows inside `tests/core/test_pipeline.py`).

---

## Delegation Groups

A test FILE belongs to exactly one group. Trace-only rows require no work.

### Group: retention-unit

**Scope:** `tests/core/test_hysteresis_retention.py`, `tests/core/test_preserve.py`, `tests/interfaces/test_retention_engine.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_hysteresis_retention.py` | 21 (14 hysteresis + 1 count-based + 1 preserve-min + 5 core-orchestrator) | NEW |
| `tests/core/test_preserve.py` | 10 (7 unchanged + 2 modify + 1 new steady-mode) | MODIFY |
| `tests/interfaces/test_retention_engine.py` | 0 (purity contract, design D3) | MODIFY |

### Group: core-pipeline-unit

**Scope:** `tests/core/test_pipeline.py`, `tests/core/test_recovery_pipeline.py`, `tests/core/test_engine.py`, `tests/core/test_dry_run_prediction.py`, `tests/core/test_dry_run_recovery_prediction.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | 11 (1 count-based trace + 10 reconciliation) | MODIFY |
| `tests/core/test_recovery_pipeline.py` | 0 (step-0 partial-prefix convergence, design D6) | MODIFY |
| `tests/core/test_engine.py` | 2 (probe failure, target-name command) | MODIFY |
| `tests/core/test_dry_run_prediction.py` | 3 (hysteresis dry-run) | MODIFY |
| `tests/core/test_dry_run_recovery_prediction.py` | 0 (dry-run intent recovery never writes phase) | MODIFY |

### Group: state-config-unit

**Scope:** `tests/state/test_manager.py`, `tests/interfaces/test_state_manager.py`, `tests/mocks/mock_state.py`, `tests/mocks/test_mock_state.py`, `tests/mocks/test_mock_validity.py`, `tests/config/test_facade.py`, `tests/config/test_model.py`, `tests/config/test_resolver.py`, `tests/config/test_fixtures.py`, `tests/fixtures/configs/hysteresis_mode.toml`, `tests/fixtures/configs/hysteresis_invalid.toml`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/state/test_manager.py` | 4 (state-management) | MODIFY |
| `tests/interfaces/test_state_manager.py` | 0 (contract: collapse methods abstract + parametrized over Json/InMemory) | MODIFY |
| `tests/mocks/mock_state.py` | 0 (implement additive collapse methods on `InMemoryStateManager`) | MODIFY |
| `tests/mocks/test_mock_state.py` | 0 (mock round-trip + reset) | MODIFY |
| `tests/mocks/test_mock_validity.py` | 0 (mock presence check for new methods) | MODIFY |
| `tests/config/test_facade.py` | 6 (2 hysteresis-validation + 4 config-model) | MODIFY |
| `tests/config/test_model.py` | 1 (default cap) | MODIFY |
| `tests/config/test_resolver.py` | 1 (VM override wins) | MODIFY |
| `tests/config/test_fixtures.py` | 0 (fixture .toml validation) | MODIFY |
| `tests/fixtures/configs/hysteresis_mode.toml` | 0 (valid H=72/L=24 + cap, VM override) | NEW |
| `tests/fixtures/configs/hysteresis_invalid.toml` | 0 (H=24/L=48 → ConfigError) | NEW |

### Group: backup-probe-unit

**Scope:** `tests/modules/backup/test_bitmap.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | 4 (2 blockjob-protocol + 2 backup-provider) | MODIFY |

### Group: utils-classifier-unit

**Scope:** `tests/utils/test_blockjob.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_blockjob.py` | 2 (none/active classification) | NEW |

### Group: integration

**Scope:** `tests/integration/test_hysteresis_retention.py`, `tests/integration/test_blockcommit_defer.py`, `tests/integration/test_commit_intent_recovery.py`, `tests/integration/test_dry_run.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_hysteresis_retention.py` | 0 (multi-run collapse, steady-default no-phase, dry-run zero-mutation on real chain) | NEW |
| `tests/integration/test_blockcommit_defer.py` | 2 (external-chain probe resolves; backup-probe resolution) | MODIFY |
| `tests/integration/test_commit_intent_recovery.py` | 0 (real partial-prefix convergence) | MODIFY |
| `tests/integration/test_dry_run.py` | 0 (real hysteresis dry-run byte-identical state) | MODIFY |

### Group: stress

**Scope:** `tests/stress/test_long_chain.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/stress/test_long_chain.py` | 0 (capped multi-cycle collapse on a 66-overlay chain, H=64) | MODIFY |

---

## Test Modifications

Behavior changes that force existing-test edits (source code stays untouched by this plan; the implementing agent applies these).

| File | Change | Reason |
|---|---|---|
| `tests/core/test_preserve.py::test_default_preserve_min_48_keeps_newest_48` | Build the Core with `MockConfigFacade(global_config=GlobalConfig(max_commits_per_run=0), ...)` so the preserve-min filter is exercised without the cap; keep the `remove == 52` assertion. Optionally add a second assertion showing the capped result (12) when the default cap applies. | New `max_commits_per_run` default 12 applies in BOTH modes (hysteresis-retention "Per-run commit cap"): a 52-item remove list would now truncate to 12, breaking `len(result.remove) == 52`. |
| `tests/core/test_preserve.py::test_preserve_min_no_trim_when_within_limit` | Same pinning: `max_commits_per_run=0` in the global config; keep `remove == 28`. | 28 > 12 → the default cap would truncate and the "no trim" assertion fails. |
| `tests/core/test_preserve.py` (new test) | Add `test_steady_mode_cap_truncates_remove_list` — steady mode, remove 28, default cap 12 → exactly the 12 OLDEST remain, newest floor entries untouched. | Cap is a NEW behavior in steady mode; existing tests only cover un-capped floor semantics. |
| `tests/modules/backup/test_bitmap.py::test_active_blockjob_defers_run_backup` | After the deferral assertions, inspect `mock_shell.call_history` and assert a `virsh blockjob --domain ... --path vda` call exists and NO call passes the base-image path (`str(disk.base_image)`) as `--path`. | Provider probe switches from `--path <base_image>` to `--path <target>` (design D5). Mock expectations match by substring so they still pass, but the behavior assertion must pin the new addressing. |
| `tests/core/test_engine.py::test_backup_probe_behavior_unchanged` | Keep the deferral assertions; extend with a patch/probe that `Core._probe_blockjob` delegates classification to `qsnap.utils.blockjob.classify_blockjob_output` (e.g. spy on the classifier) and that the probe command uses `--path vda`. | The Core probe now routes through the shared classifier; the "unchanged deferral" contract stays but the implementation seam is new. |
| `tests/core/test_pipeline.py::test_reconcile_contradictory_evidence_inconclusive` | No assertion change needed; update the docstring to name the case "non-contiguous deletion pattern" (s1 present, s2 absent) per the new protocol's step 3/8. | The new protocol still returns `inconclusive` for this pattern; only the terminology must match the spec. |
| `tests/core/test_pipeline.py` (new tests) | Add `test_single_snapshot_reconcile_byte_identical_to_legacy` — parametrized over the four n=1 outcomes (late_success/job_active/failure/inconclusive) asserting identical results to the pre-change rules. | Design D6: "for n = 1 (steady mode) behavior is byte-identical to today" — regression lock. |
| `tests/core/test_recovery_pipeline.py` (new tests) | Add `test_step0_recovery_partial_prefix_converges_suffix_retried` and `test_step0_recovery_single_snapshot_byte_identical`. | Step-0 crash recovery (baseline-less) must apply the same partial-prefix protocol per design D6. |
| `tests/core/test_dry_run_prediction.py` (new tests) | Add the three hysteresis dry-run tests (see coverage map) plus `test_hysteresis_dry_run_does_not_write_phase` (folded into the zero-mutation assertions of the three scenario tests). | Dry-run must predict phase transitions without writing them (D2/D7). |
| `tests/core/test_dry_run_recovery_prediction.py` (new test) | Add `test_dry_run_intent_recovery_does_not_write_collapse_phase`. | Zero-mutation invariant extends to the phase key during dry-run intent recovery. |
| `tests/state/test_manager.py` (new tests) | Add the four state-management tests (coverage map). | Additive key + new IStateManager methods. |
| `tests/interfaces/test_state_manager.py` (new test) | Add `test_contract_collapse_in_progress_methods_abstract` parametrized over `JsonStateManager` and `InMemoryStateManager` (mirror the existing reset/backup-allocation contract pattern). | TESTING.md contract rule: every ABC addition verified over all concrete implementations. |
| `tests/mocks/mock_state.py` | Implement `get_collapse_in_progress` / `set_collapse_in_progress` / `clear_collapse_in_progress` on `InMemoryStateManager`; clear the key in `reset_vm_state` and per-disk in `reset_vm_disk_state`. | TESTING.md: every ABC interface gets at least one mock implementation mirroring the interface. |
| `tests/mocks/test_mock_state.py`, `tests/mocks/test_mock_validity.py` | Add collapse-method round-trip / presence tests. | Mocks must never return `None` for result-bearing methods and must satisfy `isinstance(mock, ABC)`. |
| `tests/config/test_facade.py` (new tests) | Add the six config rows from the coverage map plus `test_max_commits_per_run_zero_accepted` and `test_max_commits_per_run_non_integer_rejected`. | New options + validation rules. |
| `tests/config/test_model.py` (new tests) | Add `test_global_config_max_commits_per_run_default_12`, `test_global_config_snapshot_retention_mode_default_hysteresis`, and frozen-field immutability assertions. | New frozen dataclass fields (config-model spec). |
| `tests/config/test_resolver.py` (new test) | Add `test_snapshot_retention_mode_vm_override_wins` and `test_max_commits_per_run_is_global_only` (no target-level effect). | Inheritance chain semantics. |
| `tests/config/test_fixtures.py` (new tests) | Add `test_hysteresis_mode_fixture_parses`, `test_hysteresis_invalid_fixture_rejected`. | TESTING.md new-module checklist step 4 (`.toml` fixtures). |

No changes required (verified): `tests/modules/retention/test_time_based.py` (engine stays pure per D3), `tests/modules/backup/test_bitmap_incremental.py` / `test_bitmap_convert.py` / `test_bitmap_recovery.py` (their `_expect_no_blockjob` helpers match the probe by the `"virsh blockjob"` substring, unaffected by the `--path` argument change; none asserts the base-image path), `tests/conftest.py` default blockjob expectation (substring match), `tests/core/test_full_anchor.py`, `tests/mocks/mock_config.py` (new fields have defaults; `MockConfigFacade` passes `GlobalConfig`/`VMConfig` objects through).

---

## Tests to DELETE

**None.** Explicit audit result:

- **Old probe addressing:** no existing test asserts that `BitmapBackupProvider`'s pre-backup probe passes the base-image path as `--path`. All probe expectations (`_expect_no_blockjob` in `test_bitmap.py`/`test_bitmap_incremental.py`/`test_bitmap_convert.py`/`test_bitmap_recovery.py`, the conftest default, and `test_active_blockjob_defers_run_backup`) match the substring `"virsh blockjob"` and remain valid after the fix — they must be *extended* (see modifications), not deleted.
- **Duplicate probe coverage:** Core's probe tests (`test_engine.py::test_backup_probe_behavior_unchanged`, `test_reconcile_job_active_after_timeout`) and the provider's probe tests cover different call sites; the new `classify_blockjob_output` gets its own pure tests. No duplication is superseded.
- **Reconciliation suite:** the existing n=1 reconciliation tests (`test_reconcile_late_success_after_timeout`, `test_reconcile_job_active_after_timeout`, `test_reconcile_dead_job_no_effect`, `test_reconcile_probe_failure_inconclusive`, `test_reconcile_files_gone_but_chain_unchanged_inconclusive`, `test_reconcile_files_present_but_chain_shrunk_inconclusive`, `test_reconcile_contradictory_evidence_inconclusive`, `test_late_success_converges_state_continues`) all remain valid: the new protocol is explicitly byte-identical for single-snapshot merge sets, and the "s1 present / s2 absent" case remains `inconclusive` under the non-contiguous-pattern rule. They are extended, not replaced.
- **Steady-mode retention:** hysteresis is now the default mode; every steady-only assertion that previously relied on the implicit default must set `snapshot_retention_mode="steady"` explicitly (and pin `max_commits_per_run=0` where the new cap would trim). The two preserve-min tests that would break do so because of the NEW steady-mode cap and are fixed by pinning `max_commits_per_run=0`, not by deletion.
- **No stale mock expectations or dead fixtures** were found that the refactor supersedes.

---

## Integration & Stress Updates

### tests/integration/test_hysteresis_retention.py (NEW — `@pytest.mark.integration`, real `test_vm`)

1. `test_hysteresis_multi_run_collapse_real_chain` — End-to-end hysteresis on a real disposable VM:
   - Configure `VMConfig(snapshot_retention_mode="hysteresis", snapshot_chain_length=8, snapshot_preserve_min=3, max_commits_per_run=2)` (small H/L to keep runtime sane) with `DefaultFactory` and a real `JsonStateManager` (state file on disk so the phase is observable across Core instances).
   - Simulate runs: create snapshots until `N > H` (9+), then run `core.run()`/`prune`; assert exactly 2 commits (cap) per run, `collapse_in_progress` contains `vda` in the JSON state file after each capped run, and the chain converges to the floor (N == 3) after `ceil((9-3)/2) = 3` further runs.
   - Verify backing-chain integrity with `qemu-img check --force-share` on the active overlay and a `qemu-img info --backing-chain` length equal to the state count.
   - Grow again past H afterwards and assert the collapse re-triggers (hysteresis loop).
2. `test_hysteresis_default_mode_no_phase_below_threshold_real_chain` — Default `"hysteresis"` mode against a real 10-snapshot chain (below the default threshold H=72): prune commits nothing (grow phase) and the state file NEVER contains `collapse_in_progress`.
3. `test_hysteresis_dry_run_zero_mutation_real_chain` — Persisted phase + deep chain; run `-n`; assert state-file bytes identical before/after, no `virsh blockcommit` in the real shell history, and the predicted blockcommit entry names exactly `min(N − L, cap)` oldest snapshots.

### tests/integration/test_blockcommit_defer.py (MODIFY)

4. `test_external_snapshot_chain_probe_resolves_by_target_name` — Start the VM, create 2–3 external snapshots via `virsh snapshot-create-as --disk-only` (real chain, active overlay as domain source), then run the REAL `virsh blockjob --domain <vm> --path vda` probe through `Core._probe_blockjob`; assert it returns `"none"` (idle disk) with NO `disk '...' not found in domain` in stderr. Also assert probing the base-image path FAILS with the not-found error (documenting the bug being fixed), proving the probe must use the target name. Covers blockjob-protocol scenario "External-snapshot domain resolves the target probe" and backup-provider scenario "Probe addressed by target device name resolves on external chains".

### tests/integration/test_commit_intent_recovery.py (MODIFY)

5. `test_partial_prefix_reconciliation_real_chain` — Deterministic partial-prefix convergence on a real chain (feasible without killing virsh): create 3 external snapshots `s1..s3` with a pre-commit intent `[s1,s2,s3]` recorded in the real state; manually converge `s1` by `qemu-img commit` on the oldest overlay + removing its file + dropping its state record (simulating the "first per-snapshot job completed, client died" reality); then run step-0 intent recovery; assert `late_success` for the verified prefix `[s1]`, `remove_snapshot` called once, intent rewritten to `[s2,s3]`, WARNING names both the converged and pending snapshots.
   - A killed-`virsh` mid-batch variant is feasible but timing-flaky; the manual-prefix approach exercises the same observed reality deterministically. If the implementer wants the crash variant, gate it behind the same test with a best-effort attempt and a clear skip rationale — unit coverage (test_pipeline.py) remains the authoritative gate for the protocol.

### tests/integration/test_dry_run.py (MODIFY)

6. `test_dry_run_hysteresis_collapse_zero_mutation` — Real VM with persisted `collapse_in_progress` phase and a chain above the floor: dry-run predicts the capped merge batch; the state file and snapshot files are byte-identical after the run; no lifecycle manager call executes (mirrors the existing `test_dry_run_zero_mutation_single_disk` structure).

### tests/stress/test_long_chain.py (MODIFY)

7. `test_hysteresis_long_chain_capped_collapse` (`@pytest.mark.stress`, reuse the existing `stress_env` 512M fixture and `_IntentJournalState` pattern) — Build a 66-overlay snapshot chain (a chain of 55 with H=64 would sit in the grow phase and never trigger, so N must exceed H); switch to hysteresis with H=64, L=48, cap=4; run up to `ceil((66−48)/4)+1 = 6` prune cycles; assert each cycle commits ≤ 4, the phase persists between cycles, the final chain sits at L, and `qemu-img check` passes. This is the migration-from-deep-chain stress proof (design "Migration Plan" step 2).

e2e (`tests/e2e/`) runs the default `"hysteresis"` configuration end-to-end: chains below the default threshold H=72 must not be committed, and no steady-state blockcommit-per-hour behavior is expected under defaults.

---

## Risks & Edge Cases

- **[Migration of existing deep chains]** (design: N≈74, H=72, L=24 → 50 merges → 5 capped hourly runs) → `tests/core/test_hysteresis_retention.py::test_migration_deep_chain_converges_over_capped_runs` (unit: simulate 5 runs, assert per-run cap 12, monotonic progress, phase held until floor); integration `test_hysteresis_multi_run_collapse_real_chain`; stress `test_hysteresis_long_chain_capped_collapse`.
- **[Cap vs floor interplay]** (cap truncation keeps the floor invariant; floor ≥ L newest never marked) → `test_cap_never_breaks_floor`, `test_cap_truncates_collapse_keeps_oldest`, `test_steady_mode_cap_truncates_remove_list`, `test_hysteresis_collapse_respects_floor`, plus preserve-min pinning fixes in `test_preserve.py`.
- **[Phase-flag crash windows]** (marker written before the first blockcommit; a kill between phase-set and commit must resume collapsing next run) → `test_phase_persisted_before_first_blockcommit` (spy on state writes ordered before lifecycle calls), `test_phase_resumes_after_crash_between_set_and_commit`, `test_phase_persists_after_capped_run_continues_next_run`; integration phase-persistence assertions.
- **[Phase marker orphaned by persistent commit failures / stale-entry healing shrinks N mid-phase]** (marker is inert; defensive clear when evaluation observes N ≤ L) → `test_defensive_phase_clear_on_external_shrink`, `test_phase_remains_after_deferred_commit` (deferred/failed commits leave the phase intact for retry — core-orchestrator requirement).
- **[Dry-run zero-mutation]** (predictions only; phase key read but never set/extended/cleared; state byte-identical) → `test_hysteresis_grow_phase_predicts_no_commits`, `test_hysteresis_phase_drives_prediction_below_threshold` (byte-identical key), `test_dry_run_intent_recovery_does_not_write_collapse_phase`; integration `test_dry_run_hysteresis_collapse_zero_mutation`.
- **[Probe fail-open]** (probe error/timeout/unclassifiable output → WARNING + proceed; never silently disables backups, never fails the VM) → `test_probe_error_logs_warning_and_proceeds`, `test_probe_unclassifiable_and_timeout_warns_proceeds` (asserts VM not marked failed and backup proceeds); accepted race with operator-started jobs is documented — commit-side fail-closed guards keep existing coverage in `test_pipeline.py`/`test_lifecycle_fork.py`.
- **[n=1 byte-identical reconciliation]** (steady mode must not regress) → `test_single_snapshot_reconcile_byte_identical_to_legacy` (parametrized over all four outcomes) + `test_step0_recovery_single_snapshot_byte_identical`; all six existing n=1 reconciliation tests remain green.
- **[Partial-prefix convergence misattributes externally deleted files]** (requires oldest-prefix pattern AND chain-length agreement; contradictions fail closed) → `test_reconcile_prefix_size_disagrees_with_delta_inconclusive`, `test_reconcile_partial_prefix_chain_delta_disagrees_inconclusive`, existing `test_reconcile_contradictory_evidence_inconclusive` (non-contiguous), `test_reconcile_files_gone_but_chain_unchanged_inconclusive`.
- **[Default cap slows migration on healthy hosts]** (escape hatch `max_commits_per_run = 0`) → `test_cap_zero_unlimited`, `test_max_commits_per_run_zero_accepted` (config).
- **[Two retention modes increase test matrix]** (mode × phase × cap is orthogonal and enumerable) → `test_hysteresis_mode_phase_cap_matrix` (parametrized enumeration in `test_hysteresis_retention.py`); steady-mode legacy tests retained with explicit `snapshot_retention_mode="steady"`, and default-mode tests assert hysteresis.
- **[Old code / rollback tolerance]** (old binaries ignore the new state key; new code tolerates missing key) → `test_collapse_in_progress_missing_key_reads_empty`, `test_collapse_in_progress_key_tolerated_on_load`; config downgrade is inert by default-mode `"hysteresis"`.
