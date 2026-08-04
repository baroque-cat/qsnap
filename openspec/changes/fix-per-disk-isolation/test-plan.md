# QA Strategy & Test Plan

## Coverage Map

### triple-source-check (9 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| triple-source-check | Triple-source snapshot verification per disk | All three sources consistent | tests/integration/test_check_snapshots.py | test_check_real_vm_all_consistent | check-real-multi-disk |
| triple-source-check | Triple-source snapshot verification per disk | Phantom snapshot — state has, disk and XML do not | tests/integration/test_check_snapshots.py | test_check_real_vm_after_blockcommit | check-real-multi-disk |
| triple-source-check | Triple-source snapshot verification per disk | Stale domain XML — state and disk agree, XML references missing file | tests/integration/test_check_snapshots.py | test_check_real_vm_stale_xml_after_offline_commit | check-real-multi-disk |
| triple-source-check | Triple-source snapshot verification per disk | Orphan file — disk has, state does not, XML references | tests/integration/test_check_snapshots.py | test_check_real_vm_phantom_snapshot | check-real-multi-disk |
| triple-source-check | Triple-source snapshot verification per disk | Legitimate deletion — all three sources agree file is gone | tests/integration/test_check_snapshots.py | test_check_real_vm_after_refresh_xml | check-real-multi-disk |
| triple-source-check | Triple-source snapshot verification per disk | Broken backing chain — file missing from middle | tests/integration/test_check_snapshots.py | test_check_real_vm_phantom_snapshot | check-real-multi-disk |
| triple-source-check | Triple-source snapshot verification per disk | Active layer mismatch | tests/integration/test_check_snapshots.py | test_check_real_vm_stale_xml_after_offline_commit | check-real-multi-disk |
| triple-source-check | Triple-source snapshot verification per disk | Multi-disk VM — each disk compared against its own newest snapshot | tests/core/test_check_per_disk.py | test_verify_active_layer_match_multi_disk_per_group | core-triple-check-unit |
| triple-source-check | Triple-source snapshot verification per disk | Disk without snapshots is skipped | tests/core/test_check_per_disk.py | test_verify_active_layer_match_disk_without_snapshots_skipped | core-triple-check-unit |

### result-types (3 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| result-types | BackupResult dataclass | Successful backup transfer | tests/models/test_results.py | test_backup_result_with_disk | models-results-disk |
| result-types | BackupResult dataclass | BackupResult carries disk | tests/models/test_results.py | test_backup_result_carries_disk | models-results-disk |
| result-types | BackupResult dataclass | BackupResult disk defaults to None | tests/models/test_results.py | test_backup_result_disk_defaults_none | models-results-disk |

### action-audit-trail (12 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| action-audit-trail | ActionRecord dataclass | ActionRecord is immutable | tests/models/test_results.py | test_action_record_disk_frozen | models-results-disk |
| action-audit-trail | ActionRecord dataclass | ActionRecord size and duration default to zero | tests/models/test_results.py | test_action_record_disk_defaults | models-results-disk |
| action-audit-trail | ActionRecord dataclass | ActionRecord carries disk | tests/models/test_results.py | test_action_record_carries_disk | models-results-disk |
| action-audit-trail | ActionRecord dataclass | VM-level error record has no disk | tests/models/test_results.py | test_action_record_error_disk_none | models-results-disk |
| action-audit-trail | ActionRecord accumulation in Core | Core clears actions at start of run | tests/core/test_engine.py | test_actions_cleared_at_run_start | core-audit-disk |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on snapshot create | tests/core/test_engine.py | test_action_appended_on_snapshot_create_with_disk | core-audit-disk |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on snapshot delete (blockcommit) | tests/core/test_engine.py | test_action_appended_on_snapshot_delete_with_disk | core-audit-disk |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on backup transfer | tests/core/test_engine.py | test_action_appended_on_backup_transfer_with_disk | core-audit-disk |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on FULL backup creation | tests/core/test_engine.py | test_action_appended_on_full_backup_with_disk | core-audit-disk |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on backup deletion | tests/core/test_engine.py | test_action_appended_on_backup_delete_with_disk | core-audit-disk |
| action-audit-trail | ActionRecord accumulation in Core | Core appends error action on failure | tests/core/test_engine.py | test_error_action_appended_with_disk | core-audit-disk |
| action-audit-trail | ActionRecord accumulation in Core | Core does not append actions in dry-run for mutations | tests/core/test_engine.py | test_no_actions_in_dry_run_mutations | core-audit-disk |

### backup-summary (3 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-summary | Summary lines carry disk prefix | Disk-scoped action line shows disk prefix | tests/cli/test_summary.py | test_summary_disk_scoped_shows_prefix | cli-summary-disk |
| backup-summary | Summary lines carry disk prefix | VM-level error line has no disk prefix | tests/cli/test_summary.py | test_summary_vm_level_error_no_prefix | cli-summary-disk |
| backup-summary | Summary lines carry disk prefix | Multi-disk run distinguishes disks in summary | tests/cli/test_summary.py | test_summary_multi_disk_distinguishes_disks | cli-summary-disk |

