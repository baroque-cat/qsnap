# QA Strategy & Test Plan

## Coverage Map

### Spec: verification-helpers

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| verification-helpers | Shared deep_verify_base_image function | deep_verify passes — no corruption | `tests/utils/test_verification.py` | `test_deep_verify_base_image_passes_clean` | verification-helpers-unit |
| verification-helpers | Shared deep_verify_base_image function | deep_verify fails with corruptions | `tests/utils/test_verification.py` | `test_deep_verify_base_image_fails_corruptions` | verification-helpers-unit |
| verification-helpers | Shared deep_verify_base_image function | deep_verify fails with errors | `tests/utils/test_verification.py` | `test_deep_verify_base_image_fails_errors` | verification-helpers-unit |
| verification-helpers | Shared deep_verify_base_image function | qemu-img check command fails | `tests/utils/test_verification.py` | `test_deep_verify_base_image_qemu_img_check_fails` | verification-helpers-unit |
| verification-helpers | Shared deep_verify_base_image function | JSON parsing fails | `tests/utils/test_verification.py` | `test_deep_verify_base_image_json_parse_fails` | verification-helpers-unit |
| verification-helpers | Shared scan_backing_chain function | Intact chain — all checks pass | `tests/utils/test_verification.py` | `test_scan_backing_chain_intact_chain` | verification-helpers-unit |
| verification-helpers | Shared scan_backing_chain function | Missing file in chain | `tests/utils/test_verification.py` | `test_scan_backing_chain_missing_file` | verification-helpers-unit |
| verification-helpers | Shared scan_backing_chain function | Non-qcow2 file in chain | `tests/utils/test_verification.py` | `test_scan_backing_chain_non_qcow2` | verification-helpers-unit |
| verification-helpers | Shared scan_backing_chain function | qemu-img info command fails | `tests/utils/test_verification.py` | `test_scan_backing_chain_command_fails` | verification-helpers-unit |
| verification-helpers | Shared scan_backing_chain function | Cycle detected | `tests/utils/test_verification.py` | `test_scan_backing_chain_cycle_detected` | verification-helpers-unit |
| verification-helpers | Shared scan_backing_chain function | Backing-filename mismatch | `tests/utils/test_verification.py` | `test_scan_backing_chain_backing_mismatch` | verification-helpers-unit |
| verification-helpers | Shared scan_backing_chain function | Legacy "image" key accepted | `tests/utils/test_verification.py` | `test_scan_backing_chain_legacy_image_key` | verification-helpers-unit |
| verification-helpers | Shared scan_backing_chain function | QEMU 11.0+ "filename" key accepted | `tests/utils/test_verification.py` | `test_scan_backing_chain_new_filename_key` | verification-helpers-unit |
| verification-helpers | Both managers use deep_verify_base_image | BlockCommitManager uses shared deep_verify | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_calls_shared_deep_verify` | dedup-lifecycle-unit |
| verification-helpers | Both managers use deep_verify_base_image | QemuImgCommitManager uses shared deep_verify | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_qemu_img_commit_calls_shared_deep_verify` | dedup-lifecycle-unit |
| verification-helpers | All chain verification uses scan_backing_chain | _verify_backing_chain uses scan_backing_chain | `tests/core/test_pipeline.py` | `test_verify_backing_chain_uses_scan_backing_chain` | dedup-chain-verify-unit |
| verification-helpers | All chain verification uses scan_backing_chain | _check_snapshot_chain uses scan_backing_chain | `tests/core/test_check_snapshots.py` | `test_check_snapshot_chain_uses_scan_backing_chain` | dedup-chain-verify-unit |
| verification-helpers | All chain verification uses scan_backing_chain | _check_target_consistency delegates to scan_backing_chain | `tests/core/test_check_targets.py` | `test_check_target_consistency_uses_scan_backing_chain` | dedup-chain-verify-unit |
| verification-helpers | All chain verification uses scan_backing_chain | Post-cleanup uses scan_backing_chain | `tests/core/test_pipeline.py` | `test_post_cleanup_uses_scan_backing_chain` | dedup-chain-verify-unit |

