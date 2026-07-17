# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| live-vm-full-backup | VM running-state detection for FULL backup method selection | Running VM triggers NBD-based FULL backup | tests/modules/backup/test_copy.py | test_create_full_backup_nbd_running_vm_succeeds | backup-unit |
| live-vm-full-backup | VM running-state detection for FULL backup method selection | Stopped VM triggers direct convert FULL backup | tests/modules/backup/test_copy.py | test_create_full_backup_direct_stopped_vm_succeeds | backup-unit |
| live-vm-full-backup | VM running-state detection for FULL backup method selection | VM state detection failure falls back to direct convert with warning | tests/modules/backup/test_copy.py | test_create_full_backup_vm_state_detection_fails_falls_back | backup-unit |
| live-vm-full-backup | NBD full-export helper for FULL backups | NBD full export produces standalone qcow2 | tests/modules/backup/test_copy.py | test_nbd_full_export_produces_standalone_qcow2 | backup-unit |
| live-vm-full-backup | NBD full-export helper for FULL backups | NBD socket cleaned up on success | tests/modules/backup/test_copy.py | test_nbd_socket_cleanup_on_success | backup-unit |
| live-vm-full-backup | NBD full-export helper for FULL backups | NBD socket cleaned up on failure | tests/modules/backup/test_copy.py | test_nbd_socket_cleanup_on_failure | backup-unit |
| live-vm-full-backup | NBD full-export helper for FULL backups | No checkpoint created for file-copy NBD FULL | tests/modules/backup/test_copy.py | test_nbd_full_file_copy_no_checkpoint_created | backup-unit |
| live-vm-full-backup | NBD FULL exports current disk state | FULL timestamp matches snapshot, not export time | tests/modules/backup/test_copy.py | test_nbd_full_timestamp_matches_snapshot_not_export_time | backup-unit |
| live-vm-full-backup | Libvirt version check for NBD FULL path | Old libvirt falls back to direct convert with warning | tests/modules/backup/test_copy.py | test_nbd_full_old_libvirt_falls_back_direct_convert | backup-unit |
| live-vm-full-backup | Atomic FULL file creation via NBD | NBD FULL creates tmp then renames | tests/modules/backup/test_copy.py | test_nbd_full_creates_tmp_then_renames | backup-unit |
| live-vm-full-backup | Atomic FULL file creation via NBD | NBD FULL failure leaves no final file | tests/modules/backup/test_copy.py | test_nbd_full_failure_leaves_no_final_file | backup-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via NBD full export | Bitmap FULL via NBD succeeds | tests/modules/backup/test_bitmap.py | test_bitmap_create_full_backup_nbd_succeeds | backup-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via NBD full export | Bitmap FULL socket cleanup | tests/modules/backup/test_bitmap.py | test_bitmap_full_socket_cleanup | backup-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via NBD full export | Bucket-driven FULL no longer crashes bitmap targets | tests/modules/backup/test_bitmap.py | test_bitmap_bucket_driven_full_no_longer_crashes | backup-unit |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | Uncompressed full backup succeeds (stopped VM) | tests/modules/backup/test_copy.py | test_create_full_backup_uncompressed_stopped_vm | backup-unit |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | Compressed full backup succeeds (stopped VM) | tests/modules/backup/test_copy.py | test_create_full_backup_compressed_stopped_vm | backup-unit |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | NBD full backup succeeds (running VM) | tests/modules/backup/test_copy.py | test_create_full_backup_nbd_running_vm_succeeds | backup-unit |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | NBD full backup ignores compress flag | tests/modules/backup/test_copy.py | test_nbd_full_backup_ignores_compress_flag | backup-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL no longer raises NotImplementedError | tests/modules/backup/test_bitmap.py | test_bitmap_full_backup_does_not_raise_not_implemented | backup-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL does not create checkpoint | tests/modules/backup/test_bitmap.py | test_bitmap_full_backup_no_checkpoint | backup-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bucket-driven FULL works for bitmap targets | tests/modules/backup/test_bitmap.py | test_bitmap_bucket_driven_full_no_longer_crashes | backup-unit |
| snapshot-provider | External disk-only snapshot creation | Successful snapshot creation | tests/modules/snapshot/test_external.py | test_create_snapshot_success | force-share-unit |
| snapshot-provider | External disk-only snapshot creation | virsh command fails | tests/modules/snapshot/test_external.py | test_create_snapshot_virsh_fails | force-share-unit |
| snapshot-provider | External disk-only snapshot creation | virsh command times out | tests/modules/snapshot/test_external.py | test_create_snapshot_timeout | force-share-unit |
| snapshot-provider | External disk-only snapshot creation | Post-snapshot qemu-img info uses --force-share on running VM | tests/modules/snapshot/test_external.py | test_post_snapshot_info_uses_force_share | force-share-unit |
| snapshot-provider | External disk-only snapshot creation | Post-snapshot qemu-img info without --force-share fails (regression guard) | tests/modules/snapshot/test_external.py | test_post_snapshot_info_without_force_share_regression | force-share-unit |
| backup-verification | Metadata verification after transfer | Metadata verification passes | tests/modules/backup/test_verification.py | test_metadata_verification_passes | force-share-unit |
| backup-verification | Metadata verification after transfer | Metadata verification fails — wrong format | tests/modules/backup/test_verification.py | test_metadata_verification_wrong_format | force-share-unit |
| backup-verification | Metadata verification after transfer | Metadata verification fails — size mismatch | tests/modules/backup/test_verification.py | test_metadata_verification_size_mismatch | force-share-unit |
| backup-verification | Metadata verification after transfer | Source-side info uses --force-share on active layer | tests/modules/backup/test_verification.py | test_source_side_info_uses_force_share_on_active_layer | force-share-unit |
| backup-verification | Full verification via qemu-img compare | Full verification passes (stopped VM or frozen snapshot) | tests/modules/backup/test_verification.py | test_full_verification_passes_frozen_source | force-share-unit |
| backup-verification | Full verification via qemu-img compare | Full verification detects corruption | tests/modules/backup/test_verification.py | test_full_verification_detects_corruption | force-share-unit |
| backup-verification | Full verification via qemu-img compare | Full verification on live source logs warning | tests/modules/backup/test_verification.py | test_full_verification_live_source_logs_warning | force-share-unit |
| backup-verification | Full verification via qemu-img compare | No verification when verify=off | tests/modules/backup/test_verification.py | test_no_verification_when_off | force-share-unit |
| shell-abstraction | --force-share safety classification for qemu-img operations | Metadata-only operation uses --force-share on active layer | tests/modules/snapshot/test_external.py | test_post_snapshot_info_uses_force_share | force-share-unit |
| shell-abstraction | --force-share safety classification for qemu-img operations | Data-copying operation does NOT use --force-share | tests/modules/backup/test_copy.py | test_nbd_full_no_force_share_on_convert | backup-unit |
| shell-abstraction | --force-share safety classification for qemu-img operations | Lifecycle commit operations remain offline-only | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_no_force_share | force-share-unit |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Intact chain — blockcommit proceeds | tests/core/test_pipeline.py | test_chain_verify_intact_chain_blockcommit_proceeds | core-pipeline |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Missing file in chain — blockcommit skipped | tests/core/test_pipeline.py | test_chain_verify_missing_file_blockcommit_skipped | core-pipeline |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Non-qcow2 file in chain — blockcommit skipped | tests/core/test_pipeline.py | test_chain_verify_non_qcow2_blockcommit_skipped | core-pipeline |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Cyclic reference detected — blockcommit skipped | tests/core/test_pipeline.py | test_chain_verify_cyclic_reference_blockcommit_skipped | core-pipeline |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Broken chain does NOT defer the operation | tests/core/test_pipeline.py | test_chain_verify_broken_chain_does_not_defer | core-pipeline |
| chain-integrity-verification | Post-commit chain length verification | Chain shortened as expected | tests/core/test_pipeline.py | test_chain_verify_post_commit_shortened | core-pipeline |
| chain-integrity-verification | Post-commit chain length verification | Chain length unchanged — CRITICAL | tests/core/test_pipeline.py | test_chain_verify_post_commit_unchanged_critical | core-pipeline |
| chain-integrity-verification | Post-commit chain length verification | Post-commit verification fails — snapshots preserved | tests/core/test_pipeline.py | test_chain_verify_post_commit_failure_snapshots_preserved | core-pipeline |
| chain-integrity-verification | --force-share on check_integrity qemu-img info | check_integrity uses --force-share on active layer | tests/core/test_pipeline.py | test_check_integrity_uses_force_share_on_active_layer | core-pipeline |
| chain-integrity-verification | --force-share on _deep_check_file qemu-img check | Deep check on active layer uses --force-share | tests/core/test_pipeline.py | test_deep_check_uses_force_share_on_active_layer | core-pipeline |
| fork-mode | qsnap fork command creates independent VM from snapshot | Fork creates standalone writable qcow2 (stopped VM) | tests/core/test_fork.py | test_fork_direct_convert_stopped_vm | fork-nbd |
| fork-mode | qsnap fork command creates independent VM from snapshot | Fork creates standalone writable qcow2 (running VM) | tests/core/test_fork.py | test_fork_nbd_running_vm | fork-nbd |
| fork-mode | qsnap fork command creates independent VM from snapshot | Fork defines new libvirt VM | tests/core/test_fork.py | test_fork_defines_new_libvirt_vm_with_modified_xml | fork-nbd |
| fork-mode | qsnap fork command creates independent VM from snapshot | Fork from backup | tests/core/test_fork.py | test_fork_from_backup_resolves_via_backup_provider | fork-nbd |
| fork-mode | qsnap fork command creates independent VM from snapshot | Fork with --add-to-config | tests/core/test_fork.py | test_fork_add_to_config_appends_vm_block | fork-nbd |
| fork-mode | qsnap fork command creates independent VM from snapshot | Fork chain-size estimation uses --force-share | tests/core/test_fork.py | test_fork_chain_size_estimation_uses_force_share | fork-nbd |
| map-change-detection | MapChangeDetector implements IChangeDetector | Allocation map unchanged — no changes | tests/modules/change/test_map_detector.py | test_map_unchanged_no_changes | force-share-unit |
| map-change-detection | MapChangeDetector implements IChangeDetector | Allocation map changed — new region added | tests/modules/change/test_map_detector.py | test_map_changed_new_region | force-share-unit |
| map-change-detection | MapChangeDetector implements IChangeDetector | Zero-fill changes allocation map without total size change | tests/modules/change/test_map_detector.py | test_zero_fill_changes_map_not_size | force-share-unit |
| map-change-detection | MapChangeDetector implements IChangeDetector | qemu-img map command fails | tests/modules/change/test_map_detector.py | test_map_command_fails_failsafe | force-share-unit |
| map-change-detection | MapChangeDetector implements IChangeDetector | Map on running VM uses --force-share | tests/modules/change/test_map_detector.py | test_map_on_running_vm_uses_force_share | force-share-unit |
| cli-interface | Global flag --dry-run / -n | Dry-run logs actions without executing | tests/core/test_pipeline.py | test_dry_run_logs_no_mutation | core-pipeline |
| cli-interface | Global flag --dry-run / -n | Dry-run runs environment validation | tests/core/test_validation.py | test_dry_run_runs_validation_non_fatal_warnings | core-pipeline |
| cli-interface | Global flag --dry-run / -n | Dry-run logs FULL-would-be-created | tests/core/test_pipeline.py | test_dry_run_logs_full_would_be_created | core-pipeline |
| cli-interface | Global flag --dry-run / -n | Dry-run detects VM running state for method selection | tests/core/test_pipeline.py | test_dry_run_detects_vm_running_state_for_method | core-pipeline |
| size-estimation | Core logs size estimation on every pipeline run | Size estimation logged during normal run | tests/core/test_engine.py | test_size_estimation_logged_during_normal_run | core-pipeline |
| size-estimation | Core logs size estimation on every pipeline run | Size estimation logged during dry-run | tests/core/test_engine.py | test_size_estimation_logged_during_dry_run | core-pipeline |
| size-estimation | Core logs size estimation on every pipeline run | Size estimation with no state history | tests/core/test_engine.py | test_size_estimation_no_state_history | core-pipeline |
| size-estimation | Core logs size estimation on every pipeline run | Size estimation uses --force-share on base image | tests/core/test_engine.py | test_size_estimation_uses_force_share_on_base_image | core-pipeline |
| periodic-full-backup | Core triggers full backup before incremental transfer | First backup to target creates FULL | tests/core/test_pipeline.py | test_first_backup_creates_full_via_bucket | core-pipeline |
| periodic-full-backup | Core triggers full backup before incremental transfer | New weekly period triggers FULL (all-buckets mode) | tests/core/test_pipeline.py | test_new_weekly_period_triggers_full | core-pipeline |
| periodic-full-backup | Core triggers full backup before incremental transfer | F-anchor on weekly only triggers FULL at week boundaries | tests/core/test_pipeline.py | test_f_anchor_weekly_only_full_on_week_boundary_not_day | core-pipeline |
| periodic-full-backup | Core triggers full backup before incremental transfer | FULL creation works for both file-copy and bitmap targets | tests/core/test_pipeline.py | test_full_creation_works_for_file_copy_and_bitmap | core-pipeline |
| periodic-full-backup | Core triggers full backup before incremental transfer | Dry-run logs FULL-would-be-created without executing | tests/core/test_pipeline.py | test_dry_run_logs_full_would_be_created_without_executing | core-pipeline |
| env-validation | Pre-flight environment validation before pipeline | Cleanup and orphan detection execute before main checks | tests/core/test_validation.py | test_validate_env_cleanup_before_main_checks | core-pipeline |
| env-validation | Pre-flight environment validation before pipeline | Cleanup skipped when auto_cleanup is false | tests/core/test_validation.py | test_validate_env_cleanup_skipped_when_auto_cleanup_false | core-pipeline |
| env-validation | Pre-flight environment validation before pipeline | All validations pass | tests/core/test_validation.py | test_validate_environment_all_pass | core-pipeline |
| env-validation | Pre-flight environment validation before pipeline | snapshot_dir does not exist | tests/core/test_validation.py | test_validate_environment_snapshot_dir_missing | core-pipeline |
| env-validation | Pre-flight environment validation before pipeline | virsh binary not in PATH | tests/core/test_validation.py | test_validate_environment_virsh_not_in_path | core-pipeline |
| env-validation | Pre-flight environment validation before pipeline | libvirt rejects dominfo — VM not defined | tests/core/test_validation.py | test_validate_environment_vm_not_defined | core-pipeline |
| env-validation | Pre-flight environment validation before pipeline | Dry-run runs validation as non-fatal warnings | tests/core/test_validation.py | test_dry_run_runs_validation_non_fatal_warnings | core-pipeline |
| env-validation | Pre-flight environment validation before pipeline | Non-dry-run aborts on validation failure | tests/core/test_validation.py | test_validate_environment_always_mode_target_missing_error | core-pipeline |
| backup-provider | BitmapBackupProvider.create_full_backup returns BackupResult | contract: create_full_backup returns BackupResult | tests/interfaces/test_backup_provider.py | test_backup_provider_create_full_backup_returns_backup_result | contracts |
| backup-provider | BitmapBackupProvider.create_full_backup returns BackupResult | contract: bucket_level in concrete signatures | tests/interfaces/test_backup_provider.py | test_backup_provider_create_full_backup_bucket_level_in_concrete_signatures | contracts |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via NBD full export | mock: BitmapBackupProvider mock supports create_full_backup | tests/mocks/mock_modules.py | MockBitmapBackupProvider.create_full_backup | contracts |
| live-vm-full-backup | NBD FULL backup integration | NBD full backup creates standalone qcow2 on real libvirt | tests/integration/test_nbd_full_backup.py | test_nbd_full_backup_running_vm_integration | integration |
| live-vm-full-backup | NBD FULL backup integration | NBD full backup on stopped VM uses direct convert | tests/integration/test_nbd_full_backup.py | test_full_backup_stopped_vm_direct_convert_integration | integration |
| live-vm-full-backup | NBD FULL backup integration | NBD socket cleanup after crash | tests/integration/test_nbd_full_backup.py | test_nbd_socket_cleanup_after_crash_integration | integration |
| fork-mode | Fork NBD integration | Fork from running VM via NBD | tests/integration/test_nbd_full_backup.py | test_fork_running_vm_nbd_integration | integration |

