# QA Strategy & Test Plan

Change: **recover-lost-checkpoint-bitmaps** (repo: `/home/openuser/vm/qsnap`)

## Scope Summary

This plan covers 75 spec scenarios across 10 capability specs. All tests conform to TESTING.md: directory mirrors production, markers (`unit`/`mock`/`contract`/`integration`/`stress`/`e2e`), custom mocks implementing ABCs, `MockShell.expect().returns()`, `MockNbdClient` with `block_status`/`pread`/`pwrite` handlers, `InMemoryStateManager`, `MockVMModuleFactory`, `MockConfigFacade`, fixtures (`mock_shell`, `mock_state`, `make_vm_config`, `make_target`, `frozen_clock`, `success_result`, `failure_result`). No `pytest-mock`; `unittest.mock.patch` only. Mock validity/contract tests are parametrized over every concrete implementation. New fixture JSON files live under `tests/fixtures/shell_outputs/`.

---

## Section 1: Coverage Map

One row per spec scenario (75 total). File paths are relative to `tests/`.

### checkpoint-bitmap-health-probe (9 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| checkpoint-bitmap-health-probe | Bitmap health probe for running VMs | Healthy bitmap reported by QMP | modules/backup/test_bitmap_recovery.py | `test_probe_running_vm_healthy_bitmap_reported` | provider-unit-recovery |
| checkpoint-bitmap-health-probe | Bitmap health probe for running VMs | Bitmap missing after unclean shutdown | modules/backup/test_bitmap_recovery.py | `test_probe_running_vm_missing_bitmap_returns_dead` | provider-unit-recovery |
| checkpoint-bitmap-health-probe | Bitmap health probe for running VMs | Bitmap flagged inconsistent | modules/backup/test_bitmap_recovery.py | `test_probe_running_vm_inconsistent_flag_returns_dead` | provider-unit-recovery |
| checkpoint-bitmap-health-probe | Bitmap health probe for stopped VMs | Stopped VM healthy bitmap in intermediate layer | modules/backup/test_bitmap_recovery.py | `test_probe_stopped_vm_healthy_bitmap_in_intermediate_layer` | provider-unit-recovery |
| checkpoint-bitmap-health-probe | Bitmap health probe for stopped VMs | Stopped VM dead bitmap | modules/backup/test_bitmap_recovery.py | `test_probe_stopped_vm_dead_bitmap` | provider-unit-recovery |
| checkpoint-bitmap-health-probe | Probe result tri-state and failure isolation | QMP unavailable yields UNKNOWN | modules/backup/test_bitmap_recovery.py | `test_probe_running_vm_qmp_unavailable_returns_unknown` | provider-unit-recovery |
| checkpoint-bitmap-health-probe | Probe result tri-state and failure isolation | Unparseable QMP JSON yields UNKNOWN | modules/backup/test_bitmap_recovery.py | `test_probe_running_vm_unparseable_json_returns_unknown` | provider-unit-recovery |
| checkpoint-bitmap-health-probe | Baseline assessment exposed on IBackupProvider | Assessment reports dead checkpoint with gate outcome | modules/backup/test_bitmap_recovery.py | `test_assess_baseline_dead_checkpoint_reports_gate_outcome` | provider-unit-recovery |
| checkpoint-bitmap-health-probe | Baseline assessment exposed on IBackupProvider | Assessment with no checkpoint | modules/backup/test_bitmap_recovery.py | `test_assess_baseline_no_checkpoint_reports_full_estimate` | provider-unit-recovery |

### bitmap-loss-recovery (17 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| bitmap-loss-recovery | Crash evidence collection and WARNING semantics | Unclean shutdown evidence logged | core/test_recovery_pipeline.py | `test_recovery_logs_unclean_shutdown_warning` | core-pipeline-recovery |
| bitmap-loss-recovery | Crash evidence collection and WARNING semantics | Successful recovery exits zero | core/test_recovery_pipeline.py | `test_successful_recovery_exits_zero_with_warning_only` | core-pipeline-recovery |
| bitmap-loss-recovery | Crash evidence collection and WARNING semantics | Dead bitmap without boot change still recovers | modules/backup/test_bitmap_recovery.py | `test_recovery_proceeds_without_boot_id_change` | provider-unit-recovery |
| bitmap-loss-recovery | Recovery gates G1–G3 | Commit after checkpoint freeze fails G1 | modules/backup/test_bitmap_recovery.py | `test_gate_g1_fails_when_commit_ts_after_freeze` | provider-unit-recovery |
| bitmap-loss-recovery | Recovery gates G1–G3 | Absent commit marker fails G1 | modules/backup/test_bitmap_recovery.py | `test_gate_g1_fails_when_marker_absent` | provider-unit-recovery |
| bitmap-loss-recovery | Recovery gates G1–G3 | All gates pass | modules/backup/test_bitmap_recovery.py | `test_gates_g1_g2_g3_pass_select_recovered_delta` | provider-unit-recovery |
| bitmap-loss-recovery | Copy set computation | Copy set from state timestamps | modules/backup/test_bitmap_recovery.py | `test_copy_set_bounded_by_state_timestamps` | provider-unit-recovery |
| bitmap-loss-recovery | Copy set computation | Incomplete state falls back to full overlay set | modules/backup/test_bitmap_recovery.py | `test_copy_set_falls_back_to_all_overlays_on_incomplete_state` | provider-unit-recovery |
| bitmap-loss-recovery | Recovered delta lifecycle | Successful recovered delta | modules/backup/test_bitmap_recovery.py | `test_recovered_delta_success_chains_onto_newest_backup` | provider-unit-recovery |
| bitmap-loss-recovery | Recovered delta lifecycle | Zero extents are copied | modules/backup/test_bitmap_recovery.py | `test_recovered_delta_copies_zero_extents_and_skips_holes` | provider-unit-recovery |
| bitmap-loss-recovery | Recovered delta lifecycle | Transfer failure rolls back and falls back to FULL | modules/backup/test_bitmap_recovery.py | `test_recovered_delta_failure_rolls_back_then_full_same_run` | provider-unit-recovery |
| bitmap-loss-recovery | Recovered delta lifecycle | Consistency under concurrent guest writes | modules/backup/test_bitmap_recovery.py | `test_recovered_delta_successor_checkpoint_precedes_copy` | provider-unit-recovery |
| bitmap-loss-recovery | FULL fallback with post-verification retirement | Old generation retired only after new FULL verified | core/test_recovery_pipeline.py | `test_recovery_full_retires_generation_only_after_verification` | core-pipeline-recovery |
| bitmap-loss-recovery | FULL fallback with post-verification retirement | Failed recovery FULL preserves the old generation | core/test_recovery_pipeline.py | `test_failed_recovery_full_preserves_old_generation` | core-pipeline-recovery |
| bitmap-loss-recovery | Reactive backstop for checkpoint-inconsistent errors | Backstop heals a probe miss | modules/backup/test_bitmap_recovery.py | `test_reactive_backstop_deletes_checkpoint_and_retries_once` | provider-unit-recovery |
| bitmap-loss-recovery | Reactive backstop for checkpoint-inconsistent errors | No infinite failure loop | modules/backup/test_bitmap_recovery.py | `test_two_run_incident_replay_first_heals_second_clean` | provider-unit-recovery |
| bitmap-loss-recovery | Startup invariant treats dead-bitmap checkpoints as orphans | Dead checkpoint with covering file removed at startup | core/test_recovery_pipeline.py | `test_startup_removes_dead_bitmap_checkpoint_with_covering_file` | core-pipeline-recovery |

