# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| core-orchestrator | Core._cleanup_failed_checkpoint rollback method | Checkpoint cleaned up after failed FULL | `tests/core/test_full_verification_pipeline.py` | `test_cleanup_failed_checkpoint_deletes_exact_checkpoint_name` (NEW) | rollback-unit |
| core-orchestrator | Core._cleanup_failed_checkpoint rollback method | Multi-disk rollback leaves other disks untouched | `tests/core/test_full_verification_pipeline.py` | `test_cleanup_failed_checkpoint_multi_disk_preserves_other_disks` (NEW) | rollback-unit |
| core-orchestrator | Core._cleanup_failed_checkpoint rollback method | Previous baseline of the failed disk is preserved | `tests/core/test_full_verification_pipeline.py` | `test_cleanup_failed_checkpoint_preserves_previous_baseline` (NEW) | rollback-unit |
| core-orchestrator | Core._cleanup_failed_checkpoint rollback method | Stopped-VM FULL failure deletes nothing | `tests/core/test_full_verification_pipeline.py` | `test_cleanup_failed_checkpoint_none_deletes_nothing` (NEW) | rollback-unit |
| core-orchestrator | Core._cleanup_failed_checkpoint rollback method | Checkpoint deletion failure is non-fatal | `tests/core/test_full_verification_pipeline.py` | `test_cleanup_failed_checkpoint_delete_failure_non_fatal` (NEW) | rollback-unit |
| core-orchestrator | Core._cleanup_failed_checkpoint rollback method | Checkpoint cleaned up after failed FULL | `tests/integration/test_rollback_retry.py` | `test_rollback_deletes_exact_checkpoint_not_prior_baseline` (NEW) | integration |
| core-orchestrator | Core._cleanup_failed_checkpoint rollback method | Multi-disk rollback leaves other disks untouched | `tests/integration/test_multi_disk.py` | `test_failed_full_rollback_deletes_only_failed_disk_checkpoint` (NEW) | integration |
| core-orchestrator | Core._cleanup_failed_checkpoint rollback method | Previous baseline of the failed disk is preserved | `tests/integration/test_multi_disk.py` | `test_failed_full_rollback_deletes_only_failed_disk_checkpoint` (NEW) | integration |
| core-orchestrator | Core._cleanup_failed_checkpoint rollback method | Stopped-VM FULL failure deletes nothing | `tests/integration/test_rollback_retry.py` | `test_stopped_vm_failed_full_deletes_no_checkpoint` (NEW) | integration |
| result-types | BackupResult dataclass | Successful backup transfer | `tests/models/test_results.py` | `test_backup_result_success` (existing) | result-model-unit |
| result-types | BackupResult dataclass | BackupResult carries disk | `tests/models/test_results.py` | `test_backup_result_carries_disk` (existing) | result-model-unit |
| result-types | BackupResult dataclass | BackupResult disk defaults to None | `tests/models/test_results.py` | `test_backup_result_disk_defaults_none` (existing) | result-model-unit |
| result-types | BackupResult dataclass | BackupResult carries checkpoint name | `tests/models/test_results.py` | `test_backup_result_carries_checkpoint_name` (NEW) | result-model-unit |
| result-types | BackupResult dataclass | BackupResult checkpoint defaults to None | `tests/models/test_results.py` | `test_backup_result_checkpoint_defaults_none` (NEW) | result-model-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via qemu-img convert | Bitmap FULL with zstd compression via qemu-img convert | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_with_compression` (existing) | backup-provider-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via qemu-img convert | Bitmap FULL with custom convert_parallel | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_custom_convert_parallel` (existing) | backup-provider-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via qemu-img convert | Bitmap FULL creates atomically with checkpoint (running VM) | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_atomic_rename_tmp_to_final` (MODIFY: assert `result.checkpoint` matches `qsnap-{target_hash}-vda-…`) | backup-provider-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via qemu-img convert | No checkpoint for stopped VM FULL | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_stopped_vm_direct_convert` (MODIFY: assert `result.checkpoint is None`) | backup-provider-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via qemu-img convert | Bitmap FULL does not self-record in state | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_does_not_self_record` (existing) | backup-provider-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via qemu-img convert | Bitmap FULL with dotted VM name | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_dotted_vm_name_passed_untruncated` (existing) | backup-provider-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via qemu-img convert | Running-VM FULL reports its checkpoint name | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_running_vm_reports_checkpoint_name` (NEW) | backup-provider-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via qemu-img convert | Running-VM FULL reports its checkpoint name | `tests/interfaces/test_backup_provider.py` | `test_create_full_backup_result_carries_checkpoint` (NEW — contract: field exists, is `str | None`, mirrors `.disk` style) | backup-provider-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via qemu-img convert | Stopped-VM FULL reports no checkpoint | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_stopped_vm_reports_no_checkpoint` (NEW) | backup-provider-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via qemu-img convert | backup-begin failure reports no checkpoint | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_backup_begin_failure_reports_no_checkpoint` (NEW) | backup-provider-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via qemu-img convert | Bitmap FULL creates atomically with checkpoint (running VM) | `tests/integration/test_full_backup.py` | `test_full_backup_running_vm_nbd` (MODIFY: assert `result.checkpoint` is non-None and present in `virsh checkpoint-list`) | integration |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via qemu-img convert | No checkpoint for stopped VM FULL | `tests/integration/test_full_backup.py` | `test_full_backup_stopped_vm` (MODIFY: assert `result.checkpoint is None`) | integration |
| dry-run-prediction | FULL backup prediction with size estimate | FULL prediction carries chain size estimate | `tests/core/test_dry_run_prediction.py` | `test_full_prediction_carries_chain_size` (MODIFY: chain probe targets `base_image` when source is simulated) | dry-run-prediction-unit |
| dry-run-prediction | FULL backup prediction with size estimate | Estimation failure degrades gracefully | `tests/core/test_dry_run_prediction.py` | `test_full_prediction_estimation_failure_graceful` (MODIFY: extend to "even base_image probe fails → size unknown, pipeline not aborted") | dry-run-prediction-unit |
| dry-run-prediction | FULL backup prediction with size estimate | First-run dry-run falls back to base_image | `tests/core/test_dry_run_prediction.py` | `test_first_run_dry_run_full_estimate_falls_back_to_base_image` (NEW) | dry-run-prediction-unit |
| dry-run-prediction | FULL backup prediction with size estimate | Simulated-path probe does not log ERROR | `tests/core/test_dry_run_prediction.py` | `test_dry_run_simulated_path_probe_no_error_log` (NEW) | dry-run-prediction-unit |
| dry-run-prediction | FULL backup prediction with size estimate | First-run dry-run falls back to base_image | `tests/integration/test_dry_run.py` | `test_dry_run_first_run_full_prediction_base_image_fallback` (NEW) | integration |
| dry-run-prediction | FULL backup prediction with size estimate | Simulated-path probe does not log ERROR | `tests/integration/test_dry_run.py` | `test_dry_run_first_run_full_prediction_base_image_fallback` (NEW — same test asserts no ERROR/WARNING records) | integration |
| shell-abstraction | check=True for probing shell.run() calls | Probing call with check=True logs at DEBUG on failure | `tests/utils/test_shell.py` | `test_probing_call_with_check_true_logs_debug` (existing) | shell-probe-unit |
| shell-abstraction | check=True for probing shell.run() calls | Compress driver probe uses check=True | `tests/core/test_validation.py` | `test_compress_probe_uses_check_true` (existing) | env-validation-unit |
| shell-abstraction | check=True for probing shell.run() calls | Size-estimation probes use check=True | `tests/utils/test_space.py` | `test_estimate_full_size_probe_uses_check_true` (NEW) | size-estimation-unit |
| shell-abstraction | check=True for probing shell.run() calls | Size-estimation probes use check=True | `tests/utils/test_space.py` | `test_estimate_incremental_size_probe_uses_check_true` (NEW) | size-estimation-unit |
| shell-abstraction | check=True for probing shell.run() calls | Probing call with check=True logs at DEBUG on failure | `tests/integration/test_log_levels.py` | `test_probe_failure_logged_at_debug_not_error` (existing) | integration |
| shell-abstraction | check=True for probing shell.run() calls | Compress driver probe uses check=True | `tests/integration/test_log_levels.py` | `test_compress_probe_logged_at_debug` (existing) | integration |

## Delegation Groups

### Group: rollback-unit
**Scope:** `tests/core/test_full_verification_pipeline.py`, `tests/mocks/mock_modules.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_full_verification_pipeline.py` | core-orchestrator 1–5 (all rollback scenarios) | MODIFY — add 5 new exact-name rollback tests, extend `test_failed_full_verification_triggers_rollback`, delete `test_checkpoint_cleaned_up_after_failed_full` (obsolete bulk filter) |
| `tests/mocks/mock_modules.py` | test support for core-orchestrator 1, 5 | MODIFY — `MockBitmapBackupProvider.create_full_backup` gains an optional `checkpoint` kwarg (default `None`), mirroring production (design D1); Core rollback tests set it to the successor name |

### Group: result-model-unit
**Scope:** `tests/models/test_results.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/models/test_results.py` | result-types 1–5 | MODIFY — add `test_backup_result_carries_checkpoint_name` + `test_backup_result_checkpoint_defaults_none`; existing tests unchanged (additive field) |

### Group: backup-provider-unit
**Scope:** `tests/modules/backup/test_bitmap.py`, `tests/interfaces/test_backup_provider.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | backup-provider 1–9 | MODIFY — add 3 new checkpoint-reporting tests, extend 2 existing tests with `checkpoint` assertions |
| `tests/interfaces/test_backup_provider.py` | backup-provider 7 (contract) | MODIFY — add `test_create_full_backup_result_carries_checkpoint` parametrized over `BitmapBackupProvider` + `MockBitmapBackupProvider` (risk A mitigation) |

