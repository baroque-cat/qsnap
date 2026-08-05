# QA Strategy & Test Plan

## Coverage Map

**Total scenarios covered: 41** (18 `dry-run-prediction` + 4 `core-orchestrator` + 8 `deferred-operations` + 4 `cli-interface` + 4 `backup-summary` + 3 `action-audit-trail`). Coverage map has 42 rows (action-audit-trail scenario 3 is tested from two angles: predictions channel + transaction log exclusion). The two extra `dry-run-prediction` scenarios (phantom-FULL and stale-baseline hygiene predictions) were added by the §11 follow-up (design D11).

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `dry-run-prediction` | Simulated future snapshots in dry-run | Multi-disk VM produces per-disk simulated snapshots | `tests/core/test_dry_run_prediction.py` | `test_simulated_snapshots_multi_disk` | core-simulated-snapshots |
| `dry-run-prediction` | Simulated future snapshots in dry-run | Onchange gate closed produces no simulated snapshots | `tests/core/test_dry_run_prediction.py` | `test_simulated_snapshots_onchange_gate_closed` | core-simulated-snapshots |
| `dry-run-prediction` | Simulated future snapshots in dry-run | Simulated snapshot allocation comes from read-only detection | `tests/core/test_dry_run_prediction.py` | `test_simulated_snapshot_allocation_read_only` | core-simulated-snapshots |
| `dry-run-prediction` | Retention prediction against post-run state | Retention counts the would-be-created snapshot | `tests/core/test_dry_run_prediction.py` | `test_retention_counts_simulated_snapshot` | core-simulated-snapshots |
| `dry-run-prediction` | Retention prediction against post-run state | Real run behavior unchanged | `tests/core/test_pipeline.py` | `test_per_chain_retention_keeps_entire_chain`, `test_per_chain_retention_removes_entire_old_chain`, `test_onchange_skip_runs_retention`, `test_gate_skip_retention_still_runs` (VERIFY — pass unchanged; plan error: `test_retention_real_run_unchanged` does not exist) | core-pipeline-updates |
| `dry-run-prediction` | Backup steps evaluated with simulated snapshots | First run predicts FULL sourced from simulated snapshot | `tests/core/test_dry_run_prediction.py` | `test_first_run_full_from_simulated_snapshot` | core-simulated-snapshots |
| `dry-run-prediction` | Incremental transfer prediction | Two untransferred snapshots produce two predictions | `tests/core/test_dry_run_prediction.py` | `test_incremental_transfer_predictions_two_snapshots` | core-simulated-snapshots |
| `dry-run-prediction` | Incremental transfer prediction | Snapshot already on target is not predicted | `tests/core/test_dry_run_prediction.py` | `test_already_on_target_not_predicted` | core-simulated-snapshots |
| `dry-run-prediction` | FULL backup prediction with size estimate | FULL prediction carries chain size estimate | `tests/core/test_dry_run_prediction.py` | `test_full_prediction_carries_chain_size` | core-simulated-snapshots |
| `dry-run-prediction` | FULL backup prediction with size estimate | Estimation failure degrades gracefully | `tests/core/test_dry_run_prediction.py` | `test_full_prediction_estimation_failure_graceful` | core-simulated-snapshots |
| `dry-run-prediction` | Backup retention prediction includes predicted FULLs | Generation rollover predicted | `tests/core/test_dry_run_prediction.py` | `test_backup_retention_generation_rollover_predicted` | core-simulated-snapshots |
| `dry-run-prediction` | Per-disk blockcommit prediction | Two disks produce two per-disk predictions | `tests/core/test_dry_run_prediction.py` | `test_blockcommit_prediction_per_disk` | core-simulated-snapshots |
| `dry-run-prediction` | Deferred drain prediction without mutation | Deferred queue survives dry-run byte-identical | `tests/core/test_dry_run_prediction.py` | `test_deferred_drain_prediction_no_mutation` | core-simulated-snapshots |
| `dry-run-prediction` | Zero-mutation invariant for the dry-run pipeline | State and filesystem unchanged after dry-run | `tests/integration/test_dry_run.py` | `test_dry_run_zero_mutation_state_and_fs` | integration-dry-run |
| `dry-run-prediction` | Zero-mutation invariant for the dry-run pipeline | Dry-run with phantom FULL records predicts cleanup without state writes | `tests/core/test_dry_run_prediction.py` | `test_dry_run_phantom_full_cleanup_predicted_not_executed` (NEW) | dry-run-state-hygiene |
| `dry-run-prediction` | Zero-mutation invariant for the dry-run pipeline | Dry-run with stale baseline and no FULLs predicts baseline cleanup | `tests/core/test_dry_run_prediction.py` | `test_dry_run_stale_baseline_cleanup_predicted_not_executed` (NEW) | dry-run-state-hygiene |
| `dry-run-prediction` | Structured predictions channel | Dry-run populates predictions per disk | `tests/core/test_dry_run_prediction.py` | `test_predictions_populated_per_disk_dry_run` | core-simulated-snapshots |
| `dry-run-prediction` | Structured predictions channel | Real run leaves predictions empty | `tests/core/test_dry_run_prediction.py` | `test_predictions_empty_real_run` | core-simulated-snapshots |
| `core-orchestrator` | Dry-run mode | Dry-run logs planned actions | `tests/core/test_pipeline.py` | `test_dry_run_logs_planned_actions` (MODIFY) | core-pipeline-updates |
| `core-orchestrator` | Dry-run mode | Dry-run activated from CLI | `tests/core/test_pipeline.py` | `test_dry_run_activated_from_cli` (MODIFY) | core-pipeline-updates |
| `core-orchestrator` | Dry-run mode | Dry-run predictions reflect post-run state | `tests/core/test_dry_run_prediction.py` | `test_predictions_reflect_post_run_state` | core-simulated-snapshots |
| `core-orchestrator` | Dry-run mode | Dry-run does not drain the deferred queue | `tests/core/test_dry_run_prediction.py` | `test_dry_run_does_not_drain_deferred_queue` | core-simulated-snapshots |
| `deferred-operations` | State-adaptive per-disk drain | Drain on shut-off VM uses qemu-img with per-disk base image | `tests/core/test_pipeline.py` | `test_deferred_blockcommit_executed_after_vm_shutdown` (existing) | core-pipeline-updates |
| `deferred-operations` | State-adaptive per-disk drain | Drain on running VM in virsh mode commits formerly-active layers | `tests/core/test_pipeline.py` | `test_blockcommit_live_commit_when_vm_running` (existing) | core-pipeline-updates |
| `deferred-operations` | State-adaptive per-disk drain | No drain on running VM in qemu-img mode | `tests/core/test_pipeline.py` | `test_blockcommit_deferred_when_vm_paused` (existing) | core-pipeline-updates |
| `deferred-operations` | State-adaptive per-disk drain | Deferred blockcommit still fails on retry | `tests/core/test_pipeline.py` | `test_deferred_blockcommit_executed_after_vm_shutdown` (existing) | core-pipeline-updates |
| `deferred-operations` | State-adaptive per-disk drain | Stale entry with unconfigured disk is dropped | `tests/core/test_pipeline.py` | `test_blockcommit_stale_guard_one_stale_removed` (existing) | core-pipeline-updates |
| `deferred-operations` | State-adaptive per-disk drain | Drain uses per-disk base image | `tests/core/test_pipeline.py` | `test_offline_commit_refreshes_domain_xml` (existing) | core-pipeline-updates |
| `deferred-operations` | State-adaptive per-disk drain | Dry-run predicts the drain without executing it | `tests/integration/test_blockcommit_defer.py` | `test_dry_run_deferred_drain_prediction` (NEW) | integration-existing-updates |
| `deferred-operations` | State-adaptive per-disk drain | Dry-run with unknown VM state | `tests/core/test_dry_run_prediction.py` | `test_deferred_drain_prediction_domstate_fails` | core-simulated-snapshots |
| `cli-interface` | Global flag --dry-run / -n | Dry-run logs actions without executing | `tests/core/test_pipeline.py` | `test_dry_run_logs_no_mutation` (MODIFY) | core-pipeline-updates |
| `cli-interface` | Global flag --dry-run / -n | Dry-run runs environment validation | `tests/core/test_pipeline.py` | `test_pipeline_always_mode_validation_first` (existing re-run in dry-run context) | core-pipeline-updates |
| `cli-interface` | Global flag --dry-run / -n | Dry-run logs per-disk FULL prediction with size | `tests/core/test_dry_run_prediction.py` | `test_full_prediction_carries_chain_size` (same as above) | core-simulated-snapshots |
| `cli-interface` | Global flag --dry-run / -n | Dry-run logs incremental transfer predictions | `tests/core/test_dry_run_prediction.py` | `test_incremental_transfer_predictions_two_snapshots` (same as above) | core-simulated-snapshots |
| `backup-summary` | Dry-run summary table | Dry-run summary header | `tests/cli/test_summary.py` | `test_dry_run_summary_header` (existing, OK as-is) | cli-summary-updates |
| `backup-summary` | Dry-run summary table | Dry-run summary footer | `tests/cli/test_summary.py` | `test_dry_run_summary_footer` (existing, OK as-is) | cli-summary-updates |
| `backup-summary` | Dry-run summary table | Dry-run shows predicted actions per VM and disk | `tests/cli/test_summary.py` | `test_dry_run_shows_predicted_actions` (MODIFY) | cli-summary-updates |
| `backup-summary` | Dry-run summary table | Dry-run with empty predictions | `tests/cli/test_summary.py` | `test_dry_run_empty_predictions` (NEW) | cli-summary-updates |
| `action-audit-trail` | PipelineResult carries actions | PipelineResult includes actions after successful run | `tests/core/test_engine.py` | `test_pipeline_result_includes_actions_success` (existing) | core-engine-updates |
| `action-audit-trail` | PipelineResult carries actions | PipelineResult includes error actions | `tests/core/test_engine.py` | `test_pipeline_result_includes_error_actions` (existing) | core-engine-updates |
| `action-audit-trail` | PipelineResult carries actions | PipelineResult carries predictions in dry-run | `tests/models/test_results.py` | `test_pipeline_result_predictions_dry_run` (NEW) | models-updates |
| `action-audit-trail` | PipelineResult carries actions | Predictions never written to transaction log | `tests/core/test_dry_run_prediction.py` | `test_predictions_not_in_transaction_log` | core-simulated-snapshots |