### nbd-bitmap-backup (6 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| nbd-bitmap-backup | Prior checkpoint discovery is newest-wins per disk | Multiple checkpoints — newest selected | modules/backup/test_bitmap_recovery.py | `test_prior_discovery_newest_wins_filters_foreign` | provider-unit-recovery |
| nbd-bitmap-backup | Prior checkpoint discovery is newest-wins per disk | Different disks have separate checkpoint lineages | modules/backup/test_bitmap_recovery.py | `test_prior_discovery_per_disk_lineages_isolated` | provider-unit-recovery |
| nbd-bitmap-backup | Prior checkpoint discovery is newest-wins per disk | No checkpoints for a disk — full export | modules/backup/test_bitmap.py | `test_run_backup_first_backup_creates_full_with_atomic_checkpoint` (MODIFY) | provider-unit-recovery |
| nbd-bitmap-backup | Prior checkpoint discovery is newest-wins per disk | Healthy checkpoint proceeds to delta | modules/backup/test_bitmap_recovery.py | `test_healthy_probe_proceeds_to_delta` | provider-unit-recovery |
| nbd-bitmap-backup | Prior checkpoint discovery is newest-wins per disk | Dead checkpoint routes to recovery | modules/backup/test_bitmap_recovery.py | `test_dead_probe_routes_to_recovery_no_delta_attempt` | provider-unit-recovery |
| nbd-bitmap-backup | Prior checkpoint discovery is newest-wins per disk | Unknown probe result attempts delta | modules/backup/test_bitmap_recovery.py | `test_unknown_probe_attempts_delta_with_backstop` | provider-unit-recovery |

### backup-provider (10 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-provider | Backup creation work unit run_backup | First backup — full export via qemu-img convert | modules/backup/test_bitmap.py | `test_run_backup_first_backup_creates_full_with_atomic_checkpoint` (MODIFY: assert `kind=="full"`) | provider-unit-recovery |
| backup-provider | Backup creation work unit run_backup | Incremental backup — dirty blocks only | modules/backup/test_bitmap_incremental.py | `test_copy_loop_reads_only_dirty_extents` (MODIFY: probe + `kind=="delta"`) | provider-unit-recovery |
| backup-provider | Backup creation work unit run_backup | Dead bitmap routes to recovery instead of failing | modules/backup/test_bitmap_recovery.py | `test_run_backup_dead_probe_returns_recovery_not_failure` | provider-unit-recovery |
| backup-provider | Backup creation work unit run_backup | Checkpoint rotation after successful transfer | modules/backup/test_bitmap.py | `test_checkpoint_rotation_after_successful_run_backup` (MODIFY: probe expectation) | provider-unit-recovery |
| backup-provider | Backup creation work unit run_backup | Backup failure preserves prior checkpoint | modules/backup/test_bitmap.py | `test_run_backup_backup_begin_failure_preserves_prior_checkpoint` (MODIFY: probe expectation) | provider-unit-recovery |
| backup-provider | Backup creation work unit run_backup | A second run_backup in the same batch uses the successor as baseline | modules/backup/test_bitmap_incremental.py | `test_second_run_backup_uses_successor_as_baseline` (MODIFY: healthy probe each run) | provider-unit-recovery |
| backup-provider | Read-only baseline assessment for dry-run parity | Dry-run consumes assessment without mutation | core/test_dry_run_recovery_prediction.py | `test_dry_run_assessment_zero_mutation` | dry-run-parity |
| backup-provider | Read-only baseline assessment for dry-run parity | Mock implements the assessment contract | interfaces/test_backup_provider.py | `test_backup_provider_assess_baseline_contract` (MODIFY: parametrized over `[BitmapBackupProvider, MockBitmapBackupProvider]`) | state-and-contracts |
| backup-provider | Backup results carry the backup kind | Recovered delta is auditable | core/test_recovery_pipeline.py | `test_recovered_delta_audit_and_summary_kind` | core-pipeline-recovery |
| backup-provider | Backup results carry the backup kind | Regular paths keep their kinds | models/test_results.py | `test_backup_result_kind_full_delta_recovered_delta` | state-and-contracts |

### startup-state-validation (5 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| startup-state-validation | Orphan checkpoint invariant at startup | Orphan checkpoint deleted at startup | core/test_pipeline.py | `test_startup_orphan_checkpoint_deleted_at_startup` (MODIFY: probe expectation) | core-pipeline-recovery |
| startup-state-validation | Orphan checkpoint invariant at startup | Healthy checkpoint kept | core/test_pipeline.py | `test_startup_healthy_checkpoint_kept` (MODIFY: healthy QMP expectation) | core-pipeline-recovery |
| startup-state-validation | Orphan checkpoint invariant at startup | Dead-bitmap checkpoint removed despite covering file | core/test_recovery_pipeline.py | `test_startup_removes_dead_bitmap_checkpoint_with_covering_file` | core-pipeline-recovery |
| startup-state-validation | Orphan checkpoint invariant at startup | Dry-run predicts without deleting | core/test_recovery_pipeline.py | `test_startup_dry_run_predicts_removal_no_delete` | core-pipeline-recovery |
| startup-state-validation | Orphan checkpoint invariant at startup | Invariant failure is non-fatal | core/test_pipeline.py | `test_startup_orphan_checkpoint_delete_failure_non_fatal` (MODIFY: probe expectation) | core-pipeline-recovery |

