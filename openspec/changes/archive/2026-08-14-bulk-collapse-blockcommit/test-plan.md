# QA Strategy & Test Plan

Scope: change `bulk-collapse-blockcommit` — replace the drip (per-snapshot, capped,
multi-run phase) hysteresis collapse with a single-shot bulk segment `virsh blockcommit`.
All conventions follow `TESTING.md` (unit/mock/contract/integration/stress/e2e categories,
directory mirroring, mock-first isolation, exact-string pinning for observability).

Conventions used below:
- `NEW` — test does not exist yet; implementer creates it (name given).
- `MODIFY` — existing test/function must be edited (assertions, wording, dropped args).
- `KEEP` — existing test stays unchanged (listed for coverage completeness).
- `DELETE` — existing test asserts removed behavior; must be removed (full list in
  "Tests To Delete"; functions deleted from partially-edited files are listed there too).
- Groups are kebab-case and partition test FILES disjointly (each file in exactly one group).

---

## Coverage Map

One row per `#### Scenario:` in the 8 delta specs (48 total) plus rows derived from the
REMOVED-requirement text of `state-management` (which contains no scenarios).

### hysteresis-retention (`specs/hysteresis-retention/spec.md`)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| hysteresis-retention | Hysteresis retention mode selection | Default mode is hysteresis | `tests/core/test_hysteresis_retention.py` | `test_default_mode_is_hysteresis_single_run_collapse` (NEW) | hysteresis-core |
| hysteresis-retention | Hysteresis retention mode selection | Hysteresis mode reinterprets the knobs | `tests/core/test_hysteresis_retention.py` | `test_hysteresis_mode_interprets_chain_length_as_threshold_floor` (MODIFY) | hysteresis-core |
| hysteresis-retention | Grow phase below the trigger threshold | Chain at threshold does not commit | `tests/core/test_hysteresis_retention.py` | `test_chain_at_threshold_commits_nothing` (MODIFY) | hysteresis-core |
| hysteresis-retention | Grow phase below the trigger threshold | Growth accumulates without commits | `tests/core/test_hysteresis_retention.py` | `test_growth_phase_accumulates_without_commits` (MODIFY) | hysteresis-core |
| hysteresis-retention | Collapse trigger and floor | Trigger fires above threshold | `tests/core/test_hysteresis_retention.py` | `test_trigger_marks_all_oldest_n_minus_l` (MODIFY of `test_trigger_marks_oldest_n_minus_l_before_cap`) | hysteresis-core |
| hysteresis-retention | Collapse trigger and floor | Trigger fires above threshold (all merged same run) | `tests/core/test_hysteresis_retention.py` | `test_trigger_collapse_merges_all_49_in_one_run` (NEW — drives `_blockcommit_snapshots`, asserts ONE manager call with the full 49-item set) | hysteresis-core |
| hysteresis-retention | Collapse trigger and floor | Floor snapshots are never committed | `tests/core/test_hysteresis_retention.py` | `test_floor_snapshots_never_in_remove_set` (MODIFY — drop cap truncation) | hysteresis-core |
| hysteresis-retention | Collapse trigger and floor | Deferred collapse re-triggers naturally | `tests/core/test_hysteresis_retention.py` | `test_deferred_collapse_retriggers_naturally_without_phase` (NEW — deferred run leaves N>H; next run marks the identical oldest N−L set; no phase read/write) | hysteresis-core |
| hysteresis-retention | Hysteresis observability | Trigger logs the collapse | `tests/core/test_hysteresis_retention.py` | `test_trigger_logs_collapse_initiation_info_line` (NEW — pins INFO line naming `testvm`, `vda`, 49, 73, and 24; see deliverable note re wording ambiguity) | hysteresis-core |

