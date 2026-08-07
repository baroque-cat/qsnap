# QA Strategy & Test Plan

Change: `orthogonalize-snapshots-and-backups` (D1–D10; Phase 1 + Phase 2 land in this change before archive).
Testing paradigm: **TESTING.md** — tests mirror the production hierarchy, every ABC gets a mock
(`MockShell.expect().returns()`, `MockVMModuleFactory`, `InMemoryStateManager`, `MockConfigFacade`),
Core tests use `MockVMModuleFactory` with zero real virsh/qemu-img, module tests mock `IShell`,
contract tests parametrize over ALL concrete implementations, markers `unit/mock/contract/integration/stress/e2e`
with `--strict-markers`, run via `poetry run pytest`.

---

## Coverage Map

Every `#### Scenario:` from every delta spec. Test File follows the TESTING.md hierarchy. Group = delegation group (below).

### backup-target-orthogonality

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-target-orthogonality | Backup phase SHALL NOT consume snapshot data | Backup phase runs with zero snapshots in state | tests/core/test_engine.py | test_backup_phase_runs_with_zero_snapshots_in_state | core-unit |
| backup-target-orthogonality | Backup phase SHALL NOT consume snapshot data | Provider receives no SnapshotInfo | tests/interfaces/test_backup_provider.py | test_backup_provider_api_never_references_snapshotinfo | provider-contract |
| backup-target-orthogonality | One backup work unit per disk per run | First backup of a disk creates a FULL | tests/modules/backup/test_bitmap.py | test_run_backup_first_backup_creates_full_with_atomic_checkpoint | provider-unit |
| backup-target-orthogonality | One backup work unit per disk per run | Subsequent backup creates one delta | tests/modules/backup/test_bitmap_incremental.py | test_run_backup_subsequent_creates_single_delta_chained | provider-unit |
| backup-target-orthogonality | One backup work unit per disk per run | Multiple snapshots since last backup produce one delta | tests/modules/backup/test_bitmap_incremental.py | test_three_snapshots_since_last_backup_still_one_delta | provider-unit |
| backup-target-orthogonality | Freeze-timestamp backup naming | Delta named by freeze point | tests/modules/backup/test_bitmap_incremental.py | test_delta_named_by_freeze_point_no_snapshot_name | provider-unit |
| backup-target-orthogonality | Checkpoint is the sole delta baseline | Baseline discovery uses only libvirt checkpoints | tests/modules/backup/test_bitmap.py | test_baseline_selection_uses_only_checkpoints | provider-unit |
| backup-target-orthogonality | Legacy backup files remain first-class | Mixed-generation chain resolves | tests/modules/backup/test_bitmap.py | test_mixed_generation_chain_resolves_via_backing_walk | provider-unit |
| backup-target-orthogonality | BackupInfo model for the target world | list returns BackupInfo | tests/modules/backup/test_bitmap.py | test_list_returns_backupinfo_no_snapshotinfo | provider-unit |

### backup-provider

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-provider | Backup creation work unit run_backup | First backup — full export via qemu-img convert | tests/modules/backup/test_bitmap_convert.py | test_run_backup_first_full_via_qemu_img_convert | provider-unit |
| backup-provider | Backup creation work unit run_backup | Incremental backup — dirty blocks only | tests/modules/backup/test_bitmap_incremental.py | test_run_backup_incremental_dirty_blocks_only_zero_skip_false | provider-unit |
| backup-provider | Backup creation work unit run_backup | Checkpoint rotation after successful transfer | tests/modules/backup/test_bitmap.py | test_checkpoint_rotation_after_successful_run_backup | provider-unit |
| backup-provider | Backup creation work unit run_backup | Backup failure preserves prior checkpoint | tests/modules/backup/test_bitmap.py | test_run_backup_failure_preserves_prior_checkpoint | provider-unit |
| backup-provider | Backup creation work unit run_backup | A second run_backup in the same batch uses the successor as baseline | tests/modules/backup/test_bitmap_incremental.py | test_second_run_backup_uses_successor_as_baseline | provider-unit |
| backup-provider | Deferred backup result for stopped VMs | Stopped VM with checkpoint defers | tests/modules/backup/test_bitmap.py | test_stopped_vm_with_checkpoint_defers_no_mutation | provider-unit |
| backup-provider | Deferred backup result for stopped VMs | Stopped VM without checkpoint creates offline FULL | tests/modules/backup/test_bitmap_convert.py | test_stopped_vm_no_checkpoint_offline_full | provider-unit |
| backup-provider | Deferred backup result for stopped VMs | First run after boot closes the gap | tests/integration/test_incremental_backup.py | test_stopped_vm_defers_then_catches_up_after_boot | integration-e2e |
| backup-provider | BitmapBackupProvider implements IBackupProvider | Constructor accepts IShell | tests/modules/backup/test_bitmap.py | test_constructor_accepts_ishell_and_implements_abc | provider-unit |
| backup-provider | BitmapBackupProvider implements IBackupProvider | Provider API carries no SnapshotInfo | tests/mocks/test_mock_validity.py | test_mock_backup_provider_api_carries_no_snapshotinfo | factory-mocks |
| backup-provider | BitmapBackupProvider does not consume IStateManager | Provider operates without state access | tests/modules/backup/test_bitmap.py | test_constructor_rejects_state_manager | provider-unit |
| backup-provider | Factory passes INbdClient to BitmapBackupProvider | Factory constructs BitmapBackupProvider with nbd | tests/factory/test_default.py | test_factory_constructs_bitmap_with_nbd_without_state | factory-mocks |
| backup-provider | Immediate deletion of failed backup files after verification failure | Failed backup file deleted immediately after verification failure | tests/modules/backup/test_bitmap.py | test_failed_backup_file_deleted_after_verification_failure | provider-unit |
| backup-provider | Immediate deletion of failed backup files after verification failure | Failed backup file not found by retention cleanup | tests/core/test_engine.py | test_failed_backup_file_not_listed_by_retention_cleanup | core-unit |
| backup-provider | Immediate deletion of failed backup files after verification failure | Bitmap NBD convert failure does not leave partial file | tests/modules/backup/test_bitmap_convert.py | test_convert_failure_deletes_partial_file_before_result | provider-unit |
| backup-provider | Immediate deletion of failed backup files after verification failure | One disk failure does not stop other disks | tests/core/test_engine.py | test_one_disk_failure_does_not_stop_other_disks | core-unit |
| backup-provider | Per-disk FULL backup creation in Core | First backup creates per-disk FULLs | tests/core/test_full_anchor.py | test_first_backup_creates_per_disk_fulls | core-unit |
| backup-provider | Per-disk FULL backup creation in Core | Incremental count exceeds chain length for one disk | tests/core/test_full_anchor.py | test_incremental_count_exceeds_chain_length_per_disk | core-unit |
| backup-provider | Per-disk backup naming | FULL backup named with disk and freeze timestamp | tests/modules/backup/test_bitmap.py | test_full_backup_named_freeze_ts_disk_hex | provider-unit |
| backup-provider | Per-disk backup naming | Incremental backup named with disk and freeze timestamp | tests/modules/backup/test_bitmap_incremental.py | test_incremental_backup_named_freeze_ts_disk_hex | provider-unit |
| backup-provider | Backup results carry the source disk | Backup result carries disk | tests/modules/backup/test_bitmap.py | test_run_backup_result_carries_disk | provider-unit |
| backup-provider | Backup results carry the source disk | Multi-disk run returns per-disk results | tests/core/test_engine.py | test_multi_disk_run_returns_per_disk_results | core-unit |

