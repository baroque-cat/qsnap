# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-verification | verify_full_backup function | Metadata mode — valid qcow2 with no corrupt bit | tests/modules/backup/test_full_verification.py | test_verify_full_backup_metadata_valid_qcow2 | backup-full-verify-unit |
| backup-verification | verify_full_backup function | Metadata mode — corrupt bit detected | tests/modules/backup/test_full_verification.py | test_verify_full_backup_metadata_corrupt_bit | backup-full-verify-unit |
| backup-verification | verify_full_backup function | Metadata mode — wrong format | tests/modules/backup/test_full_verification.py | test_verify_full_backup_metadata_wrong_format | backup-full-verify-unit |
| backup-verification | verify_full_backup function | Metadata mode — qemu-img info fails entirely | tests/modules/backup/test_full_verification.py | test_verify_full_backup_metadata_info_fails | backup-full-verify-unit |
| backup-verification | verify_full_backup function | Check mode — passes after M1 and M2 | tests/modules/backup/test_full_verification.py | test_verify_full_backup_check_passes | backup-full-verify-unit |
| backup-verification | verify_full_backup function | Check mode — M2 detects errors | tests/modules/backup/test_full_verification.py | test_verify_full_backup_check_errors_detected | backup-full-verify-unit |
| backup-verification | verify_full_backup function | Hash mode — hash matches | tests/modules/backup/test_full_verification.py | test_verify_full_backup_hash_match | backup-full-verify-unit |
| backup-verification | verify_full_backup function | Hash mode — hash mismatch | tests/modules/backup/test_full_verification.py | test_verify_full_backup_hash_mismatch | backup-full-verify-unit |
| backup-verification | verify_full_backup function | Hash mode with no expected hash — skips M3 | tests/modules/backup/test_full_verification.py | test_verify_full_backup_hash_none_skips_m3 | backup-full-verify-unit |
| backup-verification | verify_full_backup function | Off mode — no verification | tests/modules/backup/test_full_verification.py | test_verify_full_backup_off_no_commands | backup-full-verify-unit |
| backup-verification | verify_full_backup function | Content hash consumed at post-create verification | tests/core/test_full_verification_pipeline.py | test_full_verify_after_create_hash_uses_snapshot_hash | core-full-verify |
| backup-full-verification | M1 after creation | M1 passes — FULL valid after creation | tests/core/test_full_verification_pipeline.py | test_full_created_m1_passes_recorded_in_state | core-full-verify |
| backup-full-verification | M1 after creation | M1 fails — corrupt FULL detected after creation | tests/core/test_full_verification_pipeline.py | test_full_created_m1_fails_corrupt_bit_deleted | core-full-verify |
| backup-full-verification | M1 after creation | M1 fails — FULL not a valid qcow2 | tests/core/test_full_verification_pipeline.py | test_full_created_m1_fails_not_qcow2_deleted | core-full-verify |
| backup-full-verification | M1 before rebase | M1 passes — rebase proceeds | tests/modules/backup/test_copy.py | test_transfer_missing_rebases_to_full_anchor_m1_passes | backup-provider-mod |
| backup-full-verification | M1 before rebase | M1 fails — rebase uses alternative FULL anchor | tests/modules/backup/test_copy.py | test_transfer_missing_rebase_uses_alternative_full_on_m1_fail | backup-provider-mod |
| backup-full-verification | M1 before rebase | M1 fails — no alternative FULL anchor exists | tests/modules/backup/test_copy.py | test_transfer_missing_no_rebase_when_no_valid_full | backup-provider-mod |
| backup-full-verification | M1 before cascade-deletion | M1 passes — cascade-deletion proceeds | tests/core/test_full_verification_pipeline.py | test_cleanup_backups_m1_passes_full_deleted | core-full-verify |
| backup-full-verification | M1 before cascade-deletion | M1 fails — cascade-deletion completely blocked | tests/core/test_full_verification_pipeline.py | test_cleanup_backups_m1_fails_cascade_blocked | core-full-verify |
| backup-full-verification | M1 before cascade-deletion | M1 fails on FULL with no dependents — deletion still blocked | tests/core/test_full_verification_pipeline.py | test_cleanup_backups_m1_fails_no_dependents_still_blocked | core-full-verify |
| backup-full-verification | M2 structural verification | M2 passes — no errors or leaks | tests/modules/backup/test_full_verification.py | test_verify_full_backup_check_no_errors | backup-full-verify-unit |
| backup-full-verification | M2 structural verification | M2 fails — errors detected | tests/modules/backup/test_full_verification.py | test_verify_full_backup_check_errors_detected | backup-full-verify-unit |
| backup-full-verification | M2 structural verification | M2 skipped when configured to "metadata" only | tests/core/test_full_verification_pipeline.py | test_full_verify_metadata_mode_skips_m2 | core-full-verify |
| backup-full-verification | M3 hash verification | M3 hash matches | tests/core/test_full_verification_pipeline.py | test_full_verify_hash_match_success | core-full-verify |
| backup-full-verification | M3 hash verification | M3 hash mismatch | tests/core/test_full_verification_pipeline.py | test_full_verify_hash_mismatch_fails | core-full-verify |
| chain-integrity-verification | File existence guard before blockcommit | All snapshot files exist — blockcommit proceeds normally | tests/core/test_pipeline.py | test_blockcommit_stale_guard_all_exist_proceeds | core-pipeline-mod |
| chain-integrity-verification | File existence guard before blockcommit | One stale entry removed — remaining blockcommitted | tests/core/test_pipeline.py | test_blockcommit_stale_guard_one_stale_removed | core-pipeline-mod |
| chain-integrity-verification | File existence guard before blockcommit | All entries stale — blockcommit skipped entirely | tests/core/test_pipeline.py | test_blockcommit_stale_guard_all_stale_skipped | core-pipeline-mod |
| chain-integrity-verification | File existence guard before blockcommit | Stale entry does NOT cause short-circuit | tests/core/test_pipeline.py | test_blockcommit_stale_guard_no_short_circuit | core-pipeline-mod |
| core-orchestrator | Core triggers full backup with verification | FULL created and verified before state recording | tests/core/test_full_verification_pipeline.py | test_full_backup_verified_before_state_recording | core-full-verify |
| core-orchestrator | Core triggers full backup with verification | FULL verification fails — file deleted, not recorded | tests/core/test_full_verification_pipeline.py | test_full_backup_verify_fails_file_deleted_not_recorded | core-full-verify |
| core-orchestrator | Core triggers full backup with verification | First backup to target creates FULL | tests/core/test_full_verification_pipeline.py | test_first_backup_creates_full_with_verification | core-full-verify |
| core-orchestrator | Core triggers full backup with verification | New weekly period triggers FULL (all-buckets mode) | tests/core/test_full_verification_pipeline.py | test_new_weekly_creates_full_with_verification | core-full-verify |
| core-orchestrator | Cleanup backups with pre-deletion verification | Cleanup proceeds after M1 passes | tests/core/test_full_verification_pipeline.py | test_cleanup_proceeds_on_m1_pass | core-full-verify |
| core-orchestrator | Cleanup backups with pre-deletion verification | Cleanup blocked when M1 fails on FULL | tests/core/test_full_verification_pipeline.py | test_cleanup_blocked_on_m1_fail | core-full-verify |
| core-orchestrator | File existence guard before blockcommit | Stale entry filtered before blockcommit | tests/core/test_pipeline.py | test_blockcommit_stale_guard_stale_filtered | core-pipeline-mod |
| core-orchestrator | File existence guard before blockcommit | All entries stale — blockcommit skipped | tests/core/test_pipeline.py | test_blockcommit_stale_guard_all_stale_skipped | core-pipeline-mod |
| nbd-bitmap-backup | NBD pull-model backup | First backup — full pull via NBD | tests/modules/backup/test_bitmap.py | test_bitmap_first_full_pull_via_nbd | bitmap-mod |
| nbd-bitmap-backup | NBD pull-model backup | Incremental backup — dirty blocks via NBD | tests/modules/backup/test_bitmap.py | test_bitmap_incremental_dirty_blocks_via_nbd | bitmap-mod |
| nbd-bitmap-backup | NBD pull-model backup | NBD backup job terminated after transfer | tests/modules/backup/test_bitmap.py | test_bitmap_nbd_job_terminated_after_transfer | bitmap-mod |
| nbd-bitmap-backup | NBD pull-model backup | Socket cleanup after job abort | tests/modules/backup/test_bitmap.py | test_bitmap_socket_cleanup_after_job_abort | bitmap-mod |
| backup-provider | Transfer missing snapshots | New snapshot copied to empty target via rsync | tests/modules/backup/test_copy.py | test_transfer_missing_new_snapshot_rsync_empty_target | backup-provider-mod |
| backup-provider | Transfer missing snapshots | Transfer with rate limit uses rsync --bwlimit | tests/modules/backup/test_copy.py | test_transfer_with_rate_limit_uses_rsync | backup-provider-mod |
| backup-provider | Transfer missing snapshots | Snapshot already exists on target — skipped | tests/modules/backup/test_copy.py | test_transfer_missing_existing_snapshot_skipped | backup-provider-mod |
| backup-provider | Transfer missing snapshots | Incremental backup — rebase backing path with -F qcow2 | tests/modules/backup/test_copy.py | test_transfer_incremental_rebase_with_F_qcow2 | backup-provider-mod |
| backup-provider | Transfer missing snapshots | Rebase to FULL anchor when present | tests/modules/backup/test_copy.py | test_transfer_missing_rebases_to_full_anchor_with_m1 | backup-provider-mod |
| backup-provider | Transfer missing snapshots | No FULL anchor preserves existing behavior | tests/modules/backup/test_copy.py | test_transfer_no_full_anchor_rebase_with_F_flag | backup-provider-mod |
| backup-provider | Transfer missing snapshots | Non-incremental backup — no rebase | tests/modules/backup/test_copy.py | test_transfer_non_incremental_no_rebase | backup-provider-mod |
| backup-provider | Transfer missing snapshots | rsync unavailable — transfer fails | tests/modules/backup/test_copy.py | test_rsync_unavailable_transfer_fails_no_cp_fallback | backup-provider-mod |
| backup-provider | Transfer missing snapshots | Copy fails — disk full or permission error | tests/modules/backup/test_copy.py | test_transfer_rsync_fails_disk_full | backup-provider-mod |
| backup-provider | Transfer missing snapshots | copy_base=false prevents base.qcow2 duplication | tests/modules/backup/test_copy.py | test_copy_base_false_prevents_base_copy | backup-provider-mod |
| backup-provider | Transfer missing snapshots | copy_base=true allows legacy base copy | tests/modules/backup/test_copy.py | test_copy_base_true_allows_base_copy | backup-provider-mod |
| backup-provider | Transfer missing snapshots | Stale snapshot in state — file does not exist on disk | tests/modules/backup/test_copy.py | test_transfer_missing_stale_snapshot_skipped | backup-provider-mod |
| backup-provider | Rebase error handling | Rebase fails due to invalid backing path | tests/modules/backup/test_copy.py | test_transfer_rebase_failure_returns_backup_result_failure | backup-provider-mod |
| backup-provider | Rebase error handling | Rebase fails due to missing -F flag on QEMU 6.1+ | tests/modules/backup/test_copy.py | test_risk_rebase_missing_F_flag_detected | backup-provider-mod |
| backup-provider | Snapshot file existence guard before rsync | Snapshot file exists — transfer proceeds | tests/modules/backup/test_copy.py | test_transfer_missing_file_exists_proceeds | backup-provider-mod |
| backup-provider | Snapshot file existence guard before rsync | Snapshot file does not exist — entry cleaned and skipped | tests/modules/backup/test_copy.py | test_transfer_missing_stale_snapshot_skipped | backup-provider-mod |
| snapshot-provider | Snapshot creation retry on lock conflict | Lock conflict resolves on first retry | tests/modules/snapshot/test_external.py | test_create_snapshot_retry_lock_conflict_resolves | snapshot-mod |
| snapshot-provider | Snapshot creation retry on lock conflict | Lock conflict persists through all retries | tests/modules/snapshot/test_external.py | test_create_snapshot_retry_lock_conflict_persists | snapshot-mod |
| snapshot-provider | Snapshot creation retry on lock conflict | Non-lock error is NOT retried | tests/modules/snapshot/test_external.py | test_create_snapshot_no_retry_non_lock_error | snapshot-mod |
| snapshot-provider | Snapshot creation retry on lock conflict | Timeout on lock conflict triggers retry | tests/modules/snapshot/test_external.py | test_create_snapshot_retry_lock_conflict_timeout | snapshot-mod |
| live-vm-full-backup | NBD full-export helper | NBD full export produces standalone qcow2 | tests/modules/backup/test_copy.py | test_nbd_full_export_produces_standalone_qcow2 | backup-provider-mod |
| live-vm-full-backup | NBD full-export helper | NBD socket cleaned up on success | tests/modules/backup/test_copy.py | test_nbd_socket_and_domjobabort_on_success | backup-provider-mod |
| live-vm-full-backup | NBD full-export helper | NBD cleanup on failure — backup job aborted | tests/modules/backup/test_copy.py | test_nbd_cleanup_on_failure_domjobabort | backup-provider-mod |
| live-vm-full-backup | NBD full-export helper | NBD backup job abort fails gracefully | tests/modules/backup/test_copy.py | test_risk_domjobabort_fails_gracefully | backup-provider-mod |
| live-vm-full-backup | NBD full-export helper | No checkpoint created for file-copy NBD FULL | tests/modules/backup/test_copy.py | test_nbd_full_file_copy_no_checkpoint_created | backup-provider-mod |
| config-model | GlobalConfig full_verify_after_create | Default is check (M1 + M2) | tests/config/test_model.py | test_global_config_full_verify_after_create_default | config-mod |
| config-model | GlobalConfig full_verify_after_create | User sets metadata only | tests/config/test_model.py | test_global_config_full_verify_after_create_metadata | config-mod |
| config-model | GlobalConfig full_verify_after_create | User sets hash (M1 + M2 + M3) | tests/config/test_model.py | test_global_config_full_verify_after_create_hash | config-mod |
| config-model | GlobalConfig full_verify_before_rebase | Default is metadata | tests/config/test_model.py | test_global_config_full_verify_before_rebase_default | config-mod |
| config-model | GlobalConfig full_verify_before_delete | Default is check (M1 + M2) | tests/config/test_model.py | test_global_config_full_verify_before_delete_default | config-mod |
| config-model | GlobalConfig full_verify_before_delete | Set to off — M1 still enforced | tests/config/test_model.py | test_global_config_full_verify_before_delete_off_m1_still_enforced | config-mod |
| config-model | GlobalConfig deep_check_targets | deep_check_targets disabled by default | tests/config/test_model.py | test_global_config_deep_check_targets_default_false | config-mod |
| config-model | GlobalConfig deep_check_targets | deep_check_targets enabled | tests/config/test_model.py | test_global_config_deep_check_targets_true | config-mod |
| cascade-deletion | Core prevents deletion of FULLs with active dependents | FULL kept due to corrupt FULL — cascade blocked | tests/core/test_full_verification_pipeline.py | test_cascade_deletion_blocked_on_corrupt_full | core-full-verify |
| cascade-deletion | Core prevents deletion of FULLs with active dependents | FULL kept due to active dependent | tests/core/test_full_verification_pipeline.py | test_full_kept_due_to_active_dependent | core-full-verify |
| cascade-deletion | Core prevents deletion of FULLs with active dependents | FULL deleted when no active dependents and M1 passes | tests/core/test_full_verification_pipeline.py | test_full_deleted_no_dependents_m1_passes | core-full-verify |
| cascade-deletion | Cascade deletion of orphaned incrementals | Orphaned incrementals cascade-deleted | tests/core/test_full_verification_pipeline.py | test_orphaned_incrementals_cascade_deleted | core-full-verify |
| cascade-deletion | Cascade deletion of orphaned incrementals | Kept incremental rebased to new anchor | tests/core/test_full_verification_pipeline.py | test_kept_incremental_rebased_to_new_anchor | core-full-verify |
| pre-flight-cleanup | Stale temporary file cleanup | tmp files in snapshot_dir removed | tests/core/test_validation.py | test_preflight_cleanup_tmp_files_in_snapshot_dir_removed | core-validation |
| pre-flight-cleanup | Stale temporary file cleanup | tmp files in target directories removed | tests/core/test_validation.py | test_preflight_cleanup_tmp_files_in_target_dirs_removed | core-validation |
| pre-flight-cleanup | Stale temporary file cleanup | Stale NBD sockets removed | tests/core/test_validation.py | test_preflight_cleanup_stale_nbd_sockets_removed | core-validation |
| pre-flight-cleanup | Stale temporary file cleanup | Truncated rsync qcow2 file detected and deleted | tests/core/test_validation.py | test_preflight_cleanup_truncated_qcow2_detected_deleted | core-validation |
| pre-flight-cleanup | Stale temporary file cleanup | Valid qcow2 files are NOT deleted | tests/core/test_validation.py | test_preflight_cleanup_valid_qcow2_not_deleted | core-validation |
| pre-flight-cleanup | Stale temporary file cleanup | No stale files — no action | tests/core/test_validation.py | test_preflight_cleanup_no_stale_files_no_action | core-validation |