### transaction-log (3 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| transaction-log | Transaction log line format is frozen and disk-aware via paths | Snapshot line keeps six fields with disk in path | tests/utils/test_transaction.py | test_write_snapshot_create_line_unchanged_six_fields | utils-tx-log |
| transaction-log | Transaction log line format is frozen and disk-aware via paths | Backup transfer line keeps six fields with disk in path | tests/utils/test_transaction.py | test_write_backup_transfer_line_unchanged_six_fields | utils-tx-log |
| transaction-log | Transaction log line format is frozen and disk-aware via paths | VM-level error line unchanged | tests/utils/test_transaction.py | test_write_error_line_unchanged | utils-tx-log |

### backup-provider (4 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-provider | Backup results carry the source disk | Incremental transfer result carries disk | tests/interfaces/test_backup_provider.py | test_transfer_missing_result_carries_disk | contract-backup-disk |
| backup-provider | Backup results carry the source disk | Multi-disk transfer returns per-disk results | tests/modules/backup/test_bitmap.py | test_transfer_missing_multi_disk_per_disk_results | backup-bitmap-disk |
| backup-provider | Backup results carry the source disk | FULL creation result carries disk | tests/interfaces/test_backup_provider.py | test_create_full_backup_result_carries_disk | contract-backup-disk |
| backup-provider | Backup results carry the source disk | Failed transfer result still carries disk | tests/modules/backup/test_bitmap.py | test_transfer_missing_failed_still_carries_disk | backup-bitmap-disk |

### fork-mode (15 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| fork-mode | qsnap fork command creates independent qcow2 from snapshot or backup | Fork creates standalone writable qcow2 from snapshot | tests/integration/test_fork.py | test_fork_full_pipeline_creates_standalone_qcow2 | fork-integration |
| fork-mode | qsnap fork command creates independent qcow2 from snapshot or backup | Fork creates standalone qcow2 from backup target | tests/integration/test_fork.py | test_fork_from_incremental_flattens_chain | fork-integration |
| fork-mode | qsnap fork command creates independent qcow2 from snapshot or backup | Fork from incremental backup flattens chain | tests/integration/test_fork.py | test_fork_from_incremental_flattens_chain | fork-integration |
| fork-mode | qsnap fork command creates independent qcow2 from snapshot or backup | Fork logs estimated size before converting | tests/integration/test_fork.py | test_fork_pipeline_logs_chain_size | fork-integration |
| fork-mode | qsnap fork command creates independent qcow2 from snapshot or backup | Fork fails on nonexistent snapshot | tests/integration/test_fork.py | test_fork_pipeline_nonexistent_snapshot | fork-integration |
| fork-mode | qsnap fork command creates independent qcow2 from snapshot or backup | Fork verifies the converted output | tests/integration/test_fork.py | test_fork_verifies_output_after_convert | fork-integration |
| fork-mode | qsnap fork command creates independent qcow2 from snapshot or backup | Fork removes output when verification fails | tests/integration/test_fork.py | test_fork_removes_output_on_verify_failure | fork-integration |
| fork-mode | qsnap fork command creates independent qcow2 from snapshot or backup | Fork removes partial output when conversion fails | tests/integration/test_fork.py | test_fork_removes_partial_output_on_convert_failure | fork-integration |
| fork-mode | Core.fork method | fork returns RestoreResult on success | tests/core/test_fork.py | test_fork_returns_restore_result_on_success | core-fork-unit |
| fork-mode | Core.fork method | fork fails on nonexistent snapshot | tests/core/test_fork.py | test_fork_snapshot_not_found_returns_failure | core-fork-unit |
| fork-mode | Core.fork method | fork does not touch XML or state | tests/core/test_fork.py | test_fork_no_xml_or_state_mutation | core-fork-unit |
| fork-mode | Core.fork method | fork dry-run logs the plan and creates no file | tests/core/test_fork.py | test_fork_dry_run_logs_plan_no_file | core-fork-unit |
| fork-mode | Fork accepts a local dry-run flag | Local --dry-run activates fork dry-run | tests/cli/test_commands.py | test_fork_local_dry_run_flag | cli-commands-disk |
| fork-mode | Fork accepts a local dry-run flag | Global -n activates fork dry-run | tests/cli/test_commands.py | test_fork_global_n_flag | cli-commands-disk |
| fork-mode | Fork accepts a local dry-run flag | Fork without any dry-run flag converts normally | tests/cli/test_commands.py | test_fork_no_dry_run_converts | cli-commands-disk |

### cli-interface (4 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| cli-interface | qsnap fork subcommand | Fork command succeeds | tests/cli/test_commands.py | test_fork_command_dispatches_to_core_fork | cli-commands-disk |
| cli-interface | qsnap fork subcommand | Fork command fails on missing snapshot | tests/cli/test_commands.py | test_fork_command_missing_snapshot_exit_one | cli-commands-disk |
| cli-interface | qsnap fork subcommand | Fork without --output fails | tests/cli/test_app.py | test_fork_requires_output | cli-commands-disk |
| cli-interface | qsnap fork subcommand | Fork with --dry-run previews without converting | tests/cli/test_commands.py | test_fork_dry_run_previews | cli-commands-disk |

