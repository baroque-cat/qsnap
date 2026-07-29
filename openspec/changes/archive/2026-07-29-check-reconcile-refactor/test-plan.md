# QA Strategy & Test Plan

## Overview

This test plan provides full spec-to-test traceability for the `check-reconcile-refactor` change. It covers 75 scenarios across 8 spec files, organized into 10 parallelizable delegation groups. All tests follow the TESTING.md paradigm: unit tests use `MockShell`; integration tests use real `virsh`/`qemu-img`.

---

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| triple-source-check | Triple-source snapshot verification | All three sources consistent | `tests/core/test_check_snapshots.py` | `test_check_all_consistent` | check-snapshots-unit |
| triple-source-check | Triple-source snapshot verification | Phantom snapshot — state has, disk and XML do not | `tests/core/test_check_snapshots.py` | `test_check_phantom_snapshot_file_missing` | check-snapshots-unit |
| triple-source-check | Triple-source snapshot verification | Stale domain XML — state and disk agree, XML references missing file | `tests/core/test_check_snapshots.py` | `test_check_xml_references_missing_file` | check-snapshots-unit |
| triple-source-check | Triple-source snapshot verification | Orphan file — disk has, state does not, XML references | `tests/core/test_check_snapshots.py` | `test_check_orphan_snapshot_file_exists_not_in_state` | check-snapshots-unit |
| triple-source-check | Triple-source snapshot verification | Legitimate deletion — all three sources agree file is gone | `tests/core/test_check_snapshots.py` | `test_check_after_blockcommit_all_consistent` | check-snapshots-unit |
| triple-source-check | Triple-source snapshot verification | Broken backing chain — file missing from middle | `tests/core/test_check_snapshots.py` | `test_check_broken_chain_middle_missing` | check-snapshots-unit |
| triple-source-check | Triple-source snapshot verification | Active layer mismatch | `tests/core/test_check_snapshots.py` | `test_check_xml_active_layer_mismatch` | check-snapshots-unit |
| triple-source-check | Triple-source target verification | All targets consistent | `tests/core/test_check_targets.py` | `test_check_targets_all_consistent` | check-targets-unit |
| triple-source-check | Triple-source target verification | Phantom FULL — state has, disk does not | `tests/core/test_check_targets.py` | `test_check_phantom_full` | check-targets-unit |
| triple-source-check | Triple-source target verification | Broken backup chain — incremental's backing file missing | `tests/core/test_check_targets.py` | `test_check_broken_backup_chain` | check-targets-unit |
| triple-source-check | Triple-source target verification | Orphan checkpoint — target_hash does not match | `tests/core/test_check_targets.py` | `test_check_orphan_checkpoint` | check-targets-unit |
| triple-source-check | Triple-source target verification | Missing checkpoint — no baseline for next incremental | `tests/core/test_check_targets.py` | `test_check_missing_checkpoint` | check-targets-unit |
| triple-source-check | Check is read-only | Check does not modify state | `tests/core/test_check_snapshots.py` | `test_check_does_not_modify_state` | check-snapshots-unit |
| triple-source-check | Check is read-only | Check does not delete files | `tests/core/test_check_snapshots.py` | `test_check_does_not_delete_files` | check-snapshots-unit |
| triple-source-check | Shallow check uses JSON parsing | Shallow check detects inconsistent backing-filename | `tests/core/test_check_snapshots.py` | `test_check_inconsistent_backing_filename` | check-snapshots-unit |
| triple-source-check | Triple-source snapshot verification | Real VM all consistent | `tests/integration/test_check_snapshots.py` | `test_check_real_vm_all_consistent` | check-integration |
| triple-source-check | Triple-source snapshot verification | Real VM after blockcommit | `tests/integration/test_check_snapshots.py` | `test_check_real_vm_after_blockcommit` | check-integration |
| triple-source-check | Triple-source snapshot verification | Real VM phantom snapshot | `tests/integration/test_check_snapshots.py` | `test_check_real_vm_phantom_snapshot` | check-integration |
| triple-source-check | Triple-source snapshot verification | Real VM stale XML after offline commit | `tests/integration/test_check_snapshots.py` | `test_check_real_vm_stale_xml_after_offline_commit` | check-integration |
| triple-source-check | Triple-source snapshot verification | Real VM after refresh XML | `tests/integration/test_check_snapshots.py` | `test_check_real_vm_after_refresh_xml` | check-integration |
| triple-source-check | Triple-source target verification | Real targets all consistent | `tests/integration/test_check_targets.py` | `test_check_real_targets_all_consistent` | check-integration |
| triple-source-check | Triple-source target verification | Real targets broken chain | `tests/integration/test_check_targets.py` | `test_check_real_targets_broken_chain` | check-integration |
| triple-source-check | Triple-source target verification | Real VM orphan checkpoint | `tests/integration/test_check_targets.py` | `test_check_real_targets_orphan_checkpoint` | check-integration |
| chain-integrity-verification | --force-share on check_integrity qemu-img info | check uses --force-share on active layer | `tests/integration/test_check_snapshots.py` | `test_check_uses_force_share_on_active_layer` | check-integration |
| chain-integrity-verification | --force-share on check_integrity qemu-img info | check parses JSON and detects inconsistent backing-filename | `tests/core/test_check_snapshots.py` | `test_check_inconsistent_backing_filename` | check-snapshots-unit |
| chain-integrity-verification | --force-share on check_integrity qemu-img info | check parses JSON and detects cycle | `tests/core/test_check_snapshots.py` | `test_check_detects_cycle_in_chain` | check-snapshots-unit |
| chain-integrity-verification | --force-share on _deep_check_file qemu-img check | Deep check on active layer uses --force-share | `tests/core/test_pipeline.py` | `test_deep_check_uses_force_share_on_active_layer` | core-unit-modifications |
| chain-integrity-verification | --force-share on _deep_check_file qemu-img check | Deep check detects errors (not just corruptions) | `tests/core/test_list_commands.py` | `test_check_deep_errors_detected` | core-unit-modifications |
| chain-integrity-verification | --force-share on _deep_check_file qemu-img check | Deep check detects leaks | `tests/core/test_list_commands.py` | `test_check_deep_leaks_detected` | core-unit-modifications |
| chain-integrity-verification | --force-share on _deep_check_file qemu-img check | Deep check timeout is 7200 seconds | `tests/core/test_pipeline.py` | `test_deep_check_timeout_7200_seconds` | core-unit-modifications |
| state-reconciliation | Reconcile command actively repairs state | Reconcile removes phantom FULLs with cascade cleanup | `tests/core/test_reconcile_targets.py` | `test_reconcile_phantom_full_removed` | reconcile-targets-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile clears stale last_backup_allocation | `tests/core/test_reconcile_targets.py` | `test_reconcile_stale_baseline_cleared` | reconcile-targets-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile removes phantom snapshots | `tests/core/test_reconcile_snapshots.py` | `test_reconcile_phantom_snapshot_removed_from_state` | reconcile-snapshots-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile removes stale incremental dependencies | `tests/core/test_reconcile_targets.py` | `test_reconcile_stale_dep_removed` | reconcile-targets-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile supplements state from disk+XML reality | `tests/core/test_reconcile_snapshots.py` | `test_reconcile_orphan_snapshot_recorded_in_state` | reconcile-snapshots-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile deletes orphan files not in state or XML | `tests/core/test_reconcile_snapshots.py` | `test_reconcile_orphan_snapshot_deleted` | reconcile-snapshots-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile refreshes stale domain XML | `tests/core/test_reconcile_snapshots.py` | `test_reconcile_stale_xml_refreshed` | reconcile-snapshots-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile does NOT auto-rebase broken chains | `tests/core/test_reconcile_snapshots.py` | `test_reconcile_broken_chain_no_auto_rebase` | reconcile-snapshots-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile deletes orphaned checkpoints | `tests/core/test_reconcile_targets.py` | `test_reconcile_orphan_checkpoint_deleted` | reconcile-targets-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile dry-run mode | `tests/core/test_reconcile_snapshots.py` | `test_reconcile_dry_run_no_modifications` | reconcile-snapshots-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile returns structured result | `tests/core/test_reconcile_snapshots.py` | `test_reconcile_returns_structured_result` | reconcile-snapshots-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile with VM filter | `tests/core/test_reconcile_snapshots.py` | `test_reconcile_with_vm_filter` | reconcile-snapshots-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile removes orphan files on target | `tests/core/test_reconcile_targets.py` | `test_reconcile_orphan_broken_deleted` | reconcile-targets-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile supplements state for orphan backup with intact chain | `tests/core/test_reconcile_targets.py` | `test_reconcile_orphan_backup_recorded` | reconcile-targets-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile skips non-qsnap files on target | `tests/core/test_reconcile.py` | `test_reconcile_skips_non_qsnap_files_on_target` | core-unit-modifications |
| state-reconciliation | Reconcile command actively repairs state | Reconcile orphan file cleanup is non-fatal | `tests/core/test_reconcile.py` | `test_reconcile_orphan_file_cleanup_non_fatal` | core-unit-modifications |
| state-reconciliation | ReconcileResult dataclass | ReconcileResult is frozen | `tests/models/test_results.py` | `test_reconcile_result_is_frozen` | config-and-mock-infra |
| state-reconciliation | Broken backing chain detection in reconcile | Reconcile detects broken chain — no auto-rebase | `tests/core/test_reconcile_targets.py` | `test_reconcile_broken_backup_chain_critical` | reconcile-targets-unit |
| state-reconciliation | Broken backing chain detection in reconcile | Reconcile with intact chains — no broken_chains | `tests/core/test_reconcile.py` | `test_reconcile_intact_chains_no_broken_chains` | core-unit-modifications |
| state-reconciliation | Broken backing chain detection in reconcile | Reconcile dry-run reports broken chains without deletion | `tests/core/test_reconcile.py` | `test_reconcile_dry_run_reports_broken_chains_no_deletion` | core-unit-modifications |
| state-reconciliation | Reconcile command actively repairs state | Real VM phantom snapshot | `tests/integration/test_reconcile_snapshots.py` | `test_reconcile_real_phantom_snapshot` | reconcile-integration |
| state-reconciliation | Reconcile command actively repairs state | Real VM orphan snapshot recorded | `tests/integration/test_reconcile_snapshots.py` | `test_reconcile_real_orphan_snapshot_recorded` | reconcile-integration |
| state-reconciliation | Reconcile command actively repairs state | Real VM stale XML refreshes | `tests/integration/test_reconcile_snapshots.py` | `test_reconcile_real_stale_xml` | reconcile-integration |
| state-reconciliation | Reconcile command actively repairs state | Real VM broken chain no auto-rebase | `tests/integration/test_reconcile_snapshots.py` | `test_reconcile_real_broken_chain_no_rebase` | reconcile-integration |
| state-reconciliation | Reconcile command actively repairs state | Real VM dry-run | `tests/integration/test_reconcile_snapshots.py` | `test_reconcile_real_dry_run` | reconcile-integration |
| state-reconciliation | Reconcile command actively repairs state | Real VM phantom FULL | `tests/integration/test_reconcile_targets.py` | `test_reconcile_real_phantom_full` | reconcile-integration |
| state-reconciliation | Reconcile command actively repairs state | Real VM orphan backup recorded | `tests/integration/test_reconcile_targets.py` | `test_reconcile_real_orphan_backup_recorded` | reconcile-integration |
| state-reconciliation | Reconcile command actively repairs state | Real VM orphan checkpoint | `tests/integration/test_reconcile_targets.py` | `test_reconcile_real_orphan_checkpoint` | reconcile-integration |
| state-reconciliation | Reconcile command actively repairs state | Real VM broken chain critical | `tests/integration/test_reconcile_targets.py` | `test_reconcile_real_broken_chain_critical` | reconcile-integration |
| state-reconciliation | Reconcile command actively repairs state | Real VM target dry-run | `tests/integration/test_reconcile_targets.py` | `test_reconcile_real_dry_run` | reconcile-integration |
| post-creation-validation | Post-creation snapshot validation | All validation checks pass | `tests/integration/test_post_creation_validation.py` | `test_snapshot_post_creation_validation` | post-validation-integration |
| post-creation-validation | Post-creation snapshot validation | Snapshot file missing despite virsh success | `tests/integration/test_post_creation_validation.py` | `test_snapshot_post_creation_validation_failure` | post-validation-integration |
| post-creation-validation | Post-transfer incremental backup validation | Incremental chain traversable and checkpoint exists | `tests/integration/test_post_creation_validation.py` | `test_incremental_post_transfer_validation` | post-validation-integration |
| post-creation-validation | Post-creation FULL backup validation | FULL has no backing file and checkpoint exists | `tests/integration/test_post_creation_validation.py` | `test_full_post_creation_validation` | post-validation-integration |
| snapshot-provider | External disk-only snapshot creation | Successful snapshot creation with validation | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_success` (MODIFY) | provider-unit-modifications |
| snapshot-provider | External disk-only snapshot creation | virsh command fails | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_virsh_fails` | provider-unit-modifications |
| snapshot-provider | External disk-only snapshot creation | virsh command times out | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_timeout` | provider-unit-modifications |
| snapshot-provider | External disk-only snapshot creation | Post-snapshot qemu-img info uses --force-share on running VM | `tests/modules/snapshot/test_external.py` | `test_post_snapshot_info_uses_force_share` | provider-unit-modifications |
| snapshot-provider | External disk-only snapshot creation | Post-snapshot qemu-img info without --force-share fails (regression guard) | `tests/modules/snapshot/test_external.py` | `test_post_snapshot_info_without_force_share_regression` | provider-unit-modifications |
| snapshot-provider | External disk-only snapshot creation | Validation fails — file missing despite virsh success | `tests/modules/snapshot/test_external.py` | `test_post_creation_file_missing` (NEW) | provider-unit-modifications |
| snapshot-provider | External disk-only snapshot creation | Validation fails — wrong backing-filename | `tests/modules/snapshot/test_external.py` | `test_post_creation_wrong_backing` (NEW) | provider-unit-modifications |
| snapshot-provider | External disk-only snapshot creation | Validation fails — corrupt bit set | `tests/modules/snapshot/test_external.py` | `test_post_creation_corrupt_bit` (NEW) | provider-unit-modifications |
| snapshot-provider | External disk-only snapshot creation | Validation fails — libvirt pivot not confirmed | `tests/modules/snapshot/test_external.py` | `test_post_creation_pivot_not_confirmed` (NEW) | provider-unit-modifications |
| backup-provider | Post-transfer chain-to-FULL verification | Chain to FULL traversable after incremental transfer | `tests/modules/backup/test_bitmap.py` | `test_incremental_chain_to_full_traversable` (NEW) | provider-unit-modifications |
| backup-provider | Post-transfer chain-to-FULL verification | Broken chain to FULL detected after incremental transfer | `tests/modules/backup/test_bitmap.py` | `test_incremental_chain_to_full_broken` (NEW) | provider-unit-modifications |
| backup-provider | Post-creation FULL backup verification | FULL has no backing file and checkpoint exists | `tests/modules/backup/test_bitmap.py` | `test_full_no_backing_file_and_checkpoint_exists` (NEW) | provider-unit-modifications |
| backup-provider | Post-creation FULL backup verification | FULL has unexpected backing file | `tests/modules/backup/test_bitmap.py` | `test_full_unexpected_backing_file` (NEW) | provider-unit-modifications |
| backup-provider | Post-creation FULL backup verification | Checkpoint missing after FULL creation | `tests/modules/backup/test_bitmap.py` | `test_full_checkpoint_missing` (NEW) | provider-unit-modifications |
| deep-verification-circuit | qsnap check --deep enhanced with per-image verification | All images pass deep check | `tests/core/test_list_commands.py` | `test_check_deep_clean_image_reports_ok` (MODIFY) | core-unit-modifications |
| deep-verification-circuit | qsnap check --deep enhanced with per-image verification | Corruption detected in one image | `tests/core/test_list_commands.py` | `test_deep_chain_check_corruption_detected` (MODIFY) | core-unit-modifications |
| deep-verification-circuit | qsnap check --deep enhanced with per-image verification | Errors detected in one image | `tests/core/test_list_commands.py` | `test_check_deep_errors_detected` (NEW) | core-unit-modifications |
| deep-verification-circuit | qsnap check --deep enhanced with per-image verification | Leaks detected in one image | `tests/core/test_list_commands.py` | `test_check_deep_leaks_detected` (NEW) | core-unit-modifications |
| deep-verification-circuit | qsnap check --deep enhanced with per-image verification | Image unreadable | `tests/core/test_list_commands.py` | `test_check_deep_image_unreadable` (NEW) | core-unit-modifications |
| deep-verification-circuit | qsnap check --deep enhanced with per-image verification | Deep check timeout accommodates large disks | `tests/core/test_pipeline.py` | `test_deep_check_timeout_7200_seconds` (NEW) | core-unit-modifications |
| deep-verification-circuit | qsnap check --deep enhanced with per-image verification | Real VM deep check | `tests/integration/test_check_snapshots.py` | `test_check_real_deep_check` | check-integration |
| deep-verification-circuit | qsnap check --deep enhanced with per-image verification | Real deep targets | `tests/integration/test_check_targets.py` | `test_check_real_deep_targets` | check-integration |
| config-model | GlobalConfig count-based retention fields | Defaults are 24/168/2 | `tests/config/test_model.py` | `test_globalconfig_default_chain_lengths_24_168_2` (NEW) | config-and-mock-infra |
| config-model | GlobalConfig count-based retention fields | Explicit override still works | `tests/config/test_model.py` | `test_globalconfig_explicit_override_works` (NEW) | config-and-mock-infra |
| config-model | GlobalConfig default values | GlobalConfig default values | `tests/config/test_model.py` | `test_global_config_default_values` (MODIFY) | config-and-mock-infra |
| state-reconciliation | Reconcile command actively repairs state | Reconcile last_allocation mismatch corrects | `tests/core/test_reconcile_snapshots.py` | `test_reconcile_last_allocation_mismatch` | reconcile-snapshots-unit |
| state-reconciliation | Reconcile command actively repairs state | Reconcile after blockcommit no action | `tests/core/test_reconcile_snapshots.py` | `test_reconcile_after_blockcommit_no_action` | reconcile-snapshots-unit |
| triple-source-check | Triple-source snapshot verification | Phantom snapshot but XML OK (phantom in state) | `tests/core/test_check_snapshots.py` | `test_check_phantom_snapshot_but_xml_ok` | check-snapshots-unit |
| triple-source-check | Triple-source snapshot verification | XML backingstore chain mismatch | `tests/core/test_check_snapshots.py` | `test_check_xml_backingstore_chain_mismatch` | check-snapshots-unit |
| triple-source-check | Triple-source snapshot verification | Broken chain base missing | `tests/core/test_check_snapshots.py` | `test_check_broken_chain_base_missing` | check-snapshots-unit |
| triple-source-check | Triple-source snapshot verification | After retention all consistent | `tests/core/test_check_snapshots.py` | `test_check_after_retention_all_consistent` | check-snapshots-unit |
| triple-source-check | Triple-source target verification | Orphan backup file | `tests/core/test_check_targets.py` | `test_check_orphan_backup_file` | check-targets-unit |
| triple-source-check | Triple-source target verification | Broken backup chain full missing | `tests/core/test_check_targets.py` | `test_check_broken_backup_chain_full_missing` | check-targets-unit |
| triple-source-check | Triple-source target verification | Multiple checkpoints for same target | `tests/core/test_check_targets.py` | `test_check_multiple_checkpoints` | check-targets-unit |
| triple-source-check | Triple-source target verification | After retention cleanup | `tests/core/test_check_targets.py` | `test_check_after_retention_cleanup` | check-targets-unit |
| triple-source-check | Triple-source target verification | After force full | `tests/core/test_check_targets.py` | `test_check_after_force_full` | check-targets-unit |
| post-creation-validation | Post-creation snapshot validation | Wrong backing-filename (unit) | `tests/modules/snapshot/test_external.py` | `test_post_creation_wrong_backing` | provider-unit-modifications |
| post-creation-validation | Post-creation snapshot validation | Corrupt bit set (unit) | `tests/modules/snapshot/test_external.py` | `test_post_creation_corrupt_bit` | provider-unit-modifications |
| post-creation-validation | Post-creation snapshot validation | libvirt pivot not confirmed (unit) | `tests/modules/snapshot/test_external.py` | `test_post_creation_pivot_not_confirmed` | provider-unit-modifications |
| post-creation-validation | Post-transfer incremental backup validation | Broken chain to FULL detected (unit) | `tests/modules/backup/test_bitmap.py` | `test_incremental_chain_to_full_broken` | provider-unit-modifications |
| post-creation-validation | Post-transfer incremental backup validation | Checkpoint missing after transfer (unit) | `tests/modules/backup/test_bitmap.py` | `test_incremental_checkpoint_missing` (NEW) | provider-unit-modifications |
| post-creation-validation | Post-creation FULL backup validation | FULL has unexpected backing file (unit) | `tests/modules/backup/test_bitmap.py` | `test_full_unexpected_backing_file` | provider-unit-modifications |
| — | — | _refresh_domain_backing_store after offline commit | `tests/integration/test_refresh_backing_store.py` | `test_refresh_after_offline_commit` | post-validation-integration |
| — | — | _refresh_domain_backing_store strips all backing stores | `tests/integration/test_refresh_backing_store.py` | `test_refresh_strips_all_backing_store` | post-validation-integration |
| — | — | _refresh_domain_backing_store idempotent | `tests/integration/test_refresh_backing_store.py` | `test_refresh_idempotent` | post-validation-integration |
| — | — | _refresh_domain_backing_store failure non-fatal | `tests/integration/test_refresh_backing_store.py` | `test_refresh_failure_non_fatal` | post-validation-integration |

---

## Delegation Groups

### Group: check-snapshots-unit
Scope: New unit tests for `Core.check()` snapshot verification using `MockShell`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_check_snapshots.py` | 15 | NEW |