## Delegation Groups

### Group: backup-full-verify-unit
**Scope:** tests/modules/backup/test_full_verification.py (NEW FILE)
| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/backup/test_full_verification.py | All verify_full_backup function scenarios: metadata (valid, corrupt, wrong format, info fails), check (passes, errors), hash (match, mismatch, null skip), off mode | CREATE — new file with ~12 tests |

### Group: core-full-verify
**Scope:** tests/core/test_full_verification_pipeline.py (NEW FILE)
| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_full_verification_pipeline.py | FULL verification at M1/M2/M3 lifecycle points in Core orchestration: post-create verification (pass/fail/delete), pre-rebase, pre-deletion (pass/fail/cascade-blocked), hash verification, config-driven M2 skip, cascade-deletion integrity gates | CREATE — new file with ~18 tests |

### Group: backup-provider-mod
**Scope:** tests/modules/backup/test_copy.py
| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/backup/test_copy.py | Rebase with -F qcow2 flag, stale state guard before rsync, FULL anchor rebase with M1 verification, alternative FULL fallback, domjobabort in NBD cleanup, stale snapshot detection in transfer_missing | MODIFY — add ~8 tests, update existing rebase assertions |

### Group: bitmap-mod
**Scope:** tests/modules/backup/test_bitmap.py
| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/backup/test_bitmap.py | NBD job termination via domjobabort after transfer, socket cleanup order (abort first, then rm), first full pull via NBD, incremental dirty blocks via NBD | MODIFY — add ~4 tests, update existing NBD cleanup assertions |