### core-orchestrator

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| core-orchestrator | Blockjob probe before backup | Active block job defers the disk backup | tests/core/test_pipeline.py | test_active_blockjob_defers_disk_backup | core-unit |
| core-orchestrator | Blockjob probe before backup | No block job proceeds normally | tests/core/test_pipeline.py | test_no_blockjob_backup_proceeds | core-unit |
| core-orchestrator | Deferred backups keep the onchange gate open | Deferred result leaves baseline untouched | tests/core/test_pipeline.py | test_deferred_result_leaves_baseline_untouched | core-unit |
| core-orchestrator | Core._backup_target triggers full backup when due | Incremental count exceeds chain length triggers FULL | tests/core/test_full_anchor.py | test_incremental_count_exceeds_chain_length_triggers_full | core-unit |
| core-orchestrator | Core._backup_target triggers full backup when due | First run creates full backup | tests/core/test_full_anchor.py | test_first_backup_creates_full | core-unit |
| core-orchestrator | Core._backup_target triggers full backup when due | Verified FULL triggers retention + cleanup | tests/core/test_full_verification_pipeline.py | test_verified_full_triggers_retention_and_cleanup | core-unit |
| core-orchestrator | Core._backup_target triggers full backup when due | Failed FULL verification triggers rollback | tests/core/test_full_verification_pipeline.py | test_failed_full_verification_triggers_rollback | core-unit |
| core-orchestrator | Core._backup_target triggers full backup when due | Retries exhausted keeps old generations | tests/core/test_full_verification_pipeline.py | test_retries_exhausted_keeps_old_generations | core-unit |
| core-orchestrator | backup_failed WARNING in Core._backup_target | Disk failure warns with target and disk attribution, audits successes, then aborts | tests/core/test_engine.py | test_disk_failure_warns_audits_successes_then_aborts | core-unit |
| core-orchestrator | backup_failed WARNING in Core._backup_target | FULL failure after retries aborts with old generations preserved | tests/core/test_engine.py | test_full_failure_after_retries_aborts_preserves_generations | core-unit |
| core-orchestrator | backup_failed WARNING in Core._backup_target | No warning when all backups succeed | tests/core/test_engine.py | test_no_backup_failed_warning_when_all_succeed | core-unit |
| core-orchestrator | VM-level failure isolation | Disk failure aborts remaining steps of the VM | tests/core/test_pipeline.py | test_vdb_failure_aborts_remaining_steps | core-unit |
| core-orchestrator | VM-level failure isolation | Other VMs continue after a VM aborts | tests/core/test_pipeline.py | test_error_isolation_between_vms | core-unit |
| core-orchestrator | VM-level failure isolation | Backup failure of one disk does not abandon other disks | tests/core/test_engine.py | test_backup_failure_one_disk_still_attempts_other_disks | core-unit |
| core-orchestrator | VM-level failure isolation | MAC denial does not abort the VM | tests/core/test_pipeline.py | test_mac_denial_defers_without_aborting | core-unit |
| core-orchestrator | VM-level failure isolation | Space error suspends one target, VM continues | tests/core/test_enospc_isolation.py | test_space_error_suspends_target_vm_continues | core-unit |
| core-orchestrator | BackupAbortError marks backup-stage failures | Backup abort sets backup_failed | tests/core/test_engine.py | test_pipeline_backup_abort_returns_exit_code_10 | core-unit |
| core-orchestrator | BackupAbortError marks backup-stage failures | Space failure does not raise BackupAbortError | tests/core/test_enospc_isolation.py | test_space_failure_no_backup_abort_error | core-unit |

### nbd-bitmap-backup

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| nbd-bitmap-backup | Size-based sanity check for incremental transfer | Large transfer triggers warning | tests/modules/backup/test_bitmap_incremental.py | test_size_sanity_check_warns_on_large_transfer | provider-unit |
| nbd-bitmap-backup | Size-based sanity check for incremental transfer | No baseline skips the check | tests/modules/backup/test_bitmap_incremental.py | test_size_sanity_check_skipped_without_baseline | provider-unit |

### locking

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| locking | Lockfile acquisition on startup | Successful lock acquisition | tests/utils/test_locking.py | test_acquire_lock_when_free_returns_true | cli-locking |
| locking | Lockfile acquisition on startup | Lock already held | tests/utils/test_locking.py | test_acquire_lock_when_held_returns_false | cli-locking |
| locking | Lockfile acquisition on startup | Read-only command runs while lock is held | tests/cli/test_app.py | test_read_only_command_runs_without_lock | cli-locking |
| locking | Lockfile release on exit | Lock released on normal exit | tests/utils/test_locking.py | test_release_lock_allows_reacquisition | cli-locking |
| locking | Lockfile release on exit | Lock released on crash | tests/utils/test_locking.py | test_lock_auto_released_on_process_termination | cli-locking |
| locking | Lockfile release on exit | Default lockfile used when unconfigured | tests/utils/test_locking.py | test_default_lockfile_used_when_unconfigured | cli-locking |
| locking | Lockfile release on exit | Explicit off disables locking | tests/utils/test_locking.py | test_off_sentinel_disables_locking | cli-locking |
| locking | Lockfile path resolution | Lockfile from CLI overrides config | tests/utils/test_locking.py | test_lockfile_path_resolution_cli_overrides_config | cli-locking |
| locking | Lockfile path resolution | Off sentinel in config disables locking | tests/cli/test_app.py | test_off_sentinel_config_disables_locking | cli-locking |

### startup-state-validation

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| startup-state-validation | Orphan checkpoint invariant at startup | Orphan checkpoint deleted at startup | tests/core/test_pipeline.py | test_startup_orphan_checkpoint_deleted_at_startup | core-unit |
| startup-state-validation | Orphan checkpoint invariant at startup | Healthy checkpoint kept | tests/core/test_pipeline.py | test_startup_healthy_checkpoint_kept | core-unit |
| startup-state-validation | Orphan checkpoint invariant at startup | Invariant failure is non-fatal | tests/core/test_pipeline.py | test_startup_orphan_checkpoint_delete_failure_non_fatal | core-unit |

### restore-points-listing

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| restore-points-listing | Restore points listing command | Listing shows freeze points per target | tests/core/test_list_commands.py | test_list_restore_points_shows_freeze_points_per_target | core-unit |
| restore-points-listing | Restore points listing command | Empty target reports no points | tests/core/test_list_commands.py | test_list_restore_points_empty_target | core-unit |
| restore-points-listing | Restore points listing command | Multiple disks distinguished | tests/core/test_list_commands.py | test_list_restore_points_per_disk | core-unit |
| restore-points-listing | Restore points reflect physical coverage | Snapshot timestamps never appear as restore points | tests/core/test_list_commands.py | test_list_restore_points_ignores_snapshot_state | core-unit |

### state-management

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| state-management | Incremental dependency keys are key-format agnostic | Mixed-generation dependencies counted together | tests/state/test_manager.py | test_mixed_generation_dependencies_counted_together | state-utils |
| state-management | Incremental dependency keys are key-format agnostic | Legacy records expire naturally | tests/core/test_engine.py | test_legacy_dependency_records_removed_with_generation | core-unit |

