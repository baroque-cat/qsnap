# QA Strategy & Test Plan

## Coverage Map

Every `#### Scenario:` from every spec file in this change is mapped below to a concrete test file, test function name, and delegation group. Duplicate scenarios (same behavior described in multiple specs, e.g., config-model restates defaults from feature specs) are listed individually but point to the same test function — the cross-reference is noted in the Test Name column.

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| state-recovery | Corrupted state file recovery on load | Corrupt state file renamed and empty state returned | tests/state/test_manager.py | test_corrupt_state_file_renamed_and_empty_state_returned | state |
| state-recovery | Corrupted state file recovery on load | Clean state file loads normally | tests/state/test_manager.py | test_clean_state_file_loads_normally | state |
| state-recovery | Corrupted state file recovery on load | Missing state file returns None gracefully | tests/state/test_manager.py | test_missing_state_file_returns_none_gracefully | state |
| state-recovery | State file rotation on save | First save creates state file only | tests/state/test_manager.py | test_first_save_creates_state_file_only | state |
| state-recovery | State file rotation on save | Subsequent saves rotate state files | tests/state/test_manager.py | test_subsequent_saves_rotate_state_files | state |
| state-recovery | State file rotation on save | Backup count limit enforced | tests/state/test_manager.py | test_backup_count_limit_enforced | state |
| state-recovery | State file rotation on save | state_backup_count = 0 disables rotation | tests/state/test_manager.py | test_state_backup_count_zero_disables_rotation | state |
| state-recovery | GlobalConfig state_backup_count field | Default state_backup_count | tests/config/test_model.py | test_global_config_default_state_backup_count | config |
| pre-flight-cleanup | Stale temporary file cleanup at pipeline startup | tmp files in snapshot_dir removed | tests/core/test_validation.py | test_preflight_cleanup_tmp_files_in_snapshot_dir_removed | cleanup |
| pre-flight-cleanup | Stale temporary file cleanup at pipeline startup | tmp files in target directories removed | tests/core/test_validation.py | test_preflight_cleanup_tmp_files_in_target_dirs_removed | cleanup |
| pre-flight-cleanup | Stale temporary file cleanup at pipeline startup | Stale NBD sockets removed | tests/core/test_validation.py | test_preflight_cleanup_stale_nbd_sockets_removed | cleanup |
| pre-flight-cleanup | Stale temporary file cleanup at pipeline startup | No stale files — no action | tests/core/test_validation.py | test_preflight_cleanup_no_stale_files_no_action | cleanup |
| pre-flight-cleanup | Orphan qcow2 detection (warning only) | Orphan snapshot detected | tests/core/test_validation.py | test_preflight_cleanup_orphan_snapshot_detected | cleanup |
| pre-flight-cleanup | Orphan qcow2 detection (warning only) | All snapshots accounted for — no warning | tests/core/test_validation.py | test_preflight_cleanup_all_snapshots_accounted_no_warning | cleanup |
| pre-flight-cleanup | GlobalConfig auto_cleanup field | auto_cleanup disabled | tests/core/test_validation.py | test_preflight_cleanup_auto_cleanup_disabled | cleanup |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Intact chain — blockcommit proceeds | tests/core/test_pipeline.py | test_chain_verify_intact_chain_blockcommit_proceeds | core |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Missing file in chain — blockcommit skipped | tests/core/test_pipeline.py | test_chain_verify_missing_file_blockcommit_skipped | core |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Non-qcow2 file in chain — blockcommit skipped | tests/core/test_pipeline.py | test_chain_verify_non_qcow2_blockcommit_skipped | core |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Cyclic reference detected — blockcommit skipped | tests/core/test_pipeline.py | test_chain_verify_cyclic_reference_blockcommit_skipped | core |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Broken chain does NOT defer the operation | tests/core/test_pipeline.py | test_chain_verify_broken_chain_does_not_defer | core |
| chain-integrity-verification | Post-commit chain length verification | Chain shortened as expected | tests/core/test_pipeline.py | test_post_commit_chain_shortened_as_expected | core |
| chain-integrity-verification | Post-commit chain length verification | Chain length unchanged — CRITICAL | tests/core/test_pipeline.py | test_post_commit_chain_length_unchanged_critical | core |
| chain-integrity-verification | Post-commit chain length verification | Post-commit verification fails — snapshots preserved | tests/core/test_pipeline.py | test_post_commit_verification_fails_snapshots_preserved | core |
| chain-integrity-verification | GlobalConfig chain verification fields | Chain verification enabled by default | tests/config/test_model.py | test_global_config_chain_verify_defaults_true | config |
| chain-integrity-verification | GlobalConfig chain verification fields | Chain verification disabled | tests/core/test_pipeline.py | test_chain_verify_disabled_skips_pre_commit_check | core |
| backup-retry | Retry wrapper for backup transfers on transient errors | Transient error retried successfully | tests/core/test_pipeline.py | test_backup_retry_transient_error_retried_successfully | core |
| backup-retry | Retry wrapper for backup transfers on transient errors | All retries exhausted | tests/core/test_pipeline.py | test_backup_retry_all_retries_exhausted | core |
| backup-retry | Retry wrapper for backup transfers on transient errors | Non-retryable error fails immediately | tests/core/test_pipeline.py | test_backup_retry_non_retryable_fails_immediately | core |
| backup-retry | Retry wrapper for backup transfers on transient errors | Retry disabled when backup_retry_max = 0 | tests/core/test_pipeline.py | test_backup_retry_disabled_when_max_zero | core |
| backup-retry | Target-level retry configuration | Target defaults for retry | tests/config/test_model.py | test_target_config_default_retry_values | config |
| backup-retry | Target-level retry configuration | Target overrides retry settings | tests/config/test_facade.py | test_facade_parses_target_retry_overrides | config |
| backup-retry | Target-level retry configuration | Invalid retry base string | tests/config/test_facade.py | test_facade_invalid_retry_base_raises_config_error | config |
| backup-retry | Retry is a Core concern, not a provider concern | Providers remain retry-unaware | tests/modules/backup/test_copy.py | test_provider_remains_retry_unaware | retry |
| deep-verification-circuit | Separate systemd timer for deep verification | Weekly deep check service | tests/systemd/test_units.py | test_deep_check_timer_weekly_schedule | cli |
| deep-verification-circuit | Separate systemd timer for deep verification | Persistent timer catches up | tests/systemd/test_units.py | test_deep_check_timer_persistent_true | cli |
| deep-verification-circuit | qsnap check --deep enhanced with per-image verification | All images pass deep check | tests/cli/test_commands.py | test_check_deep_all_images_pass_exit_zero | cli |
| deep-verification-circuit | qsnap check --deep enhanced with per-image verification | Corruption detected in one image | tests/cli/test_commands.py | test_check_deep_corruption_detected_exit_zero_warning | cli |
| deep-verification-circuit | qsnap check --deep enhanced with per-image verification | Image unreadable | tests/cli/test_commands.py | test_check_deep_image_unreadable_exit_one | cli |
| deep-verification-circuit | deep_check_schedule config field | deep_check_schedule defaults to off | tests/config/test_model.py | test_global_config_default_deep_check_schedule_off | config |
| deep-verification-circuit | deep_check_schedule config field | deep_check_schedule displayed in check output | tests/cli/test_commands.py | test_check_output_displays_deep_check_schedule_overdue | cli |
| deep-verification-circuit | BlockCommitManager deep_verify flag | deep_verify passes after deferred commit | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_deep_verify_passes | lifecycle |
| deep-verification-circuit | BlockCommitManager deep_verify flag | deep_verify fails after deferred commit | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_deep_verify_fails_corruptions | lifecycle |
| deep-verification-circuit | VMConfig deep verification fields | Deep verify defaults to off | tests/config/test_model.py | test_vm_config_deep_verify_defaults_false | config |
| deep-verification-circuit | VMConfig deep verification fields | Deep verify enabled for critical VM | tests/core/test_pipeline.py | test_deferred_blockcommit_passes_deep_verify_true | core |
| config-model | GlobalConfig dataclass | GlobalConfig is immutable | tests/config/test_model.py | test_global_config_immutable | config |
| config-model | GlobalConfig dataclass | GlobalConfig default values | tests/config/test_model.py | test_global_config_defaults | config |
| config-model | VMConfig dataclass | VMConfig with required fields | tests/config/test_model.py | test_vm_config_required_fields | config |
| config-model | TargetConfig dataclass | TargetConfig with incremental enabled | tests/config/test_model.py | test_target_config_incremental | config |
| config-model | GlobalConfig auto_cleanup field | Default auto_cleanup is true | tests/config/test_model.py | test_global_config_default_auto_cleanup_true | config |
| config-model | GlobalConfig state_backup_count field | Default state_backup_count | tests/config/test_model.py | test_global_config_default_state_backup_count | config |
| config-model | GlobalConfig chain verification fields | Chain verification enabled by default | tests/config/test_model.py | test_global_config_chain_verify_defaults_true | config |
| config-model | GlobalConfig deep_check_schedule field | deep_check_schedule defaults to off | tests/config/test_model.py | test_global_config_default_deep_check_schedule_off | config |
| config-model | VMConfig deep verify fields | Deep verify defaults to off | tests/config/test_model.py | test_vm_config_deep_verify_defaults_false | config |
| config-model | TargetConfig retry fields | Default retry values | tests/config/test_model.py | test_target_config_default_retry_values | config |
| config-parsing | ConfigFacade parses new fault-tolerance fields | Global safety fields parsed | tests/config/test_facade.py | test_facade_parses_global_safety_fields | config |
| config-parsing | ConfigFacade parses new fault-tolerance fields | VM deep verify fields parsed | tests/config/test_facade.py | test_facade_parses_vm_deep_verify_fields | config |
| config-parsing | ConfigFacade parses new fault-tolerance fields | Target retry fields parsed | tests/config/test_facade.py | test_facade_parses_target_retry_fields | config |
| config-parsing | ConfigFacade updates example config | Example config is parseable with all fields documented | tests/systemd/test_units.py | test_example_config_documents_all_safety_fields | cli |
| env-validation | Pre-flight environment validation before pipeline | Cleanup and orphan detection execute before main checks | tests/core/test_validation.py | test_validate_env_cleanup_before_main_checks | cleanup |
| env-validation | Pre-flight environment validation before pipeline | Cleanup skipped when auto_cleanup is false | tests/core/test_validation.py | test_validate_env_cleanup_skipped_when_auto_cleanup_false | cleanup |
| state-management | JsonStateManager implements IStateManager | Write and read allocation size | tests/state/test_manager.py | test_write_read_allocation | state |
| state-management | JsonStateManager implements IStateManager | Missing state file returns None | tests/state/test_manager.py | test_missing_state_returns_none | state |
| state-management | Corrupted state file recovery | Corrupt state file renamed and empty state returned | tests/state/test_manager.py | test_corrupt_state_file_renamed_and_empty_state_returned | state |
| state-management | State file rotation | State files rotated on subsequent saves | tests/state/test_manager.py | test_subsequent_saves_rotate_state_files | state |
| core-orchestrator | Pre-commit chain verification before blockcommit | Chain verification blocks broken chain | tests/core/test_pipeline.py | test_chain_verify_missing_file_blockcommit_skipped | core |
| core-orchestrator | Post-commit chain verification after blockcommit | Post-commit chain check passes | tests/core/test_pipeline.py | test_post_commit_chain_shortened_as_expected | core |
| core-orchestrator | Retry wrapper for backup transfers | Backup retried on transient error | tests/core/test_pipeline.py | test_backup_retry_transient_error_retried_successfully | core |
| core-orchestrator | Deferred blockcommit with deep verify | Deep verify passed to deferred blockcommit | tests/core/test_pipeline.py | test_deferred_blockcommit_passes_deep_verify_true | core |
| lifecycle-manager | Blockcommit snapshots into base image | Successful blockcommit with deep verify passing | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_deep_verify_passes | lifecycle |
| lifecycle-manager | Blockcommit snapshots into base image | Successful blockcommit but deep verify fails | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_deep_verify_fails_corruptions | lifecycle |
| lifecycle-manager | Blockcommit snapshots into base image | deep_verify=False — no check performed | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_deep_verify_false_no_check | lifecycle |
| backup-provider | Backup providers remain retry-unaware | Provider returns error, Core handles retry | tests/modules/backup/test_copy.py | test_provider_returns_error_core_handles_retry | retry |
| backup-provider | Backup providers remain retry-unaware | BackupResult error is structured for retry detection | tests/modules/backup/test_copy.py | test_backup_result_error_structured_for_retry_detection | retry |
| cli-interface | list config shows per-VM safety settings | list config shows OFF for default deep verify | tests/cli/test_commands.py | test_list_config_shows_off_for_default_deep_verify | cli |
| cli-interface | list config shows per-VM safety settings | list config shows ON for enabled deep verify | tests/cli/test_commands.py | test_list_config_shows_on_for_enabled_deep_verify | cli |
| cli-interface | qsnap check reports safety configuration status | check output shows disabled safety features | tests/cli/test_commands.py | test_check_output_shows_disabled_safety_features | cli |
| cli-interface | qsnap check --deep provides per-image results | Deep check exit code — all pass (0) | tests/cli/test_commands.py | test_check_deep_all_images_pass_exit_zero | cli |
| cli-interface | qsnap check --deep provides per-image results | Deep check exit code — corruptions (0) | tests/cli/test_commands.py | test_check_deep_corruption_detected_exit_zero_warning | cli |
| cli-interface | qsnap check --deep provides per-image results | Deep check exit code — unreadable (1) | tests/cli/test_commands.py | test_check_deep_image_unreadable_exit_one | cli |
| systemd-integration | Example config file | Example config is parseable | tests/systemd/test_units.py | test_example_config_is_parseable_by_configfacade | cli |
| systemd-integration | Example config file | Example config documents preserve_min fields | tests/systemd/test_units.py | test_example_config_documents_preserve_min_fields | cli |
| systemd-integration | Example config file | Example config documents all safety fields | tests/systemd/test_units.py | test_example_config_documents_all_safety_fields | cli |
| systemd-integration | Deep verification systemd timer and service | Deep check timer ships with correct defaults | tests/systemd/test_units.py | test_deep_check_timer_ships_with_correct_defaults | cli |
| systemd-integration | Deep verification systemd timer and service | Enabling the deep check timer | tests/systemd/test_units.py | test_deep_check_timer_enabling_starts_weekly | cli |