---

## Tests to Delete

**Explicit inventory: nothing to delete.**

All existing dry-run tests assert the **zero-mutation invariant** (no state writes, no snapshot creation, no backup transfer), which remains correct and is strengthened by this change. Every existing test that touches dry-run behavior needs **modification** (new assertions for `result.predictions`, updated log message expectations, per-disk format), but none asserts the *old broken* behavior as the desired contract.

The closest candidate was `test_no_actions_in_dry_run_mutations` (`tests/core/test_engine.py:886`), which asserts `result.actions` is empty in dry-run. This assertion **remains correct** (dry-run still produces no action records). The test needs modification only to add: `assert len(result.predictions) > 0`.

Similarly, `test_dry_run_shows_predicted_actions` (`tests/cli/test_summary.py:222`) populates the `actions` field with `snapshot_create` records for a dry-run PipelineResult. The change will move these to `predictions`, but the **test concept** (summary renders dry-run predictions) stays. The test is modified, not deleted.

---

## Delegation Groups

Each group maps to exactly one or two test files. No file overlap between groups.

### Group 1: `core-simulated-snapshots`
**Scope:** All new unit tests for the dry-run prediction engine: simulated snapshots, threading through retention/backup, incremental transfer prediction, FULL size estimation, backup retention rollover, per-disk blockcommit prediction, deferred drain prediction, structured predictions channel. Uses `MockShell`, `MockFactory`, `InMemoryStateManager`, `MockConfigFacade`.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_dry_run_prediction.py` | 17 | NEW |

### Group 2: `core-pipeline-updates`
**Scope:** Modify existing dry-run tests in `test_pipeline.py`: stale comment fix at line 462, updated log message assertions (per-disk format), add `result.predictions` assertions. Re-run existing deferred-operations tests that must continue to pass unchanged.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | 6 (modify) + 6 (existing, re-validate) | MODIFY |

### Group 3: `core-engine-updates`
**Scope:** Modify `test_no_actions_in_dry_run_mutations` to also assert `result.predictions` is populated. Verify existing action-audit-trail tests pass unchanged.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_engine.py` | 1 (modify) + 2 (existing, re-validate) | MODIFY |

