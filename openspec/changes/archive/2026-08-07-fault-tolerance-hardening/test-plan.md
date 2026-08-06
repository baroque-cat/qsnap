# QA Strategy & Test Plan

Scope: OpenSpec change `fault-tolerance-hardening` (3 tasks: ENOSPC handling + auto-resume, atomic multi-disk quiesced snapshots via `create_multi`, `snapshot_preserve_min` default 0 → 48). All tests follow TESTING.md: mirror production hierarchy, zero real I/O in unit tests (MockShell/MockVMModuleFactory/InMemoryStateManager), result-object assertions (`.success`/`.error`), markers `unit|mock|contract|integration|stress|e2e` with `--strict-markers`, run via `poetry run pytest`.

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| enospc-fault-handling | Space-error classification helper | ENOSPC message classified | tests/utils/test_retry.py | test_is_space_error_no_space_left_on_device | utils-state-unit |
| enospc-fault-handling | Space-error classification helper | Disk quota classified | tests/utils/test_retry.py | test_is_space_error_disk_quota_exceeded | utils-state-unit |
| enospc-fault-handling | Space-error classification helper | Unrelated error not classified | tests/utils/test_retry.py | test_is_space_error_unrelated_and_none | utils-state-unit |
| enospc-fault-handling | Per-target suspension on space errors | Full target suspends only itself | tests/core/test_enospc_isolation.py (NEW) | test_enospc_suspends_only_affected_target | core-pipeline-unit |
| enospc-fault-handling | Per-target suspension on space errors | Retention still runs for the suspended target | tests/core/test_enospc_isolation.py (NEW) | test_enospc_retention_cleanup_still_run_for_suspended_target | core-pipeline-unit |
| enospc-fault-handling | Per-target suspension on space errors | Non-space failure still aborts the VM | tests/core/test_enospc_isolation.py (NEW) | test_non_space_failure_raises_backup_abort | core-pipeline-unit |
| enospc-fault-handling | Per-target suspension on space errors | Verification failure is never a space error | tests/core/test_enospc_isolation.py (NEW) | test_verification_failure_not_treated_as_space_error | core-pipeline-unit |
| enospc-fault-handling | Never-delete-on-ENOSPC invariant | Interrupted FULL leaves only a .tmp file | tests/core/test_enospc_isolation.py (NEW) | test_enospc_leaves_only_tmp_no_deletion | core-pipeline-unit |
| enospc-fault-handling | Never-delete-on-ENOSPC invariant | Space pressure never triggers deletion | tests/core/test_enospc_isolation.py (NEW) | test_space_pressure_never_triggers_deletion | core-pipeline-unit |
| enospc-fault-handling | Auto-resume contract | Next run resumes the interrupted incremental | tests/core/test_enospc_isolation.py (NEW) | test_next_run_resumes_interrupted_incremental | core-pipeline-unit |
| enospc-fault-handling | Auto-resume contract | Next run retries a FULL that never started | tests/core/test_enospc_isolation.py (NEW) | test_next_run_retries_gate_skipped_full | core-pipeline-unit |
| enospc-fault-handling | Proactive free-space gate | Strict gate blocks a doomed FULL | tests/core/test_enospc_isolation.py (NEW) | test_strict_gate_blocks_doomed_full | core-pipeline-unit |
| enospc-fault-handling | Proactive free-space gate | Warn mode proceeds | tests/core/test_enospc_isolation.py (NEW) | test_warn_mode_proceeds | core-pipeline-unit |
| enospc-fault-handling | Proactive free-space gate | Off mode skips the gate | tests/core/test_enospc_isolation.py (NEW) | test_off_mode_skips_gate | core-pipeline-unit |
| enospc-fault-handling | Proactive free-space gate | Undecidable estimate proceeds with warning | tests/utils/test_space.py (NEW) | test_estimate_none_proceeds_with_warning | utils-state-unit |
| enospc-fault-handling | Proactive free-space gate | Reserve and factor applied | tests/utils/test_space.py (NEW) | test_check_free_space_reserve_and_factor | utils-state-unit |
| enospc-fault-handling | Blockcommit space errors deferred with reason enospc | Offline commit hits ENOSPC | tests/core/test_deferred.py | test_offline_commit_enospc_defers_no_runtime_error | core-pipeline-unit |
| enospc-fault-handling | Blockcommit space errors deferred with reason enospc | Deferred enospc entry drained later | tests/core/test_deferred.py | test_deferred_enospc_drained_later | core-pipeline-unit |
| enospc-fault-handling | State write resilience on ENOSPC | ENOSPC in state directory does not crash the process | tests/state/test_manager.py | test_save_oserror_raises_runtime_error_critical | utils-state-unit |
| enospc-fault-handling | State write resilience on ENOSPC | Successful save unaffected | tests/state/test_manager.py | test_save_success_behavior_unchanged | utils-state-unit |
| enospc-fault-handling | Disk-full exit code | Space-limited run exits 4 | tests/cli/test_app.py | test_diskfull_exit_code_four | config-cli-unit |
| enospc-fault-handling | Disk-full exit code | Run without space errors unaffected | tests/cli/test_app.py | test_no_space_error_exits_one | config-cli-unit |
| enospc-fault-handling | Disk-full exit code | Backup abort precedence unchanged when no space error | tests/cli/test_app.py | test_backup_abort_still_exits_ten | config-cli-unit |
| snapshot-provider | Batch multi-disk snapshot creation via create_multi | Two-disk batch created with one virsh call | tests/modules/snapshot/test_external.py | test_create_multi_two_disks_one_virsh_call | snapshot-quiesce-unit |
| snapshot-provider | Batch multi-disk snapshot creation via create_multi | Single-disk degenerate case | tests/modules/snapshot/test_external.py | test_create_multi_single_disk_degenerate | snapshot-quiesce-unit |
| snapshot-provider | Batch multi-disk snapshot creation via create_multi | One file fails validation — whole batch reported failed | tests/modules/snapshot/test_external.py | test_create_multi_one_file_fails_validation_batch_failed | snapshot-quiesce-unit |
| snapshot-provider | Batch multi-disk snapshot creation via create_multi | virsh failure fails the whole batch | tests/modules/snapshot/test_external.py | test_create_multi_virsh_failure_all_failed | snapshot-quiesce-unit |
| snapshot-provider | Batch multi-disk snapshot creation via create_multi | Batch timeout | tests/modules/snapshot/test_external.py | test_create_multi_batch_timeout | snapshot-quiesce-unit |
| snapshot-provider | Batch leftover cleanup on failure | Validation failure removes created files | tests/modules/snapshot/test_external.py | test_create_multi_validation_failure_removes_files | snapshot-quiesce-unit |
| snapshot-provider | Snapshot creation retry on lock conflict | Lock conflict resolved on retry | tests/modules/snapshot/test_external.py | test_create_multi_lock_conflict_resolved_on_retry | snapshot-quiesce-unit |
| snapshot-provider | Snapshot creation retry on lock conflict | Lock conflict exhausted | tests/modules/snapshot/test_external.py | test_create_multi_lock_conflict_exhausted | snapshot-quiesce-unit |
| snapshot-provider | Snapshot creation retry on lock conflict | Non-lock error not retried | tests/modules/snapshot/test_external.py | test_create_multi_no_retry_non_lock_error | snapshot-quiesce-unit |
| snapshot-provider | Snapshot creation retry on lock conflict | Batch lock retry wraps the whole call | tests/modules/snapshot/test_external.py | test_create_multi_lock_retry_wraps_batch | snapshot-quiesce-unit |
| quiesce-snapshot | VMConfig snapshot_quiesce field | Quiesce enabled covers all disks in one freeze | tests/core/test_engine.py | test_core_create_multi_quiesce_all_disks | core-pipeline-unit |
| quiesce-snapshot | VMConfig snapshot_quiesce field | Quiesce disabled (default) | tests/core/test_engine.py | test_core_create_multi_no_quiesce_default | core-pipeline-unit |
| quiesce-snapshot | create_multi accepts quiesce parameter | Batch with quiesce enabled | tests/modules/snapshot/test_external.py | test_create_multi_quiesce_timeout_180s | snapshot-quiesce-unit |
| quiesce-snapshot | create_multi accepts quiesce parameter | Batch without quiesce scales timeout with disk count | tests/modules/snapshot/test_external.py | test_create_multi_non_quiesce_timeout_scales | snapshot-quiesce-unit |
| quiesce-snapshot | Quiesce batch failure is all-or-nothing | Guest agent not installed fails the whole batch | tests/modules/snapshot/test_external.py | test_create_multi_quiesce_guest_agent_missing_all_failed | snapshot-quiesce-unit |
| post-creation-validation | Post-creation snapshot validation | All validation checks pass | tests/modules/snapshot/test_external.py | test_create_multi_all_validation_checks_pass | snapshot-quiesce-unit |
| post-creation-validation | Post-creation snapshot validation | Snapshot file missing despite virsh success | tests/modules/snapshot/test_external.py | test_create_multi_file_missing_fails_batch | snapshot-quiesce-unit |
| post-creation-validation | Post-creation snapshot validation | Wrong backing-filename | tests/modules/snapshot/test_external.py | test_create_multi_wrong_backing_fails_batch | snapshot-quiesce-unit |
| post-creation-validation | Post-creation snapshot validation | Corrupt bit set | tests/modules/snapshot/test_external.py | test_create_multi_corrupt_bit_fails_batch | snapshot-quiesce-unit |
| post-creation-validation | Post-creation snapshot validation | libvirt pivot not confirmed | tests/modules/snapshot/test_external.py | test_create_multi_pivot_not_confirmed | snapshot-quiesce-unit |
| post-creation-validation | Post-creation snapshot validation | Batch — one file fails validation, whole batch rejected | tests/modules/snapshot/test_external.py | test_create_multi_batch_rejected_on_one_bad_file | snapshot-quiesce-unit |
| post-creation-validation | Post-creation snapshot validation | Batch — all files valid, all recorded | tests/core/test_pipeline.py | test_create_multi_all_success_all_recorded | core-pipeline-unit |
| core-orchestrator | Per-disk snapshot creation with configured disk list | VM with multiple disks (vda, vdb) | tests/core/test_pipeline.py | test_create_snapshot_multi_disk_batch_names | core-pipeline-unit |
| core-orchestrator | Per-disk snapshot creation with configured disk list | vdb fails — nothing recorded, VM aborts | tests/core/test_pipeline.py | test_batch_failure_records_nothing_aborts_vm | core-pipeline-unit |
| core-orchestrator | Per-disk snapshot creation with configured disk list | Single-disk VM uses the same batch path | tests/core/test_pipeline.py | test_single_disk_vm_uses_batch_path | core-pipeline-unit |
| core-orchestrator | Per-disk snapshot creation with configured disk list | onchange gate is VM-wide, snapshots cover all disks | tests/core/test_pipeline.py | test_onchange_gate_vm_wide_snapshots_all_disks | core-pipeline-unit |
| core-orchestrator | Backup target pipeline with gate/retention separation | Gate skip does not block retention | tests/core/test_pipeline.py | test_gate_skip_retention_still_runs (MODIFIED, exists at line 4778) | core-pipeline-unit |
| core-orchestrator | Backup target pipeline with gate/retention separation | Suspended target still runs retention and cleanup | tests/core/test_enospc_isolation.py (NEW) | test_suspended_target_still_runs_retention_cleanup | core-pipeline-unit |
| core-orchestrator | VM-level failure isolation | Disk failure aborts remaining steps of the VM | tests/core/test_pipeline.py | test_vdb_failure_aborts_remaining_steps | core-pipeline-unit |
| core-orchestrator | VM-level failure isolation | Other VMs continue after a VM aborts | tests/core/test_pipeline.py | test_error_isolation_between_vms (kept, extended) | core-pipeline-unit |
| core-orchestrator | VM-level failure isolation | MAC denial does not abort the VM | tests/core/test_deferred.py | test_mac_denial_defers_apparmor_selinux (kept) | core-pipeline-unit |
| core-orchestrator | VM-level failure isolation | Space error suspends one target, VM continues | tests/core/test_enospc_isolation.py (NEW) | test_space_error_suspends_target_vm_continues | core-pipeline-unit |
| core-orchestrator | BackupAbortError marks backup-stage failures | Backup abort sets backup_failed | tests/core/test_pipeline.py | test_backup_abort_sets_backup_failed (kept/extended) | core-pipeline-unit |
| core-orchestrator | BackupAbortError marks backup-stage failures | Space failure does not raise BackupAbortError | tests/core/test_enospc_isolation.py (NEW) | test_space_failure_no_backup_abort_error | core-pipeline-unit |
| core-orchestrator | Space-limited flag wired into PipelineResult | Space-limited run flagged | tests/core/test_engine.py | test_pipeline_result_space_limited_true | core-pipeline-unit |
| core-orchestrator | Space-limited flag wired into PipelineResult | Clean run not flagged | tests/core/test_engine.py | test_pipeline_result_space_limited_false | core-pipeline-unit |
| core-orchestrator | Space-limited flag wired into PipelineResult | Dry-run never flagged | tests/core/test_dry_run_prediction.py | test_dry_run_space_limited_false | core-pipeline-unit |
| core-orchestrator | Proactive free-space gate integrated into backup steps | Strict gate rejection suspends target without transfer | tests/core/test_enospc_isolation.py (NEW) | test_strict_gate_no_transfer_attempted | core-pipeline-unit |
| core-orchestrator | Proactive free-space gate integrated into backup steps | Dry-run predicts the gate | tests/core/test_dry_run_prediction.py | test_dry_run_predicts_gate_entry | core-pipeline-unit |
| state-recovery | State write survives ENOSPC without crashing the process | ENOSPC during save contained to one VM | tests/state/test_manager.py | test_save_oserror_contained_per_vm | utils-state-unit |
| state-recovery | State write survives ENOSPC without crashing the process | Partial temp file does not corrupt existing state | tests/state/test_manager.py | test_save_partial_tmp_does_not_corrupt_state | utils-state-unit |
| state-recovery | State write survives ENOSPC without crashing the process | Successful save behavior unchanged | tests/state/test_manager.py | test_atomic_write_pattern (MODIFIED, line 91) | utils-state-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Add and retrieve per-disk deferred blockcommit | tests/state/test_manager.py | test_add_and_retrieve_deferred_blockcommit (kept, line 142) | utils-state-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Multiple disks can have separate deferred entries | tests/state/test_manager.py | test_add_deferred_operations_multiple_disks (kept) | utils-state-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Add deferred blockcommit with vm_running reason | tests/state/test_manager.py | test_add_deferred_blockcommit_vm_running_reason (kept, line 160) | utils-state-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Add deferred blockcommit with active_layer reason | tests/state/test_manager.py | test_add_deferred_blockcommit_active_layer_reason (kept, line 178) | utils-state-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Add deferred blockcommit with enospc reason | tests/state/test_manager.py | test_add_deferred_blockcommit_enospc_reason (NEW) | utils-state-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Clear deferred operations | tests/state/test_manager.py | test_clear_deferred_operations (kept, line 212) | utils-state-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | No deferred operations for VM | tests/state/test_manager.py | test_no_deferred_operations_empty_list (kept, line 226) | utils-state-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | last_warned_at persists across state round-trip | tests/state/test_manager.py | test_state_round_trips_last_warned_at (kept, line 279) | utils-state-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Old state file without last_warned_at is backward-compatible | tests/state/test_manager.py | test_old_state_file_backward_compatible (kept, line 297) | utils-state-unit |
| deferred-operations | Blockcommit space errors deferred instead of aborting | Offline commit ENOSPC defers and continues | tests/core/test_deferred.py | test_offline_commit_enospc_defers_continues (NEW) | core-pipeline-unit |
| deferred-operations | Blockcommit space errors deferred instead of aborting | Live commit ENOSPC defers and continues | tests/core/test_deferred.py | test_live_commit_enospc_defers (NEW) | core-pipeline-unit |
| deferred-operations | Blockcommit space errors deferred instead of aborting | Deferred enospc entry appears in monitoring | tests/core/test_deferred.py | test_enospc_deferred_threshold_monitoring (NEW) | core-pipeline-unit |
| deferred-operations | Blockcommit space errors deferred instead of aborting | Non-space commit failure still aborts | tests/core/test_deferred.py | test_non_space_commit_failure_aborts (NEW) | core-pipeline-unit |
| cli-interface | Exit codes | Success exit code | tests/cli/test_app.py | test_success_returns_exit_code_zero (kept, line 115) | config-cli-unit |
| cli-interface | Exit codes | Lockfile error exit code | tests/cli/test_app.py | test_lockfile_held_returns_exit_code_three (kept, line 137) | config-cli-unit |
| cli-interface | Exit codes | Disk-full exit code | tests/cli/test_app.py | test_diskfull_run_exit_code_four (NEW) | config-cli-unit |
| cli-interface | Exit codes | Disk-full precedence over generic failure | tests/cli/test_app.py | test_diskfull_precedence_over_generic (NEW) | config-cli-unit |
| cli-interface | Exit codes | Non-space backup abort still exits 10 | tests/cli/test_app.py | test_non_space_backup_abort_exits_ten (NEW) | config-cli-unit |
| config-model | GlobalConfig default values | GlobalConfig default values | tests/config/test_model.py | test_global_chain_length_defaults_are_sensible (MODIFIED, line 158) | config-cli-unit |
| config-model | GlobalConfig snapshot_preserve_min field | GlobalConfig default snapshot_preserve_min is 48 | tests/config/test_model.py | test_global_config_snapshot_preserve_min_default (MODIFIED, line 168) | config-cli-unit |
| config-model | GlobalConfig snapshot_preserve_min field | Explicit zero disables the floor | tests/config/test_model.py | test_global_config_preserve_min_zero_disables (NEW) | config-cli-unit |
| config-model | GlobalConfig free-space gate fields | Defaults | tests/config/test_model.py | test_global_config_free_space_defaults (NEW) | config-cli-unit |
| config-model | GlobalConfig free-space gate fields | Explicit override | tests/config/test_model.py | test_global_config_free_space_override (NEW) | config-cli-unit |
| config-model | GlobalConfig free-space gate fields | VM inherits free_space_check from global | tests/config/test_resolver.py | test_vm_inherits_free_space_check_from_global (NEW) | config-cli-unit |
| config-parsing | ConfigFacade parses and validates free-space gate fields | Valid free-space fields parsed | tests/config/test_facade.py | test_free_space_fields_parsed (NEW) | config-cli-unit |
| config-parsing | ConfigFacade parses and validates free-space gate fields | Invalid free_space_check raises ConfigError | tests/config/test_facade.py | test_invalid_free_space_check_raises_config_error (NEW) | config-cli-unit |
| config-parsing | ConfigFacade parses and validates free-space gate fields | Negative free_space_reserve raises ConfigError | tests/config/test_facade.py | test_negative_free_space_reserve_raises_config_error (NEW) | config-cli-unit |
| config-parsing | ConfigFacade parses and validates free-space gate fields | free_space_factor below 1.0 raises ConfigError | tests/config/test_facade.py | test_free_space_factor_below_one_raises_config_error (NEW) | config-cli-unit |
| config-parsing | ConfigFacade parses and validates free-space gate fields | Absent fields use defaults | tests/config/test_facade.py | test_free_space_fields_absent_use_defaults (NEW) | config-cli-unit |
| config-parsing | ConfigFacade parses and validates free-space gate fields | snapshot_preserve_min default resolves to 48 | tests/config/test_facade.py | test_preserve_min_default_resolves_to_48 (NEW) | config-cli-unit |
| config-parsing | ConfigFacade parses and validates free-space gate fields | Explicit zero preserve_min still honored | tests/config/test_resolver.py | test_vm_sets_snapshot_preserve_min_to_zero (kept, line 304) | config-cli-unit |
| snapshot-preserve-min | Per-disk snapshot preserve_min post-processing filter | preserve_min inactive when explicitly zero | tests/core/test_preserve.py | test_preserve_min_inactive_explicit_zero (RENAMED from test_preserve_min_inactive_default, line 332) | core-pipeline-unit |
| snapshot-preserve-min | Per-disk snapshot preserve_min post-processing filter | default preserve_min 48 keeps newest 48 | tests/core/test_preserve.py | test_default_preserve_min_48_keeps_newest_48 (NEW) | core-pipeline-unit |
| snapshot-preserve-min | Per-disk snapshot preserve_min post-processing filter | default floor dominates chain_length | tests/core/test_preserve.py | test_default_floor_dominates_chain_length (NEW) | core-pipeline-unit |
| snapshot-preserve-min | Per-disk snapshot preserve_min post-processing filter | preserve_min preserves newest snapshots of a disk | tests/core/test_preserve.py | test_preserve_min_trim_excess_from_newest (kept, line 375) | core-pipeline-unit |
| snapshot-preserve-min | Per-disk snapshot preserve_min post-processing filter | preserve_min does not trigger when remove is small | tests/core/test_preserve.py | test_preserve_min_no_trim_when_within_limit (kept, line 423) | core-pipeline-unit |
| snapshot-preserve-min | Per-disk snapshot preserve_min post-processing filter | preserve_min equals total snapshots for a disk | tests/core/test_preserve.py | test_preserve_min_equals_total_no_blockcommit (kept, line 466) | core-pipeline-unit |
| snapshot-preserve-min | Per-disk snapshot preserve_min post-processing filter | preserve_min greater than total snapshots | tests/core/test_preserve.py | test_preserve_min_exceeds_total_no_blockcommit (kept, line 509) | core-pipeline-unit |
| snapshot-preserve-min | Per-disk snapshot preserve_min post-processing filter | preserve_min applied after oldest-prefix within a single disk | tests/core/test_preserve.py | test_preserve_min_applied_after_oldest_prefix (kept, line 551) | core-pipeline-unit |
| snapshot-preserve-min | Per-disk snapshot preserve_min post-processing filter | Each disk applies preserve_min independently | tests/core/test_preserve.py | test_multidisk_preserve_min_independent (kept, line 709) | core-pipeline-unit |