### Group: check-targets-unit
Scope: New unit tests for `Core.check()` target verification using `MockShell`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_check_targets.py` | 9 | NEW |

### Group: reconcile-snapshots-unit
Scope: New unit tests for `Core.reconcile()` snapshot repair using `MockShell`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_reconcile_snapshots.py` | 10 | NEW |

### Group: reconcile-targets-unit
Scope: New unit tests for `Core.reconcile()` target repair using `MockShell`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_reconcile_targets.py` | 9 | NEW |

### Group: check-integration
Scope: Integration tests for `Core.check()` using real `virsh`/`qemu-img` against a disposable test VM

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_check_snapshots.py` | 7 | NEW |
| `tests/integration/test_check_targets.py` | 5 | NEW |
| **Total** | **12** | |

### Group: reconcile-integration
Scope: Integration tests for `Core.reconcile()` using real `virsh`/`qemu-img` against a disposable test VM

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_reconcile_snapshots.py` | 5 | NEW |
| `tests/integration/test_reconcile_targets.py` | 5 | NEW |
| **Total** | **10** | |

### Group: post-validation-integration
Scope: Integration tests for post-creation validation and `_refresh_domain_backing_store()`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_post_creation_validation.py` | 4 | NEW |
| `tests/integration/test_refresh_backing_store.py` | 4 | NEW |
| **Total** | **8** | |