### Group: dry-run-prediction-unit
**Scope:** `tests/core/test_dry_run_prediction.py`, `tests/core/test_full_anchor.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_dry_run_prediction.py` | dry-run-prediction 1–4 | MODIFY — add `test_first_run_dry_run_full_estimate_falls_back_to_base_image` + `test_dry_run_simulated_path_probe_no_error_log`; extend 2 existing prediction tests for the base_image fallback |
| `tests/core/test_full_anchor.py` | dry-run-prediction 1 (FULL prediction log fragment) | MODIFY (verify only) — `test_dry_run_logs_full_would_be_created` asserts `~0 B`; confirm the fragment still holds once the estimate derives from `base_image`, otherwise update the expected size string |

### Group: size-estimation-unit
**Scope:** `tests/utils/test_space.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_space.py` | shell-abstraction 3 (size-estimation probes) | MODIFY — add `test_estimate_full_size_probe_uses_check_true` + `test_estimate_incremental_size_probe_uses_check_true`; downgrade WARNING assertion in `test_estimate_full_size_shell_failure_returns_none` to DEBUG |

### Group: shell-probe-unit
**Scope:** `tests/utils/test_shell.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_shell.py` | shell-abstraction 1 | verify only — `test_probing_call_with_check_true_logs_debug` already covers the scenario; no change expected |