### state-management (14 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| state-management | IStateManager reset_vm_state method | reset_vm_state clears all per-VM state | tests/interfaces/test_state_manager.py | test_istate_manager_reset_methods_abstract | contract-state-perdisk |
| state-management | IStateManager reset_vm_state method | reset_vm_state is atomic | tests/state/test_manager.py | test_reset_vm_state_atomic | state-per-disk-reset |
| state-management | IStateManager reset_vm_state method | reset_vm_state for nonexistent VM | tests/state/test_manager.py | test_reset_vm_state_nonexistent_noop | state-per-disk-reset |
| state-management | IStateManager reset_target_state method | reset_target_state clears all per-target state | tests/interfaces/test_state_manager.py | test_istate_manager_reset_methods_abstract | contract-state-perdisk |
| state-management | IStateManager reset_target_state method | reset_target_state is atomic | tests/state/test_manager.py | test_reset_target_state_atomic | state-per-disk-reset |
| state-management | IStateManager reset_target_state method | reset_target_state for nonexistent target | tests/state/test_manager.py | test_reset_target_state_nonexistent_noop | state-per-disk-reset |
| state-management | IStateManager reset_vm_disk_state method | reset_vm_disk_state clears only the given disk | tests/state/test_manager.py | test_reset_vm_disk_state_clears_only_given_disk | state-per-disk-reset |
| state-management | IStateManager reset_vm_disk_state method | reset_vm_disk_state handles legacy bare-integer allocation | tests/state/test_manager.py | test_reset_vm_disk_state_legacy_bare_int | state-per-disk-reset |
| state-management | IStateManager reset_vm_disk_state method | reset_vm_disk_state for unknown VM or disk | tests/state/test_manager.py | test_reset_vm_disk_state_unknown_noop | state-per-disk-reset |
| state-management | IStateManager reset_vm_disk_state method | reset_vm_disk_state is atomic | tests/state/test_manager.py | test_reset_vm_disk_state_atomic | state-per-disk-reset |
| state-management | IStateManager reset_target_disk_state method | reset_target_disk_state clears only the given VM and disk | tests/state/test_manager.py | test_reset_target_disk_state_clears_only_given_vm_disk | state-per-disk-reset |
| state-management | IStateManager reset_target_disk_state method | reset_target_disk_state removes only the disk's dependencies | tests/state/test_manager.py | test_reset_target_disk_state_removes_only_disk_deps | state-per-disk-reset |
| state-management | IStateManager reset_target_disk_state method | reset_target_disk_state for unknown target | tests/state/test_manager.py | test_reset_target_disk_state_unknown_noop | state-per-disk-reset |
| state-management | IStateManager reset_target_disk_state method | reset_target_disk_state is atomic | tests/state/test_manager.py | test_reset_target_disk_state_atomic | state-per-disk-reset |

### restore-command (18 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| restore-command | Restore command replaces the resolved disk atomically | Restore from snapshot replaces the resolved disk | tests/integration/test_restore.py | test_restore_full_pipeline_replaces_disk | core-restore-perdisk |
| restore-command | Restore command replaces the resolved disk atomically | Restore from backup with disk in filename | tests/integration/test_restore.py | test_restore_from_backup_resolves_disk | core-restore-perdisk |
| restore-command | Restore command replaces the resolved disk atomically | Restore aborts on running VM | tests/integration/test_restore.py | test_restore_pipeline_vm_running_fails | core-restore-perdisk |
| restore-command | Restore command replaces the resolved disk atomically | Restore aborts on broken source chain | tests/integration/test_restore.py | test_restore_prechecks_chain_integrity | core-restore-perdisk |
| restore-command | Restore command replaces the resolved disk atomically | Restore aborts when disk cannot be determined | tests/integration/test_restore.py | test_restore_aborts_when_disk_cannot_be_determined | core-restore-perdisk |
| restore-command | Restore command replaces the resolved disk atomically | Restore aborts when disk is not in VM config | tests/integration/test_restore.py | test_restore_aborts_when_disk_not_in_config | core-restore-perdisk |
| restore-command | Restore command replaces the resolved disk atomically | Restore with --dry-run shows planned actions | tests/integration/test_restore.py | test_restore_pipeline_dry_run | core-restore-perdisk |
| restore-command | Restore command replaces the resolved disk atomically | Restore with --yes skips confirmation | tests/cli/test_commands.py | test_handle_restore_yes_skips_confirmation | cli-commands-disk |
| restore-command | Restore command replaces the resolved disk atomically | Restore prompts for confirmation without --yes | tests/cli/test_commands.py | test_handle_restore_prompts_confirmation_without_yes | cli-commands-disk |
| restore-command | Restore command replaces the resolved disk atomically | Restore verifies the temp image before replacing the base | tests/integration/test_restore.py | test_restore_verifies_temp_before_replace | core-restore-perdisk |
| restore-command | Restore command replaces the resolved disk atomically | Restore cleans up only the restored disk's checkpoints | tests/integration/test_restore.py | test_restore_cleanup_only_restored_disk_checkpoints | core-restore-perdisk |
| restore-command | Restore command replaces the resolved disk atomically | Restore skips legacy checkpoints without a disk segment | tests/integration/test_restore.py | test_restore_skips_legacy_checkpoints | core-restore-perdisk |
| restore-command | Restore command replaces the resolved disk atomically | Restore resets only the restored disk's state | tests/integration/test_restore.py | test_restore_resets_only_restored_disk_state | core-restore-perdisk |
| restore-command | Restore command replaces the resolved disk atomically | Restore leaves other disks and other VMs intact | tests/integration/test_multi_disk.py | test_restore_single_disk_isolation | core-restore-perdisk |
| restore-command | Restore command replaces the resolved disk atomically | Restore from nonexistent snapshot | tests/integration/test_restore.py | test_restore_nonexistent_snapshot | core-restore-perdisk |
| restore-command | Core.restore method | Restore from snapshot identifies disk | tests/core/test_restore.py | test_restore_from_snapshot_identifies_disk | core-restore-unit |
| restore-command | Core.restore method | Restore from backup identifies disk | tests/core/test_restore.py | test_restore_from_backup_identifies_disk | core-restore-unit |
| restore-command | Core.restore method | Restore fails on running VM | tests/core/test_restore.py | test_restore_vm_running_fails | core-restore-unit |