### Group: provider-unit-modifications
Scope: Add new post-creation and post-transfer validation unit tests to existing provider test files

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/snapshot/test_external.py` | 8 (4 NEW + 4 existing scenarios verified) | MODIFY |
| `tests/modules/backup/test_bitmap.py` | 6 (5 NEW + 1 existing modified) | MODIFY |
| **Total** | **14** | |

### Group: core-unit-modifications
Scope: Modify existing core unit tests for new deep check behavior, reconcile behavior, and check refactoring

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_list_commands.py` | 5 (3 NEW + 2 MODIFIED) | MODIFY |
| `tests/core/test_pipeline.py` | 3 (2 NEW + 1 MODIFIED) | MODIFY |
| `tests/core/test_reconcile.py` | 3 (all modified for new behavior) | MODIFY |
| `tests/core/test_state_check.py` | 0 (kept as-is, existing `check_state()` remains) | NONE |
| `tests/integration/test_reconcile.py` | 3 (all modified for new behavior) | MODIFY |
| **Total** | **14** | |

### Group: config-and-mock-infra
Scope: Config default value tests + mock infrastructure additions + fixture files

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | 3 (2 NEW + 1 MODIFIED) | MODIFY |
| `tests/models/test_results.py` | 1 | MODIFY |
| `tests/mocks/mock_modules.py` | Infrastructure (add `MockRetentionEngine` configurability, `MockChangeDetector` configurability) | MODIFY |
| `tests/mocks/mock_shell.py` | Infrastructure (add `expect_ordered()`, `call_history`) | MODIFY |
| `tests/conftest.py` | Infrastructure (add domain XML fixtures) | MODIFY |
| `tests/fixtures/domain_xml_with_backing_store.xml` | Fixture: XML with `<backingStore>` | NEW |
| `tests/fixtures/domain_xml_stale_backing_store.xml` | Fixture: XML with stale `<backingStore>` | NEW |
| `tests/fixtures/domain_xml_no_backing_store.xml` | Fixture: XML without `<backingStore>` | NEW |
| **Total** | **4 test scenarios + 3 fixture files** | |