### backup-summary

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-summary | Summary lines carry disk prefix | Disk-scoped action line shows disk prefix | tests/cli/test_summary.py | test_summary_disk_scoped_shows_prefix | cli-locking |
| backup-summary | Summary lines carry disk prefix | Backup failure error line carries disk and target | tests/cli/test_summary.py | test_summary_backup_failure_error_carries_disk_and_target | cli-locking |
| backup-summary | Summary lines carry disk prefix | VM-level error line has no disk prefix | tests/cli/test_summary.py | test_summary_vm_level_error_no_prefix | cli-locking |
| backup-summary | Summary lines carry disk prefix | Multi-disk run distinguishes disks in summary | tests/cli/test_summary.py | test_summary_multi_disk_distinguishes_disks | cli-locking |

### action-audit-trail

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| action-audit-trail | ActionRecord dataclass | ActionRecord is immutable | tests/models/test_results.py | test_action_record_is_immutable | models-unit |
| action-audit-trail | ActionRecord dataclass | ActionRecord size and duration default to zero | tests/models/test_results.py | test_action_record_defaults_zero | models-unit |
| action-audit-trail | ActionRecord dataclass | ActionRecord carries disk | tests/models/test_results.py | test_action_record_carries_disk | models-unit |
| action-audit-trail | ActionRecord dataclass | Backup failure error record carries disk and target | tests/models/test_results.py | test_action_record_backup_failure_carries_disk_and_target | models-unit |
| action-audit-trail | ActionRecord dataclass | VM-level error record has no disk | tests/models/test_results.py | test_action_record_error_disk_none | models-unit |
| action-audit-trail | ActionRecord accumulation in Core | Core clears actions at start of run | tests/core/test_engine.py | test_actions_cleared_at_run_start | core-unit |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on snapshot create | tests/core/test_engine.py | test_action_appended_on_snapshot_create | core-unit |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on snapshot delete (blockcommit) | tests/core/test_engine.py | test_action_appended_on_snapshot_delete | core-unit |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on backup transfer | tests/core/test_engine.py | test_action_appended_on_backup_transfer | core-unit |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on FULL backup creation | tests/core/test_engine.py | test_action_appended_on_full_backup | core-unit |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on backup deletion | tests/core/test_engine.py | test_action_appended_on_backup_delete | core-unit |
| action-audit-trail | ActionRecord accumulation in Core | Core appends error action on failure | tests/core/test_engine.py | test_error_action_appended_on_failure | core-unit |
| action-audit-trail | ActionRecord accumulation in Core | Core does not append actions in dry-run for mutations | tests/core/test_engine.py | test_no_actions_in_dry_run_mutations | core-unit |

### restore-command

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| restore-command | Restore point selection policy | First point above the requested timestamp is used | tests/core/test_restore.py | test_restore_at_selects_first_point_above | core-unit |
| restore-command | Restore point selection policy | Exact match is used when present | tests/core/test_restore.py | test_restore_at_exact_match_selected | core-unit |
| restore-command | Restore point selection policy | No satisfying point fails with available list | tests/core/test_restore.py | test_restore_at_no_satisfying_point_lists_available | core-unit |
| restore-command | Restore points listing | Operator inspects points before restore | tests/cli/test_commands.py | test_list_restore_points_dispatches_to_core | cli-locking |
| restore-command | Restore command replaces the resolved disk atomically | Restore from snapshot replaces the resolved disk | tests/core/test_restore.py | test_restore_from_snapshot_replaces_resolved_disk | core-unit |
| restore-command | Restore command replaces the resolved disk atomically | Restore from backup with disk in filename | tests/core/test_restore.py | test_restore_from_backup_with_disk_in_filename | core-unit |
| restore-command | Restore command replaces the resolved disk atomically | Restore --at selects the first point above the timestamp | tests/cli/test_commands.py | test_handle_restore_at_selects_point | cli-locking |
| restore-command | Restore command replaces the resolved disk atomically | Restore --at with legacy snapshot name shim | tests/core/test_restore.py | test_restore_at_legacy_name_shim | core-unit |
| restore-command | Restore command replaces the resolved disk atomically | Restore aborts on running VM | tests/core/test_restore.py | test_restore_vm_running_fails | core-unit |
| restore-command | Restore command replaces the resolved disk atomically | Restore aborts on broken source chain | tests/core/test_restore.py | test_restore_aborts_on_broken_chain | core-unit |
| restore-command | Restore command replaces the resolved disk atomically | Restore aborts when disk cannot be determined | tests/core/test_restore.py | test_restore_aborts_when_disk_unknown | core-unit |
| restore-command | Restore command replaces the resolved disk atomically | Restore aborts when disk is not in VM config | tests/core/test_restore.py | test_restore_aborts_disk_not_in_vm_config | core-unit |
| restore-command | Restore command replaces the resolved disk atomically | Restore with --dry-run shows planned actions | tests/core/test_restore.py | test_restore_dry_run_shows_planned_actions | core-unit |
| restore-command | Restore command replaces the resolved disk atomically | Restore with --yes skips confirmation | tests/cli/test_commands.py | test_handle_restore_yes_skips_confirmation | cli-locking |
| restore-command | Restore command replaces the resolved disk atomically | Restore prompts for confirmation without --yes | tests/cli/test_commands.py | test_handle_restore_prompts_confirmation_without_yes | cli-locking |
| restore-command | Restore command replaces the resolved disk atomically | Restore verifies the temp image before replacing the base | tests/core/test_restore.py | test_restore_verifies_temp_image_before_replace | core-unit |
| restore-command | Restore command replaces the resolved disk atomically | Restore cleans up only the restored disk's checkpoints | tests/core/test_restore.py | test_restore_cleans_only_restored_disk_checkpoints | core-unit |
| restore-command | Restore command replaces the resolved disk atomically | Restore skips legacy checkpoints without a disk segment | tests/core/test_restore.py | test_restore_skips_legacy_checkpoints_with_warning | core-unit |
| restore-command | Restore command replaces the resolved disk atomically | Restore resets only the restored disk's state | tests/core/test_restore.py | test_restore_resets_only_restored_disk_state | core-unit |
| restore-command | Restore command replaces the resolved disk atomically | Restore leaves other disks and other VMs intact | tests/core/test_restore.py | test_restore_leaves_other_disks_and_vms_intact | core-unit |
| restore-command | Restore command replaces the resolved disk atomically | Restore from nonexistent snapshot | tests/cli/test_commands.py | test_handle_restore_nonexistent_backup_returns_exit_1 | cli-locking |
| restore-command | Core.restore method | Restore from snapshot identifies disk | tests/core/test_restore.py | test_core_restore_from_snapshot_replaces_disk | core-unit |
| restore-command | Core.restore method | Restore from backup identifies disk | tests/core/test_restore.py | test_core_restore_from_backup_replaces_disk | core-unit |
| restore-command | Core.restore method | Restore --at selects point and logs it | tests/core/test_restore.py | test_core_restore_at_logs_used_point | core-unit |
| restore-command | Core.restore method | Restore fails on running VM | tests/core/test_restore.py | test_core_restore_fails_on_running_vm | core-unit |
| restore-command | Core.restore method | Restore fails when neither name nor at given | tests/core/test_restore.py | test_core_restore_requires_name_or_at | core-unit |