## Delegation Groups

### Group: backup-unit

**Scope:** `tests/modules/backup/test_copy.py`, `tests/modules/backup/test_bitmap.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/backup/test_copy.py | 15 | MODIFY |
| tests/modules/backup/test_bitmap.py | 6 | MODIFY |

New tests for `test_copy.py`:
- `test_create_full_backup_nbd_running_vm_succeeds` — VM running → NBD path; verify `virsh backup-begin` called without `--incremental`, `qemu-img convert -n nbd:unix:<socket>` used, no direct `qemu-img convert`
- `test_create_full_backup_direct_stopped_vm_succeeds` — VM stopped → direct convert; verify `dominfo` returns `State: shut off`, direct `qemu-img convert -f qcow2 -O qcow2` called, no NBD
- `test_create_full_backup_vm_state_detection_fails_falls_back` — `virsh dominfo` fails → WARNING logged, direct convert attempted
- `test_nbd_full_export_produces_standalone_qcow2` — result has no backing file; `qemu-img info` on result shows `backing file: <none>`
- `test_nbd_socket_cleanup_on_success` — `rm -f` on socket called after `qemu-img convert` succeeds
- `test_nbd_socket_cleanup_on_failure` — `rm -f` on socket called in `finally` even when `qemu-img convert` fails
- `test_nbd_full_file_copy_no_checkpoint_created` — no `virsh checkpoint-create-as` or `virsh checkpoint-delete` calls
- `test_nbd_full_timestamp_matches_snapshot_not_export_time` — FULL recorded with snapshot timestamp, not NBD export time
- `test_nbd_full_old_libvirt_falls_back_direct_convert` — libvirt < 6.0 → WARNING, direct convert attempted
- `test_nbd_full_creates_tmp_then_renames` — data written to `.tmp`, renamed to final, `BackupResult(success=True, path=<final>)`
- `test_nbd_full_failure_leaves_no_final_file` — `.tmp` removed, no final `vm.FULL.*.qcow2`, `BackupResult(success=False)`
- `test_nbd_full_backup_ignores_compress_flag` — `compress=True` → WARNING logged, NBD path used without `-c`
- `test_nbd_full_no_force_share_on_convert` — no `--force-share` on `qemu-img convert` command (NBD instead)
- `test_create_full_backup_uncompressed_stopped_vm` — MODIFY existing `test_create_full_backup_uncompressed`: add `virsh dominfo` expectation returning `State: shut off`, verify direct convert used, no NBD
- `test_create_full_backup_compressed_stopped_vm` — MODIFY existing `test_create_full_backup_compressed`: add `virsh dominfo` expectation returning `State: shut off`, verify `-c` flag present, no NBD

New tests for `test_bitmap.py`:
- `test_bitmap_create_full_backup_nbd_succeeds` — `create_full_backup()` does NOT raise `NotImplementedError`; calls `virsh backup-begin` without `--incremental`; `qemu-img convert -n nbd:unix:`
- `test_bitmap_full_backup_does_not_raise_not_implemented` — explicit assertion that `create_full_backup` is callable and returns `BackupResult`
- `test_bitmap_full_socket_cleanup` — socket cleanup on success and failure
- `test_bitmap_full_backup_no_checkpoint` — no `checkpoint-create-as` or `checkpoint-delete` called
- `test_bitmap_bucket_driven_full_no_longer_crashes` — `Core._backup_target()` calls `create_full_backup()` for bitmap target, pipeline succeeds
- `test_bitmap_create_full_backup_returns_standalone_qcow2` — result is a standalone qcow2 with no backing file

### Group: force-share-unit

**Scope:** `tests/modules/snapshot/test_external.py`, `tests/modules/change/test_map_detector.py`, `tests/modules/backup/test_verification.py`, `tests/modules/lifecycle/test_blockcommit.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/snapshot/test_external.py | 5 | MODIFY |
| tests/modules/change/test_map_detector.py | 5 | MODIFY |
| tests/modules/backup/test_verification.py | 7 | MODIFY |
| tests/modules/lifecycle/test_blockcommit.py | 1 | MODIFY |