## Delegation Groups

Test files are partitioned into non-overlapping groups for parallel execution. Each file belongs to exactly one group. Groups follow logical separation by subsystem.

### Group: state

**Scope:** tests/state/test_manager.py, tests/mocks/test_mock_state.py, tests/modules/change/test_allocation.py, tests/interfaces/test_state_manager.py

| Test File | Scenarios | Action |
|---|---|---|
| tests/state/test_manager.py | 11 | MODIFY |
| tests/mocks/test_mock_state.py | 0 (contract + fixture support) | MODIFY |
| tests/modules/change/test_allocation.py | 0 (risk edge-case: first-run after recovery) | MODIFY |
| tests/interfaces/test_state_manager.py | 0 (contract verification) | MODIFY |

### Group: config

**Scope:** tests/config/test_model.py, tests/config/test_facade.py

| Test File | Scenarios | Action |
|---|---|---|
| tests/config/test_model.py | 15 | MODIFY |
| tests/config/test_facade.py | 5 | MODIFY |

### Group: core

**Scope:** tests/core/test_pipeline.py

| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_pipeline.py | 18 | MODIFY |

### Group: lifecycle

**Scope:** tests/modules/lifecycle/test_blockcommit.py, tests/interfaces/test_lifecycle_manager.py, tests/factory/test_default.py

| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/lifecycle/test_blockcommit.py | 5 | MODIFY |
| tests/interfaces/test_lifecycle_manager.py | 0 (contract: deep_verify kwarg accepted) | MODIFY |
| tests/factory/test_default.py | 0 (verify no factory wiring change needed) | MODIFY |

### Group: cleanup

**Scope:** tests/core/test_validation.py

| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_validation.py | 9 | MODIFY |

### Group: retry

**Scope:** tests/utils/test_retry.py, tests/modules/backup/test_copy.py

| Test File | Scenarios | Action |
|---|---|---|
| tests/utils/test_retry.py | 4 (pure-function support for backup-retry scenarios) | NEW |
| tests/modules/backup/test_copy.py | 2 | MODIFY |

### Group: cli

**Scope:** tests/cli/test_commands.py, tests/systemd/test_units.py

| Test File | Scenarios | Action |
|---|---|---|
| tests/cli/test_commands.py | 10 | MODIFY |
| tests/systemd/test_units.py | 7 | MODIFY |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| tests/conftest.py | Add `auto_cleanup`, `state_backup_count`, `chain_verify_before_commit`, `chain_verify_after_commit`, `deep_check_schedule` kwargs to `make_global_config`. Add `blockcommit_deep_verify`, `snapshot_deep_verify` kwargs to `make_vm_config`. Add `backup_retry_max`, `backup_retry_base` kwargs to `make_target`. | Fixtures must forward new config fields so all downstream tests construct valid dataclasses. |
| tests/state/test_manager.py | Add 7 new tests: corrupt state renamed + empty state, clean state loads normally, missing state returns None gracefully, first save creates file only, subsequent saves rotate, backup count limit enforced, state_backup_count=0 disables rotation. Modify `JsonStateManager` construction to accept `state_backup_count` parameter. | state-recovery spec adds `_load()` corruption recovery and `_save()` rotation — both need isolated unit tests with `tmp_path` fixtures. |
| tests/mocks/test_mock_state.py | Add test verifying `InMemoryStateManager` still passes `isinstance(., IStateManager)` after any interface changes. Add test verifying mock returns empty state gracefully (no file I/O, so no corruption recovery needed — but interface contract must hold). | Mock must remain a valid `IStateManager` implementation after `JsonStateManager` gains new behavior. |
| tests/modules/change/test_allocation.py | Add test: state corruption recovery → `get_last_allocation` returns `None` → `has_changed` returns `changed=True` (first-run behavior). Use `JsonStateManager` with a corrupted state file, then feed the recovered manager to `AllocationSizeDetector`. | design.md Risk 1: state recovery creates one unnecessary snapshot. Test proves `onchange` safely defaults to creating a snapshot. |
| tests/interfaces/test_state_manager.py | Add test verifying `JsonStateManager` still passes `isinstance(., IStateManager)` after corruption recovery and rotation are added. | Contract test: concrete implementation must still satisfy ABC after internal method changes. |
| tests/config/test_model.py | Modify `test_global_config_defaults` to assert `auto_cleanup == True`, `state_backup_count == 2`, `chain_verify_before_commit == True`, `chain_verify_after_commit == True`, `deep_check_schedule == "off"`. Modify `test_vm_config_required_fields` to assert `blockcommit_deep_verify == False`, `snapshot_deep_verify == False`. Modify `test_target_config_incremental` to assert `backup_retry_max == 3`, `backup_retry_base == "2s"`. Add immutability tests for each new field. | config-model spec adds 9 new fields across 3 dataclasses — defaults and frozen behavior must be verified. |
| tests/config/test_facade.py | Add 5 tests: global safety fields parsed, VM deep verify fields parsed, target retry fields parsed, target retry overrides, invalid retry base string raises `ConfigError`. Create new fixture TOML files under `tests/fixtures/configs/` (e.g., `safety_fields.toml`) with the new fields. | config-parsing spec requires `ConfigFacade` to parse all new fields from TOML with defaults and validation. |
| tests/core/test_pipeline.py | Add 18 tests covering: pre-commit chain verification (intact, missing file, non-qcow2, cyclic, no-defer), post-commit chain verification (shortened, unchanged CRITICAL, snapshots preserved), chain verify disabled, backup retry (transient retried, all exhausted, non-retryable, max=0), deferred blockcommit with deep_verify=True. Use `MockFactory` and `MockShell` with canned `qemu-img info --backing-chain` JSON outputs. | core-orchestrator spec inserts chain verification and retry steps into the pipeline. All tests use MockFactory for zero real virsh/qemu-img calls. |
| tests/core/test_validation.py | Add 9 tests: tmp files in snapshot_dir removed, tmp files in target dirs removed, stale NBD sockets removed, no stale files no action, orphan snapshot detected (warning only), all snapshots accounted for no warning, auto_cleanup disabled, cleanup before main checks, cleanup skipped when auto_cleanup false. Use `MockShell` with `rm` and `find` command expectations. | pre-flight-cleanup and env-validation specs add `_preflight_cleanup()` step to `_validate_environment()`. |
| tests/modules/lifecycle/test_blockcommit.py | Add 3 tests: `deep_verify=True` + `qemu-img check` 0 corruptions → `CommitResult(success=True)`, `deep_verify=True` + 5 corruptions → `CommitResult(success=False, error="deep verify: 5 corruptions in base image")`, `deep_verify=False` → no `qemu-img check` call. Add `qemu-img check` expectations to `MockShell`. | lifecycle-manager spec adds optional `deep_verify` kwarg to `blockcommit()`. |
| tests/interfaces/test_lifecycle_manager.py | Modify parametrized contract test to call `blockcommit(vm_config, snapshots, deep_verify=True)` and verify `CommitResult` is returned. Add `BlockCommitManager` with `deep_verify=True` to the parametrization. | Contract test: `ILifecycleManager.blockcommit()` signature now accepts `deep_verify` kwarg — all implementations must accept it. |
| tests/factory/test_default.py | Add test verifying `DefaultFactory.create_lifecycle_manager()` returns a `BlockCommitManager` that accepts `deep_verify` kwarg (no factory wiring change — deep_verify is passed by Core, not factory). | Verify factory does NOT need modification for deep_verify (it is a method parameter, not a factory concern). |
| tests/utils/test_retry.py | NEW file. Add pure-function tests: `is_retryable("Connection refused") == True`, `is_retryable("No route to host") == True`, `is_retryable("timed out") == True` (case-insensitive), `is_retryable("broken pipe") == True`, `is_retryable("EOF") == True`, `is_retryable("No space left on device") == False`, `is_retryable("Permission denied") == False`, `parse_duration("2s") == 2`, `parse_duration("10s") == 10`, `parse_duration("abc")` raises `ConfigError`, `compute_backoff(base=2, attempt=1) == 2`, `compute_backoff(base=2, attempt=2) == 4`, `compute_backoff(base=2, attempt=3) == 8`. | backup-retry spec requires a pure retryability function and duration parser. These are extracted as testable pure functions (no I/O, no Core dependency). |
| tests/modules/backup/test_copy.py | Add 2 tests: `FileCopyBackupProvider.transfer_missing()` returns `BackupResult(success=False, error="Connection refused")` without retrying (verify `transfer_missing` call count == 1), and `BackupResult.error` string contains the underlying `ShellResult.error` for Core pattern-matching. | backup-provider spec requires providers to remain retry-unaware — regression test proving no retry logic leaked into provider. |
| tests/cli/test_commands.py | Add 7 tests: `list config` shows OFF for default deep verify, `list config` shows ON for enabled deep verify, `check` output shows disabled safety features, `check --deep` all pass exit 0, `check --deep` corruptions exit 0 (warning), `check --deep` unreadable exit 1, `check` output displays deep_check_schedule overdue. Use `Mock` Core with canned return values. | cli-interface spec adds safety columns to `list config` and exit-code semantics to `check --deep`. |
| tests/systemd/test_units.py | Add 5 tests: example config parseable (modify existing), example config documents preserve_min fields, example config documents all safety fields, deep check timer ships with correct defaults (weekly, Persistent, RandomizedDelaySec, not enabled by default), enabling deep check timer. Add `qsnap-check.timer` and `qsnap-check.service` file path constants. | systemd-integration spec adds new timer/service units and expands example config with all safety fields. |
| tests/fixtures/configs/safety_fields.toml | NEW fixture file. TOML config with all fault-tolerance fields set: `auto_cleanup`, `state_backup_count`, `chain_verify_before_commit`, `chain_verify_after_commit`, `deep_check_schedule` in global; `blockcommit_deep_verify`, `snapshot_deep_verify` in `[[vm]]`; `backup_retry_max`, `backup_retry_base` in `[[vm.target]]`. | config-parsing tests need a fixture TOML with all new fields to verify `ConfigFacade` parses them correctly. |
| tests/fixtures/shell_outputs/backing_chain_intact.json | NEW fixture file. Canned `qemu-img info --backing-chain --output=json` output with 5 files, all qcow2, consistent references, no cycles. | chain-integrity-verification tests need canned backing-chain JSON for MockShell. |
| tests/fixtures/shell_outputs/backing_chain_broken.json | NEW fixture file. Canned backing-chain JSON with one missing file reference. | chain-integrity-verification tests need a broken-chain fixture for failure scenarios. |
| tests/fixtures/shell_outputs/qemu_img_check_clean.json | NEW fixture file. Canned `qemu-img check --output=json` output with 0 corruptions. | deep-verification-circuit tests need canned check output for MockShell. |
| tests/fixtures/shell_outputs/qemu_img_check_corrupt.json | NEW fixture file. Canned `qemu-img check --output=json` output with `corruptions: 5`. | deep-verification-circuit tests need a corrupted-image fixture for failure scenarios. |