### dry-run-prediction

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| dry-run-prediction | Backup prediction from target-internal data | Gate open with checkpoint predicts one delta per disk | tests/core/test_dry_run_prediction.py | test_gate_open_with_checkpoint_predicts_single_delta_per_disk | core-unit |
| dry-run-prediction | Backup prediction from target-internal data | Gate closed predicts no backup | tests/core/test_dry_run_prediction.py | test_gate_closed_predicts_no_backup | core-unit |
| dry-run-prediction | Backup prediction from target-internal data | No checkpoint predicts FULL | tests/core/test_dry_run_prediction.py | test_no_checkpoint_predicts_full | core-unit |
| dry-run-prediction | FULL backup prediction with size estimate | FULL prediction carries chain size estimate | tests/core/test_dry_run_prediction.py | test_full_prediction_carries_chain_size | core-unit |
| dry-run-prediction | FULL backup prediction with size estimate | Estimation failure degrades gracefully | tests/core/test_dry_run_prediction.py | test_full_prediction_estimation_failure_graceful | core-unit |
| dry-run-prediction | FULL backup prediction with size estimate | Estimation never uses snapshot files | tests/core/test_dry_run_prediction.py | test_full_estimation_never_uses_snapshot_files | core-unit |

---

## Delegation Groups

Non-overlapping groups for parallel execution; each test FILE belongs to exactly one group.

### provider-unit
**Scope:** `BitmapBackupProvider` behavior under the new `run_backup(vm_config, target, disk, *, opts)` contract: FULL-vs-delta autonomy, freeze-ts naming, per-disk scoping, checkpoint rotation/cleanup, stopped-VM deferral, offline FULL, immediate partial-file deletion, size-based sanity check, previous-backup chain resolution (mixed generations). Zero real I/O — `MockShell` + `MockNbdClient`.

| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/backup/test_bitmap.py | bt-ortho 1/2/4/7/8/9; provider 3/4/6/9/11/13/19/21; nbd 1 | MODIFY |
| tests/modules/backup/test_bitmap_incremental.py | bt-ortho 3/5/6; provider 2/5/20; nbd 1/2 | MODIFY |
| tests/modules/backup/test_bitmap_convert.py | provider 1/7/15 | MODIFY |

### models-unit
**Scope:** Model dataclasses: `BackupInfo` (new), `BackupResult.deferred`/`disk` (new), `ActionRecord.target` (new); frozen-ness, defaults, disk/target propagation.

| Test File | Scenarios | Action |
|---|---|---|
| tests/models/test_results.py | audit-trail 1/2/3/4/5 | MODIFY |

### provider-contract
**Scope:** Contract tests parametrized over `BitmapBackupProvider` AND `MockBitmapBackupProvider` for the NEW interface: `run_backup -> BackupResult`, `list -> list[BackupInfo]`, `delete(BackupInfo)`, `list_checkpoints`, `target_hash`; signatures never reference `SnapshotInfo`.

| Test File | Scenarios | Action |
|---|---|---|
| tests/interfaces/test_backup_provider.py | bt-ortho 2; provider 10 | MODIFY |
| tests/interfaces/test_factory.py | — (factory create_* returns correct ABCs) | MODIFY |

### factory-mocks
**Scope:** `DefaultFactory.create_backup_provider` passes `LibnbdClient()` and NO `IStateManager`; `MockBitmapBackupProvider`/`MockVMModuleFactory` satisfy the new ABC and return valid types; mock validity checks.

| Test File | Scenarios | Action |
|---|---|---|
| tests/factory/test_default.py | provider 12 | MODIFY |
| tests/mocks/test_mock_factory.py | — (mock API parity) | MODIFY |
| tests/mocks/test_mock_validity.py | provider 10 | MODIFY |

### core-unit
**Scope:** Core orchestration with `MockVMModuleFactory` (zero real virsh/qemu-img): per-disk `run_backup` loop, blockjob probe, continue-then-abort isolation, deferred baseline handling, FULL decision/verification/rollback, WARNING/`BackupAbortError` attribution, orphan-checkpoint startup invariant, dry-run predictions from target-internal data, restore `--at`/shim/atomic replacement, restore-points enumeration, mixed-generation dependency cleanup.

| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_engine.py | bt-ortho 1; provider 14/16/22; core-orch 9/10/11/14/17; state-mgmt 2; audit 6/7/8/9/10/11/12/13 | MODIFY |
| tests/core/test_pipeline.py | core-orch 1/2/3/12/13/15; startup 1/2/3 | MODIFY |
| tests/core/test_full_anchor.py | core-orch 4/5; provider 17/18 | MODIFY |
| tests/core/test_full_verification_pipeline.py | core-orch 6/7/8 | MODIFY |
| tests/core/test_enospc_isolation.py | core-orch 16/18 | MODIFY |
| tests/core/test_bitmap_dependency.py | — (dependency registration with backup-name keys) | MODIFY |
| tests/core/test_dry_run_prediction.py | dry-run 1/2/3/4/5/6 | MODIFY |
| tests/core/test_restore.py | restore 1/2/3/5/6/8/9/10/11/12/13/16/17/18/19/20/22/23/24/25/26 | MODIFY |
| tests/core/test_list_commands.py | restore-points 1/2/3/4 | MODIFY |

### state-utils
**Scope:** `JsonStateManager` dependency-key agnosticism (legacy snapshot-name + new backup-name keys coexist), per-disk reset isolation for restore, freeze-ts name parsing utilities.

| Test File | Scenarios | Action |
|---|---|---|
| tests/state/test_manager.py | state-mgmt 1 | MODIFY |
| tests/utils/test_parsing.py | — (freeze-ts name parseability, risk: clock rollback) | MODIFY |

### cli-locking
**Scope:** Lockfile default/`"off"`/resolution/exit-3 behavior, read-only commands unlocked, `restore --at` + `list restore-points` CLI dispatch and summary formatting (disk+target error lines).

| Test File | Scenarios | Action |
|---|---|---|
| tests/utils/test_locking.py | locking 1/2/4/5/6/7/8 | MODIFY |
| tests/cli/test_app.py | locking 3/9 | MODIFY |
| tests/cli/test_commands.py | restore 4/7/14/15/21 | MODIFY |
| tests/cli/test_summary.py | summary 1/2/3/4 | MODIFY |
| tests/cli/test_format.py | — (restore-points table/raw format) | MODIFY |
| tests/cli/test_thin_layer.py | — (CLI stays thin; no target-world logic in CLI) | MODIFY |
| tests/cli/test_tree.py | — (backup tree over freeze-ts + legacy names) | MODIFY |

### integration-e2e
**Scope:** Real libvirt + qemu-img: per-disk `run_backup` FULL/delta, freeze-ts files on target, gap-free run1→run2 coverage, stopped-VM defer then catch-up, one-disk-failure isolation, startup orphan-checkpoint deletion, restore `--at` end-to-end, lockfile default under concurrent runs.