### lifecycle-manager (`specs/lifecycle-manager/spec.md`)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Successful live bulk collapse of a multi-snapshot segment | `tests/modules/lifecycle/test_blockcommit.py` | `test_bulk_blockcommit_single_segment_command` (NEW — 49-item set; asserts exactly ONE `virsh blockcommit` process, `--top <49th/newest path>`, `CommitResult(success=True, committed_snapshot=<newest name>, outcome="success")`) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Single-snapshot merge set degenerates to the same command | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_single_snapshot_success` (MODIFY — add explicit `--top == snap.path` for the degenerate case) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Live blockcommit fails — virsh returns error | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_virsh_error` (KEEP) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Blockcommit blocked by AppArmor or SELinux | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_blocked_by_apparmor`, `test_blockcommit_blocked_by_selinux` (KEEP) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Offline commit pivots child and deletes file | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_qemu_img_commit_pivots_child_and_deletes` (KEEP) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Offline commit of chain tip-of-subset without child | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_qemu_img_commit_no_child_skips_rebase` (KEEP) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Offline commit failure short-circuits safely | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_qemu_img_commit_failure_no_delete_short_circuit`, `test_qemu_img_rebase_failure_keeps_file` (KEEP) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Empty snapshot list — nothing to merge | `tests/modules/lifecycle/test_blockcommit.py` + `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_blockcommit_empty_list_no_op`, `test_qemu_img_commit_empty_list_no_op` (KEEP) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Blockcommit times out — unknown outcome | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_timeout_returns_unknown` (KEEP — pins `outcome="unknown"`, error `Command timed out after 1800s`, never `"failure"`) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Injected timeout is honored | `tests/modules/lifecycle/test_blockcommit.py` | `test_bulk_blockcommit_scaled_timeout_forwarded` (NEW — 49-item set, injected timeout 1800, shell records `run_with_heartbeat(timeout=88200)`); `test_blockcommit_injected_timeout_honored` (KEEP) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Heartbeat callback invoked during the wait | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_heartbeat_callback_elapsed` (MODIFY — batch wording, single-layer set pins `[blockcommit] {vm}/{disk}: still collapsing 1 layer into base ({elapsed}s elapsed)` — singular noun for n=1) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Successful blockcommit with deep verify passing | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_deep_verify_passes` (KEEP) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Successful blockcommit but deep verify fails | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_deep_verify_fails_corruptions` (KEEP) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | deep_verify=False — no check performed | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_deep_verify_false_no_check` (KEEP) | lifecycle-manager |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Deep verify on the disk's base image (not a VM-level base) | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_multi_disk_uses_correct_disk_base` (KEEP) + `test_qemu_img_commit_deep_verify_targets_disk_base` (KEEP) | lifecycle-manager |
| lifecycle-manager | Blockcommit of multiple snapshots | Live path merges a multi-snapshot set in one job | `tests/modules/lifecycle/test_blockcommit.py` | `test_bulk_segment_command_top_is_newest` (NEW — argv-exact: `--domain`, `--path vda`, `--base <base>`, `--top <snap49.path>`, `--delete --verbose --wait`, single process) | lifecycle-manager |
| lifecycle-manager | Blockcommit of multiple snapshots | Live path merges a multi-snapshot set in one job (real chain) | `tests/integration/test_bulk_blockcommit_real_chain.py` | `test_segment_commit_shrinks_real_chain_by_merge_set_size` (NEW — integration: chain delta == merge-set size, intermediate files deleted, newest L + active layer preserved) | integration-hysteresis |
| lifecycle-manager | Blockcommit of multiple snapshots | Offline path merges sequentially without a cap | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_qemu_img_commit_uncapped_batch_processes_all` (NEW — 49-item set: 49 `qemu-img commit` calls, all processed; failure at #10 → 11–49 unprocessed, first 9 deletions stand) | lifecycle-manager |

### core-orchestrator (`specs/core-orchestrator/spec.md`)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| core-orchestrator | Scaled timeout budget for bulk collapse | Budget scales with the merge set | `tests/core/test_pipeline.py` | `test_live_collapse_timeout_scaled_by_merge_set` (NEW — `blockcommit_timeout=1800`, 49-item set, mock manager receives `timeout=88200`) | commit-core |
| core-orchestrator | Scaled timeout budget for bulk collapse | Offline budget stays per layer | `tests/core/test_pipeline.py` | `test_offline_collapse_timeout_unscaled_per_layer` (NEW — qemu-img manager receives unscaled `1800`; each `qemu-img commit` call gets 1800) | commit-core |
| core-orchestrator | Pre-commit chain-length baseline derived from the integrity scan | Baseline reused from the scan | `tests/core/test_pipeline.py` | `test_chain_length_baseline_reused_from_scan` (NEW — `chain_verify_before_commit=True`, pre-commit scan returns `chain_length=73`; spy asserts `_get_chain_length` NOT called; baseline 73 reaches reconciliation) | commit-core |
| core-orchestrator | Pre-commit chain-length baseline derived from the integrity scan | Baseline reused from the scan (model field) | `tests/models/test_results.py` | `test_chain_verify_result_chain_length_field_default_none` (NEW/MODIFY — `ChainVerifyResult.chain_length` additive field, default `None`) | models-contracts |
| core-orchestrator | Pre-commit chain-length baseline derived from the integrity scan | Fallback when verification is disabled | `tests/core/test_pipeline.py` | `test_chain_length_baseline_fallback_when_verify_disabled` (NEW — `chain_verify_before_commit=False` → `_get_chain_length` issues its own `qemu-img info --backing-chain` walk) | commit-core |
| core-orchestrator | Hysteresis retention evaluation flow | Steady mode untouched | `tests/core/test_hysteresis_retention.py` | `test_steady_mode_branch_identical_to_legacy` (MODIFY — drop cap/phase assertions, keep remove==oldest-49 / keep==newest-24) | hysteresis-core |
| core-orchestrator | Hysteresis retention evaluation flow | Hysteresis collapse evaluation | `tests/core/test_hysteresis_retention.py` | `test_trigger_marks_all_oldest_n_minus_l` (MODIFY) + `test_collapse_writes_no_phase_state` (NEW — asserts engine invoked with keep-count 24, remove set == all 49, and no `collapse_in_progress` key ever written) | hysteresis-core |
| core-orchestrator | Hysteresis retention evaluation flow | Below threshold | `tests/core/test_hysteresis_retention.py` | `test_below_threshold_remove_set_empty` (MODIFY of `test_below_threshold_inactive_phase_no_phase_write`) | hysteresis-core |

### config-model (`specs/config-model/spec.md`)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| config-model | Removed max_commits_per_run key is rejected loudly | Legacy config line fails startup | `tests/config/test_facade.py` | `test_max_commits_per_run_legacy_key_rejected` (NEW — `ConfigError` message names `max_commits_per_run` AND states the collapse is a single uncapped bulk blockcommit; applies at global level; also rejected at VM level with `[global]` hint) | config-model |
| config-model | Removed max_commits_per_run key is rejected loudly | Absent key loads normally | `tests/config/test_facade.py` | `test_absent_max_commits_key_loads_normally` (NEW — parse succeeds; `GlobalConfig` has no `max_commits_per_run` attribute) | config-model |
| config-model | GlobalConfig default values | GlobalConfig default values | `tests/config/test_model.py` | `test_global_config_defaults_without_cap_field` (NEW — pins the documented defaults incl. `blockcommit_timeout=1800`, `snapshot_retention_mode="hysteresis"`; asserts `not hasattr(cfg, "max_commits_per_run")`) | config-model |

### state-management (`specs/state-management/spec.md` — REMOVED requirement, no scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| state-management | collapse_in_progress phase key (removed) | Interface no longer declares the 3 phase methods (REMOVED text) | `tests/interfaces/test_state_manager.py` | `test_istate_manager_has_no_collapse_phase_methods` (NEW — negative contract: the three names absent from `IStateManager.__abstractmethods__`) | state-management |
| state-management | collapse_in_progress phase key (removed) | Mock no longer implements the 3 phase methods (REMOVED text) | `tests/mocks/test_mock_state.py` | `test_inmemory_state_manager_has_no_collapse_methods` (NEW — mock parity for the interface shrinkage) | state-management |
| state-management | collapse_in_progress phase key (removed) | Stale persisted key tolerated, never read or rewritten (REMOVED text) | `tests/state/test_manager.py` | `test_stale_collapse_in_progress_key_tolerated_on_load` (MODIFY of `test_collapse_in_progress_key_tolerated_on_load` — state file with `collapse_in_progress: ["vda"]` loads cleanly; existing state intact; no rewrite on read; no reader method exists) | state-management |
| state-management | collapse_in_progress phase key (removed) | `reset_vm_state` / `reset_vm_disk_state` stop touching the key (REMOVED text) | `tests/state/test_manager.py` | `test_reset_vm_state_leaves_stale_collapse_key_untouched` (NEW — stale key survives reset byte-for-byte because nothing writes the key anymore) | state-management |

### dry-run-prediction (`specs/dry-run-prediction/spec.md`)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| dry-run-prediction | Hysteresis retention prediction in dry-run | Grow phase predicts no commits | `tests/core/test_dry_run_prediction.py` | `test_hysteresis_grow_phase_predicts_no_commits` (MODIFY — drop phase assertions; keep: no blockcommit prediction, no `virsh blockcommit`, state byte-identical) | hysteresis-core |
| dry-run-prediction | Hysteresis retention prediction in dry-run | Collapse prediction names the full uncapped set | `tests/core/test_dry_run_prediction.py` | `test_hysteresis_collapse_prediction_names_full_uncapped_set` (NEW — replaces capped variant; N=73/H=72/L=24: one per-disk prediction naming ALL 49 oldest; newest 24 never named; no lifecycle-manager call; state byte-identical) | hysteresis-core |
| dry-run-prediction | Hysteresis retention prediction in dry-run | Prediction below threshold stays silent even above floor | `tests/core/test_dry_run_prediction.py` | `test_hysteresis_prediction_silent_between_floor_and_threshold` (NEW — N=60, L=24, H=72: no prediction; state byte-identical) | hysteresis-core |

### commit-observability (`specs/commit-observability/spec.md`)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| commit-observability | Commit intent log before the manager call | Intent line precedes every commit attempt | `tests/core/test_pipeline.py` | `test_intent_info_log_precedes_commit` (MODIFY — pins exact new line `[blockcommit] testvm/vda: collapsing 49 snapshot(s) into /var/lib/libvirt/images/testvm.qcow2 (mode=virsh, timeout=88200s)` emitted before `virsh blockcommit` spawns) | commit-core |
| commit-observability | Commit intent log before the manager call | Drain path also logs intent | `tests/core/test_deferred.py` | `test_drain_path_logs_intent_line` (MODIFY — wording `committing` → `collapsing`; scaled timeout on live drain) | commit-core |
| commit-observability | Heartbeat during live commit waits | Heartbeat lines appear during a long collapse | `tests/modules/lifecycle/test_blockcommit.py` | `test_heartbeat_lines_during_long_commit` (MODIFY — `heartbeats=2`, pins `still collapsing {n} layers into base (60s elapsed)` then `(120s elapsed)`, each naming VM, disk, layer count) | lifecycle-manager |
| commit-observability | Heartbeat during live commit waits | Fast collapse produces no heartbeat | `tests/modules/lifecycle/test_blockcommit.py` | `test_fast_commit_no_heartbeat_lines` (MODIFY — heartbeat filter wording; fast job logs no heartbeat and result logs normally) | lifecycle-manager |

### count-based-retention (`specs/count-based-retention/spec.md`)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| count-based-retention | Count-based retention policy | Snapshot chain length triggers blockcommit (steady mode) | `tests/modules/retention/test_time_based.py` | `test_snapshot_chain_length_triggers_removal` (KEEP) + `tests/core/test_preserve.py::test_preserve_min_applied_after_oldest_prefix` (KEEP — pins the contiguous oldest-prefix post-processing) | retention-engine |
| count-based-retention | Count-based retention policy | Snapshot chain length not exceeded | `tests/modules/retention/test_time_based.py` | `test_snapshot_count_within_chain_length_keeps_all` (KEEP) | retention-engine |
| count-based-retention | Count-based retention policy | Hysteresis mode defers to the hysteresis capability | `tests/core/test_hysteresis_retention.py` | `test_hysteresis_uses_threshold_floor_not_steady_rule` (MODIFY of `test_hysteresis_uses_threshold_floor_phase_not_steady_rule` — N=50 in band: empty remove, steady rule NOT applied; above H: full N−L collapse) | hysteresis-core |
| count-based-retention | Count-based retention policy | Target chain length triggers new FULL | `tests/core/test_full_anchor.py` | `test_incremental_count_exceeds_chain_length_triggers_full` (KEEP) + `test_full_verification_pipeline.py` M1/M2 verify-before-delete (KEEP) | retention-engine |
| count-based-retention | Count-based retention policy | Target keep generations limits chains | `tests/modules/retention/test_time_based.py` | `test_keep_generations_limits_chains` (KEEP) | retention-engine |
| count-based-retention | Count-based retention policy | First backup to target creates FULL | `tests/core/test_full_anchor.py` | `test_first_backup_creates_full` (KEEP) | retention-engine |

---

## Delegation Groups

Non-overlapping partitions of test FILES for parallel execution; a pre-step group
(`shared-fixtures`) must land first because every other group depends on the updated
`conftest.py` and the fixed `hysteresis_mode.toml` fixture.

### `shared-fixtures` (pre-step)
Scope: shared test scaffolding touched by the option/phase removal.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/conftest.py` | 0 (scenarios) | MODIFY |
| `tests/helpers.py` | 0 | KEEP |
| `tests/fixtures/configs/hysteresis_mode.toml` | 0 | MODIFY |