### core-orchestrator (9 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| core-orchestrator | Core.fork method | fork from snapshot creates standalone qcow2 | tests/core/test_fork.py | test_fork_creates_standalone_qcow2_from_snapshot | core-fork-unit |
| core-orchestrator | Core.fork method | fork from incremental backup flattens chain | tests/core/test_fork.py | test_fork_flattens_incremental_chain | core-fork-unit |
| core-orchestrator | Core.fork method | fork dry-run creates no file | tests/core/test_fork.py | test_fork_dry_run_logs_plan_no_file | core-fork-unit |
| core-orchestrator | Core.fork method | fork verifies output and removes it on verification failure | tests/core/test_fork.py | test_fork_verify_failure_removes_output | core-fork-unit |
| core-orchestrator | Core.restore method | restore from snapshot replaces VM disk | tests/integration/test_restore.py | test_restore_full_pipeline_replaces_disk | core-restore-perdisk |
| core-orchestrator | Core.restore method | restore aborts on running VM | tests/integration/test_restore.py | test_restore_pipeline_vm_running_fails | core-restore-perdisk |
| core-orchestrator | Core.restore method | restore aborts on broken source chain | tests/integration/test_restore.py | test_restore_prechecks_chain_integrity | core-restore-perdisk |
| core-orchestrator | Core.restore method | restore aborts when temp image verification fails | tests/integration/test_restore.py | test_restore_verifies_temp_before_replace | core-restore-perdisk |
| core-orchestrator | Core.restore method | restore dry-run shows planned actions | tests/integration/test_restore.py | test_restore_pipeline_dry_run | core-restore-perdisk |
| core-orchestrator | Core.restore method | restore keeps other disks' state and checkpoints | tests/integration/test_multi_disk.py | test_restore_single_disk_isolation | core-restore-perdisk |

### standalone-image-conversion (9 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| standalone-image-conversion | convert_to_standalone flattens a backing chain | Successful conversion | tests/utils/test_convert.py | test_convert_to_standalone_success | convert-utils-unit |
| standalone-image-conversion | convert_to_standalone flattens a backing chain | Failed conversion removes partial output | tests/utils/test_convert.py | test_convert_failure_removes_partial | convert-utils-unit |
| standalone-image-conversion | convert_to_standalone flattens a backing chain | Expected failures are returned, not raised | tests/utils/test_convert.py | test_convert_failures_returned_not_raised | convert-utils-unit |
| standalone-image-conversion | verify_standalone_image verifies conversion output | Healthy image passes verification | tests/utils/test_convert.py | test_verify_standalone_image_passes | convert-utils-unit |
| standalone-image-conversion | verify_standalone_image verifies conversion output | Virtual-size mismatch fails M1 | tests/utils/test_convert.py | test_verify_m1_virtual_size_mismatch | convert-utils-unit |
| standalone-image-conversion | verify_standalone_image verifies conversion output | Corrupted output fails M2 | tests/utils/test_convert.py | test_verify_m2_corrupted_output | convert-utils-unit |
| standalone-image-conversion | convert_with_retry applies the backup retry policy | Transient failure then success | tests/utils/test_convert.py | test_convert_with_retry_transient_then_success | convert-utils-unit |
| standalone-image-conversion | convert_with_retry applies the backup retry policy | Non-retryable error fails immediately | tests/utils/test_convert.py | test_convert_with_retry_non_retryable_fails | convert-utils-unit |
| standalone-image-conversion | convert_with_retry applies the backup retry policy | Retries exhausted | tests/utils/test_convert.py | test_convert_with_retry_exhausted | convert-utils-unit |
| standalone-image-conversion | Conversion helpers are stateless utilities | Helpers perform no state or libvirt mutations | tests/utils/test_convert.py | test_convert_helpers_stateless_no_mutations | convert-utils-unit |

## Delegation Groups

### Group: models-results-disk
**Scope:** `tests/models/test_results.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/models/test_results.py | 7 (ActionRecord disk + immutability, BackupResult disk + defaults) | MODIFY |

### Group: core-audit-disk
**Scope:** `tests/core/test_engine.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_engine.py | 8 (ActionRecord accumulation with disk field across all action types) | MODIFY |

### Group: core-triple-check-unit
**Scope:** `tests/core/test_check_per_disk.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_check_per_disk.py | 2 (multi-disk per-group comparison, disk-without-snapshots skipped) | NEW |

### Group: cli-summary-disk
**Scope:** `tests/cli/test_summary.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/cli/test_summary.py | 3 (disk prefix on scoped lines, VM-level no prefix, multi-disk distinction) | MODIFY |