| Test File | Scenarios | Action |
|---|---|---|
| tests/integration/test_incremental_backup.py | provider 8; NEW gap-free coverage | MODIFY |
| tests/integration/test_full_backup.py | — (run_backup FULL path, freeze-ts) | MODIFY |
| tests/integration/test_count_based_full.py | — (Core FULL decision via run_backup) | MODIFY |
| tests/integration/test_multi_disk.py | — (per-disk run_backup + one-disk-fails isolation) | MODIFY |
| tests/integration/test_startup_validation.py | startup 1 (integration-level invariant) | MODIFY |
| tests/integration/test_auto_recovery.py | — (chain recovery via run_backup) | MODIFY |
| tests/integration/test_restore.py | — (restore --at + legacy shim) | MODIFY |
| tests/integration/test_verify_before_delete.py | — (verify-before-delete gate over new naming) | MODIFY |
| tests/integration/test_rollback_retry.py | — (FULL rollback via run_backup) | MODIFY |
| tests/integration/test_broken_chain.py | — (entry-point rewrite) | MODIFY |
| tests/integration/test_post_creation_validation.py | — (entry-point rewrite) | MODIFY |
| tests/integration/test_target_chain_length_none.py | — (entry-point rewrite) | MODIFY |
| tests/integration/test_backup_retry_max_zero.py | — (entry-point rewrite) | MODIFY |
| tests/stress/test_concurrent.py | locking default under load | MODIFY |
| tests/e2e/test_restore.py | restore --at end-to-end | MODIFY |
| tests/e2e/test_from_config.py | freeze-ts files + list restore-points output | MODIFY |

---

## Test Modifications

Existing tests that must change (inspected against the actual files; line refs are current).

| File | Change | Reason |
|---|---|---|
| tests/conftest.py | Add default `virsh blockjob` expectation (no active job) to `_setup_validation_expectations`; add default `virsh domstate`/`checkpoint-list` expectations needed by the per-disk `run_backup` loop | core-orchestrator "Blockjob probe before backup"; every Core backup test now probes blockjob per disk |
| tests/mocks/mock_modules.py | `MockBitmapBackupProvider`: remove `transfer_missing`/`create_full_backup`; add `run_backup(vm_config, target, disk, *, opts) -> BackupResult(success=True, disk=disk)` (optionally `deferred=True`); `list(target) -> list[BackupInfo]`; `delete(backup: BackupInfo)`; keep `list_checkpoints`/`target_hash` | backup-provider BREAKING interface (D2); TESTING.md mock parity rule |
| tests/mocks/mock_factory.py | No signature change, but the provider it returns must expose `run_backup`; keep `create_backup_provider(vm_config, target)` | provider-contract |
| tests/interfaces/test_backup_provider.py | Rewrite parametrized contract: `test_backup_provider_run_backup_returns_backup_result`, `test_backup_provider_list_returns_backupinfo`, `test_backup_provider_delete_accepts_backupinfo`, `test_backup_provider_run_backup_accepts_opts`; delete all `transfer_missing`/`create_full_backup`/SnapshotInfo-typed contract tests (see Tests To Delete) | backup-provider REMOVED requirements; backup-target-orthogonality "Provider receives no SnapshotInfo" |
| tests/interfaces/test_factory.py | Keep ABC surface check; assert `create_backup_provider` exists and returns `IBackupProvider` | unchanged contract |
| tests/factory/test_default.py | `test_factory_passes_state_to_bitmap_provider` → `test_factory_constructs_bitmap_with_nbd_without_state`: assert `BitmapBackupProvider(shell=self._shell, nbd=LibnbdClient())` and NO state arg | backup-provider "Factory passes INbdClient…", "Provider does not consume IStateManager" |
| tests/mocks/test_mock_factory.py | Replace `test_mock_backup_provider_*_transfer_missing/create_full_backup*` tests with `run_backup` mock tests; `test_mock_backup_provider_has_run_backup` | BREAKING interface; mock parity |
| tests/mocks/test_mock_validity.py | Add `test_mock_backup_provider_api_carries_no_snapshotinfo` (inspect all public method signatures) | backup-provider "Provider API carries no SnapshotInfo" |
| tests/models/test_results.py | Add `target` field tests to `ActionRecord` (immutable/default/carried); add `BackupInfo` dataclass tests; add `BackupResult.deferred` default test | action-audit-trail ActionRecord dataclass; BackupInfo model |
| tests/modules/backup/test_bitmap.py | Mechanical entry-point rewrite `transfer_missing(vm, target, [snap])` → `run_backup(vm, target, "vda")`; `create_full_backup(...)` → `run_backup` with FULL decision; replace `snapshot_name`/`SnapshotInfo`-based fixtures with freeze-ts names; assert `BackupResult.disk`; assert `BackupInfo` from `list()`; assert `rm -f` timeout=10 cleanup and `deferred=True` on stopped-VM-with-checkpoint | D2/D3/D8; all 22 provider scenarios |
| tests/modules/backup/test_bitmap_incremental.py | Same entry-point rewrite; size-sanity baseline now `last_backup_allocation` from `_target_state.json` (passed via opts), threshold 10× the expected delta upper bound; no snapshot allocation/timestamps referenced | nbd-bitmap-backup RENAMED/MODIFIED check |
| tests/modules/backup/test_bitmap_convert.py | Entry-point rewrite; freeze-ts naming for FULL; keep `qemu-img convert` engine assertions | D3 naming |
| tests/core/test_engine.py | Patch `run_backup` instead of `transfer_missing`/`create_full_backup`; `_backup_target` per-disk; WARNING text `Backup to target <t> failed for VM <v>: disk <d>` (never "snapshot(s) failed"); `backup_transfer`/`backup_full`/`backup_delete`/`error` ActionRecords carry `target`; failed-backup error records carry disk+target | core-orchestrator WARNING/attribution (D10); action-audit-trail |
| tests/core/test_pipeline.py | Per-disk `run_backup` patches; blockjob probe expectations; startup orphan-checkpoint invariant (new checks replace `test_startup_validation_no_checkpoint_deletion`); deferred baseline untouched tests | core-orchestrator; startup-state-validation; D6/D9 |
| tests/core/test_full_anchor.py | `_backup_target(vm, target, [snap])` → per-disk loop; count-based FULL decision asserts `run_backup` called with FULL direction for the disk; delete `test_full_source_snapshot_excluded_from_transfer` | core-orchestrator "Core._backup_target triggers full backup when due"; D5 (transitional queue deleted) |
| tests/core/test_full_verification_pipeline.py | FULL creation via `run_backup`; rollback deletes FULL file via `provider.delete(BackupInfo)`, checkpoint via `_cleanup_failed_checkpoint`, state via `remove_full_backup`; retry up to `backup_retry_max`; CRITICAL + old generations preserved | core-orchestrator 6/7/8 |
| tests/core/test_enospc_isolation.py | `transfer_side_effect(vm_config, target, snapshots)` → per-disk `run_backup(vm_config, target, disk)` side effect; per-disk ENOSPC suspension still skips remaining disks of that target only | core-orchestrator VM-level isolation / space-error scenarios |
| tests/core/test_bitmap_dependency.py | Dependency keys now freeze-ts backup names (and legacy names accepted); `record_incremental_dependency` called by Core after verification | backup-target-orthogonality; state-management |
| tests/core/test_dry_run_prediction.py | Backup predictions from target-internal data only (`list_checkpoints`, gate state, dependency count); delete simulated-snapshot threading into backup predictions; FULL estimate from `base_image` backing chain via shared helper | dry-run-prediction REMOVED+MODIFIED requirements |
| tests/core/test_restore.py | `Core.restore(name=None, at=None, vm_filter=None)`; `--at` superset selection + logged used point; `_resolve_snapshot` legacy name shim → `--at`; `target_dir` param removed (write to `disk_cfg.base_image`) | restore-command all scenarios |
| tests/core/test_list_commands.py | `list_backups` consumes `list[BackupInfo]`; new `list_restore_points(vm)` enumerates freeze points per target/disk with FULL markers, sorted by timestamp | restore-points-listing |
| tests/state/test_manager.py | Add mixed-generation dependency tests (legacy + backup-name keys counted together); extend `reset_target_disk_state` coverage for backup-name keys | state-management; restore-command "leaves other disks and other VMs intact" |
| tests/utils/test_parsing.py | Add freeze-ts name format tests: `vm.20260808T031542_vda_a1b2c3` parses via `parse_timestamp`/`parse_disk_from_snapshot_name` | backup-target-orthogonality "Freeze-timestamp backup naming" |
| tests/utils/test_locking.py | `resolve_lockfile_path(None, None)` → default `/var/lib/qsnap/qsnap.lock`; `"off"` sentinel → no lock; parent dir auto-created; replace `test_none_lockfile_path_means_no_locking` and `test_lockfile_path_resolution_none_when_both_none` | locking D9 scenarios 6/7 |
| tests/cli/test_app.py | Default lockfile acquired for mutating commands (tests pass `--lockfile <tmp>` or `--lockfile off` to avoid touching `/var/lib/qsnap`); read-only commands (`list`, `check`, `stats`, `estimate`) skip lock; `--lockfile off` parses; `list restore-points` sub-subcommand parses; exit-3 message "Lockfile is held by another qsnap instance" | locking all scenarios |
| tests/cli/test_commands.py | `handle_restore` gains `--at`; `handle_list` gains `restore-points`; `restore --at` dispatches to `core.restore(at=...)`; summary/error output carries target+disk | restore-command; backup-summary |
| tests/cli/test_summary.py | Backup-failure error row rendered `!!! [vda] backup to target <path> failed — <reason>` (no "snapshot" wording) | backup-summary scenario 2 |
| tests/cli/test_tree.py | Backup tree handles freeze-ts + legacy names in one chain | backup-target-orthogonality "Legacy backup files remain first-class" |