### dry-run-prediction (9 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| dry-run-prediction | Backup prediction from target-internal data | Gate open with healthy checkpoint predicts one delta per disk | core/test_dry_run_recovery_prediction.py | `test_dry_run_healthy_checkpoint_predicts_single_delta` | dry-run-parity |
| dry-run-prediction | Backup prediction from target-internal data | Gate closed predicts no backup | core/test_dry_run_prediction.py | `test_simulated_snapshots_onchange_gate_closed` (MODIFY: no `assess_baseline` prediction) | core-pipeline-recovery |
| dry-run-prediction | Backup prediction from target-internal data | No checkpoint predicts FULL | core/test_dry_run_prediction.py | `test_no_checkpoint_predicts_full` (MODIFY: prediction via `assess_baseline`) | core-pipeline-recovery |
| dry-run-prediction | Backup prediction from target-internal data | Dead checkpoint with passing gates predicts recovered delta | core/test_dry_run_recovery_prediction.py | `test_dry_run_dead_checkpoint_gates_pass_predicts_recovered_delta` | dry-run-parity |
| dry-run-prediction | Backup prediction from target-internal data | Dead checkpoint with failed gate predicts FULL with reason | core/test_dry_run_recovery_prediction.py | `test_dry_run_dead_checkpoint_gate_fail_predicts_full_with_reason` | dry-run-parity |
| dry-run-prediction | Zero-mutation invariant for the dry-run pipeline | State and filesystem unchanged after dry-run | integration/test_dry_run.py | `test_dry_run_zero_mutation_single_disk` (MODIFY: checkpoint-set unchanged assertion) | dry-run-parity |
| dry-run-prediction | Zero-mutation invariant for the dry-run pipeline | Dry-run with phantom FULL records predicts cleanup without state writes | core/test_dry_run_prediction.py | `test_dry_run_phantom_full_cleanup_predicted_not_executed` (MODIFY: probe expectations) | core-pipeline-recovery |
| dry-run-prediction | Zero-mutation invariant for the dry-run pipeline | Dry-run with stale baseline and no FULLs predicts baseline cleanup | core/test_dry_run_prediction.py | `test_dry_run_stale_baseline_cleanup_predicted_not_executed` (MODIFY: probe expectations) | core-pipeline-recovery |
| dry-run-prediction | Zero-mutation invariant for the dry-run pipeline | Dry-run checkpoint probes are read-only | integration/test_dry_run.py | `test_dry_run_shell_calls_are_all_read_only` (MODIFY: allowlist gains `virsh qemu-monitor-command`, `virsh blockjob`) | dry-run-parity |

### state-management (6 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| state-management | Host boot_id tracking per VM | Boot id recorded on successful run | core/test_recovery_pipeline.py | `test_boot_id_recorded_after_successful_run` | core-pipeline-recovery |
| state-management | Host boot_id tracking per VM | Boot id change detected across a crash | state/test_recovery_state.py | `test_boot_id_change_detected_across_crash` | state-and-contracts |
| state-management | Host boot_id tracking per VM | Missing boot id is unknown, not an error | state/test_recovery_state.py | `test_missing_boot_id_returns_none_not_error` | state-and-contracts |
| state-management | Per-disk last-commit timestamp tracking | Marker written after successful blockcommit | core/test_recovery_pipeline.py | `test_last_commit_ts_written_after_blockcommit` | core-pipeline-recovery |
| state-management | Per-disk last-commit timestamp tracking | Marker written after successful offline commit | core/test_recovery_pipeline.py | `test_last_commit_ts_written_after_offline_commit` | core-pipeline-recovery |
| state-management | Per-disk last-commit timestamp tracking | Absent marker is conservative | modules/backup/test_bitmap_recovery.py | `test_gate_g1_fails_when_marker_absent` | provider-unit-recovery |

### backup-target-orthogonality (6 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-target-orthogonality | Backup phase SHALL NOT consume snapshot data | Backup phase runs with zero snapshots in state | core/test_recovery_pipeline.py | `test_backup_phase_with_zero_snapshots_produces_full` | core-pipeline-recovery |
| backup-target-orthogonality | Backup phase SHALL NOT consume snapshot data | Provider receives no SnapshotInfo | interfaces/test_backup_provider.py | `test_backup_provider_api_never_references_snapshotinfo` (MODIFY: includes new `assess_baseline` signature) | state-and-contracts |
| backup-target-orthogonality | Backup phase SHALL NOT consume snapshot data | Normal path never reads snapshot state | modules/backup/test_bitmap_recovery.py | `test_healthy_delta_path_never_reads_snapshot_state` | provider-unit-recovery |
| backup-target-orthogonality | Backup phase SHALL NOT consume snapshot data | Recovery path reads timestamps only | modules/backup/test_bitmap_recovery.py | `test_recovery_path_reads_timestamps_only` | provider-unit-recovery |
| backup-target-orthogonality | Checkpoint is the sole delta baseline | Baseline discovery uses only libvirt checkpoints | modules/backup/test_bitmap_recovery.py | `test_prior_discovery_newest_wins_filters_foreign` | provider-unit-recovery |
| backup-target-orthogonality | Checkpoint is the sole delta baseline | Dead checkpoint is not a baseline | modules/backup/test_bitmap_recovery.py | `test_dead_probe_routes_to_recovery_no_delta_attempt` | provider-unit-recovery |

### per-chain-retention (4 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| per-chain-retention | Immediate retirement of recovery-superseded generations | Recovery FULL retires the old generation immediately | core/test_recovery_pipeline.py | `test_recovery_full_retires_generation_immediately_ignoring_keep_generations` | core-pipeline-recovery |
| per-chain-retention | Immediate retirement of recovery-superseded generations | Corrupt superseded FULL is preserved | core/test_recovery_pipeline.py | `test_corrupt_superseded_full_preserved_critical_log` | core-pipeline-recovery |
| per-chain-retention | Immediate retirement of recovery-superseded generations | Recovered delta retires nothing | modules/backup/test_bitmap_recovery.py | `test_recovered_delta_retires_no_generation` | provider-unit-recovery |
| per-chain-retention | Immediate retirement of recovery-superseded generations | Normal retention unaffected | core/test_recovery_pipeline.py | `test_normal_retention_keep_generations_unchanged` | core-pipeline-recovery |