### Group: utils-tx-log
**Scope:** `tests/utils/test_transaction.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/utils/test_transaction.py | 3 (six-field format unchanged for snapshot, backup transfer, error lines) | MODIFY |

### Group: contract-backup-disk
**Scope:** `tests/interfaces/test_backup_provider.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/interfaces/test_backup_provider.py | 2 (transfer_missing result carries disk, create_full_backup result carries disk) | MODIFY |

### Group: backup-bitmap-disk
**Scope:** `tests/modules/backup/test_bitmap.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/backup/test_bitmap.py | 2 (multi-disk per-disk results, failed transfer still carries disk) | MODIFY |

### Group: contract-state-perdisk
**Scope:** `tests/interfaces/test_state_manager.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/interfaces/test_state_manager.py | 4 (per-disk reset abstract enforcement, concrete implementations have methods) | MODIFY |

### Group: state-per-disk-reset
**Scope:** `tests/state/test_manager.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/state/test_manager.py | 12 (all 4 per-disk reset methods × atomic/nonexistent/disk-isolation scenarios) | MODIFY |

### Group: core-fork-unit
**Scope:** `tests/core/test_fork.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_fork.py | 8 (fork dry-run, verify/remove-on-failure, no XML/state mutation) | MODIFY |

### Group: cli-commands-disk
**Scope:** `tests/cli/test_commands.py`, `tests/cli/test_app.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/cli/test_commands.py | 8 (fork local/global dry-run flags, dispatch, preview log; restore --yes, confirmation prompt, dispatch) | MODIFY |
| tests/cli/test_app.py | 2 (fork --output required, fork --dry-run parsed) | MODIFY |

### Group: fork-integration
**Scope:** `tests/integration/test_fork.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/integration/test_fork.py | 8 (fork pipeline, verification, retry, partial-file cleanup, dry-run) | MODIFY |

### Group: core-restore-perdisk
**Scope:** `tests/integration/test_restore.py`, `tests/integration/test_multi_disk.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/integration/test_restore.py | 11 (per-disk state reset, per-disk checkpoint cleanup, tmp verify before replace, legacy checkpoint skip) | MODIFY |
| tests/integration/test_multi_disk.py | 2 (restore single disk leaves other disk intact, state isolation) | MODIFY |

### Group: core-restore-unit
**Scope:** `tests/core/test_restore.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_restore.py | 3 (disk identification from snapshot/backup, VM-running failure) + 2 deletions + 5 replacement/new tests (see Test Deletions & Modifications) | MODIFY |

### Group: convert-utils-unit
**Scope:** `tests/utils/test_convert.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/utils/test_convert.py | 10 (convert/verify/retry/partial-cleanup/stateless all scenarios) | NEW |

### Group: check-real-multi-disk
**Scope:** `tests/integration/test_check_snapshots.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/integration/test_check_snapshots.py | 9 (7 existing tests verified unchanged with single-disk VMs; 2 new multi-disk tests) | MODIFY |

## Test Deletions

| File | Test Function | Why Deleted | Replaced By |
|---|---|---|---|
| `tests/core/test_restore.py` | `test_restore_resets_all_vm_state` | Asserts restore calls `reset_vm_state('testvm')` and `reset_target_state(target.path)` — the full-reset behavior REMOVED by design D4. Under the restore-command delta (step 11) restore MUST call `reset_vm_disk_state(vm_name, disk)` and `reset_target_disk_state(target_path, vm_name, disk)` instead; the old assertions are now exactly wrong. | `test_restore_resets_only_restored_disk_state` in the same file: assert per-disk reset calls with the resolved disk, assert `reset_vm_state`/`reset_target_state` are NOT called, assert other disks' state survives (spec: restore-command "Restore resets only the restored disk's state"). |
| `tests/core/test_restore.py` | `test_restore_best_effort_checkpoint_cleanup` | Asserts ALL `qsnap-*` checkpoints are deleted, using legacy 3-part checkpoint names (`qsnap-abc12345-snap1`). Under the restore-command delta (step 12) legacy names without a disk segment MUST NOT be deleted (WARNING only), and only checkpoints whose 3rd dash-segment equals the restored disk are deleted — the old test's fixture and assertions are now exactly wrong. | `test_restore_cleans_only_restored_disk_checkpoints` (4-part names, only restored disk's deleted) + `test_restore_skips_legacy_checkpoints_with_warning` (5-part/legacy names skipped, WARNING logged) in the same file (spec: restore-command "Restore cleans up only the restored disk's checkpoints" and "Restore skips legacy checkpoints without a disk segment"). |

Note: `tests/integration/test_restore.py::test_restore_pipeline_resets_state` asserts the same removed full-reset behavior at integration level; it is classified under Test Modifications (full rewrite of its assertions) rather than deletions because the test scaffold (VM fixture, mock wiring) is reused.

## Test Modifications

### Core & Pipeline Tests