New/modified tests for `test_external.py`:
- `test_post_snapshot_info_uses_force_share` — NEW: after `virsh snapshot-create-as`, verify `qemu-img info` includes `--force-share`; command succeeds despite VM write lock
- `test_post_snapshot_info_without_force_share_regression` — NEW: document the bug being fixed; without `--force-share`, `qemu-img info` fails with lock error on running VM
- `test_create_snapshot_success` — MODIFY: add assertion that `--force-share` is present in `qemu-img info` command

New/modified tests for `test_map_detector.py`:
- `test_map_on_running_vm_uses_force_share` — NEW: verify `qemu-img map` command includes `--force-share` for running VM; command succeeds despite write lock
- `test_map_changed_detected` — MODIFY: add assertion that `--force-share` flag is present in `qemu-img map` command string
- `test_map_unchanged_no_changes` — MODIFY: add assertion that `--force-share` flag is present
- `test_map_command_fails_failsafe` — MODIFY: add assertion that `--force-share` flag is present in the command

New/modified tests for `test_verification.py`:
- `test_source_side_info_uses_force_share_on_active_layer` — NEW: when source may be active layer, source-side `qemu-img info` includes `--force-share`
- `test_full_verification_live_source_logs_warning` — NEW: `verify=full` on running VM active layer → WARNING logged, `qemu-img compare` still executed without `--force-share`
- `test_full_verification_live_source_lock_conflict` — NEW: `qemu-img compare` on locked active layer fails with lock conflict → `BackupResult(success=False, error="lock conflict")` and recommendation for `verify=metadata`
- `test_full_verification_passes_frozen_source` — MODIFY existing: add assertion that `--force-share` is NOT on `qemu-img compare`