## Delegation Groups

Six non-overlapping groups; every test file belongs to exactly one group.

### Group: utils-state-unit

**Scope:** Pure helpers (`is_space_error`, free-space estimation/gate) and JsonStateManager ENOSPC resilience + deferred-queue persistence. Zero I/O; `MockShell` only where `IShell` is required (`estimate_full_size`/`estimate_incremental_size` use mocked `qemu-img info`).

| Test File | Scenarios | Action |
|---|---|---|
| tests/utils/test_retry.py | 3 | MODIFY (add `is_space_error` tests next to `is_retryable`) |
| tests/utils/test_space.py (NEW) | 2 | NEW (`estimate_full_size`, `estimate_incremental_size`, `check_free_space` + happy/error paths) |
| tests/state/test_manager.py | 14 | MODIFY (`_save` OSError→RuntimeError tests; enospc reason round-trip) |

### Group: snapshot-quiesce-unit

**Scope:** `ExternalSnapshotProvider.create_multi` (single virsh batch, lock retry, timeouts, per-file validation, one domblklist pivot, best-effort cleanup) and `SnapshotSpec` model. All via `MockShell` with canned outputs.

| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/snapshot/test_external.py | 19 | MODIFY (add `create_multi` suite; keep single-disk `create()` tests) |
| tests/models/test_results.py | 0 (SnapshotSpec dataclass: frozen, fields disk/name/path) | MODIFY |