| File | Change | Reason |
|---|---|---|
| `tests/core/test_fork.py` | Add `test_fork_dry_run_logs_plan_no_file`: assert `core.dry_run=True` → no convert, no file, INFO log with `[dry-run]`. Add `test_fork_verify_failure_removes_output`: mock `verify_standalone_image` to return error, assert output removed, result.success=False. Add `test_fork_no_xml_or_state_mutation`: assert no `virsh dumpxml`/`virsh define`/`IStateManager` calls. Add `test_fork_removes_partial_on_convert_failure`: assert partial output file removed. | F3 (fork dry-run), F5 (verify + retry + partial cleanup), core-orchestrator spec. Design D3 (dry-run gate after estimate). |
| `tests/core/test_engine.py` | Modify `test_action_appended_on_snapshot_create` to assert `record.disk == "vda"`. Same for `snapshot_delete`, `backup_transfer`, `backup_full`, `backup_delete`, `error`. Add `test_multi_disk_actions_each_carry_disk`: 2-disk VM, assert each disk's ActionRecord has correct disk. | F2 (ActionRecord.disk). action-audit-trail spec: every disk-scoped action MUST carry its disk. |
| `tests/core/test_pipeline.py` | No changes to deletion logic. The 2-disk `test_create_snapshot_multi_disk_vda_vdb_creates_two_with_suffix` already validates per-disk snapshot creation. | — |
| `tests/core/test_restore.py` (EXISTS — 15 tests) | Delete `test_restore_resets_all_vm_state` and `test_restore_best_effort_checkpoint_cleanup` (see Test Deletions). Add `test_restore_resets_only_restored_disk_state`, `test_restore_cleans_only_restored_disk_checkpoints`, `test_restore_skips_legacy_checkpoints_with_warning`, `test_restore_verifies_tmp_before_replace` (verify failure → tmp removed, `os.replace` NOT called), `test_restore_convert_retries_on_retryable_error`. Existing tests `test_restore_from_snapshot_replaces_vm_disk`, `test_restore_aborts_on_running_vm`, `test_restore_aborts_on_broken_chain`, `test_restore_dry_run_shows_planned_actions` etc. remain valid; update their MockShell wiring where the convert step now runs through `convert_with_retry` and where a `verify_standalone_image` success must be stubbed. | F4/F5. restore-command + core-orchestrator deltas: per-disk reset (step 11), per-disk checkpoint cleanup (step 12), verify-before-replace (step 7). Design D4/D5. |

### State Management Tests

| File | Change | Reason |
|---|---|---|
| `tests/state/test_manager.py` | Add `test_reset_vm_disk_state_clears_only_given_disk`: pre-populate both vda/vdb state, call `reset_vm_disk_state("myvm", "vda")`, assert vda snapshots/allocation/deferred gone, vdb preserved. Add `test_reset_vm_disk_state_legacy_bare_int`: legacy bare-int `last_allocation`, call reset → `get_last_allocation` returns None. Add `test_reset_vm_disk_state_unknown_noop`: no error raised. Add `test_reset_vm_disk_state_atomic`: crash during os.replace → state intact. Add `test_reset_target_disk_state_clears_only_given_vm_disk`: FULLs for `(myvm, vda)`, `(myvm, vdb)`, `(othervm, vda)` on shared target, reset `(myvm, vda)`, assert only that entry removed. Add `test_reset_target_disk_state_removes_only_disk_deps`: dependencies filtered by disk. Add `test_reset_target_disk_state_unknown_noop` + atomic. | F4 (per-disk state reset). state-management spec: `reset_vm_disk_state`, `reset_target_disk_state`. |
| `tests/state/test_manager.py` (mocks parity) | `InMemoryStateManager` gains `reset_vm_disk_state` and `reset_target_disk_state` method implementations. | F4. Design decision D4: two new `IStateManager` methods; all implementations and mocks must implement them. |

### Mock Implementation Tests

| File | Change | Reason |
|---|---|---|
| `tests/mocks/mock_state.py` | Add `reset_vm_disk_state(vm_name, disk)` and `reset_target_disk_state(target_path, vm_name, disk)` methods to `InMemoryStateManager`. | F4. ABC breakage: `IStateManager` gains 2 abstract methods; mocks must implement. |
| `tests/mocks/mock_modules.py` | `MockBitmapBackupProvider.transfer_missing`: set `disk=s.disk` on each `BackupResult`. `create_full_backup`: set `disk=source_snapshot.disk`. | F2. `IBackupProvider` result contract: `BackupResult` must carry `disk`. |
| `tests/mocks/mock_modules.py` | Add `MockBackupProvider` (for legacy compatibility in tests that use `_backup_provider` alias — update it to populate disk as well). | F2. Mock-parity requirement per TESTING.md. |

### Contract Tests

| File | Change | Reason |
|---|---|---|
| `tests/interfaces/test_state_manager.py` | Add `test_istate_manager_per_disk_reset_methods_abstract`: assert `reset_vm_disk_state` and `reset_target_disk_state` in `__abstractmethods__`. Add `test_concrete_implementations_have_per_disk_reset_methods`: assert both `JsonStateManager` and `InMemoryStateManager` implement them. Add `test_missing_per_disk_reset_fails_instantiation`: subclass missing the methods → TypeError. | F4. ABC breakage: contract tests enforce every concrete implementation provides the new methods. |
| `tests/interfaces/test_backup_provider.py` | Add `test_transfer_missing_result_carries_disk`: MockBitmapBackupProvider's transfer_missing returns `BackupResult` with `disk` field populated per snapshot. Add `test_create_full_backup_result_carries_disk`. | F2. `IBackupProvider` result contract widening. |

### CLI Tests