### Group: snapshot-mod
**Scope:** tests/modules/snapshot/test_external.py
| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/snapshot/test_external.py | Lock conflict retry (resolves, persists, non-lock no retry, timeout retry) | MODIFY — add ~4 tests for retry mechanism |

### Group: core-pipeline-mod
**Scope:** tests/core/test_pipeline.py
| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_pipeline.py | Stale state self-healing in _blockcommit_snapshots: all exist, one stale, all stale, no short-circuit. Also: stale state guard in _backup_target | MODIFY — add ~4 tests |

### Group: core-validation
**Scope:** tests/core/test_validation.py
| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_validation.py | Truncated qcow2 detection in pre-flight cleanup | MODIFY — add ~2 tests (truncated detected + deleted, valid qcow2 not deleted) |

### Group: config-mod
**Scope:** tests/config/test_model.py, tests/config/test_facade.py
| Test File | Scenarios | Action |
|---|---|---|
| tests/config/test_model.py | New GlobalConfig fields: full_verify_after_create, full_verify_before_rebase, full_verify_before_delete, deep_check_targets — defaults, immutability, valid values | MODIFY — add ~8 tests |
| tests/config/test_facade.py | TOML parsing of new GlobalConfig fields | MODIFY — add ~4 tests |

### Group: integration-tests
**Scope:** tests/integration/
| Test File | Scenarios | Action |
|---|---|---|
| tests/integration/test_nbd_full_backup.py | domjobabort in integration, stale state recovery, qemu-img rebase -F qcow2 on real files | MODIFY — add ~3 integration tests |
| tests/integration/test_stale_state_recovery.py (NEW) | Integration test: simulate stale state + crash recovery, verify self-healing | CREATE — 1 test |