### Group: env-validation-unit
**Scope:** `tests/core/test_validation.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_validation.py` | shell-abstraction 2 (compress driver probe) | verify only — `test_compress_probe_uses_check_true` already covers the scenario; no change expected |

### Group: integration
**Scope:** `tests/integration/test_dry_run.py`, `tests/integration/test_multi_disk.py`, `tests/integration/test_rollback_retry.py`, `tests/integration/test_full_backup.py`, `tests/integration/test_log_levels.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_dry_run.py` | dry-run-prediction 3, 4 | MODIFY — add `test_dry_run_first_run_full_prediction_base_image_fallback` (`@pytest.mark.integration`) |
| `tests/integration/test_multi_disk.py` | core-orchestrator 2, 3; backup-provider 7 | MODIFY — add `test_failed_full_rollback_deletes_only_failed_disk_checkpoint` (`@pytest.mark.integration`); extend `test_backup_both_disks` with `checkpoint` assertions |
| `tests/integration/test_rollback_retry.py` | core-orchestrator 1, 4 | MODIFY — add `test_stopped_vm_failed_full_deletes_no_checkpoint` (`@pytest.mark.integration`); extend `test_rollback_deletes_broken_full_and_checkpoint` to assert exact-name deletion |
| `tests/integration/test_full_backup.py` | backup-provider 3, 4 | MODIFY — `test_full_backup_running_vm_nbd` and `test_full_backup_stopped_vm` gain `checkpoint` assertions |
| `tests/integration/test_log_levels.py` | shell-abstraction 1, 2 | verify only — existing tests cover DEBUG-vs-ERROR probe logging; no change expected |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/core/test_full_verification_pipeline.py` | `test_failed_full_verification_triggers_rollback` (line 1568): patch `mock_factory._bitmap_backup_provider.create_full_backup` to return `BackupResult(..., checkpoint="qsnap-ab12cd34-vda-20260807T020000-9f8e7d")` and assert the shell spy sees exactly one `virsh checkpoint-delete --metadata --domain testvm qsnap-ab12cd34-vda-20260807T020000-9f8e7d` call alongside the existing `remove_full_backup` assertion. | core-orchestrator scenario "Checkpoint cleaned up after failed FULL"; design D1 (exact-name rollback via `BackupResult.checkpoint`). |
| `tests/mocks/mock_modules.py` | `MockBitmapBackupProvider.create_full_backup` gains `checkpoint: str | None = None` kwarg included in the returned `BackupResult`; default `None` keeps all existing Core tests green. | result-types scenario "BackupResult carries checkpoint name"; paradigm table (mocks mirror production). |
| `tests/models/test_results.py` | Add `test_backup_result_carries_checkpoint_name` (frozen + exact name round-trip) and `test_backup_result_checkpoint_defaults_none`. | result-types scenarios "BackupResult carries checkpoint name" / "BackupResult checkpoint defaults to None". |
| `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_atomic_rename_tmp_to_final` (line 919): add `assert result.checkpoint is not None` and that it matches `qsnap-{target_hash}-vda-{ts}-{6hex}` (derive expected hash via `BitmapBackupProvider.target_hash(str(target.path))`). | backup-provider scenarios "Bitmap FULL creates atomically with checkpoint (running VM)" + "Running-VM FULL reports its checkpoint name". |
| `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_stopped_vm_direct_convert` (line 2921): add `assert result.checkpoint is None`. | backup-provider scenarios "No checkpoint for stopped VM FULL" + "Stopped-VM FULL reports no checkpoint". |
| `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_stopped_vm_returns_error` (line 2440, backup-begin "domain is not running" path): add `assert result.checkpoint is None`. | backup-provider scenario "backup-begin failure reports no checkpoint" (atomic failure path — nothing created). |
| `tests/interfaces/test_backup_provider.py` | Add `test_create_full_backup_result_carries_checkpoint` parametrized over `BitmapBackupProvider` and `MockBitmapBackupProvider`: every returned `BackupResult` has `checkpoint` as `str | None` (with `MockShell` failing `backup-begin`, the real provider must report `None`). | design.md risk [A] (future provider forgets `checkpoint`); contract-test rules in TESTING.md §3. |
| `tests/core/test_dry_run_prediction.py` | `test_full_prediction_carries_chain_size` (line 391): the simulated snapshot file does not exist, so under D3 the probe must target `disk.base_image` — assert `shell.call_history` contains `qemu-img info --force-share --backing-chain --output=json /var/lib/libvirt/images/testvm.qcow2` and `full_pred.size == 10485760`. | dry-run-prediction scenario "FULL prediction carries chain size estimate"; design D3. |
| `tests/core/test_dry_run_prediction.py` | `test_full_prediction_estimation_failure_graceful` (line 433): extend so both the source probe AND the `base_image` fallback probe fail → prediction still emitted with size 0 / "size unknown", pipeline not aborted. | dry-run-prediction scenario "Estimation failure degrades gracefully"; design.md risk [Fallback masks a genuinely broken base chain]. |
| `tests/core/test_dry_run_prediction.py` | `test_full_prediction_carries_chain_size` / new fallback tests must also cover the free-space gate estimate (line 4887) via `test_dry_run_predicts_gate_entry` — the gate's `estimate_full_size` probe and the prediction share one fallback helper, so both return the base-chain sum and never disagree. | design D3 ("one shared helper so the two never disagree"). |
| `tests/core/test_full_anchor.py` | `test_dry_run_logs_full_would_be_created` (line 356): expected fragment `(~0 B, …)` depends on the conftest default backing-chain JSON (no `actual-size`). With D3 the estimate is computed from `base_image`'s chain — verify the fragment still holds; if the fallback yields a non-zero sum, update the expected fragment to the base-chain size. | dry-run-prediction scenario "FULL prediction carries chain size estimate"; design D3. |
| `tests/utils/test_space.py` | `test_estimate_full_size_shell_failure_returns_none` (line 79): replace the `logging.WARNING` + "Cannot estimate FULL size" assertion with `caplog.at_level(logging.DEBUG)` and assert a DEBUG record, plus no WARNING/ERROR records. | shell-abstraction scenario "Size-estimation probes use check=True"; dry-run-prediction scenario "Simulated-path probe does not log ERROR"; design D4. |
| `tests/integration/test_full_backup.py` | `test_full_backup_running_vm_nbd` (line 357): after the checkpoint-list assertion, add `assert result.checkpoint is not None` and `assert result.checkpoint in qsnap_cps`. | backup-provider scenario "Bitmap FULL creates atomically with checkpoint (running VM)" + "Running-VM FULL reports its checkpoint name". |
| `tests/integration/test_full_backup.py` | `test_full_backup_stopped_vm` (line 285): add `assert result.checkpoint is None`. | backup-provider scenarios "No checkpoint for stopped VM FULL" + "Stopped-VM FULL reports no checkpoint". |

## Risks & Edge Cases

- **[Risk] A future second `IBackupProvider` implementation forgets to populate `checkpoint`** → contract test `tests/interfaces/test_backup_provider.py::test_create_full_backup_result_carries_checkpoint` (parametrized over all concrete implementations) asserts the field exists and is `str | None`; Core's None-means-no-op keeps omission safe (worst case: orphan handled by `qsnap reconcile`).
- **[Risk] Crash between `backup-begin` and result return leaves a checkpoint Core cannot name** → no new test needed; existing orphan-checkpoint detection coverage (`tests/core/test_state_check.py`, `tests/core/test_reconcile.py`, `tests/integration/test_reconcile.py`) remains authoritative — document in the core-orchestrator spec as unchanged behavior (design D2).
- **[Risk] `base_image` estimate slightly understates a real snapshot-chain size (missing overlay contribution)** → acceptance test `tests/core/test_dry_run_prediction.py::test_first_run_dry_run_full_estimate_falls_back_to_base_image` asserts the estimate equals the sum of `base_image` chain `actual-size` values (documenting the approximation), not the disk's full allocation.
- **[Risk] Fallback masks a genuinely broken base chain** → `tests/core/test_dry_run_prediction.py::test_full_prediction_estimation_failure_graceful` (extended): when the `base_image` probe also fails, the prediction degrades to "size unknown" at DEBUG and the pipeline continues; pre-flight validation / `qsnap check --deep` remain the authoritative chain-health signals.
- **[Risk] Existing tests assert the bulk-filter behavior** → explicit delete of `tests/core/test_full_verification_pipeline.py::test_checkpoint_cleaned_up_after_failed_full` (see Tests To Delete) plus the exact-name replacements in the rollback-unit group.

## Tests To Delete (Refactoring Inventory)

| File | Test name(s) | Why obsolete |
|---|---|---|
| `tests/core/test_full_verification_pipeline.py` | `test_checkpoint_cleaned_up_after_failed_full` (line 1704) | Asserts the OLD rollback contract: patches `provider.list_checkpoints` to return `[qsnap-{target_hash}-snap1]` and asserts `checkpoint-delete` fires for the bulk-filtered set. Design D1 removes `list_checkpoints` from `_cleanup_failed_checkpoint` entirely — deletion is by the exact name in `full_result.checkpoint` (which the mock never sets, so the new method correctly deletes nothing). Not re-expressible as a small edit; it is replaced by the five new exact-name tests in the rollback-unit group. |

## Integration Test Review

Existing `tests/integration/*` files that must assert the NEW behavior (all new/changed tests carry `@pytest.mark.integration`):

| File | Test | Exact NEW-behavior assertion |
|---|---|---|
| `tests/integration/test_multi_disk.py` | `test_failed_full_rollback_deletes_only_failed_disk_checkpoint` (NEW) | Seed a prior baseline checkpoint on vda and baseline checkpoints on vdb/vdc (via successful FULLs, `_cleanup_checkpoints` then a fresh successful run per disk). Force a failed vda FULL (patch `qsnap.core.verify_full_backup` to return an error for the vda attempt). Assert: `virsh checkpoint-list --name` still contains the vda prior baseline, the vdb and vdc checkpoints, and the failed attempt's successor checkpoint is gone (set difference = exactly the failed attempt's name). |
| `tests/integration/test_multi_disk.py` | `test_backup_both_disks` (MODIFY, line 452) | After each successful `create_full_backup`, assert `result.checkpoint is not None` and that it appears in `virsh checkpoint-list` output; assert `_vda_` result's checkpoint is not present in the `_vdb_` result's checkpoint and vice versa (per-disk naming). |
| `tests/integration/test_rollback_retry.py` | `test_stopped_vm_failed_full_deletes_no_checkpoint` (NEW) | Destroy the VM (stopped-VM FULL path — no checkpoint created), pre-seed one `qsnap-*` baseline checkpoint via a prior running-VM FULL, force verification failure (`full_verify_after_create="check"` with patched `verify_full_backup`). Assert: no `virsh checkpoint-delete` command was issued during the rollback and the pre-seeded baseline is still listed. |
| `tests/integration/test_rollback_retry.py` | `test_rollback_deletes_broken_full_and_checkpoint` (MODIFY, line 97) | Capture `virsh checkpoint-list --name` before the run; after the failed-FULL rollback, assert the failed attempt's successor checkpoint (the one `BackupResult.checkpoint` names, identifiable as the checkpoint that appeared during the attempt) is deleted while any pre-existing baseline remains. |
| `tests/integration/test_dry_run.py` | `test_dry_run_first_run_full_prediction_base_image_fallback` (NEW) | Fresh `test_vm` with zero state snapshots (FULL source is the simulated snapshot — file absent). Run `core.run(vm_name)` in dry-run. Assert: at least one `backup_full` prediction with `pred.size > 0` (derived from the `base_image` chain) and `caplog` contains no ERROR/WARNING record mentioning the simulated snapshot path or "Cannot estimate FULL size". |
| `tests/integration/test_dry_run.py` | `test_dry_run_shell_calls_are_all_read_only` (MODIFY, line 586) | Verify-only: the D3 fallback adds no new command families (still `qemu-img info` against `base_image`), so the existing `_READ_ONLY_PREFIXES` allowlist keeps passing — no change to the allowlist expected. |

## Verification

- **Scenario coverage:** all 26 `#### Scenario:` rows across the five delta specs (core-orchestrator 5, result-types 5, backup-provider 9, dry-run-prediction 4, shell-abstraction 3) each have ≥1 Coverage Map row; the map carries 38 rows (26 primary + 12 integration/contract rows).
- **Group ownership:** each test file appears in exactly one Delegation Group.
- **Naming:** group names are kebab-case; test names follow the repo `snake_case` `test_...` convention.
- **Traceability:** every Test Modification row cites a spec scenario or design decision (D1/D3/D4).
- **Deletion inventory:** populated from real inspection of `tests/core/test_full_verification_pipeline.py`; exactly one genuinely obsolete test (bulk-filter rollback) is marked for deletion.
- **Integration review:** populated from real inspection of `tests/integration/test_multi_disk.py`, `test_rollback_retry.py`, `test_dry_run.py`, `test_full_backup.py`, `test_log_levels.py`.