| File | Change | Reason |
|---|---|---|
| `tests/cli/test_commands.py` | Add `test_fork_local_dry_run_flag`: `--dry-run` sets `core.dry_run=True`. Add `test_fork_global_n_flag`: `-n fork` sets `core.dry_run=True`. Add `test_fork_no_dry_run_converts`: without flags, `core.dry_run` is False. Add `test_fork_dry_run_previews`: calls `Core.fork()` with dry_run, no file created. | F3. cli-interface spec: fork subcommand accepts local `--dry-run` with `argparse.SUPPRESS`. |
| `tests/cli/test_app.py` | Add `test_fork_parses_dry_run_flag`: parse `["fork", "snap1", "--output", "/tmp/o.qcow2", "--dry-run"]`, assert `ns.dry_run is True`. | F3. |
| `tests/cli/test_summary.py` | Modify existing summary tests: add `disk="vda"` to `ActionRecord` constructors in `_make_action`. Add `test_summary_disk_scoped_shows_prefix`: assert `+++ [vda]` output. Add `test_summary_vm_level_error_no_prefix`: `ActionRecord(disk=None)` → no `[disk]`. Add `test_summary_multi_disk_distinguishes_disks`: 2-disk actions, assert distinct `[vda]`/`[vdb]` prefixes. | F2. backup-summary spec: summary lines render `[disk]` prefix. |

### Transaction Log Tests

| File | Change | Reason |
|---|---|---|
| `tests/utils/test_transaction.py` | Add test verifying that `ActionRecord.disk` is NOT injected as a 7th field in the transaction log line. The existing tests already verify 6 fields; add explicit assertion that even when `ActionRecord` has `.disk`, the line still has exactly 6 space-separated fields and disk only appears in file paths. | F2. transaction-log spec: format frozen at 6 btrbk fields. |

### Model Tests

| File | Change | Reason |
|---|---|---|
| `tests/models/test_results.py` | Add `test_action_record_carries_disk`: construct with `disk="vda"`, assert `.disk == "vda"`, dataclass is frozen. Add `test_action_record_disk_defaults`: construct without `disk`, assert `.disk is None`. Add `test_backup_result_carries_disk`: construct with `disk="vda"`, assert. Add `test_backup_result_disk_defaults_none`: construct without, assert None. | F2. result-types spec: `ActionRecord.disk` and `BackupResult.disk`. |

### Integration Tests (real virsh/qemu-img)

| File | Change | Reason |
|---|---|---|
| `tests/integration/test_restore.py` | Modify `test_restore_pipeline_resets_state`: pre-populate state for both `vda` and `vdb` disks + targets. After restore of `vda` only, assert `reset_vm_disk_state("testvm", "vda")` was called, NOT `reset_vm_state()`; assert `vdb` snapshots and state remain. | F4. restore-command spec: step 8 uses per-disk state reset. |
| `tests/integration/test_restore.py` | Modify `test_restore_cleanup_libvirt_checkpoints`: use proper checkpoint names `qsnap-{hash}-{disk}-{ts}-{hex}`. Assert only the restored disk's checkpoints are deleted via `virsh checkpoint-delete`. Add checkpoint with different disk → assert NOT deleted. Add legacy 5-part checkpoint → assert skipped with WARNING. | F4. restore-command spec: step 12 per-disk checkpoint cleanup, legacy names skipped. |
| `tests/integration/test_restore.py` | Add `test_restore_verifies_temp_before_replace`: mock `verify_standalone_image` to return error, assert temp file removed and base image untouched (`os.replace` NOT called). | F5. restore-command spec: verify temp BEFORE `os.replace`. |
| `tests/integration/test_restore.py` | Add `test_restore_skips_legacy_checkpoints`: libvirt has `qsnap-abc123-20260701T120000-a1b2c3` (5 parts, no disk), assert it is NOT deleted, WARNING logged. | F4. Design D4: legacy 5-part checkpoint names skipped with WARNING. |
| `tests/integration/test_restore.py` | Add `test_restore_aborts_when_disk_cannot_be_determined`: snapshot with no `.disk` and unparseable name → RestoreResult(success=False). | restore-command spec. |
| `tests/integration/test_fork.py` | Add `test_fork_dry_run_no_file`: `core.dry_run=True`, assert no convert, no output file, INFO log. Add `test_fork_verifies_output_after_convert`: mock `verify_standalone_image` returns None → success. Add `test_fork_removes_output_on_verify_failure`: mock verification error → output removed, result.success=False. Add `test_fork_removes_partial_output_on_convert_failure`: convert fails mid-stream → partial output removed. | F3 (dry-run), F5 (verify + retry + partial cleanup). |
| `tests/integration/test_multi_disk.py` | Modify `test_restore_single_disk_isolation`: after restore of `vdb`, additionally assert that `IStateManager.reset_vm_disk_state(vm_name, "vdb")` was called (not full reset), and `vda` state records remain in IStateManager. Assert checkpoint cleanup only affects `vdb`-segment checkpoints. | F4. restore-command spec: other disks' state and checkpoints intact. |
| `tests/integration/test_check_snapshots.py` | Add `test_check_multi_disk_per_group_matching`: create 2-disk real VM (via `test_vm_multi_disk` fixture), create snapshots on both vda and vdb with different timestamps, run `core.check()`, assert each disk compared to its own newest snapshot → status=ok even though one disk's newest is older. | F1. triple-source-check spec: multi-disk VM scenario — each disk compared against its own newest. |
| `tests/integration/test_check_snapshots.py` | Add `test_check_disk_without_snapshots_skipped`: VM with disk that has no snapshots in state → active-layer comparison skipped for that disk, no mismatch. | F1. triple-source-check spec: disk without snapshots is skipped. |