## Test Modifications
| File | Change | Reason |
|---|---|---|
| tests/modules/backup/test_copy.py:test_transfer_incremental_rebase_backing_path | Add assertion that rebase command includes `-F qcow2` | Spec now requires -F flag for QEMU 6.1+ compatibility |
| tests/modules/backup/test_copy.py:test_transfer_missing_rebases_to_full_anchor | Add assertion that rebase includes `-F qcow2` | Same -F requirement |
| tests/modules/backup/test_copy.py:test_transfer_missing_no_full_anchor_uses_source_backing | Add assertion that rebase includes `-F qcow2` | Same -F requirement |
| tests/modules/backup/test_copy.py:test_nbd_socket_cleanup_on_success | Add assertion that `virsh domjobabort` is called in finally block before `rm -f` socket | New domjobabort requirement |
| tests/modules/backup/test_copy.py:test_nbd_socket_cleanup_on_failure | Add assertion that `virsh domjobabort` still runs in finally block on failure | New domjobabort requirement |
| tests/modules/backup/test_copy.py:test_create_full_backup_nbd_running_vm_succeeds | Add mock expectations for `virsh domjobabort` in finally block | New finally-block behavior |
| tests/modules/backup/test_copy.py:test_nbd_full_export_produces_standalone_qcow2 | Add domjobabort expectation | Same reason |
| tests/modules/backup/test_bitmap.py:test_socket_cleanup_on_success | Add assertion that `virsh domjobabort` called before socket rm | New domjobabort requirement |
| tests/modules/backup/test_bitmap.py:test_socket_cleanup_on_failure | Add assertion that `virsh domjobabort` still called in finally | New domjobabort requirement |
| tests/modules/backup/test_bitmap.py:test_bitmap_full_socket_cleanup | Add domjobabort expectation | Same reason |
| tests/modules/backup/test_bitmap.py:test_bitmap_create_full_backup_nbd_succeeds | Add domjobabort expectation | Same reason |
| tests/config/test_model.py:test_global_config_defaults | Add assertions for new fields: full_verify_after_create="check", full_verify_before_rebase="metadata", full_verify_before_delete="check", deep_check_targets=False | New fields added to GlobalConfig |
| tests/config/test_model.py:test_global_config_immutable | Add freeze assertions for new fields | New fields must be frozen |
| tests/core/test_pipeline.py:test_first_backup_creates_full_via_bucket | Add mock expectations for verify_full_backup post-create verification | New verification step in Core pipeline |
| tests/core/test_pipeline.py:test_new_monthly_period_triggers_full | Add mock expectations for verify_full_backup | Same reason |
| tests/conftest.py:make_global_config | Add parameters for new fields: full_verify_after_create, full_verify_before_rebase, full_verify_before_delete, deep_check_targets | New config fields needed by tests |