### Group 4: `core-full-anchor-updates`
**Scope:** Modify `test_dry_run_logs_full_would_be_created` in `test_full_anchor.py` to expect `chain_size` estimate and per-disk context in the log output.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_full_anchor.py` | 1 | MODIFY |

### Group 5: `cli-summary-updates`
**Scope:** Modify existing summary dry-run tests to use `predictions` field. Add new `test_dry_run_empty_predictions`. Keep header/footer tests as-is.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/cli/test_summary.py` | 3 (modify) + 1 (new) | MODIFY |

### Group 6: `models-updates`
**Scope:** Add tests for `PipelineResult.predictions` field (dataclass field existence, default empty, dry-run populated). Verify `ActionRecord` immutability unchanged.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/models/test_results.py` | 2 (new) | MODIFY |

### Group 7: `integration-dry-run`
**Scope:** Real libvirt/qemu integration tests. Verify zero-mutation invariant (state byte-identical, no new files in snapshot dirs or targets, no transaction log), per-disk predictions present, deferred drain not executed, predicted sizes sane. Uses `test_vm` and `test_vm_multi_disk` fixtures.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_dry_run.py` | 6 | NEW |

### Group 8: `integration-existing-updates`
**Scope:** Extend existing integration tests: `test_count_based_full.py` dry-run test updated for `predictions` channel; `test_blockcommit_defer.py` gets new dry-run deferred drain prediction test.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_count_based_full.py` | 1 | MODIFY |
| `tests/integration/test_blockcommit_defer.py` | 1 (new) | MODIFY |

### Group 9: `dry-run-state-hygiene`
**Scope:** §11 follow-up (design D11). NEW unit tests for state-hygiene self-healing in dry-run: phantom FULL cleanup predicted but not executed, stale baseline cleanup predicted but not executed, per-run healing-log dedupe, and real-run regression (cleanup still executes when not dry-run). Uses `MockShell`, `MockFactory`, `InMemoryStateManager`, `MockConfigFacade`.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_dry_run_prediction.py` | 2 (new) + 2 (auxiliary: dedupe, real-run regression) | MODIFY |