## Integration & E2E Tests (real libvirt/qemu)

### Summary Table

| Test File | Test Name | Fixture | Action | Covers |
|---|---|---|---|---|
| tests/integration/test_check_snapshots.py | test_check_multi_disk_per_group_matching | test_vm_multi_disk | NEW | F1: multi-disk per-disk active-layer matching |
| tests/integration/test_check_snapshots.py | test_check_disk_without_snapshots_skipped | test_vm_multi_disk | NEW | F1: disk without snapshots skipped |
| tests/integration/test_restore.py | test_restore_pipeline_resets_state | mock_shell | MODIFY | F4: per-disk state reset, vdb preserved |
| tests/integration/test_restore.py | test_restore_cleanup_libvirt_checkpoints | mock_shell | MODIFY | F4: per-disk checkpoint cleanup |
| tests/integration/test_restore.py | test_restore_verifies_temp_before_replace | mock_shell | NEW | F5: verify tmp before os.replace |
| tests/integration/test_restore.py | test_restore_skips_legacy_checkpoints | mock_shell | NEW | F4: legacy 5-part checkpoint skip |
| tests/integration/test_restore.py | test_restore_aborts_when_disk_cannot_be_determined | mock_shell | NEW | restore-command: disk resolution failure |
| tests/integration/test_fork.py | test_fork_dry_run_no_file | mock_shell | NEW | F3: fork dry-run |
| tests/integration/test_fork.py | test_fork_verifies_output_after_convert | mock_shell | NEW | F5: fork verify output |
| tests/integration/test_fork.py | test_fork_removes_output_on_verify_failure | mock_shell | NEW | F5: fork removes output on verify failure |
| tests/integration/test_fork.py | test_fork_removes_partial_output_on_convert_failure | mock_shell | NEW | F5: partial-file cleanup |
| tests/integration/test_multi_disk.py | test_restore_single_disk_isolation | test_vm_multi_disk | MODIFY | F4: vdb state and checkpoints intact after vda restore |
| tests/integration/test_restore.py | test_restore_from_backup_resolves_disk | mock_shell | NEW | F4: restore from backup with disk resolution |

All integration tests use the real libvirt/qemu environment. Tests with `mock_shell` use `MockShell` with `@pytest.mark.integration` (no real VM needed — they exercise Core through MockShell). Tests with `test_vm_multi_disk` fixture use real `virsh`/`qemu-img` against a disposable dual-disk VM defined in `tests/integration/conftest.py` (design D2 multi-disk fixture already exists).

All tests follow TESTING.md rules:
- `@pytest.mark.integration` marker
- Disposable VM fixtures (`test_vm`, `test_vm_multi_disk`) with proper teardown
- Per-disk snapshot directories and two base images for multi-disk tests

## Risks & Edge Cases

- **[ABC breakage: `IStateManager` +2 methods, `IBackupProvider` result contract]** → Covered by `tests/interfaces/test_state_manager.py` (contract tests for abstract method enforcement + concrete implementation verification) and `tests/interfaces/test_backup_provider.py` (BackupResult.disk contract). Additionally, `tests/mocks/mock_state.py` (InMemoryStateManager parity tests in `test_manager.py`) and `tests/mocks/mock_modules.py` (MockBitmapBackupProvider disk population) ensure mock parity. See groups `contract-state-perdisk` and `contract-backup-disk`.

- **[Checkpoint name parsing by `-` segments is fragile if the format ever changes]** → Covered by `tests/integration/test_restore.py::test_restore_skips_legacy_checkpoints` (5-part legacy names skipped with WARNING, never deleted). Format is fixed by `_new_checkpoint_name`. See group `core-restore-perdisk`.

- **[Verification adds time to restore]** → Covered by `tests/integration/test_restore.py::test_restore_verifies_temp_before_replace` (M1+M2 are metadata/structure checks — seconds). This is a design trade-off documented in the risk, not a code defect to test. The test verifies correctness (not timing).

- **[Restore behavior change on shared targets (other VMs keep state)]** → Covered by `tests/state/test_manager.py::test_reset_target_disk_state_clears_only_given_vm_disk` (other VMs' FULLs and deps preserved). Also `tests/integration/test_multi_disk.py::test_restore_single_disk_isolation` (vda state survives vdb restore). See groups `state-per-disk-reset` and `core-restore-perdisk`.

- **[Retrying a multi-GB convert repeats large writes]** → Covered by `tests/utils/test_convert.py::test_convert_with_retry_transient_then_success` (retries on retryable errors only) and `test_convert_with_retry_non_retryable_fails` (non-retryable exits immediately). Partial output removed before each retry → `test_convert_with_retry_exhausted`. See group `convert-utils-unit`.

- **[`BackupResult.disk` default `None` could hide a missing population site]** → Covered by `tests/interfaces/test_backup_provider.py::test_transfer_missing_result_carries_disk` (contract asserts disk is populated for BitmapBackupProvider) and `tests/core/test_engine.py` action audit tests (assert disk is populated on every action). Summary renders missing disk as no prefix → `tests/cli/test_summary.py::test_summary_vm_level_error_no_prefix`. See groups `contract-backup-disk`, `core-audit-disk`, `cli-summary-disk`.