## Test Removals
| File | Test Name | Reason |
|---|---|---|
| tests/modules/backup/test_copy.py | test_transfer_incremental_rebase_backing_path (partial) | The assertion on `rebase_cmd` that does NOT check for `-F qcow2` is now insufficient. Test stays but must be updated (see Modifications). |
| tests/modules/backup/test_copy.py | test_nbd_socket_cleanup_on_success (partial) | The `rm -f` socket cleanup assertion is still valid, but the test does not mock `virsh domjobabort` which is now part of the same finally block. Test must be updated with new expectation. |
| tests/modules/backup/test_copy.py | test_nbd_socket_cleanup_on_failure (partial) | Same as above — needs domjobabort expectation added. |
| tests/core/test_validation.py | test_preflight_cleanup_tmp_files_in_snapshot_dir_removed (partial) | The pre-flight cleanup now additionally detects truncated .qcow2 files. No test removal, but new tests needed for the additional behavior. |

## Risks & Edge Cases
- **[Risk] M2 on large FULL files (100GB+) adds minutes to pipeline latency** → `test_risk_m2_large_full_performance_caveat` in tests/modules/backup/test_full_verification.py: verifies M2 is configurable and default can be metadata-only via GlobalConfig.full_verify_after_create
- **[Risk] M1 on freshly-created FULL might fail due to filesystem cache** → `test_risk_m1_on_atomic_rename_is_safe` in tests/core/test_full_verification_pipeline.py: verifies M1 reads only qcow2 header which is always flushed after atomic rename
- **[Risk] virsh domjobabort fails because backup job already terminated** → `test_risk_domjobabort_fails_gracefully` in tests/modules/backup/test_copy.py: verifies WARNING logged on domjobabort failure, error NOT propagated, socket cleanup proceeds
- **[Risk] Stale state removal before pre-commit chain verification** → `test_risk_stale_removal_independent_of_chain_verify` in tests/core/test_pipeline.py: verifies pre-commit chain verification uses qemu-img info --backing-chain (disk-level), not IStateManager entries — removing stale state entries does not affect chain verification
- **[Risk] Snapshot lock-conflict retry could mask genuine persistent lock** → `test_risk_lock_retry_capped_at_three_attempts` in tests/modules/snapshot/test_external.py: verifies retry stops at 3 attempts max, error propagates on persistent lock, total wait ≤14s
- **[Risk] verify_full_backup signature diverges from verify_backup** → `test_risk_verify_full_backup_no_source_comparison` in tests/modules/backup/test_full_verification.py: verifies verify_full_backup does NOT call source-side qemu-img info, and verify_backup is unchanged (not broken)
- **[Risk] Pre-flight cleanup deleting partial .qcow2 files could conflict with another qsnap process** → `test_risk_preflight_cleanup_lockfile_protects_concurrent` in tests/core/test_validation.py: verifies lockfile (/run/qsnap.lock) prevents concurrent cleanup
- **[Risk] Config field full_verify_before_delete="off" could mislead users** → `test_risk_off_setting_still_enforces_m1_at_delete` in tests/config/test_model.py and tests/core/test_full_verification_pipeline.py: verifies M1 always runs regardless of config, "off" only disables M2
- **[Risk] Rebase without -F qcow2 flag on QEMU 6.1+ causes silent chain corruption** → `test_risk_rebase_missing_F_flag_detected` in tests/modules/backup/test_copy.py: verifies rebase command always includes -F qcow2, and a negative test confirms that without -F the rebase would fail on QEMU 6.1+
- **[Risk] Stale state causing short-circuit blocks ALL blockcommits** → `test_risk_stale_entry_does_not_short_circuit_remaining` in tests/core/test_pipeline.py: verifies that a single stale entry in to_merge does not block blockcommit of subsequent snapshots
- **[Risk] Transfer of stale (already-blockcommitted) snapshot files via rsync** → `test_risk_stale_snapshot_not_rsynced` in tests/modules/backup/test_copy.py: verifies os.path.exists() guard prevents rsync of non-existent snapshot files, state is cleaned
## Phase 2 Additions — State Integrity & M3 Fixes