## Risks & Edge Cases

- **State recovery creates one unnecessary snapshot (onchange returns True)** — When `JsonStateManager._load()` recovers from corruption, `get_last_allocation()` returns `None`, which triggers `AllocationSizeDetector` first-run behavior (`changed=True`). This creates one extra snapshot. → Covered by `tests/modules/change/test_allocation.py` :: `test_state_recovery_triggers_first_run_changed_true` — construct a `JsonStateManager` pointing at a corrupted state file, feed the recovered manager to `AllocationSizeDetector`, assert `result.changed is True` and no shell calls were made (short-circuit).

- **Broken chain blocks blockcommit but doesn't self-heal** — Pre-commit chain verification detects a missing file and skips blockcommit, but does NOT add the operation to deferred operations or attempt repair. The CRITICAL log must include remediation guidance. → Covered by `tests/core/test_pipeline.py` :: `test_chain_verify_broken_chain_does_not_defer` — assert `get_deferred_operations()` returns `[]` after a broken-chain blockcommit, and the CRITICAL log contains "Check file existence" or "restore from backup".

- **Post-commit verification with deleted snapshot file** — `virsh blockcommit --delete` may remove the snapshot file even if the data merge didn't complete. Post-commit verification detects chain length unchanged, but the snapshot file is already gone. → Covered by `tests/core/test_pipeline.py` :: `test_post_commit_verification_fails_snapshots_preserved` — assert snapshot entries remain in `IStateManager` (NOT removed) when post-commit verification fails, and the CRITICAL log includes the snapshot file paths for manual recovery.