### Spec: retry-abstraction

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| retry-abstraction | Generic retry wrapper _execute_with_retry | max_retries <= 0 — single attempt | `tests/core/test_pipeline.py` | `test_execute_with_retry_max_retries_zero` | retry-abstraction-unit |
| retry-abstraction | Generic retry wrapper _execute_with_retry | Transient error retried successfully | `tests/core/test_pipeline.py` | `test_execute_with_retry_transient_error_retried` | retry-abstraction-unit |
| retry-abstraction | Generic retry wrapper _execute_with_retry | Non-retryable error fails immediately | `tests/core/test_pipeline.py` | `test_execute_with_retry_non_retryable_immediate` | retry-abstraction-unit |
| retry-abstraction | Generic retry wrapper _execute_with_retry | All retries exhausted | `tests/core/test_pipeline.py` | `test_execute_with_retry_all_attempts_exhausted` | retry-abstraction-unit |
| retry-abstraction | FULL backup creation uses _execute_with_retry | FULL creation retried on transient error | `tests/core/test_pipeline.py` | `test_full_creation_retried_via_execute_with_retry` | retry-abstraction-unit |
| retry-abstraction | FULL backup creation uses _execute_with_retry | FULL creation not retried on non-transient error | `tests/core/test_pipeline.py` | `test_full_creation_not_retried_non_transient` | retry-abstraction-unit |
| retry-abstraction | Incremental transfer uses _execute_with_retry | Incremental transfer retry via shared wrapper | `tests/core/test_pipeline.py` | `test_incremental_transfer_uses_execute_with_retry` | retry-abstraction-unit |

### Spec: config-model

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| config-model | TargetConfig dataclass | TargetConfig with path only — all defaults | `tests/config/test_model.py` | `test_target_config_no_incremental_field_exists` | config-cleanup-unit |
| config-model | TargetConfig dataclass | TOML with incremental key logs deprecation WARNING | `tests/config/test_facade.py` | `test_incremental_toml_key_logs_deprecation_warning` | config-cleanup-unit |
| config-model | parse_duration and parse_stall_timeout in utils | Functions moved to qsnap/utils/time.py | `tests/utils/test_time.py` | `test_parse_duration_and_parse_stall_timeout_in_utils_time` | config-cleanup-unit |

### Spec: periodic-full-backup

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| periodic-full-backup | Core triggers full backup before incremental transfer | First backup to target creates FULL | `tests/core/test_full_anchor.py` | `test_first_backup_creates_full` | bugfix-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | Incremental count exceeds chain length triggers FULL | `tests/core/test_full_anchor.py` | `test_incremental_count_exceeds_chain_length_triggers_full` | bugfix-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | target_chain_length is None — no FULL triggered by count | `tests/core/test_full_anchor.py` | `test_target_chain_length_none_no_full_triggered` | bugfix-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | Incremental count within chain length skips FULL | `tests/core/test_full_anchor.py` | `test_incremental_count_within_chain_length_skips_full` | bugfix-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | backup_retry_max = 0 — single attempt | `tests/core/test_full_anchor.py` | `test_backup_retry_max_zero_single_full_attempt` | bugfix-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | Verified FULL triggers retention + cleanup | `tests/core/test_full_verification_pipeline.py` | `test_verified_full_triggers_retention` | bugfix-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | Failed FULL verification triggers rollback | `tests/core/test_full_verification_pipeline.py` | `test_failed_full_verification_triggers_rollback` | bugfix-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | Retries exhausted keeps old generations | `tests/core/test_full_verification_pipeline.py` | `test_retries_exhausted_keeps_old_generations` | bugfix-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | Dry-run logs FULL-would-be-created without executing | `tests/core/test_full_anchor.py` | `test_dry_run_logs_full_would_be_created` | bugfix-unit |

### Spec: deep-verification-circuit

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| deep-verification-circuit | VMConfig deep verification fields | Deep verify defaults to off | `tests/config/test_model.py` | `test_blockcommit_deep_verify_defaults_false` | bugfix-unit |
| deep-verification-circuit | VMConfig deep verification fields | Deep verify enabled for critical VM — main blockcommit path | `tests/core/test_pipeline.py` | `test_deep_verify_passed_in_main_blockcommit_path` | bugfix-unit |
| deep-verification-circuit | VMConfig deep verification fields | Deep verify enabled for critical VM — deferred path | `tests/core/test_deferred.py` | `test_deep_verify_passed_in_deferred_blockcommit_path` | bugfix-unit |