### size-estimation (3 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| size-estimation | Recovered-delta size estimation | Estimate sums the copy set | utils/test_space.py | `test_estimate_recovered_delta_size_sums_copy_set` | provider-unit-recovery |
| size-estimation | Recovered-delta size estimation | Unreadable layer falls back to FULL estimate | utils/test_space.py | `test_estimate_recovered_delta_falls_back_to_full_on_unreadable_layer` | provider-unit-recovery |
| size-estimation | Recovered-delta size estimation | Estimate feeds the free-space gate in dry-run | core/test_dry_run_recovery_prediction.py | `test_dry_run_free_space_gate_uses_recovered_delta_estimate` | dry-run-parity |

---

## Section 2: Delegation Groups

Non-overlapping groups; every test file belongs to exactly one group.

### Group 1: `provider-unit-recovery`

**Scope:** `BitmapBackupProvider` probe tri-state, recovery decision, gates G1–G3, copy-set computation, recovered-delta lifecycle, FULL fallback inside `run_backup`, reactive backstop, `assess_baseline` at provider level, `BackupResult.kind` production, zero-extent copy-loop behavior, and the `utils/space.py` recovered-delta estimator. Zero real I/O (MockShell + MockNbdClient).

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap_recovery.py` | probe (9), recovery (gates/copy-set/lifecycle/backstop/no-loop), nbd-backup (newest-wins, DEAD/UNKNOWN routing), provider (DEAD routing, kind), orthogonality (normal-path/state-timestamp-only), retention (recovered delta retires nothing) | **NEW** |
| `tests/modules/backup/test_bitmap.py` | nbd-backup (no checkpoint → FULL), provider (FULL kind, rotation, failure preservation) | **MODIFY** |
| `tests/modules/backup/test_bitmap_incremental.py` | provider (dirty-blocks-only delta, successor baseline), kind="delta" | **MODIFY** |
| `tests/modules/backup/test_bitmap_convert.py` | `run_backup` FULL path gains `kind=="full"` in result assertions | **MODIFY** |
| `tests/utils/test_space.py` | size-estimation (sums copy set, FULL fallback) | **MODIFY** |
| `tests/utils/test_extents.py` | zero-extent copy semantics in `base:allocation` iteration (data+zero copied, holes skipped) | **MODIFY** |

### Group 2: `core-pipeline-recovery`

**Scope:** Core orchestration of recovery: `boot_id` / `last_commit_ts` recording, startup dead-bitmap orphan invariant, immediate retirement ordering, exit-code semantics (0 vs 10), audit trail `kind`, and adjustments to all existing `tests/core/` files whose MockShell expectations or `BackupResult` constructors change.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_recovery_pipeline.py` | recovery (WARNING evidence, exit 0, retirement ordering, failed-FULL preservation, startup removal), state (boot_id recorded, markers after commits), orthogonality (zero snapshots → FULL), retention (immediate retirement, corrupt FULL preserved, normal unaffected), provider (audit kind) | **NEW** |
| `tests/core/test_pipeline.py` | startup-state-validation (3 MODIFY scenarios), plus every `BackupResult(...)` constructor gains `kind` where producers require it | **MODIFY** |
| `tests/core/test_dry_run_prediction.py` | dry-run (gate-closed, no-checkpoint FULL, phantom, stale baseline — now driven by `assess_baseline`; log-wording assertions updated) | **MODIFY** |
| `tests/core/test_engine.py` | exit-code semantics: `EXIT_BACKUP_ABORT` only on exhausted recovery | **MODIFY** |
| `tests/core/test_full_verification_pipeline.py` | `BackupResult` constructor `kind`; M1/M2 ordering in recovery retirement | **MODIFY** |
| `tests/core/test_bitmap_dependency.py` | `BackupResult` constructor `kind` | **MODIFY** |
| `tests/core/test_enospc_isolation.py` | `BackupResult` constructor `kind` | **MODIFY** |

### Group 3: `state-and-contracts`

**Scope:** Additive state fields (`boot_id`, `last_commit_ts`) in `JsonStateManager` + `InMemoryStateManager`, `IBackupProvider.assess_baseline` and `BackupResult.kind` contracts, mock validity, and model immutability tests.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/state/test_recovery_state.py` | state (boot_id persistence/change/missing, last_commit_ts persistence, additive-optional JSON round-trip, old state files readable) | **NEW** |
| `tests/state/test_manager.py` | state — new fields serialize as optional; no migration of legacy files; `reset_vm_disk_state` leaves new fields coherent | **MODIFY** |
| `tests/interfaces/test_backup_provider.py` | provider (mock contract), orthogonality (no SnapshotInfo incl. `assess_baseline`) | **MODIFY** |
| `tests/interfaces/test_state_manager.py` | state — `get_boot_id`/`set_boot_id`/`get_last_commit_ts`/`set_last_commit_ts` abstract; missing-method subclass raises `TypeError` | **MODIFY** |
| `tests/mocks/mock_modules.py` | `MockBitmapBackupProvider.assess_baseline` + `run_backup` sets `kind` | **MODIFY** |
| `tests/mocks/mock_state.py` | `InMemoryStateManager` implements the four new methods | **MODIFY** |
| `tests/mocks/test_mock_validity.py` | mock contract — mock returns valid assessment object, never `None`; `isinstance` checks | **MODIFY** |
| `tests/models/test_results.py` | provider (kind values + default), frozen/immutability of the new field | **MODIFY** |

### Group 4: `dry-run-parity`

**Scope:** dry-run = real run minus mutations: probe execution in dry-run, `assess_baseline`-driven recovery predictions, free-space gate on recovered-delta estimate, summary rendering, and the integration zero-mutation/read-only allowlist checks.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_dry_run_recovery_prediction.py` | provider (assessment zero-mutation), dry-run (healthy delta, recovered-delta predict, gate-fail FULL predict, read-only probes, free-space gate) | **NEW** |
| `tests/cli/test_summary.py` | provider (recovered-delta audit rendering distinct from plain delta) | **MODIFY** |
| `tests/integration/test_dry_run.py` | dry-run (zero-mutation + checkpoint-set unchanged, read-only allowlist incl. new probes) | **MODIFY** |

### Group 5: `integration-recovery`