### New Coverage Map Entries

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-verification | verify_full_backup (M3 qemu-img compare) | Hash mode — content comparison matches via qemu-img compare | tests/modules/backup/test_full_verification.py | test_verify_full_backup_hash_compare_match | backup-full-verify-unit |
| backup-verification | verify_full_backup (M3 qemu-img compare) | Hash mode — content comparison mismatch | tests/modules/backup/test_full_verification.py | test_verify_full_backup_hash_compare_mismatch | backup-full-verify-unit |
| backup-verification | verify_full_backup (M3 qemu-img compare) | Hash mode with no source_path — M3 skipped | tests/modules/backup/test_full_verification.py | test_verify_full_backup_hash_no_source_skips_m3 | backup-full-verify-unit |
| backup-full-verification | M3 qemu-img compare after creation | M3 compare match — FULL recorded | tests/core/test_full_verification_pipeline.py | test_full_verify_hash_compare_match_success | core-full-verify |
| backup-full-verification | M3 qemu-img compare after creation | M3 compare mismatch — FULL deleted | tests/core/test_full_verification_pipeline.py | test_full_verify_hash_compare_mismatch_fails | core-full-verify |
| backup-provider | transfer_missing does NOT create FULL | Empty target without FULL — no FULL auto-created | tests/modules/backup/test_copy.py | test_transfer_missing_does_not_create_full_when_empty_target | backup-provider-mod |
| backup-provider | transfer_missing does NOT create FULL | Target with FULLs — normal rsync transfer | tests/modules/backup/test_copy.py | test_transfer_missing_normal_rsync_when_fulls_exist | backup-provider-mod |
| cascade-deletion | State cleanup when FULL deleted | FULL deleted — FullBackupInfo removed from state | tests/core/test_full_verification_pipeline.py | test_full_deleted_fullbackupinfo_removed_from_state | core-full-verify |
| cascade-deletion | State cleanup when incremental deleted | Orphaned incremental deleted — dependency removed from state | tests/core/test_full_verification_pipeline.py | test_incremental_deleted_dependency_removed_from_state | core-full-verify |
| core-orchestrator | Phantom FULL detection | Phantom FULL detected and removed from state | tests/core/test_full_verification_pipeline.py | test_phantom_full_detected_removed_from_state | core-full-verify |
| core-orchestrator | Phantom FULL detection | All FULLs exist — no entries removed | tests/core/test_full_verification_pipeline.py | test_all_fulls_exist_no_phantom_cleanup | core-full-verify |
| core-orchestrator | M3 receives source_path | Hash mode passes source_path to verify_full_backup | tests/core/test_full_verification_pipeline.py | test_hash_mode_passes_source_path_to_verify | core-full-verify |
| config-model | Active buckets required with targets | All-zero buckets with targets raises ConfigError | tests/config/test_facade.py | test_all_zero_buckets_with_targets_raises_config_error | config-mod |
| config-model | Active buckets required with targets | preserve_min="all" allows all-zero buckets | tests/config/test_facade.py | test_preserve_min_all_allows_zero_buckets | config-mod |
| state-consistency-check | Phantom snapshot detection | All snapshot files exist — clean state | tests/core/test_state_check.py | test_check_state_all_snapshots_exist_clean | state-check |
| state-consistency-check | Phantom snapshot detection | Phantom snapshot detected — reported not auto-cleaned | tests/core/test_state_check.py | test_check_state_phantom_snapshot_detected | state-check |
| state-consistency-check | Phantom FULL detection | Phantom FULL detected | tests/core/test_state_check.py | test_check_state_phantom_full_detected | state-check |
| state-consistency-check | Orphaned dependency detection | Incremental dependency with deleted incremental | tests/core/test_state_check.py | test_check_state_orphaned_dependency_detected | state-check |
| state-consistency-check | Orphaned dependency detection | Incremental dependency with deleted FULL | tests/core/test_state_check.py | test_check_state_detached_dependency_detected | state-check |
| state-consistency-check | State file integrity | Corrupted state file detected | tests/core/test_state_check.py | test_check_state_corrupted_json_detected | state-check |