---

## Tests To Delete

Each entry: test (file:line) — justification (removed requirement / obsolete design decision). Tests whose intent survives under the new API are listed as MODIFY-INTO-REPLACEMENT instead.

### Deleted — temporal mismatch removal (nbd-bitmap-backup REMOVED requirement)

| # | Test (file:line) | Justification |
|---|---|---|
| 1 | `test_temporal_mismatch_snapshot_predates_checkpoint` (tests/modules/backup/test_bitmap_incremental.py:1671) | Asserts the temporal mismatch guard that is REMOVED (compared checkpoint-name seconds vs snapshot microseconds). |
| 2 | `test_temporal_mismatch_snapshot_after_checkpoint_proceeds` (tests/modules/backup/test_bitmap_incremental.py:1723) | Same guard, "proceeds" branch — obsolete. |
| 3 | `test_temporal_mismatch_no_checkpoint_proceeds` (tests/modules/backup/test_bitmap_incremental.py:1870) | Premise is `transfer_missing` with no checkpoint; superseded by `run_backup` no-checkpoint → FULL scenarios. |

### Deleted — removed `transfer_missing`/`create_full_backup` API (backup-provider REMOVED + backup-target-orthogonality D2)

| # | Test (file:line) | Justification |
|---|---|---|
| 4 | `test_transfer_missing_safety_net_prior_none_full_export` (tests/modules/backup/test_bitmap_incremental.py:2001) | Asserts the removed "safety net when prior is None" (produced a full copy mislabeled with a snapshot name); replaced by honest `FULL.{freeze_ts}` naming. |
| 5 | `test_transfer_missing_collision_successor_differs_from_prior` (tests/modules/backup/test_bitmap.py:2324) | Entry point `transfer_missing` removed; checkpoint-collision bump is re-verified under `run_backup` (replacement: `test_new_checkpoint_name_bumps_on_collision` retained). |
| 6 | `test_transfer_missing_defaults_to_qemu_img_convert` (tests/modules/backup/test_bitmap.py:2533) | Entry point removed. |
| 7 | `test_bitmap_full_backup_does_not_raise_not_implemented` (tests/modules/backup/test_bitmap.py:886) | `create_full_backup` no longer exists on the interface; `run_backup` FULL path is the single entry. |
| 8 | `test_full_timestamp_matches_snapshot` (tests/modules/backup/test_bitmap_convert.py:677) | Asserts FULL filename embeds the SNAPSHOT timestamp — direct contradiction of freeze-ts naming (D3). |
| 9 | `test_constructor_accepts_state_manager` (tests/modules/backup/test_bitmap.py:294) | Provider constructor no longer accepts `IStateManager` (backup-provider MODIFIED requirement). `test_constructor_works_without_state_manager` is retained as-is. |
| 10 | `test_create_full_backup_does_not_self_record` (tests/modules/backup/test_bitmap.py:1627) | Provider no longer records state; recording is Core's responsibility after verification. |
| 11 | `test_create_full_backup_skips_state_when_none` (tests/modules/backup/test_bitmap.py:1658) | Same — state-access plumbing removed from provider. |

### Deleted — contract tests for removed methods (tests/interfaces/test_backup_provider.py)

| # | Test (file:line) | Justification |
|---|---|---|
| 12 | `test_backup_provider_transfer_missing_returns_list_of_backup_result` (:53) | Method removed. |
| 13 | `test_backup_provider_list_returns_list_of_snapshotinfo` (:85) | `list()` now returns `list[BackupInfo]`, never `SnapshotInfo`. |
| 14 | `test_backup_provider_delete_returns_shellresult` (:103) | Signature changes to `delete(BackupInfo)`; replaced by `test_backup_provider_delete_accepts_backupinfo`. |
| 15 | `test_ibackup_provider_create_full_backup_abstract` (:117) | `create_full_backup` removed from interface. |
| 16 | `test_backup_provider_create_full_backup_returns_backup_result` (:181) | Method removed. |
| 17 | `test_backup_provider_create_full_backup_accepts_convert_parallel` (:212) | Method removed. |
| 18 | `test_backup_provider_create_full_backup_accepts_convert_out_of_order` (:251) | Method removed. |
| 19 | `test_backup_provider_transfer_missing_accepts_convert_parallel` (:290) | Method removed. |
| 20 | `test_backup_provider_transfer_missing_accepts_convert_out_of_order` (:327) | Method removed. |
| 21 | `test_transfer_missing_result_carries_disk` (:393) | Asserts **break-on-first-failure** batch semantics ("Bitmap stops at the first definitive transfer failure and returns the partial results collected so far") — replaced by continue-then-abort (D8). |
| 22 | `test_create_full_backup_result_carries_disk` (:464) | Method removed. |
| 23 | `test_create_full_backup_result_carries_checkpoint` (:528) | Method removed. |

### Deleted — mock tests asserting removed methods (tests/mocks/test_mock_factory.py)

| # | Test (file:line) | Justification |
|---|---|---|
| 24 | `test_mock_backup_provider_has_create_full_backup` (:99) | Mock must not expose removed method. |
| 25 | `test_mock_backup_provider_create_full_backup_accepts_new_params` (:118) | Removed method. |
| 26 | `test_mock_backup_provider_transfer_missing_accepts_new_params` (:138) | Removed method. |
| 27 | `test_mock_bitmap_backup_provider_create_full_backup_accepts_new_params` (:162) | Removed method. |
| 28 | `test_mock_bitmap_backup_provider_transfer_missing_accepts_new_params` (:184) | Removed method. |

### Deleted — "backup queue = snapshot list" dry-run premise (dry-run-prediction REMOVED requirement)