### Spec: lifecycle-manager

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| lifecycle-manager | Blockcommit snapshots into base image | Successful live blockcommit of a single snapshot | `tests/modules/lifecycle/test_blockcommit.py` | `test_successful_live_blockcommit_single_snapshot` | dedup-lifecycle-unit |
| lifecycle-manager | Blockcommit snapshots into base image | Live blockcommit fails — virsh returns error | `tests/modules/lifecycle/test_blockcommit.py` | `test_live_blockcommit_virsh_error` | dedup-lifecycle-unit |
| lifecycle-manager | Blockcommit snapshots into base image | Blockcommit blocked by AppArmor or SELinux | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_blocked_by_mac` | dedup-lifecycle-unit |
| lifecycle-manager | Blockcommit snapshots into base image | Offline commit pivots child and deletes file | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_offline_commit_pivots_child_deletes_file` | dedup-lifecycle-unit |
| lifecycle-manager | Blockcommit snapshots into base image | Offline commit of chain tip-of-subset without child | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_offline_commit_tip_without_child` | dedup-lifecycle-unit |
| lifecycle-manager | Blockcommit snapshots into base image | Offline commit failure short-circuits safely | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_offline_commit_failure_short_circuits` | dedup-lifecycle-unit |
| lifecycle-manager | Blockcommit snapshots into base image | Empty snapshot list — nothing to merge | `tests/modules/lifecycle/test_blockcommit.py` | `test_empty_snapshot_list_noop` | dedup-lifecycle-unit |
| lifecycle-manager | Blockcommit snapshots into base image | Blockcommit times out | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_timeout` | dedup-lifecycle-unit |
| lifecycle-manager | Blockcommit snapshots into base image | Successful blockcommit with deep verify passing | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_deep_verify_passes` | dedup-lifecycle-unit |
| lifecycle-manager | Blockcommit snapshots into base image | Successful blockcommit but deep verify fails | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_deep_verify_fails_corruption` | dedup-lifecycle-unit |
| lifecycle-manager | Blockcommit snapshots into base image | deep_verify=False — no check performed | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_deep_verify_false_no_check` | dedup-lifecycle-unit |
| lifecycle-manager | Blockcommit snapshots into base image | Deep verify qemu-img check fails gracefully (no crash) | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_deep_verify_graceful_qemu_img_failure` | dedup-lifecycle-unit |

### Spec: chain-integrity-verification

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| chain-integrity-verification | Pre-commit backing chain integrity verification | Intact chain — blockcommit proceeds | `tests/core/test_pipeline.py` | `test_chain_verify_intact_chain_proceeds` | dedup-chain-verify-unit |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Missing file in chain — partial blockcommit attempted | `tests/core/test_pipeline.py` | `test_chain_verify_missing_file_partial` | dedup-chain-verify-unit |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Non-qcow2 file in chain — blockcommit skipped | `tests/core/test_pipeline.py` | `test_chain_verify_non_qcow2_skipped` | dedup-chain-verify-unit |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Cyclic reference detected — blockcommit skipped | `tests/core/test_pipeline.py` | `test_chain_verify_cyclic_reference_skipped` | dedup-chain-verify-unit |
| chain-integrity-verification | Triple-source check uses scan_backing_chain | _check_snapshot_chain delegates to scan_backing_chain | `tests/core/test_check_snapshots.py` | `test_check_snapshot_chain_calls_scan_backing_chain` | dedup-chain-verify-unit |
| chain-integrity-verification | Target consistency check uses scan_backing_chain | _check_target_consistency delegates to scan_backing_chain | `tests/core/test_check_targets.py` | `test_check_target_consistency_calls_scan_backing_chain` | dedup-chain-verify-unit |
| chain-integrity-verification | Post-cleanup verification uses scan_backing_chain | Post-cleanup uses scan_backing_chain | `tests/core/test_pipeline.py` | `test_post_cleanup_verify_keep_set_uses_scan` | dedup-chain-verify-unit |

### Spec: state-reconciliation

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| state-reconciliation | Reconcile uses shared detection methods from Core | Reconcile phantom snapshot detection uses shared detector | `tests/core/test_reconcile_snapshots.py` | `test_reconcile_calls_detect_phantom_snapshots` | dedup-check-reconcile-unit |
| state-reconciliation | Reconcile uses shared detection methods from Core | Reconcile phantom FULL detection uses shared detector | `tests/core/test_reconcile_targets.py` | `test_reconcile_calls_detect_phantom_fulls` | dedup-check-reconcile-unit |
| state-reconciliation | Reconcile uses shared detection methods from Core | Reconcile stale dependency detection uses shared detector | `tests/core/test_reconcile_targets.py` | `test_reconcile_calls_detect_stale_deps` | dedup-check-reconcile-unit |
| state-reconciliation | Reconcile uses shared detection methods from Core | Reconcile broken chain detection uses shared detector | `tests/core/test_reconcile.py` | `test_reconcile_calls_detect_broken_chains` | dedup-check-reconcile-unit |
| state-reconciliation | Reconcile uses shared detection methods from Core | Detectors return data only — no side effects | `tests/core/test_pipeline.py` | `test_detectors_return_data_only_no_mutation` | dedup-check-reconcile-unit |

### Spec: state-consistency-check

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| state-consistency-check | check_state uses shared detection methods from Core | check_state phantom snapshot detection uses shared detector | `tests/core/test_check_snapshots.py` | `test_check_state_calls_detect_phantom_snapshots` | dedup-check-reconcile-unit |
| state-consistency-check | check_state uses shared detection methods from Core | check_state phantom FULL detection uses shared detector | `tests/core/test_check_targets.py` | `test_check_state_calls_detect_phantom_fulls` | dedup-check-reconcile-unit |
| state-consistency-check | check_state uses shared detection methods from Core | check_state stale dependency detection uses shared detector | `tests/core/test_check_targets.py` | `test_check_state_calls_detect_stale_deps` | dedup-check-reconcile-unit |
| state-consistency-check | check_state uses shared detection methods from Core | check_state broken chain detection uses shared detector | `tests/core/test_check_snapshots.py` | `test_check_state_calls_detect_broken_chains` | dedup-check-reconcile-unit |
| state-consistency-check | check_state uses shared detection methods from Core | check_state and reconcile produce identical detection results | `tests/core/test_pipeline.py` | `test_check_state_and_reconcile_identical_detection` | dedup-check-reconcile-unit |

### Spec: backup-retry

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-retry | Retry wrapper for backup transfers on transient errors | Transient error retried successfully | `tests/utils/test_retry.py` | `test_transient_error_retried_successfully` | retry-abstraction-unit |
| backup-retry | Retry wrapper for backup transfers on transient errors | Content comparison mismatch retried | `tests/utils/test_retry.py` | `test_content_comparison_mismatch_retried` | retry-abstraction-unit |
| backup-retry | Retry wrapper for backup transfers on transient errors | All retries exhausted | `tests/utils/test_retry.py` | `test_all_retries_exhausted` | retry-abstraction-unit |
| backup-retry | Retry wrapper for backup transfers on transient errors | Non-retryable error fails immediately | `tests/utils/test_retry.py` | `test_non_retryable_error_fails_immediately` | retry-abstraction-unit |
| backup-retry | Retry wrapper for backup transfers on transient errors | Format verification error not retried | `tests/utils/test_retry.py` | `test_format_verification_error_not_retried` | retry-abstraction-unit |
| backup-retry | Retry wrapper for backup transfers on transient errors | Retry disabled when backup_retry_max = 0 | `tests/utils/test_retry.py` | `test_retry_disabled_when_backup_retry_max_zero` | retry-abstraction-unit |
| backup-retry | Retry wrapper for backup transfers on transient errors | FULL backup creation retries on transient errors | `tests/core/test_full_verification_pipeline.py` | `test_full_backup_creation_retried_transient` | retry-abstraction-unit |
| backup-retry | Retry wrapper for backup transfers on transient errors | FULL backup creation does NOT retry "No space left on device" | `tests/core/test_full_verification_pipeline.py` | `test_full_backup_creation_not_retried_no_space` | retry-abstraction-unit |

### Spec: backup-provider

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-provider | BitmapBackupProvider.create_full_backup | Bitmap FULL with zstd compression via qemu-img convert | `tests/modules/backup/test_bitmap.py` | `test_create_full_zstd_qemu_img_convert` | dedup-lifecycle-unit |
| backup-provider | BitmapBackupProvider.create_full_backup | Bitmap FULL with zstd compression via libnbd | `tests/modules/backup/test_bitmap.py` | `test_create_full_zstd_libnbd` | dedup-lifecycle-unit |
| backup-provider | BitmapBackupProvider.create_full_backup | Bitmap FULL with custom convert_parallel | `tests/modules/backup/test_bitmap_convert.py` | `test_create_full_custom_convert_parallel` | dedup-lifecycle-unit |
| backup-provider | BitmapBackupProvider.create_full_backup | Bitmap FULL creates atomically with checkpoint | `tests/modules/backup/test_bitmap.py` | `test_create_full_atomic_checkpoint` | dedup-lifecycle-unit |
| backup-provider | BitmapBackupProvider.create_full_backup | Bitmap FULL does not self-record in state | `tests/modules/backup/test_bitmap.py` | `test_create_full_does_not_self_record_state` | dedup-lifecycle-unit |
| backup-provider | BitmapBackupProvider.create_full_backup | Bitmap FULL with dotted VM name | `tests/modules/backup/test_bitmap.py` | `test_create_full_dotted_vm_name` | dedup-lifecycle-unit |
| backup-provider | transfer_missing safety net when prior is None | Normal path — prior is always set | `tests/modules/backup/test_bitmap_incremental.py` | `test_transfer_missing_normal_prior_always_set` | dedup-lifecycle-unit |
| backup-provider | transfer_missing safety net when prior is None | Safety net — prior is None triggers full export | `tests/modules/backup/test_bitmap_incremental.py` | `test_transfer_missing_safety_net_prior_none_full_export` | dedup-lifecycle-unit |

## Delegation Groups

### Group: test-suite-cleanup
**Scope:** conftest.py, test helpers consolidation, test_pipeline.py section deletion, dead mock removal
**Tests:** 31 deletions, 3 fixes, 0 new tests

### Group: verification-helpers-unit
**Scope:** tests/utils/test_verification.py (NEW), tests/conftest.py (fixture additions)
**Tests:** 13 new unit tests

### Group: retry-abstraction-unit
**Scope:** tests/core/test_pipeline.py, tests/utils/test_retry.py
**Tests:** 13 tests

### Group: bugfix-unit
**Scope:** tests/core/test_full_anchor.py, tests/core/test_full_verification_pipeline.py, tests/config/test_model.py, tests/core/test_pipeline.py, tests/core/test_deferred.py
**Tests:** 13 tests

### Group: dedup-lifecycle-unit
**Scope:** tests/modules/lifecycle/, tests/modules/backup/
**Tests:** 21 tests

### Group: dedup-chain-verify-unit
**Scope:** tests/core/test_pipeline.py, tests/core/test_check_snapshots.py, tests/core/test_check_targets.py
**Tests:** 7 tests

### Group: dedup-check-reconcile-unit
**Scope:** tests/core/test_reconcile*.py, tests/core/test_check_*.py, tests/core/test_pipeline.py
**Tests:** 10 tests

### Group: config-cleanup-unit
**Scope:** tests/config/, tests/utils/test_time.py
**Tests:** 3 tests

### Group: integration-tests
**Scope:** tests/integration/ (NEW files: test_target_chain_length_none.py, test_deep_verify_main_path.py, test_backup_retry_max_zero.py, test_scan_backing_chain_real_chain.py)
**Tests:** 4 integration tests (requires libvirt)

## Test Modifications

See [plan_test.md](../../../plan_test.md) for the complete list of 31 tests to delete across 5 files, plus 3 test fixes.

## Risks & Edge Cases

- **[Chain-verify unification]** The 4 call sites have subtly different JSON parsing. `scan_backing_chain()` must handle legacy "image" key and QEMU 11.0+ "filename" key → tested by `test_scan_backing_chain_legacy_image_key`, `test_scan_backing_chain_new_filename_key`
- **[Check/reconcile detector risk]** Detectors must not include reconcile-specific state → tested by `test_detectors_return_data_only_no_mutation`
- **[incremental field removal]** Users with `incremental = false` need clear deprecation message → tested by `test_incremental_false_logs_warning_deferred_behavior`
- **[qemu-img check crash risk]** `check=True` removed from deep_verify → tested by `test_blockcommit_deep_verify_does_not_pass_check_true`