Modified tests for `test_blockcommit.py`:
- `test_blockcommit_no_force_share` — NEW: verify `qemu-img commit` does NOT include `--force-share` (lifecycle operations remain offline-only per design D5)

### Group: core-pipeline

**Scope:** `tests/core/test_validation.py`, `tests/core/test_pipeline.py`, `tests/core/test_engine.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_validation.py | 8 | MODIFY |
| tests/core/test_pipeline.py | 17 | MODIFY |
| tests/core/test_engine.py | 4 | MODIFY |

New/modified tests for `test_validation.py`:
- `test_dry_run_runs_validation_non_fatal_warnings` — NEW: dry-run mode → `_validate_environment()` called; validation failure logged as WARNING; pipeline does NOT abort; pipeline continues to log planned actions
- `test_non_dry_run_aborts_on_validation_failure` — ALREADY EXISTS: `test_validate_environment_always_mode_target_missing_error` covers this
- `test_validate_env_cleanup_before_main_checks` — ALREADY EXISTS
- `test_validate_env_cleanup_skipped_when_auto_cleanup_false` — ALREADY EXISTS
- `test_validate_environment_all_pass` — ALREADY EXISTS
- `test_validate_environment_snapshot_dir_missing` — ALREADY EXISTS
- `test_validate_environment_virsh_not_in_path` — ALREADY EXISTS
- `test_validate_environment_vm_not_defined` — ALREADY EXISTS

New/modified tests for `test_pipeline.py`:
- `test_dry_run_logs_full_would_be_created` — NEW: dry-run mode, `_should_create_bucket_full` returns `(True, "weekly")` → INFO log "[dry-run] Would create FULL backup (bucket=weekly, method=NBD, VM=running)"; `provider.create_full_backup()` NOT called
- `test_dry_run_detects_vm_running_state_for_method` — NEW: dry-run detects running VM → log includes `method=NBD`; stopped VM → log includes `method=direct convert`
- `test_dry_run_logs_full_would_be_created_without_executing` — NEW: dry-run with `_should_create_bucket_full` returning True → log indicates FULL would be created; no `virsh backup-begin` or `qemu-img convert` executed
- `test_full_creation_works_for_file_copy_and_bitmap` — NEW: bitmap-mode target with weekly trigger → `BitmapBackupProvider.create_full_backup()` called, succeeds
- `test_check_integrity_uses_force_share_on_active_layer` — NEW: `Core.check_integrity()` iterates over snapshots including active layer → `qemu-img info --force-share --backing-chain` used
- `test_deep_check_uses_force_share_on_active_layer` — NEW: `Core._deep_check_file()` on active layer → `qemu-img check --force-share --output=json` used
- `test_chain_verify_post_commit_shortened` — NEW: post-commit chain length < pre-commit length → verification passes silently
- `test_chain_verify_post_commit_unchanged_critical` — NEW: chain length unchanged after commit → CRITICAL log emitted
- `test_chain_verify_post_commit_failure_snapshots_preserved` — NEW: post-commit verification fails → snapshot removal from state NOT performed
- `test_dry_run_logs_no_mutation` — MODIFY: existing dry-run test, add assertion that `--force-share` is used on read-only shell calls
- `test_dry_run_runs_validation_non_fatal_warnings` — NEW (alternative name in pipeline)

New/modified tests for `test_engine.py`:
- `test_size_estimation_uses_force_share_on_base_image` — NEW: `qemu-img info` on base image includes `--force-share` when base is locked as backing file by running VM
- `test_size_estimation_logged_during_dry_run` — MODIFY: add assertion that dry-run output includes `[dry-run] FULL would be created (bucket=..., method=...)` when `_should_create_bucket_full` returns True
- `test_size_estimation_logged_during_normal_run` — ALREADY EXISTS
- `test_size_estimation_no_state_history` — ALREADY EXISTS

### Group: fork-nbd

**Scope:** `tests/core/test_fork.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_fork.py | 6 | MODIFY |