| # | Test (file:line) | Justification |
|---|---|---|
| 29 | `test_incremental_transfer_predictions_two_snapshots` (tests/core/test_dry_run_prediction.py:284) | Predicts one transfer per snapshot — the per-snapshot transfer-list prediction is removed; backup predictions are per-disk from target-internal data. |
| 30 | `test_already_on_target_not_predicted` (tests/core/test_dry_run_prediction.py:346) | Premise: snapshot present in `provider.list()` is skipped from the snapshot transfer queue — queue concept removed. |

### Deleted — old lockfile-default-None behavior (locking MODIFIED requirement)

| # | Test (file:line) | Justification |
|---|---|---|
| 31 | `test_none_lockfile_path_means_no_locking` (tests/utils/test_locking.py:75) | `None` no longer means "no locking" — the default is `/var/lib/qsnap/qsnap.lock`; disabling requires explicit `"off"`. |
| 32 | `test_lockfile_path_resolution_none_when_both_none` (tests/utils/test_locking.py:93) | Same — resolution now returns the default path instead of `None`. |

### Deleted — Phase-1 transitional anchor-queue premise (design D5)

| # | Test (file:line) | Justification |
|---|---|---|
| 33 | `test_full_source_snapshot_excluded_from_transfer` (tests/core/test_full_anchor.py:87) | Asserts the Phase-1 "anchor-based queue rule" (exclude the FULL source snapshot from the transfer queue). Phase 2 removes the queue entirely: `run_backup` transfers one delta per disk regardless of snapshots. |

### MODIFY-INTO-REPLACEMENT (intent survives under the new API — NOT deletions)

| Test (file:line) | Replacement intent |
|---|---|
| `test_transfer_missing_multi_disk_per_disk_results` (tests/modules/backup/test_bitmap.py:4281) | Becomes `test_multi_disk_run_returns_per_disk_results` (provider-level; per-disk `run_backup` results carry disk). |
| `test_transfer_missing_failed_still_carries_disk` (tests/modules/backup/test_bitmap.py:4341) | Becomes `test_failed_run_backup_still_carries_disk`. |
| `test_first_run_full_from_simulated_snapshot` (tests/core/test_dry_run_prediction.py:258) | Becomes `test_no_checkpoint_predicts_full` (prediction from target-internal data). |
| `test_startup_validation_no_checkpoint_deletion` (tests/core/test_pipeline.py:5861) | Becomes `test_startup_orphan_checkpoint_deleted_at_startup` (startup now DELETES crash orphans). |
| `test_factory_passes_state_to_bitmap_provider` (tests/factory/test_default.py:254) | Becomes `test_factory_constructs_bitmap_with_nbd_without_state`. |
| `test_pipeline_skips_retention_when_backup_transfer_fails` (tests/core/test_pipeline.py:7995) | Re-asserted under continue-then-abort: all disks attempted, successes audited, then `BackupAbortError` aborts remaining VM steps. |

**Total tests to delete: 33.**

---

## Integration / Stress / E2E Updates

### tests/integration/test_incremental_backup.py
- Rewrite all five tests (`test_incremental_after_full`, `test_incremental_compression_not_applied`, `test_vm_level_stall_timeout_reaches_incremental`, `test_incremental_dirty_bytes_proportional`, `test_free_space_gate_strict_blocks_incremental_before_transfer`) from `create_full_backup`+`transfer_missing` to per-disk `provider.run_backup(vm_config, target, "vda", ...)`. Assert delta filename is `{vm}.{freeze_ts}_vda_{6hex}.qcow2` and chains onto the previous file.
- **NEW `test_stopped_vm_defers_then_catches_up_after_boot`**: run1 while running creates FULL+checkpoint; stop VM; `run_backup` returns `deferred=True` (no file, no checkpoint mutation, baseline NOT updated); boot VM; next `run_backup` transfers the complete delta since the last checkpoint; verify no coverage gap by dirty-block count and file size proportionality.
- **NEW `test_run1_full_checkpoint_run2_delta_gap_free`**: run1 `run_backup` → FULL + exactly one checkpoint; write data; run2 `run_backup` → exactly one delta absorbing all writes since the checkpoint (gap-free), successor checkpoint rotated (exactly one qsnap checkpoint remains for the disk).

### tests/integration/test_full_backup.py
- Rewrite `test_full_backup_compression_modes`, `test_full_backup_stopped_vm`, `test_full_backup_running_vm_nbd`, `test_full_backup_qemu_img_convert_engine_default`, `test_free_space_gate_*`, `test_full_backup_custom_convert_parallel_and_out_of_order`, `test_vm_level_engine_options_reach_convert_command` to call `run_backup` with no checkpoint (FULL decision). Assert `{vm}.FULL.{freeze_ts}_{disk}_{6hex}.qcow2` naming and standalone qcow2 (no backing file).

### tests/integration/test_count_based_full.py
- Drive the count-based FULL decision through `Core`/`_backup_target` per disk (169 > 168 → FULL directed for that disk; within limit → delta). Assert `run_backup` invoked once per disk per run.

### tests/integration/test_multi_disk.py
- `test_backup_both_disks`: replace `create_full_backup` calls with `run_backup(vm_config, target, "vda")` / `run_backup(..., "vdb")`; assert freeze-ts per-disk names and distinct per-disk checkpoints.
- **NEW `test_one_disk_fails_other_disk_continues`**: force a definitive failure for `vda` (e.g., pre-delete the previous backup after checkpoint exists, or corrupt the target file) while `vdb` is pending; assert `vdb` still completes and is recorded, then VM aborts with `BackupAbortError` attributing target+`vda`; assert no partial `vda` file remains on the target (immediate `rm -f`).

### tests/integration/test_startup_validation.py
- **Invert the orphan-checkpoint assertion** (lines 211–234): startup validation now DELETES a crash-orphan checkpoint (newest checkpoint with no backup file `mtime >= checkpoint ts`) best-effort with a WARNING; the next run re-covers the interval from the previous checkpoint. Keep the phantom-FULL self-healing assertions; add `test_startup_deletes_crash_orphan_checkpoint` (delete the newest backup file, keep the checkpoint → next `core.run()` deletes the orphan checkpoint, logs WARNING naming checkpoint+target, and the delta re-covers the interval).

### tests/integration/test_restore.py
- Add `test_restore_at_first_point_above_end_to_end`: run1 FULL, write data, run2 delta; stop VM; `qsnap restore --at <between-timestamps> --yes`; assert the 04:00-style (newer) chain is restored, requested vs used point logged, VM boots.
- Extend `test_restore_from_backup_resolves_disk` for freeze-ts backup names and the legacy-name shim (restore a legacy snapshot-named backup → behaves as `--at`).

### tests/integration/test_auto_recovery.py, test_verify_before_delete.py, test_rollback_retry.py, test_broken_chain.py, test_post_creation_validation.py, test_target_chain_length_none.py, test_backup_retry_max_zero.py
- Mechanical `create_full_backup`/`transfer_missing` → `run_backup` rewrites; keep the verify-before-delete (D3) and rollback assertions; assert failed-FULL rollback deletes FULL file + checkpoint + state records and retries up to `backup_retry_max`.

### tests/stress/test_concurrent.py
- Replace the placeholder skip with a real lockfile test using the NEW default: `test_default_lockfile_blocks_second_run` — launch `qsnap run` (config with no `lockfile` key → `/var/lib/qsnap/qsnap.lock`) in a background process holding the lock; second `qsnap run` exits 3 and prints "Lockfile is held by another qsnap instance". Also `test_read_only_command_runs_during_locked_run` — `qsnap list`/`qsnap check` succeed while the mutating run holds the lock.