### `config-model`
Scope: `max_commits_per_run` removal, loud rejection, defaults.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/config/test_facade.py` | 2 | MODIFY |
| `tests/config/test_model.py` | 1 | MODIFY |
| `tests/config/test_resolver.py` | 0 | MODIFY |
| `tests/config/test_fixtures.py` | 0 | MODIFY |
| `tests/config/test_unknown_keys.py` | 0 | KEEP (must still pass: removed key becomes a *known-but-rejected* key, not "Unknown key") |

### `state-management`
Scope: `IStateManager`/`JsonStateManager`/`InMemoryStateManager` shrinkage, stale-key tolerance.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/state/test_manager.py` | 2 | MODIFY |
| `tests/state/test_recovery_state.py` | 0 | KEEP |
| `tests/interfaces/test_state_manager.py` | 1 | MODIFY |
| `tests/mocks/mock_state.py` | 0 (impl file) | MODIFY |
| `tests/mocks/test_mock_state.py` | 1 | MODIFY |
| `tests/mocks/test_mock_validity.py` | 0 | MODIFY |

### `lifecycle-manager`
Scope: `BlockCommitManager` single-segment command, heartbeat wording, offline uncapped batch.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/modules/lifecycle/test_blockcommit.py` | 11 | MODIFY |
| `tests/modules/lifecycle/test_qemu_img_commit.py` | 5 | MODIFY |
| `tests/interfaces/test_lifecycle_manager.py` | 0 | KEEP (signature unchanged; parametrization already covers `timeout`/`deep_verify`) |

### `hysteresis-core`
Scope: retention evaluation branch (single-run collapse, no phase, no cap), dry-run predictions.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/core/test_hysteresis_retention.py` | 11 | MODIFY |
| `tests/core/test_preserve.py` | 1 | MODIFY |
| `tests/core/test_dry_run_prediction.py` | 3 | MODIFY |
| `tests/core/test_dry_run_recovery_prediction.py` | 0 | MODIFY |