New/modified tests:
- `test_fork_direct_convert_stopped_vm` — MODIFY `test_fork_creates_standalone_qcow2_via_qemu_img_convert`: add `virsh dominfo` expectation returning `State: shut off`, verify direct `qemu-img convert -O qcow2` used
- `test_fork_nbd_running_vm` — NEW: VM running → `virsh backup-begin` called, `qemu-img convert -n nbd:unix:<socket>` used, no lock conflict
- `test_fork_chain_size_estimation_uses_force_share` — NEW: fork chain-size estimation → `qemu-img info --force-share --backing-chain` called; command succeeds despite VM holding write lock
- `test_fork_defines_new_libvirt_vm_with_modified_xml` — ALREADY EXISTS
- `test_fork_from_backup_resolves_via_backup_provider` — ALREADY EXISTS
- `test_fork_add_to_config_appends_vm_block` — ALREADY EXISTS

### Group: contracts

**Scope:** `tests/interfaces/test_backup_provider.py`, `tests/mocks/mock_modules.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/interfaces/test_backup_provider.py | 2 | MODIFY |
| tests/mocks/mock_modules.py | 1 | MODIFY |

Modifications:
- `tests/interfaces/test_backup_provider.py`:
  - `test_backup_provider_create_full_backup_returns_backup_result` — MODIFY: add `BitmapBackupProvider` (with pre-configured MockShell) to parametrization; verify it returns `BackupResult` (not raises `NotImplementedError`)
  - `test_backup_provider_create_full_backup_bucket_level_in_concrete_signatures` — MODIFY: add `BitmapBackupProvider` to parametrization; verify `bucket_level` in signature
- `tests/mocks/mock_modules.py`:
  - `MockBitmapBackupProvider` — MODIFY: implement `create_full_backup()` that returns `BackupResult(success=True, ...)` with `.FULL.bucket_level.qcow2` in target path, instead of raising `NotImplementedError`

### Group: integration

**Scope:** `tests/integration/test_nbd_full_backup.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/integration/test_nbd_full_backup.py | 4 | NEW |