### tests/e2e/test_restore.py
- Add `test_restore_at_from_freeze_ts_target`: full pipeline `qsnap run` → verify freeze-ts files on target → `qsnap list restore-points` shows the points → stop VM → `qsnap restore --at <ts> --yes` → boot and verify.

### tests/e2e/test_from_config.py
- Assert target files use freeze-ts naming (`*.FULL.*.qcow2` / `*.{ts}_{disk}_*.qcow2`), exactly one checkpoint per disk after each run, and `qsnap list restore-points` output matches the files on disk.

### NEW integration tests (summary)
1. `test_stopped_vm_defers_then_catches_up_after_boot` — D6 variant A, gap-free catch-up.
2. `test_run1_full_checkpoint_run2_delta_gap_free` — successor-as-baseline + checkpoint rotation end-to-end.
3. `test_one_disk_fails_other_disk_continues` — D8 continue-then-abort isolation with a real second disk.
4. `test_startup_deletes_crash_orphan_checkpoint` — D9 orphan-checkpoint invariant at startup.

---

## Risks & Edge Cases

Each risk from design.md "Risks / Trade-offs" mapped to dedicated tests.

| Risk (design.md) | Test file / scenario | What it verifies |
|---|---|---|
| Restore points no longer snapshot-shaped; operator muscle memory | tests/core/test_list_commands.py::test_list_restore_points_shows_freeze_points_per_target + tests/core/test_restore.py::test_restore_at_selects_first_point_above | `list restore-points` shows real freeze points (RPO readable); `--at` superset policy is explicit; local snapshot chain remains the exact-point source (`list snapshots` unaffected — covered by existing test_list_commands tests). |
| `run_backup` is a BREAKING ABC change — implementations and mocks must update | tests/interfaces/test_backup_provider.py (parametrized over `BitmapBackupProvider` + `MockBitmapBackupProvider`: `run_backup`/`list→BackupInfo`/`delete(BackupInfo)`), tests/mocks/test_mock_validity.py::test_mock_backup_provider_api_carries_no_snapshotinfo, tests/factory/test_default.py::test_factory_constructs_bitmap_with_nbd_without_state | Contract completeness: any implementation/mock missing the new methods fails collection; both concrete classes pass the same contract without edits (migration completeness gate). |
| Phase 1→Phase 2 transitional anchor queue ships in the window | tests/core/test_engine.py::test_backup_phase_runs_with_zero_snapshots_in_state + deletion of `test_full_source_snapshot_excluded_from_transfer` | Proves the backup phase consumes NO snapshot list/names/timestamps (queue provably absent); the transitional mechanism cannot regress in. |
| Clock rollback (NTP) breaks newest-wins ordering of wall-clock names | tests/utils/test_parsing.py::test_freeze_ts_name_format_parses + tests/core/test_list_commands.py::test_list_restore_points_sorted_by_timestamp (out-of-order timestamps still listed sorted; newest-wins baseline selection is by checkpoint list order, documented limitation) | Names remain parseable; listing never crashes on out-of-order timestamps; baseline selection stays deterministic. |
| mtime-based orphan-checkpoint invariant false positives (targets copied without mtimes) | tests/core/test_pipeline.py::test_startup_orphan_invariant_deletes_only_when_no_file_qualifies | The invariant deletes ONLY when NO file with `mtime >= checkpoint ts` exists; a qualifying file (even from a copy) keeps the checkpoint; non-fatal on deletion failure (test_startup_orphan_checkpoint_delete_failure_non_fatal). |
| Deferred backups on long-stopped VMs produce one large delta at boot | tests/integration/test_incremental_backup.py::test_stopped_vm_defers_then_catches_up_after_boot (delta size bounded by actual writes) + existing stall-detection coverage (tests/modules/backup/test_bitmap_incremental.py::test_stall_watchdog_aborts_with_correct_error_string, test_slow_progressing_loop_not_killed) | Gap-free catch-up; long transfers covered by `run_with_stall_detection` (no max timeout when data flows). |
| Dry-run predictions become coarser than per-snapshot lines | tests/core/test_dry_run_prediction.py::test_gate_open_with_checkpoint_predicts_single_delta_per_disk, test_no_checkpoint_predicts_full, test_full_prediction_carries_chain_size, test_full_estimation_never_uses_snapshot_files | Exactly one per-disk prediction; estimate from `base_image` chain via the shared helper; no snapshot path participates; estimation failure degrades to "size unknown" without aborting. |
| Mixed-generation chains (legacy + freeze-ts) on one target | tests/modules/backup/test_bitmap.py::test_mixed_generation_chain_resolves_via_backing_walk + tests/state/test_manager.py::test_mixed_generation_dependencies_counted_together + tests/core/test_engine.py::test_legacy_dependency_records_removed_with_generation | Previous-backup resolution walks files + backing headers (never names); dependency counting is key-format agnostic; legacy records expire via generation rotation with no migration. |
| Error attribution: "snapshot(s) failed" misdirects operators | tests/core/test_engine.py::test_disk_failure_warns_audits_successes_then_aborts + tests/cli/test_summary.py::test_summary_backup_failure_error_carries_disk_and_target | WARNING and summary error lines name target+disk only; the phrase "snapshot(s) failed" is asserted absent. |
| One disk's failure abandoning other disks (old `break`) | tests/core/test_engine.py::test_backup_failure_one_disk_still_attempts_other_disks + tests/integration/test_multi_disk.py::test_one_disk_fails_other_disk_continues | Continue-then-abort (D8): all disks attempted, successes audited/recorded, abort only after the batch. |
| Deferred results must not close the onchange gate | tests/core/test_pipeline.py::test_deferred_result_leaves_baseline_untouched | `set_last_backup_allocation` NOT called for `deferred=True`; no `BackupAbortError`; next run re-evaluates. |
| Stopped-VM with checkpoint: no data mutation | tests/modules/backup/test_bitmap.py::test_stopped_vm_with_checkpoint_defers_no_mutation | No `backup-begin`, no file creation, no checkpoint create/delete; result `success=True, deferred=True, disk=<disk>`. |
| Failed-file leak into retention cleanup | tests/core/test_engine.py::test_failed_backup_file_not_listed_by_retention_cleanup + tests/modules/backup/test_bitmap.py::test_failed_backup_file_deleted_after_verification_failure | `rm -f` with 10 s timeout immediately after verification failure; failed file absent from `glob("*.qcow2")`; no `[delete] removed backup` log for it. |

---

## Execution

```bash
# Fast gate (unit + mock + contract; no I/O) — all groups except integration-e2e:
poetry run pytest tests/ -m "not integration and not stress and not e2e"

# Grouped parallel execution (one pytest per group):
poetry run pytest tests/modules/backup/ tests/models/ -m unit
poetry run pytest tests/interfaces/ -m contract
poetry run pytest tests/factory/ tests/mocks/ -m "unit or mock"
poetry run pytest tests/core/ -m "unit or mock"
poetry run pytest tests/state/ tests/utils/ -m unit
poetry run pytest tests/cli/ tests/utils/test_locking.py -m "unit or mock"

# Integration / stress / e2e (need libvirt):
poetry run pytest tests/integration/ -m integration
poetry run pytest tests/stress/ -m stress
poetry run pytest tests/e2e/ -m e2e
```