### Group: core-pipeline-unit

**Scope:** Core orchestration — batch snapshot step (all-or-nothing), per-target ENOSPC isolation, space_limited flag, free-space gate wiring, blockcommit enospc deferral, dry-run gate prediction, preserve_min floor. `MockVMModuleFactory` + `InMemoryStateManager` + `MockShell`.

| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_enospc_isolation.py (NEW) | 15 | NEW (per-target isolation suite) |
| tests/core/test_pipeline.py | 9 | MODIFY (25 `create`→`create_multi` patch sites incl. test_validation.py:3, test_engine.py:4; batch-recording tests) |
| tests/core/test_engine.py | 4 | MODIFY (quiesce/create_multi spies; space_limited flag) |
| tests/core/test_dry_run_prediction.py | 2 | MODIFY (dry-run gate prediction; space_limited=False) |
| tests/core/test_deferred.py | 7 | MODIFY (enospc deferral + monitoring; keep existing reasons) |
| tests/core/test_preserve.py | 9 | MODIFY (default-48 floor scenarios; pin explicit 0 where old semantics intended) |

### Group: config-cli-unit

**Scope:** GlobalConfig model defaults + ConfigFacade parsing/validation of free-space fields and preserve_min inheritance; CLI exit-code mapping incl. new `EXIT_DISKFULL=4`; summary naming of space-limited targets. Fixture TOMLs and `qsnap.toml.example` updates.