**Scope:** Real `virsh`/`qemu-img`/`qemu-io` against the disposable `test_vm` fixture: producing a real dead checkpoint, real recovery (recovered delta and FULL fallback), two-run heal sequence, and amending existing integration tests whose custom `BackupResult` factories or call flows change.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_bitmap_loss_recovery.py` | bitmap-loss-recovery (no-infinite-loop real replay, FULL fallback retirement), backstop via wrapped-shell UNKNOWN probe | **NEW** |
| `tests/integration/test_full_backup.py` | provider — FULL results now carry `kind=="full"`; extra QMP probe tolerated | **MODIFY** |
| `tests/integration/test_incremental_backup.py` | provider — delta results carry `kind=="delta"`; probe runs before `backup-begin` | **MODIFY** |
| `tests/integration/test_auto_recovery.py` | `test_production_incident_reproduction` + `test_per_chain_retention_multiple_chains_over_time` — custom `run_backup` flows tolerate probe; kind assertions optional | **MODIFY** |
| `tests/integration/test_startup_validation.py` | startup — covered checkpoints on the real VM are probed; healthy bitmaps must be kept | **MODIFY** |
| `tests/integration/test_verify_before_delete.py` | custom `BackupResult` factories gain `kind` | **MODIFY** |
| `tests/integration/test_backup_retry_max_zero.py` | custom `BackupResult` factories gain `kind` | **MODIFY** |

### Group 6: `e2e-recovery`

**Scope:** one end-to-end sanity path: config → run with a dead checkpoint → recovered delta → restore VM from the recovered chain.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/e2e/test_from_config.py` | adds `test_config_to_restore_after_recovered_delta` — full journey incl. recovery | **MODIFY** |

---

## Section 3: Test Modifications AND Tests To Remove

### 3a. Modifications to existing tests

| File | Change | Reason (spec scenario / design decision) |
|---|---|---|
| `tests/mocks/mock_modules.py` | `MockBitmapBackupProvider` implements `assess_baseline(vm_config, target, disk)` returning a valid frozen assessment result (status/checkpoint/gates/estimate) and sets `kind="full"/"delta"` in `run_backup` | backup-provider spec: mock implements assessment contract; BackupResult.kind |
| `tests/mocks/mock_state.py` | `InMemoryStateManager` implements `get_boot_id/set_boot_id`, `get_last_commit_ts/set_last_commit_ts` backed by `self._state` dicts | state-management spec: additive optional fields, mock parity (TESTING.md paradigm table) |
| `tests/interfaces/test_backup_provider.py` | Add `assess_baseline` to `PROVIDER_CLASSES` contract (signature, return type, read-only), assert `BackupResult.kind` on `run_backup` results, extend `test_backup_provider_api_never_references_snapshotinfo` to inspect the new method's signature | backup-provider spec: BREAKING interface addition; orthogonality scenario "Provider receives no SnapshotInfo" |
| `tests/interfaces/test_state_manager.py` | Add abstract-method assertions for the 4 new `IStateManager` members + missing-method `TypeError` subclass tests | state-management spec; TESTING.md contract-test rule |
| `tests/state/test_manager.py` | New optional JSON keys (`boot_id`, `last_commit_ts`) round-trip; legacy state files without them still load (`None`); `reset_vm_disk_state`/`reset_target_disk_state` keep new fields coherent | state-management spec: absence is unknown, no migration |
| `tests/models/test_results.py` | `BackupResult` gains `kind` (default `"delta"` for backward-compatible constructor calls; producers set real value); frozen/immutability + value tests for `"full"`/`"delta"`/`"recovered_delta"` | backup-provider spec: BackupResult carries the backup kind |
| `tests/modules/backup/test_bitmap.py` | Every test whose `run_backup` sees a prior checkpoint adds a `virsh qemu-monitor-command` expectation (new helper `_expect_healthy_probe(mock_shell, cp_name)`); `test_stopped_vm_with_checkpoint_defers_no_mutation` updates its `len(all_run_cmds)==2` assertion (stopped-VM probe via `qemu-img info -U --backing-chain` is now a third read-only call); FULL-path results assert `kind=="full"` | nbd-bitmap-backup / bitmap-loss-recovery specs: probe before delta decision; stopped-VM probe |
| `tests/modules/backup/test_bitmap_incremental.py` | Delta-path tests add healthy-probe expectations before `backup-begin`; result assertions add `kind=="delta"`; `test_second_run_backup_uses_successor_as_baseline` probes the successor each run | nbd-bitmap-backup spec scenario "Healthy checkpoint proceeds to delta"; provider scenario "second run_backup uses successor" |
| `tests/modules/backup/test_bitmap_convert.py` | FULL convert results assert `kind=="full"` | provider spec "Regular paths keep their kinds" |
| `tests/core/test_pipeline.py` | `test_startup_orphan_checkpoint_deleted_at_startup`, `test_startup_healthy_checkpoint_kept`, `test_startup_orphan_checkpoint_delete_failure_non_fatal` gain probe expectations (HEALTHY for covered, DEAD/absent for orphan); all `BackupResult(...)` constructor call sites set or accept `kind` | startup-state-validation spec: covered checkpoints are probed; D12 |
| `tests/core/test_dry_run_prediction.py` | Predictions driven by `provider.assess_baseline` instead of name-only `list_checkpoints`; log-wording assertions updated to spec phrasing ("delta will be created since checkpoint …"); `test_delta_prediction_uses_incremental_size_estimate` and the phantom/stale-baseline tests configure `MockBitmapBackupProvider.assess_baseline` and add read-only probe expectations | dry-run-prediction spec (D10): dry-run runs every read-only probe; prediction via assessment |
| `tests/core/test_engine.py`, `test_full_verification_pipeline.py`, `test_bitmap_dependency.py`, `test_enospc_isolation.py` | `BackupResult(...)` constructor calls accept/gain `kind`; M1/M2 ordering tests may assert recovery retirement order via shell call history | provider spec kind field; per-chain-retention spec ordering |
| `tests/integration/test_dry_run.py` | `_READ_ONLY_PREFIXES` gains `virsh qemu-monitor-command` and `virsh blockjob`; `_zero_mutation_assertions` (and the single-disk test) assert the libvirt checkpoint set is byte-identical before/after; new dead-checkpoint dry-run parity test | dry-run-prediction spec: dry-run probes are read-only; state/filesystem/checkpoints unchanged |
| `tests/integration/test_full_backup.py`, `test_incremental_backup.py`, `test_auto_recovery.py` | Assert `kind` on results; tolerate the extra read-only QMP call per delta; `test_per_chain_retention_multiple_chains_over_time` gains `kind=="full"` on the recovery branch it drives | provider spec kind; nbd-bitmap-backup probe placement |
| `tests/integration/test_verify_before_delete.py`, `test_backup_retry_max_zero.py` | Custom provider `BackupResult(...)` factories set `kind` (or accept default) | provider spec kind field |
| `tests/integration/test_startup_validation.py` | Startup flow on the real VM now probes the covered checkpoint; assertions updated to expect keep-on-HEALTHY and delete-on-DEAD | startup-state-validation spec D12 |
| `tests/cli/test_summary.py` | Summary formatter distinguishes `kind=="recovered_delta"` rows; `test_summary_table_backup_transfers` extended | provider spec "Recovered delta is auditable" |
| `tests/utils/test_space.py` | New `estimate_recovered_delta_size(shell, copy_set)` tests: sums `actual-size`, `~` approximate marker, FULL chain-sum fallback | size-estimation spec |
| `tests/utils/test_extents.py` | Copy-loop extent iteration for the recovered delta copies data+zero extents, skips only holes | bitmap-loss-recovery spec "Zero extents are copied" (D5 correctness) |