- **Retry adds latency to pipeline** — Worst case: 3 retries × (2+4+8=14s) = ~14s added to a single backup transfer. Non-retryable errors (disk full, permission denied) must fail immediately without delay. → Covered by `tests/utils/test_retry.py` :: `test_compute_backoff_max_three_attempts` (verify backoff sequence 2/4/8) and `tests/core/test_pipeline.py` :: `test_backup_retry_non_retryable_fails_immediately` (verify `transfer_missing` call count == 1 for "No space left on device").

- **Deep timer overlap with main pipeline via lockfile** — If `qsnap-check.timer` fires while `qsnap run` is executing, both could contend for the same lockfile. The preferred mitigation is that `qsnap check` acquires the same lockfile, blocking `qsnap run` until check completes. → Covered by `tests/systemd/test_units.py` :: `test_deep_check_service_uses_same_config_flag` — verify `qsnap-check.service` ExecStart uses `-c /etc/qsnap/qsnap.toml` (same config → same lockfile path). Additionally, `test_deep_check_timer_not_enabled_by_default` verifies the timer is NOT enabled by default (operator must opt in).

- **Orphan files warned but not deleted** — Orphan `.qcow2` files (matching qsnap naming pattern but not in state) are detected and logged as WARNING, but NOT deleted. Auto-deletion could destroy data if state was corrupted (Decision 2). → Covered by `tests/core/test_validation.py` :: `test_preflight_cleanup_orphan_snapshot_detected` — assert the orphan file still exists on disk after cleanup, and a WARNING log was emitted with the file path. Use `tmp_path` with a real `.qcow2` file to verify non-deletion.