| Test File | Scenarios | Action |
|---|---|---|
| tests/config/test_model.py | 5 | MODIFY (defaults 0→48; free-space fields) |
| tests/config/test_facade.py | 6 | MODIFY (free-space parse/validate; preserve_min 48) |
| tests/config/test_resolver.py | 2 | MODIFY (free_space inheritance; explicit-zero override) |
| tests/config/test_fixtures.py | 0 (assertions at lines 84–89, 129, 143 updated) | MODIFY |
| tests/cli/test_app.py | 8 | MODIFY (exit 4 + precedence; help epilog) |
| tests/cli/test_commands.py | 0 (exit-code mapping in `_format_pipeline_result` gets `space_limited` branch) | MODIFY |
| tests/cli/test_summary.py | 0 (summary names space-limited target) | MODIFY |
| tests/fixtures/configs/*.toml | 0 (new `free_space_fields.toml`, `preserve_min_default.toml`; update `global_fields.toml`) | MODIFY |
| qsnap.toml.example | 0 (preserve_min default 48 + free-space docs) | MODIFY |

### Group: mocks-contracts

**Scope:** `MockSnapshotProvider.create_multi`; mock validity/factory tests; contract parametrization over all `ISnapshotProvider` implementations; IStateManager contract for enospc reason.

| Test File | Scenarios | Action |
|---|---|---|
| tests/mocks/mock_modules.py | 0 (add `create_multi` returning list[SnapshotResult] in spec order) | MODIFY |
| tests/mocks/mock_config.py | 0 (add free-space defaults to MockConfigFacade/GlobalConfig) | MODIFY |
| tests/mocks/test_mock_factory.py | 0 | MODIFY (add create_multi mock tests) |
| tests/mocks/test_mock_validity.py | 0 | MODIFY |
| tests/interfaces/test_snapshot_provider.py | 0 | MODIFY (parametrize `create_multi` contract over External+Mock) |
| tests/interfaces/test_state_manager.py | 0 | MODIFY (contract: `add_deferred_blockcommit` accepts `enospc`) |

### Group: integration-stress-e2e

**Scope:** Real-virsh verification of the new behavior: quiesced 2-disk batch snapshots, default-48 floor with real blockcommit, ENOSPC self-heal (no deletion, `.tmp` only, exit 4, other target continues, auto-resume), gate-before-transfer, e2e from config. Uses `test_vm`, `test_vm_multi_disk`, `stress_env`, `e2e_vm` fixtures.

| Test File | Scenarios | Action |
|---|---|---|
| tests/integration/test_multi_disk.py | 0 | MODIFY (add quiesced batch snapshot via Core on real 2-disk VM) |
| tests/integration/test_preserve_min.py | 0 | MODIFY (default-48 real blockcommit case) |
| tests/integration/test_full_backup.py, test_incremental_backup.py | 0 | MODIFY (free-space gate runs before real transfer) |
| tests/integration/test_blockcommit_defer.py | 0 | MODIFY (enospc deferral + drain with real commit) |
| tests/integration/test_infrastructure.py | 0 | MODIFY (stale-state self-healing still passes with `_save` RuntimeError path; `test_stale_state_self_healing` at line 307) |
| tests/integration/test_verify_before_delete.py | 0 | MODIFY (verification failures still abort; space errors don't) |
| tests/stress/test_long_chain.py | 0 | MODIFY (50+ chain with default 48 floor — commit only beyond 48) |
| tests/stress/test_enospc.py (NEW) | 0 | NEW (disk-full stress scenario) |
| tests/e2e/test_from_config.py | 0 | MODIFY (default 48 → 2 runs, no commit under 48) |
| tests/e2e/test_restore.py | 0 | MODIFY (restore still succeeds with floor active) |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| tests/utils/test_retry.py | Add `test_is_space_error_*` (3 scenarios: ENOSPC, EDQUOT, unrelated/None) | enospc-fault-handling "Space-error classification helper"; design D1 |
| tests/utils/test_space.py (NEW) | Unit tests for `estimate_full_size` (sum actual-size over backing chain, None when undecidable), `estimate_incremental_size` (active-layer actual-size), `check_free_space` (free >= est×factor+reserve); mocked `qemu-img info` + patched `shutil.disk_usage` | enospc-fault-handling "Proactive free-space gate" scenarios 15–16; design D5 |
| tests/state/test_manager.py | 1) `test_atomic_write_pattern` (line 121), `test_json_clear_last_backup_allocation_atomic` (line 1314), and the 2 `reset_*` crash tests (lines 1745, 1917): change `pytest.raises(OSError)` → `pytest.raises(RuntimeError)` + assert CRITICAL log names path/errno — wherever the path routes through the wrapped save; keep OSError expectation only if `_save_target_state` is left unmodified (verify implementer scope). 2) Add `test_add_deferred_blockcommit_enospc_reason`, `test_save_oserror_raises_runtime_error_critical`, `test_save_oserror_contained_per_vm`, `test_save_partial_tmp_does_not_corrupt_state` | state-recovery scenarios 1–3; enospc scenarios 19–20; deferred-operations "enospc reason" |
| tests/modules/snapshot/test_external.py | Add `create_multi` suite (19 scenario tests): single-batch command shape (`--diskspec` ×N, `--disk-only --atomic --no-metadata [--quiesce]`), per-file validation reuse, ONE domblklist, lock retry wrapping whole call, timeouts (180s quiesce / `120+30×(N−1)`), best-effort `rm -f` on failure, result list in spec order | snapshot-provider (10), quiesce-snapshot (3), post-creation-validation (6) scenarios; design D9/D11 |
| tests/models/test_results.py | Add `SnapshotSpec` frozen-dataclass tests (fields `disk`, `name`, `path`; immutability) | snapshot-provider requirement; design D8 |
| tests/core/test_pipeline.py | 1) Replace all `patch.object(snapshot_provider, "create"...)` (18 sites) with `create_multi` spies/side-effects; update call-count assertions (per-VM = 1, not per-disk). 2) Replace partial-recording assertions with all-or-nothing (new `test_batch_failure_records_nothing_aborts_vm`). 3) Add multi-disk name/path generation + per-disk `SnapshotInfo(disk=target)` assertions | core-orchestrator scenarios 1–3, 7; post-creation-validation scenario 7; design D10 |
| tests/core/test_engine.py | Replace `test_core_passes_quiesce_*` (lines 445/484, patch `create` kwarg) with `create_multi` spies asserting ONE call with `quiesce=vm_config.snapshot_quiesce` and both disk specs; add `test_pipeline_result_space_limited_{true,false}` | quiesce-snapshot scenarios 1–2; core-orchestrator scenarios 13–14 |
| tests/core/test_enospc_isolation.py (NEW) | Full per-target isolation suite: space error suspends only target A (target B continues, no `BackupAbortError`), retention+cleanup still run (spies), non-space still aborts, verification failure never classified as space, never-delete invariant, `.tmp`-only leftover, auto-resume (run N+1 reuses same checkpoint), strict-gate suspension without transfer, warn/off modes | enospc-fault-handling scenarios 4–14; core-orchestrator scenarios 6, 10, 12, 16; design D2/D7 |
| tests/core/test_dry_run_prediction.py | Add `test_dry_run_predicts_gate_entry` (prediction record naming target + estimate, no transfer/suspension/mutation) and `test_dry_run_space_limited_false` | core-orchestrator scenarios 15, 17; design D12 (simulation stays per-disk) |
| tests/core/test_deferred.py | Add `test_offline_commit_enospc_defers_no_runtime_error`, `test_live_commit_enospc_defers`, `test_deferred_enospc_drained_later`, `test_enospc_deferred_threshold_monitoring`, `test_non_space_commit_failure_aborts`; keep MAC-denial tests | enospc scenarios 17–18; deferred-operations scenarios 10–13; design D4 |
| tests/core/test_preserve.py | Add `test_default_preserve_min_48_keeps_newest_48` (100 snaps, chain_length=24 → keep 48/remove 52) and `test_default_floor_dominates_chain_length` (30 snaps → remove 0); rename `test_preserve_min_inactive_default` → explicit-0 semantics; pin `snapshot_preserve_min=0` in any test that hand-builds VMs expecting old floor | snapshot-preserve-min scenarios 2–3; design D13/D14 |
| tests/config/test_model.py | `test_global_chain_length_defaults_are_sensible` (line 158) and `test_global_config_snapshot_preserve_min_default` (line 168): assert 48; add free-space default/override/immutability tests | config-model scenarios 1–5 |
| tests/config/test_facade.py | Add free-space parse/validate tests (valid, invalid enum naming strict/warn/off, negative reserve, factor<1.0, absent defaults) + preserve_min resolves to 48 through inheritance | config-parsing scenarios 1–6 |
| tests/config/test_resolver.py | Add `test_vm_inherits_free_space_check_from_global`; keep explicit-zero preserve_min test | config-model scenario 6; config-parsing scenario 7 |
| tests/config/test_fixtures.py | Update `make_global_config` docstring (fixture pins 0 explicitly; no longer "GlobalConfig default"); update `test_example_config_parseable` (lines 129/143) to expect 48 after `qsnap.toml.example` update | default-change blast radius; config-model scenario 1 |
| tests/fixtures/configs/ | New `free_space_fields.toml` (valid values), `invalid_free_space_check.toml`, `negative_free_space_reserve.toml`, `low_free_space_factor.toml`, `preserve_min_default.toml` (omits preserve_min → 48); extend `global_fields.toml` with free-space fields | config-parsing scenarios 1–6 |
| tests/cli/test_app.py | Add exit-4 tests (`space_limited=True` → 4; `success=False + space_limited=True` → 4 not 1; `backup_failed + space_limited=True` → 4; no-space failure → 1; lockfile/parse precedence over 4); assert `--help` epilog documents exit 4 | cli-interface scenarios 3–5; enospc scenarios 21–23; design D6 |
| tests/cli/test_commands.py | Extend `_format_pipeline_result`-adjacent tests with `PipelineResult(space_limited=True)` mapping; assert summary names the space-limited target | cli-interface scenario 3 |
| tests/cli/test_summary.py | Assert summary output names space-limited targets when `space_limited=True` | cli-interface scenario 3 |
| tests/mocks/mock_modules.py | Add `MockSnapshotProvider.create_multi(vm_config, specs, quiesce)` → one `SnapshotResult(success=True, ...)` per spec in order; allow failure injection for Core tests | snapshot-provider requirement; design D8 |
| tests/mocks/mock_config.py | Ensure `MockConfigFacade` global carries new free-space defaults (48 preserve_min, strict/0/1.0) | config-model scenario 1 |
| tests/mocks/test_mock_factory.py | Add `test_mock_snapshot_provider_has_create_multi` (returns list of SnapshotResult, isinstance ISnapshotProvider, no Core inheritance) | mocks rules in TESTING.md §2 |
| tests/mocks/test_mock_validity.py | Add create_multi validity check (never returns None; list length == len(specs)) | TESTING.md §2 |
| tests/interfaces/test_snapshot_provider.py | Parametrize `create_multi` contract over `[ExternalSnapshotProvider, MockSnapshotProvider]`: returns `list[SnapshotResult]`, one result per spec, `isinstance(result.success, bool)` | contract rules TESTING.md §3 |
| tests/interfaces/test_state_manager.py | Contract: `add_deferred_blockcommit` accepts `reason="enospc"` and round-trips it | deferred-operations requirement |
| tests/integration/test_multi_disk.py | Add `test_quiesced_batch_snapshot_multi_disk` (real VM): run Core._create_snapshot on 2-disk VM with `snapshot_quiesce=True`; assert exactly one `virsh snapshot-create-as` with 2 `--diskspec` + `--atomic --quiesce`, both files exist, both recorded | quiesce-snapshot scenario 1; design D9; risk: libvirt version variance |
| tests/integration/test_preserve_min.py | Add default-48 case: 30 snapshots, default chain_length=24, NO explicit preserve_min → `core.prune()` performs zero blockcommits; then explicit `snapshot_preserve_min=0` → old behavior | snapshot-preserve-min scenarios 2–3; design D13 |
| tests/integration/test_full_backup.py / test_incremental_backup.py | Add gate-before-transfer check: with `free_space_check="strict"` and a tight `free_space_reserve`, assert no `qemu-img convert` starts; with `"warn"`, transfer proceeds after WARNING | enospc scenarios 12–13 |
| tests/integration/test_blockcommit_defer.py | Add real offline-commit ENOSPC deferral (fill snapshot dir, run prune) → deferred entry reason `enospc`, no VM abort; after freeing space, next prune drains and commits | enospc scenarios 17–18; deferred-operations 10–11 |
| tests/integration/test_infrastructure.py | Re-run `test_stale_state_self_healing` (line 307) against new `_save` RuntimeError path; add per-VM containment check (vm1 state-write ENOSPC, vm2 still processed) | state-recovery scenario 1 |
| tests/integration/test_verify_before_delete.py | Add explicit assertion that verification-failure abort path is untouched by isolation (old generation NOT deleted) | enospc scenario 7; risk: verify-before-delete weakening |
| tests/stress/test_long_chain.py | Run 50+ snapshots with default preserve_min=48: assert commits start only at snapshot 49+; chain intact | snapshot-preserve-min scenario 3 |
| tests/stress/test_enospc.py (NEW) | Disk-full stress: small loopback/ext4 or filled target dir via fallocate; run backup; assert no deletion, only `.tmp` leftover, exit code 4, other target continues, next run after freeing space auto-resumes and passes verification | enospc scenarios 4, 8–11; TESTING.md §5 "Disk-full scenarios" |
| tests/e2e/test_from_config.py | Run full pipeline twice with default config; assert snapshots exist, no commit under 48, backups on target; run `qsnap check` | e2e rules TESTING.md §6 |
| tests/e2e/test_restore.py | Restore from backup with floor active; boot restored VM | e2e rules |

## Risks & Edge Cases

Risks from design.md "Risks / Trade-offs", each mapped to dedicated coverage:

| Risk | Dedicated Test Coverage |
|---|---|
| libvirt version variance for multi `--diskspec` + `--atomic` + `--quiesce` | tests/integration/test_multi_disk.py `test_quiesced_batch_snapshot_multi_disk` (real 2-disk VM, real libvirt); env-validation error-message test in tests/integration/test_env_validation.py (assert guidance message for unsupported libvirt); unit shape assertions in test_external.py |
| Size estimation inaccuracy (compression, sparse allocation) | tests/utils/test_space.py reserve/factor unit tests (scenario 16); warn-mode proceed test (scenario 13); undecidable-estimate proceeds-with-WARNING test (scenario 15); `free_space_factor`/`free_space_reserve` config validation tests (config-parsing 2–4) |
| Default snapshot-dir usage roughly doubles (24 → 48 kept) | config-model default 48 tests; tests/core/test_preserve.py default-floor tests; integration default-48 no-commit test; `qsnap.toml.example` doc + test_fixtures.py example-parseable test (opt-out line `snapshot_preserve_min = 0` verified in resolver explicit-zero test) |
| Per-target isolation accidentally weakening verify-before-delete | tests/core/test_enospc_isolation.py `test_verification_failure_not_treated_as_space_error` (scenario 7) + `test_non_space_failure_raises_backup_abort` (scenario 6); tests/integration/test_verify_before_delete.py explicit no-weakening assertion; BackupAbortError `backup_failed=True` mapping test (core-orchestrator scenario 11) |
| Exit code 4 surprises monitoring | cli-interface scenario tests (3–5) incl. precedence matrix; `--help` epilog test in test_app.py; test_commands.py mapping tests for 4 vs 1 vs 10 |
| Test-suite churn from the default change | This plan's blast-radius modifications (test_model.py, test_fixtures.py, test_preserve.py, conftest `make_global_config` pinning, fixture TOMLs); explicit pin `snapshot_preserve_min=0` guidance in group 3 |
| `--atomic` rollback may still leave files on some libvirt versions | test_external.py `test_create_multi_validation_failure_removes_files` (best-effort `rm -f`); pre-flight orphan cleanup integration coverage in test_validation.py + test_infrastructure.py |
| Auto-resume depends on success-only advancement (contract, no machinery) | tests/core/test_enospc_isolation.py `test_next_run_resumes_interrupted_incremental` (same checkpoint reused) + `test_next_run_retries_gate_skipped_full`; stress test_enospc.py run-N+1 completion |
| Timeout formula `120 + 30×(N−1)` unvalidated on 3+ disks | unit: `test_create_multi_non_quiesce_timeout_scales` (N=3 → 180s); integration: 2-disk real VM validates N=2 → 150s; open-question note for 3+ disk environments |

## Tests To Delete

| Test | Reason |
|---|---|
| tests/core/test_pipeline.py::`test_snapshot_creation_failure_does_not_record_state` (line 7684) | Asserts partial recording ("vda recorded in state — vdb NOT"); obsolete under all-or-nothing batch semantics (core-orchestrator scenario "vdb fails — nothing recorded, VM aborts", design D10). Replaced by new `test_batch_failure_records_nothing_aborts_vm`. |
| tests/core/test_engine.py::`test_core_passes_quiesce_true_to_snapshot_provider` (line 445) | Patches per-disk `create()` and asserts `quiesce` kwarg; Core now issues ONE `create_multi(vm_config, specs, quiesce)` call with `quiesce=vm_config.snapshot_quiesce` (quiesce-snapshot scenarios 1–2). Replaced by `test_core_create_multi_quiesce_all_disks`. |
| tests/core/test_engine.py::`test_core_passes_quiesce_false_to_snapshot_provider` (line 484) | Same obsolete `create()`-kwarg assertion for `quiesce=False`. Replaced by `test_core_create_multi_no_quiesce_default`. |
| tests/config/test_model.py::`test_global_chain_length_defaults_are_sensible` (line 158) | Asserts `GlobalConfig().snapshot_preserve_min == 0`; default is now 48 (config-model scenario 1). Rewritten, not kept. |
| tests/config/test_model.py::`test_global_config_snapshot_preserve_min_default` (line 168) | Asserts default 0; obsolete (config-model scenario 2). Rewritten to 48. |
| tests/core/test_preserve.py::`test_preserve_min_inactive_default` (line 332) | Name/docstring encode "default is inactive"; with default 48 the "inactive" case only exists for explicit 0. Kept as logic but renamed `test_preserve_min_inactive_explicit_zero` (it already passes `snapshot_preserve_min=0` explicitly — semantics preserved, wording fixed). |
| (Audit item) Any test in tests/core/test_pipeline.py / test_engine.py / test_validation.py asserting per-disk sequential `create()` call counts (25 patch sites) | All patch/wrap `snapshot_provider.create`, which Core stops calling; each is converted to `create_multi` spy/side-effect (MODIFY, not delete) — no test asserting "N create calls for N disks" remains valid. |
| (Audit item) Any fixture/assertion treating `GlobalConfig()` default preserve_min as 0: conftest `make_global_config` param default (line 286) | Fixture intentionally pins `0`; docstring in test_fixtures.py:84 updated to say "explicitly pinned 0". GlobalConfig() itself now defaults 48 — assertions that rely on the un-pinned default are rewritten in test_model.py. |

## Integration/Stress/E2E Test Updates

**tests/integration/test_multi_disk.py**
- NEW `test_quiesced_batch_snapshot_multi_disk` (`@pytest.mark.integration`, `test_vm_multi_disk`): start the 2-disk VM, run `Core._create_snapshot` with `snapshot_quiesce=True`; assert exactly one `virsh snapshot-create-as` containing `--diskspec vda,file=...` and `--diskspec vdb,file=...` plus `--disk-only --atomic --no-metadata --quiesce`; both `.qcow2` files exist in their per-disk dirs; both `SnapshotInfo` recorded with `disk="vda"`/`disk="vdb"`; a single `domblklist` shows both pivots.
- NEW `test_batch_snapshot_no_quiesce_default`: same without `snapshot_quiesce`; assert no `--quiesce` flag and timeout 150s (N=2 → `120+30×1`).
- Keep existing per-disk isolation/restore tests (they exercise `snapshot_create` helper → single-disk `create()`, still supported).

**tests/integration/test_preserve_min.py**
- NEW `test_default_preserve_min_48_real_blockcommit`: create 30 real snapshots via Core on `test_vm` with default config (no explicit preserve_min; chain_length default 24); `core.prune()` → zero blockcommits (floor dominates); then flip `snapshot_preserve_min=0` in config → commits resume. Verifies "default floor dominates chain_length" and the opt-out path on real libvirt.

**tests/integration/test_full_backup.py / test_incremental_backup.py**
- NEW gate-before-transfer assertions: with `free_space_check="strict"` and a reserve sized above free space, assert no `backup-begin`/`qemu-img convert` command executes and the target is suspended; with `"warn"` the transfer runs and a WARNING is logged naming target/estimate/free.

**tests/integration/test_blockcommit_defer.py**
- NEW `test_offline_commit_enospc_defers_integration`: fill the snapshot filesystem (small loopback or tight `free_space_reserve`); run prune → offline `qemu-img commit` fails with ENOSPC → deferred entry reason `enospc`, snapshot records intact, no VM abort; free space; next prune drains and commits.

**tests/integration/test_verify_before_delete.py**
- Add assertion: a verification (M2) failure still raises `BackupAbortError` and old generations are NOT deleted even when the error text is unusual — pins verify-before-delete against the new isolation path (enospc scenario 7).

**tests/integration/test_infrastructure.py**
- `test_stale_state_self_healing` (line 307) updated to the new `_save` contract: an `OSError` during save now surfaces as `RuntimeError` with CRITICAL log; add per-VM containment check (vm1 state write fails, vm2 completes).

**tests/stress/**
- NEW `tests/stress/test_enospc.py` (`@pytest.mark.stress`, `stress_env`):
  1. Create a small target filesystem (loopback ext4, e.g. 64M) or simulate via `fallocate` filling `target_dir`.
  2. Run a FULL backup → ENOSPC mid-transfer: assert NO snapshot/backup/checkpoint/state deletion, only `<target>.qcow2.tmp` remains, run result `space_limited=True`, CLI exit code 4, and a second target on different storage completed normally.
  3. Free space, run again → incremental resumes from the same prior checkpoint, passes verification, checkpoint rotates and state records (auto-resume contract).
- `test_long_chain.py`: extend to run 50+ snapshots with default preserve_min=48 and assert blockcommit only trims beyond the newest 48 (floor dominates chain_length under load).

**tests/e2e/**
- `test_from_config.py`: run `qsnap run` twice from a TOML with NO explicit preserve_min; assert snapshots exist, no blockcommit occurred (fewer than 48), backups on target, second run performs no duplicate work, `qsnap check` passes; then assert exit code 0 (no space errors).
- `test_restore.py`: restore path unchanged in behavior; assert restore still succeeds and boots with the floor active (snapshots preserved).

**Conftest updates (integration/stress/e2e):** no fixture signature changes required — new tests reuse `test_vm`, `test_vm_multi_disk`, `stress_env`, `e2e_vm`. `stress_env` docstring already promises "Disk-full scenarios"; TESTING.md §5 rule now satisfied by `test_enospc.py`.

**Note on file names:** `TESTING.md` references `test_nbd_full_backup.py` and `test_stale_state_recovery.py`, but the current tree contains `test_full_backup.py`/`test_incremental_backup.py` (NBD pull-model coverage) and `test_infrastructure.py::test_stale_state_self_healing`. Integration updates above target the files that actually exist; if the naming is reconciled during implementation, map the updates to the renamed files.