### `commit-core`
Scope: scaled timeout wiring, baseline-from-scan, intent/heartbeat observability wiring, reconciliation/deferral interaction with bulk jobs.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | 4 | MODIFY |
| `tests/core/test_deferred.py` | 1 | MODIFY |
| `tests/core/test_engine.py` | 0 | MODIFY |
| `tests/core/test_reconcile.py` | 0 | KEEP |
| `tests/core/test_recovery_pipeline.py` | 0 | KEEP (intent-journal recovery, unchanged machinery) |

### `retention-engine`
Scope: pure count-based engine + FULL-anchor behavior (cross-reference wording only).

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/modules/retention/test_time_based.py` | 3 | KEEP |
| `tests/interfaces/test_retention_engine.py` | 0 | KEEP |
| `tests/core/test_full_anchor.py` | 2 | KEEP |
| `tests/core/test_full_verification_pipeline.py` | 0 | KEEP |

Note: there is no `tests/core/test_retention_policy.py` in this repo — the count-based
policy coverage lives in `tests/modules/retention/test_time_based.py` (engine level) and
`tests/core/test_preserve.py` (preserve-floor level, owned by `hysteresis-core`).

### `models-contracts`
Scope: `ChainVerifyResult.chain_length` additive field; factory/mock contracts.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/models/test_results.py` | 1 | MODIFY |
| `tests/interfaces/test_factory.py` | 0 | KEEP |
| `tests/factory/test_default.py` | 0 | KEEP |
| `tests/mocks/test_mock_factory.py` | 0 | KEEP |
| `tests/mocks/test_mock_config.py` | 0 | KEEP |
| `tests/mocks/test_mock_shell.py` | 0 | KEEP |

### `integration-hysteresis`
Scope: real-libvirt verification of segment commits, error-path realism, crash recovery, dry-run on real chains. Runs only with `-m integration` (TESTING.md §4).

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/integration/test_hysteresis_retention.py` | 1 | MODIFY (rewrite to single-run bulk) |
| `tests/integration/test_dry_run.py` | 0 | MODIFY |
| `tests/integration/test_blockcommit_defer.py` | 0 | MODIFY (log wording only) |
| `tests/integration/test_commit_intent_recovery.py` | 0 | KEEP (partial-prefix test already uses offline convergence) |
| `tests/integration/test_auto_recovery.py` | 0 | KEEP |
| `tests/integration/test_bulk_blockcommit_real_chain.py` | 1 | NEW |
| `tests/integration/test_blockcommit_error_realism.py` | 0 | NEW |
| `tests/integration/test_bulk_crash_recovery.py` | 0 | NEW |

### `stress`
Scope: deep real chains, single-job segment collapse, lockfile contention during bulk job. Runs only with `-m stress` (TESTING.md §5).

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/stress/test_long_chain.py` | 0 | MODIFY (drop capped hysteresis test; add bulk equivalent) |
| `tests/stress/test_bulk_segment_commit.py` | 0 | NEW |
| `tests/stress/test_concurrent.py` | 0 | KEEP |
| `tests/stress/test_enospc.py` | 0 | KEEP |