### 3b. Tests To Remove

Searched the whole suite (grep over `tests/` for `checkpoint inconsistent`, `missing or broken bitmap`, `blockjob.*dry`, `orphan.*covering`, `prediction.*checkpoint`). Findings per category:

1. **Tests asserting "checkpoint inconsistent" is a terminal non-retryable failure** — **NONE FOUND.** The incident behavior (exit 10 forever) has no test coverage anywhere: `tests/modules/backup/test_bitmap.py` only covers the `"already exists"` collision path (`test_checkpoint_collision_force_cleanup_and_retry`, `test_run_backup_backup_begin_failure_preserves_prior_checkpoint`), and `is_retryable` (`tests/utils/test_retry.py`) has no pattern for checkpoint-inconsistent errors. Nothing to delete.

2. **Tests asserting dry-run skips the blockjob/startup probes** — **NONE FOUND as deletion candidates.** No unit test asserts the blockjob probe is skipped in dry-run. The one implicit assertion lives in `tests/integration/test_dry_run.py::test_dry_run_shell_calls_are_all_read_only` via the `_READ_ONLY_PREFIXES` allowlist (which currently forbids `virsh blockjob`/`virsh qemu-monitor-command`); this test is **amended, not deleted** (see 3a — the allowlist is the intended extension point per the spec's explicit read-only command list).

3. **Tests asserting the orphan invariant keeps a covered checkpoint regardless of bitmap state** — **NONE FOUND.** `tests/core/test_pipeline.py::test_startup_healthy_checkpoint_kept` asserts keep-on-covering-file, but with no probe in the system today; under the new design the fixture simply receives a HEALTHY probe result (UNKNOWN also keeps it). It is modified, not deleted, because the underlying "covered healthy checkpoint is kept" behavior remains correct.

4. **Tests asserting backup prediction uses checkpoint names only** — **NONE FOUND as deletion candidates.** `test_delta_prediction_uses_incremental_size_estimate` asserts the delta prediction log names the checkpoint; the prediction still names the checkpoint, only the wording and the data source (`assess_baseline`) change → modification (3a).

5. **Tests asserting `_check_orphan_checkpoint` deletes without a dry-run guard** — **NONE FOUND.** The latent mutation bug is not covered by any test today (the startup-orphan tests run non-dry Core). The missing guard is covered by the **new** test `test_startup_dry_run_predicts_removal_no_delete`.

**Conclusion:** No existing test is deleted outright by this change. All contradicting assertions are amended (3a) because they assert behavior that remains correct under the new, more specific conditions (healthy probe, amended allowlist, new log wording). Two sweep-wide mechanical updates (probe expectations + `kind` constructor arg) touch many files but remove none.

---

## Section 4: Risks & Edge Cases (design.md Risks → dedicated coverage)

| Risk (design.md) | Test coverage |
|---|---|
| **QMP availability/permissions differ across hosts** → UNKNOWN keeps today's behavior; reactive backstop guarantees healing | `test_probe_running_vm_qmp_unavailable_returns_unknown` (probe returns UNKNOWN, no exception, no block); `test_unknown_probe_attempts_delta_with_backstop` (delta attempted; `backup-begin` "checkpoint inconsistent" → delete + retry once). Integration: wrapped-shell UNKNOWN test in `test_bitmap_loss_recovery.py`. |
| **`checkpoint-create` rejected on some libvirt versions** → D6 fallback via `backup-begin` FULL-XML + checkpoint-XML + abort unused job | `test_checkpoint_create_failure_falls_back_to_backup_begin` (unit: `virsh checkpoint-create` fails → provider uses the `backup-begin` checkpoint-XML form and aborts the unused job via `domjobabort`); integration `test_checkpoint_create_rejected_uses_backup_begin_fallback` on the deployed libvirt. |
| **Torn/stale reads while copying the live topmost layer** → successor bitmap records every post-T' write; next delta re-copies | `test_recovered_delta_successor_checkpoint_precedes_copy` (asserts `virsh checkpoint-create` runs before any NBD read of the topmost layer); `test_recovered_delta_concurrent_writes_recorded_in_successor_bitmap` (unit: `MockNbdClient.block_status_payload` on the successor bitmap marks post-T' writes dirty; the **next** regular delta's `block_status` reads the successor bitmap — dirty∩allocated copied). |
| **Copy set over-includes pre-freeze writes** → harmless shadowing; estimate is an upper bound, marked `~` | `test_recovered_delta_copies_oldest_to_newest_shadowing_safe` (copy loop iterates S oldest→newest; `MockNbdClient.writes` assert newer layer writes land after older — final file valid); `test_estimate_recovered_delta_size_sums_copy_set` (estimate includes the full `actual-size` of the freeze-layer, marked approximate); `test_dry_run_recovered_delta_estimate_marked_approximate` (log/summary `~`). |
| **`last_commit_ts` absent on upgraded installs** → one conservative FULL, self-clearing | `test_gate_g1_fails_when_marker_absent` (G1 fails → FULL selected); `test_absent_marker_first_loss_takes_full_then_heals` (Core-level: first run FULL + marker written; next run clean delta). |
| **Immediate retirement reduces restore-point redundancy** → verified new FULL is a complete restore point before anything is deleted | `test_recovery_full_retires_generation_only_after_verification` (shell-call ordering: `qemu-img check` of the new FULL **before** `checkpoint-delete` and before `rm` of the old FULL); `test_failed_recovery_full_preserves_old_generation` (failed M1/M2 → old generation untouched, abort path); integration `test_recovery_full_retires_after_m1_m2_on_real_vm`. |
| **Orthogonality exception invites scope creep** → recovery-only, timestamp-only, codified | `test_healthy_delta_path_never_reads_snapshot_state` (state spy: `get_snapshots` never called on HEALTHY/absent paths); `test_recovery_path_reads_timestamps_only` (DEAD path calls `get_snapshots` for timestamps but never passes `SnapshotInfo` into the backup call chain); `test_backup_provider_api_never_references_snapshotinfo` (contract, now including `assess_baseline`); `test_recovered_delta_retires_no_generation` (recovery-delta path deletes no backup files). |
| **Extra QMP call per delta attempt** → one read-only call | `test_delta_probe_issued_exactly_once_per_run_backup` (call-history count: exactly one `qemu-monitor-command` per delta `run_backup`, before `backup-begin`); integration tolerates the extra call (real shell, no assertion break). |
| **Probe miss / TOCTOU race** (D9 backstop) | `test_reactive_backstop_deletes_checkpoint_and_retries_once` (UNKNOWN probe then inconsistent `backup-begin` → exactly that checkpoint deleted, retry once as recovered-delta/FULL, second failure follows normal path); `test_backstop_preserves_bitmap_already_exists_recovery` (existing collision branch unchanged — `_is_collision_error` behavior preserved). |
| **Stopped VM + checkpoint** (new probe surface) | `test_probe_stopped_vm_healthy_bitmap_in_intermediate_layer` / `test_probe_stopped_vm_dead_bitmap`; `test_stopped_vm_healthy_checkpoint_still_defers` (defer semantics unchanged); `test_stopped_vm_dead_checkpoint_routes_to_offline_full` (recovery for a stopped VM terminates in the offline FULL branch — `qemu-img convert` from source file, no checkpoint-create). |

---

## Section 5: Synthetic and Integration Test Design

### 5.1 Synthetic/Unit level (MockShell canned fixtures)

**New fixtures under `tests/fixtures/shell_outputs/`:**

| Fixture file | Content / purpose |
|---|---|
| `qmp_block_nodes_healthy.json` | `{"return": [{"node-name": "testvm-vda", "dirty-bitmaps": [{"name": "qsnap-abc12345-vda-20260808T160755-e1eb7a", "inconsistent": false}]}]}` — the disk chain node advertises the checkpoint-named bitmap, `inconsistent: false` → HEALTHY. |
| `qmp_block_nodes_bitmap_missing.json` | `{"return": [{"node-name": "testvm-vda", "dirty-bitmaps": [{"name": "qsnap-abc12345-vda-20260701T000000-aa11bb", "inconsistent": false}]}]}` — chain nodes exist but no node advertises the target checkpoint name → DEAD. |
| `qmp_block_nodes_bitmap_inconsistent.json` | `{"return": [{"node-name": "testvm-vda", "dirty-bitmaps": [{"name": "qsnap-abc12345-vda-20260808T160755-e1eb7a", "inconsistent": true}]}]}` → DEAD. |
| `qmp_error.json` | `{"error": {"class": "GenericError", "desc": "..."}}` plus the command-failure `ShellResult` variant → UNKNOWN. |
| `qemu_img_info_backing_chain_with_bitmaps.json` | Chain array (base → snap1 → snap2), `dirty-bitmaps` present on the **intermediate** layer `snap1` with `inconsistent: false` → stopped-VM HEALTHY. |
| `qemu_img_info_backing_chain_no_bitmaps.json` | Same chain, no `dirty-bitmaps` anywhere → stopped-VM DEAD. |
| `checkpoint_list_mixed.txt` | `qsnap-abc12345-vda-20260720T120000-aa11bb`, `qsnap-abc12345-vda-20260808T160755-e1eb7a`, foreign `manual-one` → newest-wins discovery. |

**Incident replay scenario (unit, `test_two_run_incident_replay_first_heals_second_clean`):**

Setup: state holds `boot_id="boot-A"`; `last_commit_ts[vda]=20260808T160000`; snapshot state lists overlays with timestamps bounding freeze `20260808T160755`; target contains covering file `testvm.20260808T160755_vda_e1eb7a.qcow2` (chain anchored on `testvm.FULL....qcow2`). `MockShell` reads the **current** `boot_id` as `"boot-B"`.

- **Run 1:** `checkpoint-list` → dead checkpoint; QMP → `qmp_block_nodes_bitmap_missing.json` (DEAD); blockjob → "No current block job"; gates: `get_last_commit_ts` (16:00:00 < 16:07:55 → G1 pass), live-chain `qemu-img info --force-share --backing-chain` matches state (G2), per-overlay `qemu-img info` succeeds (G3); `virsh checkpoint-create` (successor) then `qemu-img create -b <newest backup>` + qemu-nbd write server + `MockNbdClient` copy (data+zero extents oldest→newest, `writes` recorded) → `mv` → chain-to-FULL verify + `qemu-img check` → `checkpoint-delete` of the dead checkpoint.
  - Assert: `BackupResult(success=True, kind="recovered_delta")`, WARNING contains checkpoint name, disk, and **"unclean host shutdown detected"**; dead checkpoint deleted; successor checkpoint present with a HEALTHY probe on the next call; old FULL+incrementals **not** deleted; exit 0 when driven through Core.
- **Run 2:** `checkpoint-list` → successor; QMP → `qmp_block_nodes_healthy.json` (HEALTHY) → normal delta `backup-begin` with `incremental=successor`.
  - Assert: `kind=="delta"`, **no WARNING** about the incident, no recovery branch entered.

Additional replay variants: **gate-fail replay** (same fixtures but `last_commit_ts` absent → `test_absent_marker_first_loss_takes_full_then_heals`) and **UNKNOWN-probe replay** (QMP fixture replaced by `qmp_error.json`; `backup-begin` returns "checkpoint inconsistent: missing or broken bitmap 'qsnap-…-e1eb7a'" → backstop deletes exactly that checkpoint and retries once).

### 5.2 Integration level (`tests/integration/`, `@pytest.mark.integration`)

**Producing a REAL dead checkpoint on the disposable `test_vm`** (`tests/integration/conftest.py::test_vm`, 256M qcow2, `SubprocessShell`):

Candidate mechanisms (evaluated):

1. **(a) `virsh destroy` after `backup-begin`** — **NOT recommended as primary.** `backup-begin` + immediate `virsh destroy` is a *controlled* QEMU teardown; QEMU's shutdown path clears the bitmap `in_use` flag and flushes, so the checkpoint bitmap typically survives and the probe stays HEALTHY. It must be verified empirically on the deployed libvirt once; if the deployed version does produce a dead bitmap, prefer it because it is closest to the incident, but do not rely on it.
2. **(b) `kill -9` the QEMU process of the test VM** — **recommended as the high-fidelity power-cut reproduction.** Start VM → `virsh backup-begin` with the checkpoint XML (creates checkpoint + unsynced bitmap) → `domjobabort`/wait, then find the QEMU pid (`virsh qemu-monitor-command --hmp "info status"` or `pgrep -f <vmname>`) and `kill -9`. The `in_use` bit is never cleared; on the next `virsh start` QEMU discards the unsynced bitmap while libvirt checkpoint metadata survives → probe returns DEAD. Caveat: libvirt may auto-delete checkpoints whose bitmaps vanish on reconnect on some versions — assert `virsh checkpoint-list` still shows the checkpoint before proceeding, else fall back to (c).
3. **(c) stop the VM and remove the bitmap directly** — **recommended as the deterministic primary.** Run a normal delta/FULL first so a checkpoint+bitmap exists, `virsh destroy` the VM, then `qemu-img bitmap --remove <top-layer.qcow2> <bitmap-name>` (bitmap name == checkpoint name; locate it via `qemu-img info --output=json`). Restart the VM. This deterministically yields checkpoint-metadata-present + bitmap-absent on **every** libvirt version, which is exactly the incident state, and is the mechanism integration tests should use by default; (b) is added as a separate test for fidelity of the power-cut path.

**New integration test module `tests/integration/test_bitmap_loss_recovery.py`:**

| Test | Mechanism | Asserts |
|---|---|---|
| `test_real_dead_checkpoint_recovered_delta_heals` | (c) bitmap removal; healthy chain state + `last_commit_ts` marker | probe returns DEAD on the real VM (`BitmapBackupProvider` via `SubprocessShell`); recovery produces `kind=="recovered_delta"` (or FULL if a gate fails); exit 0; dead checkpoint gone; next run is a clean delta with **no** WARNING (acceptance criterion). |
| `test_real_dead_checkpoint_power_cut_kill9` | (b) `kill -9` QEMU | same heal assertions on the true power-cut path (skipped with a clear message if the checkpoint does not survive the reconnect). |
| `test_recovery_full_fallback_retires_generation_real` | (c) + force gate failure (remove `last_commit_ts` state, or tamper chain order) | FULL created; M1/M2 pass; superseded generation removed same run despite `keep_generations`; verify-before-delete gates applied; next run clean delta. |
| `test_recovery_full_failed_verification_preserves_old_generation_real` | (c) + corrupt the new FULL transfer (ENOSPC via small target or killed convert) | old generation and dead checkpoint remain; run reports failure via abort path. |
| `test_backstop_heals_when_probe_unknown_real` | (c) + wrap the shell so `qemu-monitor-command` returns an error (UNKNOWN) | `backup-begin` fails "checkpoint inconsistent"; exactly that checkpoint deleted; retry once recovers; exit 0. |
| `test_dry_run_on_dead_checkpoint_predicts_recovery_no_mutation` | (c) then `core.dry_run = True; core.run(vm_name)` | prediction text names recovered-delta/FULL + gate reason; **checkpoint set identical before/after** (`virsh checkpoint-list --name` diff empty); no new target files; state byte-identical. |

**Amendments to existing integration tests:**

- `tests/integration/test_full_backup.py` (TESTING.md's `test_nbd_full_backup.py` successor): FIRST FULL has no prior checkpoint → no probe; add `assert result.kind == "full"` on every `run_backup` success.
- `tests/integration/test_incremental_backup.py`: delta runs now issue one read-only `qemu-monitor-command` before `backup-begin`; add `kind == "delta"`; existing size/dirty-byte assertions unchanged.
- `tests/integration/test_auto_recovery.py` (TESTING.md's `test_stale_state_recovery.py` successor): `test_production_incident_reproduction` and `test_per_chain_retention_multiple_chains_over_time` construct real `BitmapBackupProvider` runs — they gain probe calls (real shell, no breakage) and optional `kind` assertions.
- `tests/integration/test_startup_validation.py`: startup now probes covered checkpoints; the real VM's checkpoints created via `backup-begin` are HEALTHY → keep assertions hold.
- `tests/integration/test_verify_before_delete.py`, `test_backup_retry_max_zero.py`: custom `BackupResult` factories set `kind`.
- `tests/integration/test_dry_run.py`: allowlist + checkpoint-set zero-mutation assertion (3a).

**Dry-run parity integration check (explicit):** in `test_dry_run_on_dead_checkpoint_predicts_recovery_no_mutation`, run `core.dry_run = True` and `core.run(vm_name)` (equivalent to `qsnap -n run`) against the dead-checkpoint VM; assert (1) the prediction log contains the crash WARNING and the exact recovery outcome ("recovered-delta will be created (~…, gates OK)" or "FULL will be created (recovery gate failed: …)"), and (2) `virsh checkpoint-list --name` output is identical before and after, `state.get_snapshots`/`get_full_backups`/`get_deferred_operations` unchanged, and no `checkpoint-delete`/`checkpoint-create`/`backup-begin`/`domjobabort` appears in the recorded command list.

### 5.3 e2e level (one test, `tests/e2e/test_from_config.py`)

`test_config_to_restore_after_recovered_delta`: minimal TOML config + disposable VM; create FULL + delta normally; manufacture a dead bitmap via mechanism (c); run the full pipeline (`qsnap run` equivalent) → recovery warning, exit 0, recovered-delta/FULL on target; restore from the newest backup to a second VM and boot it (reusing `tests/e2e/test_restore.py` helpers). This closes the loop that the recovered backup chain is a valid restore point.

### 5.4 Execution commands (per TESTING.md)

```bash
# Unit + mock + contract (fast, no I/O):
poetry run pytest tests/ -m "not integration and not stress and not e2e"
# New/affected files specifically:
poetry run pytest tests/modules/backup/test_bitmap_recovery.py \
  tests/core/test_recovery_pipeline.py tests/core/test_dry_run_recovery_prediction.py \
  tests/state/test_recovery_state.py tests/interfaces/ -v
# Integration (needs libvirt):
poetry run pytest tests/integration/ -m integration
# End-to-end (needs libvirt + disposable VM):
poetry run pytest tests/e2e/ -m e2e
# Coverage:
poetry run pytest tests/ --cov=qsnap --cov-report=html
```

All new modules are registered under the existing markers (`unit`/`mock`/`contract`/`integration`/`e2e`); `--strict-markers` is satisfied because no new markers are introduced.