---

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/modules/snapshot/test_external.py` | Add 4 new tests for post-creation validation: `test_post_creation_file_missing`, `test_post_creation_wrong_backing`, `test_post_creation_corrupt_bit`, `test_post_creation_pivot_not_confirmed`. Modify `test_create_snapshot_success` to also verify `test -f`, backing-filename validation, and `virsh domblklist` pivot confirmation in the mock expectations. | ExternalSnapshotProvider.create() now performs post-creation validation after virsh success (design D4). Existing tests only verify virsh exit code + chmod + qemu-img info allocation. |
| `tests/modules/backup/test_bitmap.py` | Add 5 new tests: `test_incremental_chain_to_full_traversable`, `test_incremental_chain_to_full_broken`, `test_incremental_checkpoint_missing`, `test_full_no_backing_file_and_checkpoint_exists`, `test_full_unexpected_backing_file`. | BitmapBackupProvider now performs post-transfer chain-to-FULL verification and checkpoint existence checks for both incremental and FULL backups (design D5). |
| `tests/core/test_list_commands.py` | Add 3 new tests: `test_check_deep_errors_detected`, `test_check_deep_leaks_detected`, `test_check_deep_image_unreadable`. Modify `test_check_deep_clean_image_reports_ok` and `test_deep_chain_check_corruption_detected` to mock JSON with all 3 fields (`errors`, `leaks`, `corruptions`) and assert all are checked. | Deep check now checks `errors` + `leaks` + `corruptions` (not just `corruptions`). Existing tests only mock `corruptions` and `leaks` fields. |
| `tests/core/test_pipeline.py` | Add 2 new tests: `test_deep_check_errors_not_just_corruptions`, `test_deep_check_timeout_7200_seconds`. Modify `test_deep_check_uses_force_share_on_active_layer` to assert 7200s timeout is passed. | Deep check timeout changed from 60s to 7200s (design D6). Existing test does not verify timeout value. |
| `tests/core/test_reconcile.py` | Modify `test_reconcile_removes_orphan_snapshot_files` to add XML context — verify that when XML does NOT reference the file, it is deleted; when XML DOES reference it, it is NOT deleted. Modify `test_reconcile_detects_broken_chain_before_orphan` — under new behavior broken chains are CRITICAL-logged but NOT deleted (for snapshots with XML reference or broken incremental chain). | Reconcile now supplements state from disk+XML reality instead of always deleting orphan files (design D2). Broken chains are NOT auto-rebased or deleted — only CRITICAL logged (design D3). |
| `tests/integration/test_reconcile.py` | Modify `test_reconcile_command` to assert new `ReconcileResult` fields (`state_supplemented`, `xml_refreshed`, `allocation_fixed`). Modify `test_reconcile_removes_orphan_backup_files` to verify state supplementation path when an orphan has an intact chain to a tracked FULL. Modify `test_reconcile_removes_orphan_snapshot_files` to verify the file is only deleted when NOT referenced by domain XML. | New ReconcileResult fields (design D8). New reconcile behavior: supplement state instead of deleting when chain is intact and XML references the file. |
| `tests/config/test_model.py` | Add 2 new tests: `test_globalconfig_default_chain_lengths_24_168_2`, `test_globalconfig_explicit_override_works`. Modify `test_global_config_default_values` to verify new default values. | Default values changed from `None` to `24`/`168`/`2` (design D7). |
| `tests/models/test_results.py` | Add 1 new test: `test_reconcile_result_is_frozen`. Modify existing ReconcileResult tests if any to verify new fields (`state_supplemented`, `xml_refreshed`, `allocation_fixed`). | New ReconcileResult fields added (design D8). |
| `tests/mocks/mock_shell.py` | Add `expect_ordered()` method for verifying command execution order. Add `call_history` property for recording all calls. | New check/reconcile tests need to verify `test -f` → `qemu-img info --backing-chain` → `virsh dumpxml` order and count specific calls. |
| `tests/mocks/mock_modules.py` | Add configurable `MockRetentionEngine` (accept keep/remove lists). Add configurable `MockChangeDetector` (accept `changed` boolean). | New reconcile unit tests need them to simulate various state/detection scenarios. |
| `tests/conftest.py` | Add `domain_xml_backing_store` fixture that returns XML string with valid `<backingStore>` chain. Add `domain_xml_stale_backing_store` fixture that returns XML with stale `<backingStore>`. Add `domain_xml_no_backing_store` fixture. | New check/reconcile unit tests need domain XML fixtures to simulate `virsh dumpxml` output for triple-source verification. |

---

## Test Deletions

| File | Test | Reason |
|---|---|---|
| *(none require outright deletion)* | | |

**Rationale:** All existing tests that cover the old behavior need MODIFICATION rather than DELETION:

| Old Behavior Being Removed | Affected Test | Disposition |
|---|---|---|
| Auto-rebase in reconcile (`_auto_rebase_stuck()`) | No direct reconcile auto-rebase tests exist. The existing auto-rebase tests in `test_blockcommit_recovery.py` and `test_engine.py` test the BLOCKCOMMIT pipeline — `_auto_rebase_stuck()` remains in the pipeline (design D3 exception). | No tests to delete. |
| Orphan file always deleted (no state supplementation) | `test_reconcile_removes_orphan_snapshot_files` (both core unit and integration), `test_reconcile_removes_orphan_backup_files` (integration) | MODIFY: Add XML context check. The deletion path is still valid for files NOT referenced by XML. The new "supplement state" path needs additional test coverage. |
| Deep check only checks `corruptions` | `test_check_deep_clean_image_reports_ok`, `test_deep_chain_check_corruption_detected`, `test_deep_check_uses_force_share_on_active_layer` | MODIFY: These tests need to mock and assert all 3 fields (`errors`, `leaks`, `corruptions`), not just `corruptions`. The tests are still valuable; they just need richer mock data. |
| Reconcile deletes broken-chain orphan files | `test_reconcile_detects_broken_chain_before_orphan` (core unit) | MODIFY: New behavior is CRITICAL log + no deletion. The file should remain on disk and be listed in `broken_chains`. |
| Old default values (None → 0/1) | No existing tests directly test None-default resolution for `snapshot_chain_length`/`target_chain_length`/`target_keep_generations`. These were only tested as explicit values in other retention tests. | No tests to delete. New tests needed to cover new defaults. |

---

## Risks & Edge Cases

| Risk (from design.md Risks / Trade-offs) | Test Coverage |
|---|---|
| **Performance: triple-source check adds ~150-450ms per VM** | Performance not tested in unit tests. Integration tests implicitly verify total runtime is within timeout (3600s). Stress tests in `tests/stress/` could be extended for chain-depth performance profiling. |
| **Behavior change: reconcile no longer deletes orphan files when XML references them** | Covered by `test_reconcile_orphan_snapshot_recorded_in_state` (unit), `test_reconcile_real_orphan_snapshot_recorded` (integration). Dry-run tests verify `--dry-run` shows what WOULD change: `test_reconcile_dry_run_no_modifications` (unit), `test_reconcile_real_dry_run` (integration). |
| **Behavior change: reconcile no longer auto-rebases broken chains** | Covered by `test_reconcile_broken_chain_no_auto_rebase` (unit) — asserts `qemu-img rebase` is NOT called; `test_reconcile_real_broken_chain_no_rebase` (integration) — verifies CRITICAL log emitted, no rebase command. Also covered by `test_reconcile_broken_backup_chain_critical` (targets unit) and `test_reconcile_real_broken_chain_critical` (targets integration). |
| **Default values change may surprise existing users** | Covered by `test_globalconfig_default_chain_lengths_24_168_2` and `test_globalconfig_explicit_override_works` — verifies only defaults change; explicit config values are unaffected. Integration tests verify pipeline behavior with new defaults. |
| **Post-creation validation may reject snapshots that virsh reported as successful** | Covered by `test_post_creation_file_missing`, `test_post_creation_wrong_backing`, `test_post_creation_corrupt_bit`, `test_post_creation_pivot_not_confirmed` (all unit tests). Integration test `test_snapshot_post_creation_validation_failure` tests real virsh success followed by manual file deletion. |
| **Deep check timeout increase from 60s to 7200s may cause long-running checks** | Covered by `test_deep_check_timeout_7200_seconds` (unit) — verifies the timeout parameter is 7200s, not 60s. Integration tests use small test VM disks (256MB) so timeout is not an issue. |
| **Stale domain XML not detected if check is shallow (no `--deep`)** | Covered by `test_check_xml_references_missing_file` (unit) — shallow check with `virsh dumpxml` parsing. Also `test_check_real_vm_stale_xml_after_offline_commit` (integration) — real virsh dumpxml after offline blockcommit. |
| **Race condition: file deleted between `test -f` and `qemu-img info`** | Edge case: post-creation validation runs sequentially (test -f → qemu-img info → domblklist). Integration tests create snapshots on real running VMs where the file is the active layer — it cannot be deleted by external processes in normal operation. A stress test could be added to simulate concurrent deletion, but this is a pathological scenario. |
| **Domain XML with no `<backingStore>` at all (fresh VM, no snapshots)** | Covered by `test_check_all_consistent` (unit) — mock XML with no backingStore. Covered by `test_check_real_vm_all_consistent` (integration) — real VM after snapshot creation. |
| **VM with multiple disks (`vda`, `vdb`) — check verifies all disks** | Edge case not covered in initial test plan. The existing system hardcodes `vda`. Multi-disk support is out of scope for this change. |
| **Checkpoint naming collision on retry** | Covered by existing tests in `tests/modules/backup/test_bitmap.py`: `test_checkpoint_collision_force_cleanup_and_retry`, `test_force_cleanup_checkpoints_deletes_all`. |
| **`_refresh_domain_backing_store()` called on running VM** | Covered by `test_refresh_strips_all_backing_store` (integration) — verifies libvirt re-probes chain on next `virsh start`. Edge case: if VM is running and XML is re-defined, does it affect active domain? This is a libvirt behavior edge case, not a qsnap concern. |
| **Recovery from orphan checkpoint after target config change** | Covered by `test_check_orphan_checkpoint` (unit), `test_check_real_targets_orphan_checkpoint` (integration), `test_reconcile_orphan_checkpoint_deleted` (unit), `test_reconcile_real_orphan_checkpoint` (integration). |
| **Large backing chain depth (50+ snapshots) — triple-source check performance** | Not covered in this test plan. Stress tests in `tests/stress/test_long_chain.py` could be extended to run `core.check()` after a 50+ snapshot chain. Consider for P2/P3. |

---

## Mock Infrastructure Additions

| What | Location | Purpose |
|---|---|---|
| `MockShell.expect_ordered()` | `tests/mocks/mock_shell.py` | Verify command execution order for check (test -f → qemu-img info → virsh dumpxml → virsh domblklist) and reconcile (test -f → qemu-img info → rm -f / record_snapshot) |
| `MockShell.call_history` | `tests/mocks/mock_shell.py` | Record all calls for assertion: "qemu-img rebase was NOT called", "exactly N virsh calls made" |
| Configurable `MockRetentionEngine` | `tests/mocks/mock_modules.py` | Accept `keep`/`remove` lists for varying retention scenarios in reconcile tests |
| Configurable `MockChangeDetector` | `tests/mocks/mock_modules.py` | Accept `changed` boolean for varying change detection in reconcile tests |
| `domain_xml_with_backing_store.xml` fixture | `tests/fixtures/` | XML string with valid `<backingStore>` chain for `virsh dumpxml` mock output |
| `domain_xml_stale_backing_store.xml` fixture | `tests/fixtures/` | XML string with stale `<backingStore>` references for stale XML tests |
| `domain_xml_no_backing_store.xml` fixture | `tests/fixtures/` | XML string with no `<backingStore>` elements for refreshed XML tests |
| `ReconcileResult` new fields | `qsnap/models/results.py` (source code) | `state_supplemented: int`, `xml_refreshed: bool`, `allocation_fixed: bool` (design D8) |

---

## Test Command Invocation

```bash
# Unit + mock + contract (fast, no I/O) — Groups 1-5:
poetry run pytest tests/core/test_check_snapshots.py tests/core/test_check_targets.py tests/core/test_reconcile_snapshots.py tests/core/test_reconcile_targets.py tests/modules/snapshot/test_external.py tests/modules/backup/test_bitmap.py tests/core/test_list_commands.py tests/core/test_pipeline.py tests/core/test_reconcile.py tests/core/test_state_check.py tests/config/test_model.py tests/models/test_results.py tests/mocks/ -v

# Integration (needs libvirt) — Groups 6-8:
poetry run pytest tests/integration/test_check_snapshots.py tests/integration/test_check_targets.py tests/integration/test_reconcile_snapshots.py tests/integration/test_reconcile_targets.py tests/integration/test_post_creation_validation.py tests/integration/test_refresh_backing_store.py tests/integration/test_reconcile.py -v -m integration
```

---

## Implementation Priority

| Priority | Groups | Rationale |
|---|---|---|
| **P0** | `reconcile-snapshots-unit`, `reconcile-targets-unit`, `check-snapshots-unit`, `mock-infra` | Core functionality of the refactored check and reconcile commands. Mock infrastructure needed by all other groups. |
| **P1** | `check-targets-unit`, `check-integration`, `reconcile-integration` | Target verification (backup chains, checkpoints) and real integration verification. |
| **P2** | `post-validation-integration`, `provider-unit-modifications`, `core-unit-modifications` | Post-creation validation, deep check improvements, and modifications to existing tests. |
| **P3** | `config-and-mock-infra` (config tests), fixture XML files | Config default value tests and domain XML fixture files. Low risk, isolated changes. |