### `e2e`
Scope: grow-phase journeys must not regress.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/e2e/test_from_config.py` | 0 | KEEP |
| `tests/e2e/test_restore.py` | 0 | KEEP |

---

## Test Modifications

Existing tests that must be UPDATED (functions deleted from these files are listed in
"Tests To Delete"; `KEEP` files need no change).

| File | Change | Reason |
|---|---|---|
| `tests/conftest.py` | Remove `max_commits_per_run: int = 12` parameter and its `GlobalConfig(...)` kwarg from `make_global_config` (lines ~337/366) | Option removed; the fixture would otherwise fail construction. |
| `tests/fixtures/configs/hysteresis_mode.toml` | Delete the `max_commits_per_run = 4` line (optionally add a new `legacy_max_commits.toml` fixture containing it for the rejection test) | Fixture must keep parsing under the new validator (config-model "Absent key loads normally"). |
| `tests/config/test_fixtures.py` | `test_hysteresis_mode_fixture_parses`: drop `global_cfg.max_commits_per_run == 4`; assert the attribute does not exist | Ditto. |
| `tests/config/test_model.py` | `test_make_global_config_accepts_hysteresis_kwargs`: drop `max_commits_per_run=0`; add `not hasattr(cfg, "max_commits_per_run")` | Option removed from `GlobalConfig` (config-model). |
| `tests/config/test_resolver.py` | `test_max_commits_per_run_is_global_only` → replaced by the loud-rejection test in `test_facade.py`; the remaining resolver coverage needs no other edit | Key no longer parses anywhere. |
| `tests/config/test_unknown_keys.py` | `test_all_fixture_configs_parse_without_unknown_key_errors`: confirm the removed key raises the dedicated "removed option" `ConfigError`, NOT the generic "Unknown key" message | Rejection wording contract (config-model "Legacy config line fails startup"). |
| `tests/core/test_hysteresis_retention.py` | `test_hysteresis_mode_interprets_chain_length_as_threshold_floor`, `test_chain_at_threshold_commits_nothing`, `test_growth_phase_accumulates_without_commits`, `test_trigger_marks_all_oldest_n_minus_l`, `test_floor_snapshots_never_in_remove_set`, `test_hysteresis_collapse_respects_floor`, `test_steady_mode_branch_identical_to_legacy`, `test_below_threshold_inactive_phase_no_phase_write`: drop `max_commits_per_run=` kwargs and all `get_collapse_in_progress(...)` / "collapse phase" assertions; `test_trigger_logs_collapse_start_info` rewritten to the new collapse-initiation wording; `test_hysteresis_uses_threshold_floor_phase_not_steady_rule` keeps only the mid-band grow assertion (phase-driven below-threshold collapse is gone) | Cap and phase machinery removed (hysteresis-retention/core-orchestrator). |
| `tests/core/test_preserve.py` | `test_default_preserve_min_48_keeps_newest_48`: remove the second "default cap 12 truncates to 12" block (lines ~446–468); `test_preserve_min_no_trim_when_within_limit`: replace `GlobalConfig(max_commits_per_run=0)` with plain `GlobalConfig()` | Steady mode loses the shared cap (core-orchestrator "Steady mode untouched"). |
| `tests/core/test_dry_run_prediction.py` | `test_hysteresis_grow_phase_predicts_no_commits`: drop `mock_state.get_collapse_in_progress(...)` assertion; keep byte-identical state check | Phase key removed (dry-run-prediction). |
| `tests/core/test_dry_run_recovery_prediction.py` | `test_dry_run_intent_recovery_does_not_write_collapse_phase`: strip the persisted-phase precondition and all phase assertions; keep the stale-intent zero-mutation coverage (intent journal still exists) | Phase removed; intent-recovery behavior unchanged. |
| `tests/core/test_pipeline.py` | `test_configured_timeout_reaches_manager`, `test_intent_info_log_precedes_commit`: update expected intent line to `collapsing {n} snapshot(s)` and the success line to `collapsed {n} snapshot(s)`; scaled-timeout variants added as NEW tests (see Coverage Map) | D9 observability wording + scaled budget (commit-observability/core-orchestrator). |
| `tests/core/test_engine.py` | `test_snapshot_delete_info_log`: `"merged" in caplog.text` → `"collapsed" in caplog.text` | D9 success wording. |
| `tests/core/test_deferred.py` | `test_drain_path_logs_intent_line`: expected string `committing 1 snapshot(s)` → `collapsing 1 snapshot(s)` (and scaled timeout on the live drain path) | D9 wording; drain-path intent line (commit-observability "Drain path also logs intent"). |
| `tests/modules/lifecycle/test_blockcommit.py` | Heartbeat tests (`test_blockcommit_heartbeat_callback_elapsed`, `test_heartbeat_lines_during_long_commit`, `test_fast_commit_no_heartbeat_lines`): `still merging {snap.name} into base` → `still collapsing {n} layer(s) into base` (singular `layer` for n=1); heartbeat filters updated; module docstring section 7 rewritten from "sequential (design D4)" to "single segment command (design D1)" | Heartbeat wording (commit-observability); bulk command (lifecycle-manager). |
| `tests/modules/lifecycle/test_qemu_img_commit.py` | Docstring: offline path remains per-layer but uncapped; no assertion changes to existing tests | D8. |
| `tests/interfaces/test_state_manager.py` | Remove the two collapse contract tests (see Tests To Delete); add `test_istate_manager_has_no_collapse_phase_methods` | Interface shrinkage (state-management). |
| `tests/mocks/mock_state.py` | Remove `get/set/clear_collapse_in_progress` (lines ~447–478), the `vm_state["collapse_in_progress"] = []` write in `reset_vm_state` (line ~289), and the per-disk filter in `reset_vm_disk_state` (lines ~327–329) | Implementation of the removed interface methods (state-management). |
| `tests/mocks/test_mock_validity.py` | Remove `test_inmemory_state_manager_collapse_methods_present`; ensure the generic abstract-method enumeration no longer lists the three phase methods | Mock parity (state-management). |
| `tests/models/test_results.py` | `test_chain_verify_result_*` (line ~1184): add assertion that the new additive `chain_length: int \| None` field defaults to `None` and is frozen | D7 (core-orchestrator "Baseline reused from the scan"). |
| `tests/integration/test_hysteresis_retention.py` | Rewrite file: `_build_core` drops `cap`/`max_commits_per_run` and the `_collapse_phase_from_file` helper; `test_hysteresis_default_mode_no_phase_below_threshold_real_chain` drops "state file never contains collapse_in_progress" assertions; `test_hysteresis_dry_run_zero_mutation_real_chain` asserts the prediction names the FULL `N−L` set; `test_hysteresis_multi_run_collapse_real_chain` is replaced by a single-run bulk test (below) | Drip/phase semantics removed; real-chain verification must match the new single-job behavior. |
| `tests/integration/test_dry_run.py` | `_build_hysteresis_core` drops `cap`; `test_dry_run_hysteresis_collapse_zero_mutation` no longer seeds a phase, asserts predicted batch == full `N−L` set (uncapped), and drops the persisted-phase byte-comparison precondition | dry-run-prediction "Collapse prediction names the full uncapped set". |
| `tests/integration/test_blockcommit_defer.py` | Lines ~391–393 and ~674–676: intent-line assertion `"committing" in m` → `"collapsing" in m` (mode substrings unchanged) | D9 wording (integration parity). |
| `tests/stress/test_long_chain.py` | `test_hysteresis_long_chain_capped_collapse` → replaced by `test_hysteresis_long_chain_bulk_collapse` (single run, N=66→48, ONE `virsh blockcommit` job, chain delta exactly 18, floor files survive, VM running); module docstring "capped prune cycles" wording updated | Stress suite must exercise the new single-job expectation instead of drip semantics. |
| `tests/e2e/*` | No changes (grow-phase journeys only) — listed for awareness | None; verify no regression after the option removal (config used by `e2e` fixtures must not set the removed key). |

---

## Tests To Delete

Every existing test that asserts REMOVED behavior: per-run cap batching,
`collapse_in_progress` phase transitions, started/active/complete log lines, the
per-snapshot `virsh blockcommit` loop on the live path, and `max_commits_per_run`
parsing/defaults.

| File (or test function) | Why obsolete |
|---|---|
| `tests/core/test_hysteresis_retention.py::test_default_hysteresis_mode_no_phase_state_written` | Asserts `get_collapse_in_progress() == []` and "collapse phase" absent — phase API removed |
| `tests/core/test_hysteresis_retention.py::test_phase_persists_after_capped_run_continues_next_run` | Multi-run phase continuation under the cap — removed |
| `tests/core/test_hysteresis_retention.py::test_phase_cleared_when_floor_reached` | Phase clear at floor — removed |
| `tests/core/test_hysteresis_retention.py::test_phase_persisted_before_first_blockcommit` | Phase marker written before commit — removed |
| `tests/core/test_hysteresis_retention.py::test_defensive_phase_clear_on_external_shrink` | Phase defensive clear — removed |
| `tests/core/test_hysteresis_retention.py::test_cap_truncates_collapse_keeps_oldest` | Cap truncation — removed |
| `tests/core/test_hysteresis_retention.py::test_cap_zero_unlimited` | `max_commits_per_run = 0` semantics — option removed |
| `tests/core/test_hysteresis_retention.py::test_cap_never_breaks_floor` | Cap/floor interaction — removed |
| `tests/core/test_hysteresis_retention.py::test_collapse_evaluation_engine_floor_and_cap` | Asserts cap applied after floor trim — removed |
| `tests/core/test_hysteresis_retention.py::test_collapse_complete_info_logged_at_floor` | Asserts `"collapse phase complete (N=24, floor=24)"` — removed |
| `tests/core/test_hysteresis_retention.py::test_cap_reached_keeps_phase_logs_continuation` | Asserts `"collapse phase active (N=100, committing 12 of 76, floor=24)"` — removed |
| `tests/core/test_hysteresis_retention.py::test_migration_deep_chain_converges_over_capped_runs` | 5-run capped migration convergence — replaced by single-run convergence |
| `tests/core/test_hysteresis_retention.py::test_phase_resumes_after_crash_between_set_and_commit` | Crash recovery via phase marker — replaced by intent-journal recovery (already covered in `test_recovery_pipeline.py`) |
| `tests/core/test_hysteresis_retention.py::test_phase_remains_after_deferred_commit` | Deferral keeps phase alive — replaced by `test_deferred_collapse_retriggers_naturally_without_phase` |
| `tests/core/test_hysteresis_retention.py::test_hysteresis_mode_phase_cap_matrix` | Parametrized mode × phase × cap matrix — phase and cap removed |
| `tests/core/test_dry_run_prediction.py::test_hysteresis_collapse_prediction_capped_oldest` | Predicts `min(N−L, cap)`; cap removed (replaced by full-set prediction test) |
| `tests/core/test_dry_run_prediction.py::test_hysteresis_phase_drives_prediction_below_threshold` | Persisted phase drives below-threshold prediction — phase removed |
| `tests/core/test_dry_run_recovery_prediction.py::test_dry_run_intent_recovery_does_not_write_collapse_phase` | Entirely about the phase key; intent-recovery zero-mutation survives in other intent tests |
| `tests/core/test_preserve.py::test_steady_mode_cap_truncates_remove_list` | Steady mode cap truncation — cap removed |
| `tests/modules/lifecycle/test_blockcommit.py::test_blockcommit_multiple_snapshots_sequential` | Asserts one `virsh blockcommit` per snapshot + short-circuit loop — replaced by single segment command |
| `tests/interfaces/test_state_manager.py::test_istate_manager_collapse_in_progress_methods_abstract` | Asserts the three phase methods are abstract on `IStateManager` — removed |
| `tests/interfaces/test_state_manager.py::test_contract_collapse_in_progress_methods_abstract` | Parametrized phase round-trip contract over both state managers — removed |
| `tests/state/test_manager.py::test_collapse_in_progress_missing_key_reads_empty` | `get_collapse_in_progress` reader — removed |
| `tests/state/test_manager.py::test_collapse_in_progress_round_trip_atomic` | `set/get/clear_collapse_in_progress` round-trip — removed |
| `tests/state/test_manager.py::test_reset_vm_state_clears_collapse_in_progress` | Reset clears phase key — removed (replaced by leave-untouched test) |
| `tests/state/test_manager.py::test_reset_vm_disk_state_removes_one_disk` | Per-disk reset removes phase entry — removed |
| `tests/mocks/test_mock_state.py::test_inmemory_collapse_in_progress_round_trip` | Mock phase round-trip — removed |
| `tests/mocks/test_mock_state.py::test_inmemory_reset_vm_state_clears_collapse_in_progress` | Mock reset clears phase — removed |
| `tests/mocks/test_mock_state.py::test_inmemory_reset_vm_disk_state_removes_one_disk` | Mock per-disk reset — removed |
| `tests/mocks/test_mock_validity.py::test_inmemory_state_manager_collapse_methods_present` | Mock must implement the phase methods — removed |
| `tests/config/test_facade.py::test_max_commits_per_run_negative_rejected` | `-1` rejected with "must be >= 0" — any presence now raises the removal error |
| `tests/config/test_facade.py::test_max_commits_per_run_zero_accepted` | `0 = unlimited` accepted — option removed |
| `tests/config/test_facade.py::test_max_commits_per_run_non_integer_rejected` | Non-integer rejected — option removed |
| `tests/config/test_model.py::test_global_config_max_commits_per_run_default_12` | Default 12 — option removed |
| `tests/config/test_model.py::test_global_config_max_commits_per_run_immutable` | Field immutability — option removed |
| `tests/config/test_resolver.py::test_max_commits_per_run_is_global_only` | Global-only resolution + VM-level rejection hint — key no longer resolves anywhere |
| `tests/integration/test_hysteresis_retention.py::test_hysteresis_multi_run_collapse_real_chain` | Real-chain multi-run capped collapse with phase persistence — replaced by single-run bulk test |
| `tests/stress/test_long_chain.py::test_hysteresis_long_chain_capped_collapse` | Stress capped multi-cycle collapse with phase persistence — replaced by `test_hysteresis_long_chain_bulk_collapse` |

---

## Risks & Edge Cases

From `design.md` "Risks / Trade-offs" → dedicated test proposals.

| Risk (design.md) | Test proposal | Location / Group |
|---|---|---|
| Long bulk job holds the lockfile; hourly timer runs exit 3 until it finishes | Unit: `test_lock_held_during_bulk_commit` — with the mock shell scripting a slow blockcommit, assert the run stays inside the exclusive-lock window and a second Core run path fails closed with the documented exit-3 message; stress: extend `tests/stress/test_concurrent.py` with `test_second_run_during_bulk_job_exits_3` using a throttled real `virsh blockcommit --bandwidth` | `tests/core/test_pipeline.py` (commit-core) / `tests/stress/test_concurrent.py` (stress) |
| All-or-nothing: a failure at 95% retries the whole segment | Unit: failure/timeout mapping unchanged — `test_blockcommit_virsh_error` (KEEP), `test_blockcommit_timeout_returns_unknown` (KEEP); NEW `test_bulk_timeout_routes_to_reconcile_deferral` — timeout on the bulk job keeps the intent, reconciliation sees `job_active` → deferral re-queues the full set; next run re-triggers identically | `tests/core/test_recovery_pipeline.py` (commit-core) |
| libvirt `--delete` correctness on deep segments | Integration (NEW `tests/integration/test_bulk_blockcommit_real_chain.py`): create a real 9-layer chain (H=8, L=3), run ONE prune; assert (a) exactly ONE `virsh blockcommit` with `--base <base> --top <newest removable> --delete` executed (recording shell), (b) chain length shrank by exactly the merge-set size (6), (c) every intermediate file deleted, (d) newest L=3 files + active layer intact, (e) VM still running and disk writable (`virsh domstate` running; write test via `guest`-free `virsh qemu-monitor-command` `write` on a test file or `qemu-img check` on the active layer) | `tests/integration/test_bulk_blockcommit_real_chain.py` (integration-hysteresis) |
| Base image grows during the job; target FS fills | Offline ENOSPC deferral already covered (`tests/integration/test_blockcommit_defer.py::test_offline_commit_enospc_defers_then_drains_integration`, `tests/stress/test_enospc.py` — KEEP); NEW unit `test_bulk_enospc_error_classified_enospc_deferral` for the live path (error string from virsh containing ENOSPC patterns → deferral, intent kept) | `tests/core/test_deferred.py` (commit-core) |
| Merge-set ordering violated by a future caller | Unit: `test_bulk_command_uses_newest_of_ordered_set_as_top` (NEW) pins `--top = snapshots_to_merge[-1].path`; ordering contract backstopped by `test_trigger_marks_all_oldest_n_minus_l` (remove set is exactly oldest-first `snap001..snap049`) and `tests/modules/retention/test_time_based.py::test_evaluate_deterministic` (KEEP); manager asserts only non-emptiness | `tests/modules/lifecycle/test_blockcommit.py` (lifecycle-manager) |
| Stale `collapse_in_progress` keys after downgrade/upgrade | `tests/state/test_manager.py::test_stale_collapse_in_progress_key_tolerated_on_load` (MODIFY) + `test_reset_vm_state_leaves_stale_collapse_key_untouched` (NEW) + `tests/mocks/test_mock_state.py::test_inmemory_state_manager_has_no_collapse_methods` (NEW) | state-management group |
| Error-path realism: what virsh actually prints for an invalid `--top` | Integration (NEW `tests/integration/test_blockcommit_error_realism.py`): `test_invalid_top_nonexistent_path_classified` — invoke the real manager with `--top` pointing to a nonexistent path; assert the outcome maps to qsnap's `failure` (or `unknown` if virsh times out) WITHOUT assuming the exact stderr text; chain and VM untouched afterwards | integration-hysteresis |
| Error-path realism: `--top` equal to the active layer | Integration (NEW): `test_top_equal_active_layer_classified` — virsh rejects an active-layer `--top`; assert classification (expected `failure`, must not be `unknown`/crash), chain length unchanged, VM still running | integration-hysteresis |
| Error-path realism: busy-disk / foreign blockjob collision | Existing `tests/integration/test_commit_intent_recovery.py::test_active_foreign_blockjob_defers` (KEEP — real background `virsh blockcommit` + `blockjob_active` deferral); NEW unit `test_bulk_job_active_probe_defers_not_failure` pins `probe == "active"` → defer with reason `blockjob_active` (never a definitive failure) | `tests/core/test_pipeline.py` (commit-core) |
| Crash recovery mid-bulk-job | Integration (NEW `tests/integration/test_bulk_crash_recovery.py`): (a) `test_bulk_job_killed_midflight_reconciles_next_run` — start a real throttled segment `virsh blockcommit` via Core, SIGKILL the virsh client (or let the injected scaled timeout fire), then run the next pipeline; step-0 intent recovery probes `virsh blockjob`, sees the job active → defers with intent kept; when the job completes, `late_success` converges the full set; (b) `test_bulk_timeout_then_late_success_real_chain` — inject a tiny scaled timeout so the client dies while QEMU finishes the job; assert next-run reconciliation converges without data loss | `tests/integration/test_bulk_crash_recovery.py` (integration-hysteresis) |
| Offline partial-prefix reconciliation must survive (D3) | Existing `tests/integration/test_commit_intent_recovery.py::test_partial_prefix_reconciliation_real_chain` (KEEP — already simulates per-layer death offline); NEW unit `test_offline_partial_prefix_still_reconciled` on the qemu-img path (first 9 of 49 deleted, 10th failed → `0 < k < n` reconcile) | `tests/modules/lifecycle/test_qemu_img_commit.py` (lifecycle-manager) |
| Dry-run zero-mutation invariant (incl. no phase reads) | `test_hysteresis_grow_phase_predicts_no_commits` (MODIFY), `test_hysteresis_collapse_prediction_names_full_uncapped_set` (NEW), `test_hysteresis_prediction_silent_between_floor_and_threshold` (NEW) — all assert state byte-identical | hysteresis-core |
| Exact-string regressions (libvirt/QEMU output drift) | All unit scenarios above pin exact command argv (`--base`/`--top`/`--delete --verbose --wait`), log lines (`collapsing {n} snapshot(s)`, `still collapsing {n} layer(s) into base`, `collapsed {n} snapshot(s)`), `ConfigError` content (`max_commits_per_run` + removal wording), and `CommitResult` fields; the error-realism integration tests deliberately do NOT pin virsh stderr text — they classify outcomes | lifecycle-manager / commit-core / config-model groups |

---

## Notes for the Implementer

1. **Exact observable strings to pin** (from design D9 + specs):
   - Intent: `[blockcommit] {vm}/{disk}: collapsing {n} snapshot(s) into {base} (mode={mode}, timeout={scaled}s)` — emitted for BOTH the main pipeline path and the deferred drain path.
   - Heartbeat: `[blockcommit] {vm}/{disk}: still collapsing {n} layer(s) into base ({elapsed}s elapsed)` (singular `layer` when n = 1, plural `layers` otherwise).
   - Success: `[blockcommit] {vm}/{disk}: collapsed {n} snapshot(s) — {names}` (per-snapshot `ActionRecord(snapshot_delete)` rows in the summary stay).
   - Dry-run: "would collapse N snapshot(s) in one blockcommit".
2. **Command argv contract** (single process, exactly once per non-empty merge set):
   `virsh blockcommit --domain <vm> --path <disk> --base <base_image> --top <snapshots_to_merge[-1].path> --delete --verbose --wait` via `IShell.run_with_heartbeat` only.
3. **Contract tests**: bulk behavior lives inside the existing `BlockCommitManager` class (design D1) — `tests/interfaces/test_lifecycle_manager.py` needs no parametrization changes, only the `IStateManager` contract tests must drop the removed phase methods.
4. **Markers**: new real-libvirt tests must be `@pytest.mark.integration` / `@pytest.mark.stress` / `@pytest.mark.e2e` with generous `@pytest.mark.timeout` and use the disposable-VM fixtures per TESTING.md (stress_env 512M disk, test_vm 256M). `--strict-markers` applies.