### Group 10: `integration-allowlist`
**Scope:** §11 follow-up (verify SUGGESTION #2). Rewrite `test_dry_run_shell_calls_are_all_read_only` in `tests/integration/test_dry_run.py` from a mutating-command denylist to the spec-aligned read-only allowlist. Uses the `test_vm` fixture.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_dry_run.py` | 1 (rewrite) | MODIFY |

---

## Test Modifications

### `tests/core/test_pipeline.py`

| Change | Reason |
|---|---|
| **Stale comment fix at line 462–466:** Replace the comment referencing removed `_log_size_estimate` with something like: `# IShell.run may be called for read-only operations (qemu-img info --force-share` `# for chain-size estimation, virsh domstate for deferred drain planning,` `# and _validate_environment() read-only calls).` | D5 extracts chain-size helper; stale reference to removed method causes confusion. (proposal.md line 49, requirement 5) |
| **`test_dry_run_logs_no_mutation` (line 403):** Add `virsh domstate` to `read_only_patterns` allowlist. Add assertions: `assert len(result.predictions) > 0`, `assert result.actions == []`. Update the `assert "[dry-run]" in caplog.text` to also check per-disk format. | D8: dry-run deferred drain planning calls `virsh domstate`; spec `deferred-operations`, scenario "Dry-run with unknown VM state". |
| **`test_dry_run_logs_planned_actions` (line 4009):** Change log assertion from `"Would create snapshot for VM"` to per-disk format `"Would create snapshot for disk"`. Add `assert result.predictions is not None` and `assert len(result.predictions) > 0`. | D9: per-disk logging convention replaces per-VM counter. Spec `core-orchestrator`, scenario "Dry-run logs planned actions". |
| **`test_dry_run_activated_from_cli` (line 4069):** Add `assert len(result.predictions) > 0`. | Spec `action-audit-trail`, scenario "PipelineResult carries predictions in dry-run". |
| **`test_dry_run_logs_full_would_be_created` (line 1034):** Update log assertion to expect `chain_length` *and* `~<size>` estimate. Verify per-disk context in log. | D5: `_estimate_chain_size()` helper; D9: per-disk format. Spec `dry-run-prediction`, scenario "FULL prediction carries chain size estimate". |
| **`test_dry_run_logs_full_would_be_created_without_executing` (line 2616):** Same as above — add size estimate and per-disk assertions. | D5, D9. |
| **`test_dry_run_detects_vm_running_state_for_method` (line 2562):** Add assertions that the dry-run log contains disk context. | D9: all dry-run logs are per-disk. |

### `tests/core/test_engine.py`

| Change | Reason |
|---|---|
| **`test_no_actions_in_dry_run_mutations` (line 886):** After `assert len(result.actions) == 0`, add: `assert len(result.predictions) > 0`, `assert all(isinstance(p, ActionRecord) for p in result.predictions)`, `assert result.dry_run is True`. | Spec `action-audit-trail`, scenario "PipelineResult carries predictions in dry-run". |

### `tests/core/test_full_anchor.py`

| Change | Reason |
|---|---|
| **`test_dry_run_logs_full_would_be_created` (line 356):** Update `assert "[dry-run] Would create FULL backup"` to also check for `~`-prefixed size estimate and `[vda]` disk context. Optionally add `assert "chain_size" in caplog.text` or equivalent. | D5, D9. Spec `dry-run-prediction`, scenario "FULL prediction carries chain size estimate". |

### `tests/cli/test_summary.py`

| Change | Reason |
|---|---|
| **`test_dry_run_summary_header` (line 182) and `test_dry_run_summary_footer` (line 204):** No functional change needed — these tests construct `PipelineResult` with `dry_run=True` and assert the header/footer strings. They still pass. Confirm they pass with `predictions` defaulting to empty. | Spec `backup-summary`, scenarios "Dry-run summary header", "Dry-run summary footer". |
| **`test_dry_run_shows_predicted_actions` (line 222):** Move `actions` contents to `predictions` kwarg (requires PipelineResult gain `predictions` field first). Assert the planned-actions section renders from `predictions` field, with per-disk prefix `[vda]`. | D10: summary renders from `predictions`, not `actions`. Spec `backup-summary`, scenario "Dry-run shows predicted actions per VM and disk". |
| **NEW `test_dry_run_empty_predictions`:** Construct PipelineResult with `dry_run=True`, `predictions=[]`, `actions=[]`. Verify `"Dryrun: YES"` and disclaimer footer present but no planned-actions rows. | Spec `backup-summary`, scenario "Dry-run with empty predictions". |

### `tests/models/test_results.py`

| Change | Reason |
|---|---|
| **NEW `test_pipeline_result_predictions_default_empty`:** Construct `PipelineResult()` with defaults. Assert `result.predictions == []` and type is `list`. | Spec `action-audit-trail`. |
| **NEW `test_pipeline_result_predictions_dry_run`:** Construct `PipelineResult(predictions=[mock_action_record], dry_run=True)`. Assert `result.predictions` contains the record, `result.dry_run is True`. Verify frozen dataclass — predictions list is not mutated by construction. | Spec `action-audit-trail`, scenario "PipelineResult carries predictions in dry-run". |

### `tests/integration/test_count_based_full.py`

| Change | Reason |
|---|---|
| **`test_dry_run_does_not_create_full` (line 426):** Change `core._dry_run = True` to `core.dry_run = True` (use public property). Add: `assert result.predictions is not None`, `assert len(result.predictions) > 0`, `assert any("FULL" in p.name for p in result.predictions)`. | D7: structured predictions channel. |

### `tests/integration/test_blockcommit_defer.py`

| Change | Reason |
|---|---|
| **NEW `test_dry_run_deferred_drain_prediction` (integration):** Start VM, create snapshots, add deferred entry, shut down VM. Call `core.dry_run = True; core.run()`. Assert: deferred queue unchanged (same entries pre/post), no blockcommit executed (tip chain length unchanged), predicted drain logged, `result.predictions` contains `blockcommit` action type. | D8, spec `deferred-operations`, scenario "Dry-run predicts the drain without executing it". |

### `tests/core/test_dry_run_prediction.py` (§11 additions — Group 9)

| Change | Reason |
|---|---|
| **NEW `test_dry_run_phantom_full_cleanup_predicted_not_executed`:** Seed state with a FULL record whose file does not exist on disk plus incremental dependency records for it. Run dry-run. Assert: log contains `[dry-run] Would remove phantom FULL` with cascade count; phantom FULL record and its dependency records are STILL present in state after the run; follow-up baseline cleanup is logged (`[dry-run] Would clear last_backup_allocation`), not executed. | Design D11. Spec `dry-run-prediction`, scenario "Dry-run with phantom FULL records predicts cleanup without state writes". |
| **NEW `test_dry_run_stale_baseline_cleanup_predicted_not_executed`:** Seed state with a `last_backup_allocation` baseline but NO FULL records. Run dry-run. Assert: log contains `[dry-run] Would clear stale last_backup_allocation`; baseline is STILL present in state after the run. | Design D11. Spec `dry-run-prediction`, scenario "Dry-run with stale baseline and no FULLs predicts baseline cleanup". |
| **NEW `test_dry_run_healing_logs_deduplicated`:** One dry-run pipeline run (which calls `_validate_state_at_startup` twice and reaches the `_backup_target` phantom filter) logs each healing prediction exactly once (`Core._healing_logged` dedupe). | Design D11 per-run dedupe decision. |
| **NEW `test_real_run_phantom_cleanup_still_executes`:** Regression — a real (non-dry-run) run still removes the phantom FULL record, cleans the dependency cascade, and clears the stale baseline. | Design D11: real-run behavior byte-for-byte unchanged. |

### `tests/integration/test_dry_run.py` (§11 rewrite — Group 10)

| Change | Reason |
|---|---|
| **REWRITE `test_dry_run_shell_calls_are_all_read_only`:** Replace the mutating-command denylist with the spec-aligned allowlist: every recorded shell command must start with one of `qemu-img info`, `virsh domstate`, `virsh dominfo`, `virsh domblklist`, `virsh dumpxml`, `virsh checkpoint-list`, `virsh --version`, `test `, `which `, `find `, `du `. Any unknown command fails the test with the offending command printed. | Verify SUGGESTION #2. Spec `dry-run-prediction`, zero-mutation invariant read-only command list. |

---

## New Integration Tests

### `tests/integration/test_dry_run.py` (NEW file)

**Marker:** `@pytest.mark.integration`

All tests use the existing `test_vm` fixture (single disk, 256M qcow2) and `test_vm_multi_disk` fixture (vda 256M + vdb 128M). They run `qsnap -n run` (via `Core` with `dry_run=True`) against real VMs.

| Test Name | Fixture | What It Verifies |
|---|---|---|
| `test_dry_run_zero_mutation_single_disk` | `test_vm` | After `core.run()` with `dry_run=True`: snapshot dir contains only files pre-run (no new `.qcow2`); state content is byte-identical to pre-run (dump state dict before/after via `InMemoryStateManager` or `JsonStateManager.get_snapshots()`); no FULL/incremental files on target dir; no transaction log written. |
| `test_dry_run_zero_mutation_multi_disk` | `test_vm_multi_disk` | Same invariants for a dual-disk VM. Per-disk snapshot dirs unchanged. |
| `test_dry_run_predictions_per_disk_present` | `test_vm_multi_disk` | After `core.run()` with `dry_run=True`: `result.predictions` contains records with `disk="vda"` and `disk="vdb"`. Each snapshot_create prediction has `name` and `size > 0`. |
| `test_dry_run_deferred_queue_unchanged` | `test_vm` | After creating snapshots and adding a deferred entry, run `core.dry_run = True; core.run()`. Assert: deferred queue entries identical before/after; no files deleted from snapshot dir; `result.predictions` contains a `blockcommit` type action. |
| `test_dry_run_full_prediction_has_size_estimate` | `test_vm` | Create snapshots so a FULL is triggered. Run `core.dry_run = True; core.run()`. Assert: at least one prediction with `action="backup_full"` has `size > 0`; log output contains `~` prefix on size. |
| `test_dry_run_incremental_predictions_approximate` | `test_vm` | Create 2 snapshots, transfer 1 to target. Run dry-run. Assert exactly 1 incremental transfer prediction for the untransferred snapshot with `~` size marker. No NBD socket created. |

---

## Risks & Edge Cases

From `design.md` Risks / Trade-offs section. Each risk gets a dedicated test.

### Risk: Predicted names differ from a later real run
- **Test:** `test_predicted_names_are_illustrative` in `tests/core/test_dry_run_prediction.py` — Run dry-run twice with `frozen_clock` (same timestamp, same `token_hex`). Assert simulated snapshot names match exactly on both runs (determinism). Then run without frozen clock — assert names differ (illustrative, not stable). Document in test docstring that names are illustrative per spec.

### Risk: Extra read-only shell calls (qemu-img info per snapshot, domstate per deferred disk)
- **Test:** `test_dry_run_shell_calls_are_all_read_only` in `tests/integration/test_dry_run.py` — After `core.run()` with dry-run, inspect the shell spy trace and assert EVERY recorded command starts with one of the allowed read-only prefixes: `qemu-img info`, `virsh domstate`, `virsh dominfo`, `virsh domblklist`, `virsh dumpxml`, `virsh checkpoint-list`, `virsh --version`, `test `, `which `, `find `, `du `. Any unknown command fails the test with the offending command printed (allowlist semantics, not denylist). §11 rewrote this test from a denylist to this spec-aligned allowlist (verify SUGGESTION #2).
- **Test:** `test_dry_run_shell_call_timeouts_enforced` in `tests/core/test_dry_run_prediction.py` — Mock `MockShell.run` to simulate a slow `qemu-img info`. Assert the estimation degrades to `size=0` or `"unknown"` without hanging or aborting the pipeline.

### Risk: Upper-bound sizes misread as exact
- **Test:** `test_dry_run_sizes_marked_approximate` in `tests/cli/test_summary.py` — Construct `PipelineResult` with predictions carrying various sizes. Assert `format_summary` output for dry-run uses `~` prefix or "approx." language. No prediction appears without the approximate marker.

### Risk: Conditional generation deletions (D6) could alarm operators
- **Test:** `test_backup_retention_conditional_deletion_wording` in `tests/core/test_dry_run_prediction.py` — Mock backup retention to return a deletion candidate that is conditional on new FULL verification. Assert log message contains `"after new FULL verification"` or equivalent conditional language. Assert the prediction `ActionRecord` has a distinguishing marker (e.g., `action="backup_delete"` with a condition in the `error` field or log message).

### Risk: Simulated snapshots accidentally persisted
- **Test:** `test_dry_run_simulated_snapshots_not_in_state` in `tests/core/test_dry_run_prediction.py` — Pre-populate 3 snapshots in `InMemoryStateManager`. Run `core.run()` with `dry_run=True`. Assert `state.get_snapshots("testvm")` still returns exactly 3 entries (same names, paths, timestamps). No entries with predicted names.
- **Test:** `test_dry_run_zero_mutation_single_disk` in `tests/integration/test_dry_run.py` (covers both state and filesystem).

### Risk: Test churn breaks unrelated suites
- **Mitigation:** All modifications preserve the zero-mutation assertion contract. The new `predictions` field defaults to empty — existing non-dry-run `PipelineResult` constructions are unaffected. All existing non-dry-run tests pass unchanged because `predictions` defaults to `[]`.
- **Verification:** After implementation, run `poetry run pytest tests/ -m "not integration and not stress and not e2e"` to confirm no unexpected failures. Existing integration tests that pass `actions` to `PipelineResult` without `predictions` must still pass (default empty).

---

## Spec Scenario Coverage Per Spec

| Spec | Total Scenarios | Covered | Notes |
|---|---|---|---|
| `dry-run-prediction` | 18 | 18 | All 10 requirements covered; 18 scenario rows in coverage map (2 state-hygiene scenarios added by §11 / design D11). |
| `core-orchestrator` | 4 | 4 | 1 MODIFIED requirement; all 4 scenarios covered. |
| `deferred-operations` | 8 | 8 | 1 MODIFIED requirement; 6 existing scenarios re-validated by existing tests; 2 new dry-run scenarios get NEW tests. |
| `cli-interface` | 4 | 4 | 1 MODIFIED requirement; all 4 scenarios covered (2 map to same tests as dry-run-prediction). |
| `backup-summary` | 4 | 4 | 1 MODIFIED requirement; all 4 scenarios covered. |
| `action-audit-trail` | 3 | 3 | 1 MODIFIED requirement; 2 existing scenarios re-validated; 1 new scenario tested from 2 angles. |

**No scenario is uncovered.** All 41 scenarios across all 6 delta specs have >=1 test in the coverage map. Where a scenario is implicitly covered by existing tests that must pass unchanged, those tests are listed in the coverage map with `(existing)` noted. The coverage map has 42 rows (one scenario is tested from two complementary angles).

---

## Stale Comment Cleanup

`tests/core/test_pipeline.py` line 462–466 currently reads:

```python
    # IShell.run may be called for read-only operations (_log_size_estimate
    # runs even in dry-run mode to provide size projections via qemu-img info
    # and du -sb).  In dry-run mode, _validate_environment() also runs
    # (design D6) making read-only validation calls (test, which, virsh
    # dominfo, find).  Verify only read-only shell calls were made.
```

This references the **removed** `_log_size_estimate()` method (deleted in archived change `2026-07-19-zstd-compression-and-stall-detection`). The comment must be updated in `core-pipeline-updates` group to reflect the new read-only calls: `_estimate_chain_size()` (qemu-img info --backing-chain, per D5), `virsh domstate` (deferred drain planning, per D8), and validation calls. Example replacement:

```python
    # IShell.run may be called for read-only operations: qemu-img info
    # --force-share for chain-size and allocation estimation (design D5),
    # virsh domstate for deferred drain planning (design D8), and
    # _validate_environment() read-only calls (which, test, virsh dominfo,
    # find).  Verify only read-only shell calls were made.
```

---

## Running the Tests

```bash
# Unit + mock + contract (fast, no I/O) — run after Groups 1-6:
poetry run pytest tests/core/test_dry_run_prediction.py tests/core/test_pipeline.py \
  tests/core/test_engine.py tests/core/test_full_anchor.py \
  tests/cli/test_summary.py tests/models/test_results.py -v

# Integration — run after Groups 7-8 (needs libvirt):
poetry run pytest tests/integration/test_dry_run.py \
  tests/integration/test_count_based_full.py \
  tests/integration/test_blockcommit_defer.py -v -m integration

# Full project regression after all groups complete:
poetry run pytest tests/ -m "not stress and not e2e"
```