New integration tests (marked `@pytest.mark.integration`, require running libvirt daemon):
- `test_nbd_full_backup_running_vm_integration` — Create a tiny test VM, start it, call `FileCopyBackupProvider.create_full_backup()`, verify standalone qcow2 produced via NBD, no lock conflict
- `test_full_backup_stopped_vm_direct_convert_integration` — Stop the test VM, call `create_full_backup()`, verify direct `qemu-img convert` used, no NBD started
- `test_nbd_socket_cleanup_after_crash_integration` — Simulate kill during NBD export, verify socket file removed, no leftover `.tmp` files
- `test_fork_running_vm_nbd_integration` — Fork from running VM snapshot via NBD, verify standalone qcow2 produced, VM defined successfully

## Test Modifications

| File | Change | Reason |
|---|---|---|
| tests/modules/backup/test_copy.py | Add 11 new NBD FULL backup tests; add `virsh dominfo` expectations to existing `test_create_full_backup_uncompressed` and `test_create_full_backup_compressed` to verify VM state detection branch | FileCopyBackupProvider.create_full_backup now has a hybrid NBD/direct path (D1); need test coverage for both branches, VM state detection, socket lifecycle, atomic creation, compress flag handling, and force-share safety |
| tests/modules/backup/test_bitmap.py | Add 6 new tests for BitmapBackupProvider.create_full_backup via NBD full export | BitmapBackupProvider.create_full_backup no longer raises NotImplementedError (D4); tests must verify NBD full export, socket cleanup, no checkpoint creation, and bucket-driven FULL compatibility |
| tests/modules/snapshot/test_external.py | Add `test_post_snapshot_info_uses_force_share` and `test_post_snapshot_info_without_force_share_regression`; modify existing success test to assert `--force-share` in `qemu-img info` command | Post-snapshot `qemu-img info` now uses `--force-share` per design D5; fix confirmed lock-conflict bug |
| tests/modules/change/test_map_detector.py | Add `test_map_on_running_vm_uses_force_share`; modify existing tests to assert `--force-share` in `qemu-img map` command | MapChangeDetector now uses `--force-share` per design D5; fix confirmed lock-conflict bug |
| tests/modules/backup/test_verification.py | Add `test_source_side_info_uses_force_share_on_active_layer`, `test_full_verification_live_source_logs_warning`, `test_full_verification_live_source_lock_conflict` | Source-side verification needs `--force-share` for metadata operations; full verification on live sources must log warning and must NOT add `--force-share` to `qemu-img compare` (data-copying operation) per design D5 and risk mitigation |
| tests/modules/lifecycle/test_blockcommit.py | Add `test_blockcommit_no_force_share` | Lifecycle commit operations remain offline-only; must NOT add `--force-share` per design D5 |
| tests/core/test_validation.py | Add `test_dry_run_runs_validation_non_fatal_warnings` | Dry-run now runs `_validate_environment()` and logs failures as warnings (D6); existing skip behavior removed |
| tests/core/test_pipeline.py | Add 8 new tests for dry-run FULL-would-be-created, VM state detection for method selection, chain integrity `--force-share`, post-commit verification, bitmap target FULL; modify existing dry-run test to assert `--force-share` on read-only calls | Dry-run now evaluates FULL creation decision (D7) and runs validation (D6); chain integrity uses `--force-share` on active layers |
| tests/core/test_fork.py | Add `test_fork_nbd_running_vm` and `test_fork_chain_size_estimation_uses_force_share`; modify `test_fork_creates_standalone_qcow2_via_qemu_img_convert` to verify VM state branch | Fork now uses NBD for running VMs (D9) and `--force-share` on chain info |
| tests/core/test_engine.py | Add `test_size_estimation_uses_force_share_on_base_image`; modify dry-run size estimation test to assert FULL-would-be-created indicator | Size estimation uses `--force-share` on active-layer base image per design D5; dry-run now includes FULL creation indicator |
| tests/interfaces/test_backup_provider.py | Add `BitmapBackupProvider` (with pre-configured MockShell for libvirt version check) to parametrization of `test_backup_provider_create_full_backup_returns_backup_result` and `test_backup_provider_create_full_backup_bucket_level_in_concrete_signatures` | New concrete implementation (BitmapBackupProvider.create_full_backup) must pass contract tests |
| tests/mocks/mock_modules.py | Implement `MockBitmapBackupProvider.create_full_backup()` to return valid `BackupResult` instead of raising `NotImplementedError` | Mock must match updated interface behavior; contract tests need a working mock |