### New Delegation Groups

### Group: state-check
**Scope:** tests/core/test_state_check.py (NEW FILE)
| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_state_check.py | State consistency validation: phantom snapshots, phantom FULLs, orphaned dependencies, detached dependencies, corrupted state JSON, clean state | CREATE — new file with ~6 tests |

### Added Test Modifications
| File | Change | Reason |
|---|---|---|
| tests/modules/backup/test_full_verification.py | Update hash mode tests: replace expected_hash with source_path; test qemu-img compare via MockShell | M3 changed from SHA-256 to qemu-img compare |
| tests/core/test_full_verification_pipeline.py | Add source_path to hash verification calls in pipeline tests; add phantom FULL tests; add state cleanup tests | New M3 source_path param; new state cleanup requirements |
| tests/core/test_pipeline.py | Remove any D4-path expectations (create_full_backup called from transfer_missing) | D4 path removed |
| tests/modules/backup/test_copy.py | Remove test_copy_base_false_prevents_base_copy D4 assertion; add negative test verifying transfer_missing does NOT call create_full_backup | D4 path removed |
| tests/config/test_facade.py | Add config validation tests | New bucket validation requirement |

### Added Risks & Edge Cases
- **[Risk] qemu-img compare on NBD source snapshots may fail with lock error** → `test_risk_hash_compare_live_source_lock_handling` in tests/modules/backup/test_full_verification.py: verifies --force-share is used and lock errors are handled gracefully in M3
- **[Risk] Phantom FULL detection may be slow with many targets** → `test_risk_phantom_full_detection_scales` in tests/core/test_full_verification_pipeline.py: verifies os.path.exists per FULL is O(num_fulls), acceptable even with 10+ FULLs
- **[Risk] State consistency check may block normal pipeline execution** → `test_risk_state_check_is_non_mutating` in tests/core/test_state_check.py: verifies check_state() only reports, never deletes — operator is responsible for cleanup
- **[Risk] IStateManager.remove_full_backup and remove_incremental_dependency are new ABC methods** → Verify that InMemoryStateManager (test mock) and JsonStateManager (production) both implement the new methods