## Risks & Edge Cases

- **[Risk] NBD export fails on old libvirt (< 6.0)** → `test_nbd_full_old_libvirt_falls_back_direct_convert`: verify WARNING logged and direct convert attempted; if that fails with lock error, `BackupResult(success=False)` returned with clear error message
- **[Risk] NBD FULL exports current disk state, not snapshot state** → `test_nbd_full_timestamp_matches_snapshot_not_export_time`: verify FULL timestamp is snapshot timestamp, not NBD export time; retention bucket alignment uses snapshot time
- **[Risk] `qemu-img compare` with `--force-share` on live sources** → `test_full_verification_live_source_logs_warning`: verify WARNING logged; `qemu-img compare` run WITHOUT `--force-share`; if it fails with lock error, `BackupResult(success=False)` with clear message recommending `verify=metadata`
- **[Risk] Dry-run validation may produce false positives** → `test_dry_run_runs_validation_non_fatal_warnings`: verify failures are WARNING only, pipeline does NOT abort; real run still aborts on validation failure (tested separately)
- **[Risk] NBD socket left behind on crash** → `test_nbd_socket_cleanup_on_failure` and integration `test_nbd_socket_cleanup_after_crash_integration`: verify socket removed in `finally` block; stale socket cleaned before starting (`rm -f`)
- **[Trade-off] NBD FULL is slower than direct convert for stopped VMs** → `test_create_full_backup_direct_stopped_vm_succeeds`: verify direct convert path used for stopped VMs, no NBD overhead; performance regression guard
- **[Trade-off] Two code paths for FULL backup** → All backup-unit tests cover both branches (NBD for running, direct for stopped); `test_nbd_full_no_force_share_on_convert` verifies `--force-share` NOT used on data-copying operations; `test_nbd_full_backup_ignores_compress_flag` covers compress flag branch
- **[Edge] VM state detection failure** → `test_create_full_backup_vm_state_detection_fails_falls_back`: `virsh dominfo` fails (non-zero exit) → WARNING logged, direct convert attempted as best-effort fallback
- **[Edge] BitmapBackupProvider constructor libvirt check as side effect in contract tests** → `test_backup_provider_create_full_backup_returns_backup_result` must use pre-configured MockShell with `virsh --version` returning >= 6.0 for BitmapBackupProvider parametrization
- **[Edge] Concurrent qsnap runs — socket collision** → Integration test: verify PID-based socket naming (`/tmp/qsnap-backup-{pid}.sock`) prevents collision between concurrent qsnap instances
- **[Edge] `qemu-img commit` intentionally excluded from `--force-share`** → `test_blockcommit_no_force_share`: verify lifecycle operations do NOT add `--force-share`; these are intentionally offline-only per design D5 classification table
