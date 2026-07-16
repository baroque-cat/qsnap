# Test Plan: Rate Limiting & Deferred Monitoring

**Change:** `ratelimit-deferred-monitoring`
**Total Spec Scenarios:** 83 (across 9 spec files)
**Test Framework:** pytest with MockShell / InMemoryStateManager / MockVMModuleFactory

---

## 1. Coverage Map

Every `#### Scenario:` from every spec file is traced to a concrete test function below.

| # | Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|---|
| 1 | rate-limit | Rate limit config field on GlobalConfig | Global rate_limit is parsed | tests/config/test_facade.py | test_global_rate_limit_parsed | config-parsing |
| 2 | rate-limit | Rate limit config field on GlobalConfig | Global rate_limit defaults to "no" | tests/config/test_model.py | test_global_config_rate_limit_defaults_no | config-parsing |
| 3 | rate-limit | Rate limit config field on GlobalConfig | Invalid rate_limit format raises ConfigError | tests/config/test_facade.py | test_invalid_rate_limit_raises_config_error | config-parsing |
| 4 | rate-limit | Rate limit config field on TargetConfig | Target overrides global rate_limit | tests/config/test_facade.py | test_target_overrides_global_rate_limit | config-parsing |
| 5 | rate-limit | Rate limit config field on TargetConfig | Target inherits global rate_limit | tests/config/test_facade.py | test_target_inherits_global_rate_limit | config-parsing |
| 6 | rate-limit | GlobalConfig immutability includes rate_limit | GlobalConfig with rate_limit is frozen | tests/config/test_model.py | test_global_config_rate_limit_frozen | config-parsing |
| 7 | rate-limit | TargetConfig immutability includes rate_limit | TargetConfig with rate_limit is frozen | tests/config/test_model.py | test_target_config_rate_limit_frozen | config-parsing |
| 8 | rate-limit | Rsync used for file-copy transfers when rate_limit is set | Transfer with rate limit uses rsync | tests/modules/backup/test_copy.py | test_transfer_with_rate_limit_uses_rsync | backup-rsync |
| 9 | rate-limit | Rsync used for file-copy transfers when rate_limit is set | Transfer without rate limit uses cp (unchanged) | tests/modules/backup/test_copy.py | test_transfer_without_rate_limit_uses_cp | backup-rsync |
| 10 | rate-limit | Rsync --partial enables resume-after-interruption | Partial file exists on target | tests/modules/backup/test_copy.py | test_partial_file_resumes_with_rsync | backup-rsync |
| 11 | rate-limit | Fallback to cp when rsync is unavailable with rate_limit set | Rsync not found with rate_limit set | tests/modules/backup/test_copy.py | test_rsync_not_found_falls_back_to_cp | backup-rsync |
| 12 | rate-limit | Transfer logging for rate-limited transfers | Pre-transfer INFO log | tests/modules/backup/test_copy.py | test_pre_transfer_info_log | backup-rsync |
| 13 | rate-limit | Transfer logging for rate-limited transfers | Post-transfer INFO log with throughput | tests/modules/backup/test_copy.py | test_post_transfer_info_log_throughput | backup-rsync |
| 14 | rate-limit | Transfer logging for rate-limited transfers | DEBUG log contains full rsync command | tests/modules/backup/test_copy.py | test_debug_log_contains_rsync_command | backup-rsync |
| 15 | rate-limit | Anomalous throughput warning | Slow transfer triggers warning | tests/modules/backup/test_copy.py | test_slow_transfer_triggers_warning | backup-rsync |
| 16 | rate-limit | Rate limit is parsed with binary Ki/Mi/Gi/Ti suffixes | "500K" parsed correctly | tests/utils/test_parsing.py | test_parse_rate_limit_500k | config-parsing |
| 17 | rate-limit | Rate limit is parsed with binary Ki/Mi/Gi/Ti suffixes | "100M" parsed correctly | tests/utils/test_parsing.py | test_parse_rate_limit_100m | config-parsing |
| 18 | rate-limit | Rsync bwlimit receives KiB/s value | 100M rate_limit becomes rsync bwlimit 102400 | tests/utils/test_parsing.py | test_rate_limit_to_kib_100m | config-parsing |
| 19 | rate-limit | Full backup and bitmap backup unaffected by rate_limit | Full backup ignores rate_limit | tests/modules/backup/test_copy.py | test_full_backup_ignores_rate_limit | backup-rsync |
| 20 | rate-limit | Full backup and bitmap backup unaffected by rate_limit | Bitmap backup ignores rate_limit | tests/modules/backup/test_bitmap.py | test_bitmap_backup_ignores_rate_limit | backup-rsync |
| 21 | rate-limit | Pre-flight rsync availability check | Rsync available — silent | tests/core/test_validation.py | test_rsync_available_no_warning | validation-rsync |
| 22 | rate-limit | Pre-flight rsync availability check | Rsync unavailable — WARNING | tests/core/test_validation.py | test_rsync_unavailable_warning | validation-rsync |
| 23 | rate-limit | Pre-flight rsync availability check | Rate limit not set — rsync not checked | tests/core/test_validation.py | test_rate_limit_no_rsync_check | validation-rsync |
| 24 | deferred-monitoring | Deferred threshold config fields | All deferred thresholds have defaults | tests/config/test_model.py | test_global_config_deferred_thresholds_defaults | config-parsing |
| 25 | deferred-monitoring | Deferred threshold config fields | Deferred thresholds can be overridden | tests/config/test_facade.py | test_deferred_thresholds_overridden | config-parsing |
| 26 | deferred-monitoring | GlobalConfig immutability includes deferred thresholds | Attempted mutation raises FrozenInstanceError | tests/config/test_model.py | test_global_config_deferred_thresholds_frozen | config-parsing |
| 27 | deferred-monitoring | Post-pipeline deferred threshold check | Deferred count below warn threshold — silent | tests/core/test_deferred.py | test_deferred_count_below_warn_silent | core-monitoring |
| 28 | deferred-monitoring | Post-pipeline deferred threshold check | Deferred count meets WARNING threshold | tests/core/test_deferred.py | test_deferred_count_meets_warn_threshold | core-monitoring |
| 29 | deferred-monitoring | Post-pipeline deferred threshold check | Deferred count meets CRITICAL threshold | tests/core/test_deferred.py | test_deferred_count_meets_crit_threshold | core-monitoring |
| 30 | deferred-monitoring | Post-pipeline deferred threshold check | Deferred age meets WARNING threshold | tests/core/test_deferred.py | test_deferred_age_meets_warn_threshold | core-monitoring |
| 31 | deferred-monitoring | Post-pipeline deferred threshold check | Deferred age meets CRITICAL threshold | tests/core/test_deferred.py | test_deferred_age_meets_crit_threshold | core-monitoring |
| 32 | deferred-monitoring | Post-pipeline deferred threshold check | Threshold check does not change exit code | tests/core/test_deferred.py | test_threshold_check_exit_code_unchanged | core-monitoring |
| 33 | deferred-monitoring | CLI `list deferred` command | List all deferred operations | tests/cli/test_commands.py | test_list_deferred_all_operations | cli-deferred |
| 34 | deferred-monitoring | CLI `list deferred` command | List deferred filtered by VM name | tests/cli/test_commands.py | test_list_deferred_filtered_by_vm | cli-deferred |
| 35 | deferred-monitoring | CLI `list deferred` command | List deferred with no deferred operations | tests/cli/test_commands.py | test_list_deferred_no_operations | cli-deferred |
| 36 | deferred-monitoring | CLI `list deferred` command | List deferred with --format raw | tests/cli/test_commands.py | test_list_deferred_format_raw | cli-deferred |
| 37 | deferred-monitoring | DeferredBlockcommit gains last_warned_at field | DeferredBlockcommit defaults last_warned_at to None | tests/models/test_results.py | test_deferred_blockcommit_defaults_last_warned_at_none | deferred-model |
| 38 | deferred-monitoring | DeferredBlockcommit gains last_warned_at field | DeferredBlockcommit with explicit last_warned_at | tests/models/test_results.py | test_deferred_blockcommit_explicit_last_warned_at | deferred-model |
| 39 | deferred-monitoring | DeferredBlockcommit gains last_warned_at field | State file round-trips last_warned_at | tests/state/test_manager.py | test_state_round_trips_last_warned_at | deferred-model |
| 40 | deferred-monitoring | DeferredBlockcommit gains last_warned_at field | Old state file without last_warned_at is backward-compatible | tests/state/test_manager.py | test_old_state_file_backward_compatible | deferred-model |
| 41 | deferred-monitoring | Remediation guidance in qsnap check | Check shows deferred with apparmor remediation | tests/core/test_list_commands.py | test_check_deferred_apparmor_remediation | core-monitoring |
| 42 | deferred-monitoring | Remediation guidance in qsnap check | Check shows deferred with selinux remediation | tests/core/test_list_commands.py | test_check_deferred_selinux_remediation | core-monitoring |
| 43 | deferred-monitoring | Remediation guidance in qsnap check | Check shows healthy VM with no remediation | tests/core/test_list_commands.py | test_check_healthy_vm_no_remediation | core-monitoring |
| 44 | deferred-monitoring | Deferred severity levels | OK status when below all thresholds | tests/core/test_deferred.py | test_deferred_status_ok_below_thresholds | core-monitoring |
| 45 | deferred-monitoring | Deferred severity levels | WARNING status when count meets threshold | tests/core/test_deferred.py | test_deferred_status_warning_count | core-monitoring |
| 46 | deferred-monitoring | Deferred severity levels | CRITICAL status when age meets threshold | tests/core/test_deferred.py | test_deferred_status_critical_age | core-monitoring |
| 47 | config-model | GlobalConfig rate_limit field | (No scenario — requirement references rate-limit spec) | tests/config/test_model.py | test_global_config_has_rate_limit_field | config-parsing |
| 48 | config-model | GlobalConfig deferred threshold fields | (No scenario — requirement references deferred-monitoring spec) | tests/config/test_model.py | test_global_config_has_deferred_threshold_fields | config-parsing |
| 49 | config-model | TargetConfig rate_limit field | (No scenario — requirement references rate-limit spec) | tests/config/test_model.py | test_target_config_has_rate_limit_field | config-parsing |
| 50 | config-model | GlobalConfig dataclass | GlobalConfig is immutable | tests/config/test_model.py | test_global_config_immutable | config-parsing |
| 51 | config-model | GlobalConfig dataclass | GlobalConfig default values | tests/config/test_model.py | test_global_config_defaults | config-parsing |
| 52 | config-model | TargetConfig dataclass | TargetConfig with incremental enabled | tests/config/test_model.py | test_target_config_incremental | config-parsing |
| 53 | backup-provider | Transfer missing snapshots to backup target | New snapshot copied to empty target | tests/modules/backup/test_copy.py | test_transfer_missing_new_snapshot_empty_target | backup-rsync |
| 54 | backup-provider | Transfer missing snapshots to backup target | Transfer with rate limit uses rsync | tests/modules/backup/test_copy.py | test_transfer_with_rate_limit_uses_rsync | backup-rsync |
| 55 | backup-provider | Transfer missing snapshots to backup target | Snapshot already exists on target — skipped | tests/modules/backup/test_copy.py | test_transfer_missing_existing_snapshot_skipped | backup-rsync |
| 56 | backup-provider | Transfer missing snapshots to backup target | Incremental backup — rebase backing path | tests/modules/backup/test_copy.py | test_transfer_incremental_rebase_backing_path | backup-rsync |
| 57 | backup-provider | Transfer missing snapshots to backup target | Rebase to FULL anchor when present | tests/modules/backup/test_copy.py | test_transfer_missing_rebases_to_full_anchor | backup-rsync |
| 58 | backup-provider | Transfer missing snapshots to backup target | Fallback to cp when rsync unavailable with rate_limit set | tests/modules/backup/test_copy.py | test_rsync_not_found_falls_back_to_cp | backup-rsync |
| 59 | deferred-operations | Deferred blockcommit queue in IStateManager | Add and retrieve deferred blockcommit | tests/state/test_manager.py | test_add_and_retrieve_deferred_blockcommit | deferred-model |
| 60 | deferred-operations | Deferred blockcommit queue in IStateManager | Clear deferred operations | tests/state/test_manager.py | test_clear_deferred_operations | deferred-model |
| 61 | deferred-operations | Deferred blockcommit queue in IStateManager | No deferred operations for VM | tests/state/test_manager.py | test_no_deferred_operations_empty_list | deferred-model |
| 62 | deferred-operations | Deferred blockcommit queue in IStateManager | last_warned_at persists across state round-trip | tests/state/test_manager.py | test_state_round_trips_last_warned_at | deferred-model |
| 63 | deferred-operations | Deferred blockcommit queue in IStateManager | Old state file without last_warned_at is backward-compatible | tests/state/test_manager.py | test_old_state_file_backward_compatible | deferred-model |
| 64 | core-orchestrator | Post-pipeline deferred threshold check | Deferred threshold WARNING logged | tests/core/test_deferred.py | test_deferred_threshold_warning_logged | core-monitoring |
| 65 | core-orchestrator | Post-pipeline deferred threshold check | Deferred threshold CRITICAL logged | tests/core/test_deferred.py | test_deferred_threshold_critical_logged | core-monitoring |
| 66 | core-orchestrator | Core.list_deferred() method | list_deferred returns summaries for all VMs | tests/core/test_list_commands.py | test_list_deferred_returns_all_vm_summaries | core-monitoring |
| 67 | core-orchestrator | Core.list_deferred() method | list_deferred with VM filter | tests/core/test_list_commands.py | test_list_deferred_with_vm_filter | core-monitoring |
| 68 | core-orchestrator | Core.check() includes deferred status with remediation | Check includes deferred status | tests/core/test_list_commands.py | test_check_includes_deferred_status | core-monitoring |
| 69 | core-orchestrator | Pipeline step order | Pipeline with always mode | tests/core/test_pipeline.py | test_pipeline_always_mode_creates_snapshot | core-monitoring |
| 70 | core-orchestrator | Pipeline step order | Pipeline with onchange mode, no changes | tests/core/test_pipeline.py | test_pipeline_onchange_no_changes_skips_snapshot | core-monitoring |
| 71 | cli-interface | CLI `list deferred` subcommand | list deferred dispatches to Core | tests/cli/test_commands.py | test_list_deferred_dispatches_to_core | cli-deferred |
| 72 | cli-interface | CLI `list deferred` subcommand | list deferred with VM filter dispatches to Core | tests/cli/test_commands.py | test_list_deferred_with_vm_filter_dispatches | cli-deferred |
| 73 | cli-interface | CLI `list deferred` subcommand | list deferred with --format raw | tests/cli/test_commands.py | test_list_deferred_format_raw | cli-deferred |
| 74 | cli-interface | CLI entry point | Help text | tests/cli/test_app.py | test_help_text_lists_subcommands_and_flags | cli-deferred |
| 75 | cli-interface | CLI entry point | Subcommand dispatch | tests/cli/test_commands.py | test_run_subcommand_dispatches_to_core_run | cli-deferred |
| 76 | cli-interface | CLI entry point | list deferred sub-subcommand | tests/cli/test_app.py | test_list_deferred_sub_subcommand | cli-deferred |
| 77 | list-commands | Core.list_deferred() method | list_deferred returns per-VM summaries | tests/core/test_list_commands.py | test_list_deferred_returns_per_vm_summaries | core-monitoring |
| 78 | list-commands | Core.list_deferred() method | list_deferred with no deferred operations | tests/core/test_list_commands.py | test_list_deferred_no_deferred_operations | core-monitoring |
| 79 | list-commands | Core.list_deferred() method | list_deferred filtered by VM name | tests/core/test_list_commands.py | test_list_deferred_filtered_by_vm_name | core-monitoring |
| 80 | env-validation | Pre-flight rsync availability check | Rsync available — no warning | tests/core/test_validation.py | test_rsync_available_no_warning | validation-rsync |
| 81 | env-validation | Pre-flight rsync availability check | Rsync unavailable — warning logged | tests/core/test_validation.py | test_rsync_unavailable_warning | validation-rsync |
| 82 | env-validation | Pre-flight rsync availability check | Rsync check skipped when rate_limit is "no" | tests/core/test_validation.py | test_rsync_check_skipped_when_rate_limit_no | validation-rsync |
| 83 | env-validation | Pre-flight environment validation before pipeline | All validations pass | tests/core/test_validation.py | test_validate_environment_all_pass | validation-rsync |
| 84 | env-validation | Pre-flight environment validation before pipeline | snapshot_dir does not exist | tests/core/test_validation.py | test_validate_environment_snapshot_dir_missing | validation-rsync |
| 85 | env-validation | Pre-flight environment validation before pipeline | virsh binary not in PATH | tests/core/test_validation.py | test_validate_environment_virsh_not_in_path | validation-rsync |
| 86 | env-validation | Pre-flight environment validation before pipeline | libvirt rejects dominfo — VM not defined | tests/core/test_validation.py | test_validate_environment_vm_not_defined | validation-rsync |

**Verification:** 83 scenario rows from 9 spec files + 3 requirement-only rows (config-model has 3 requirements without scenarios, mapped to field-existence tests #47-49) = 86 coverage rows total. Every `#### Scenario:` header is represented.

---

## 2. Delegation Groups

Each group is a self-contained @Mr.Tester subagent session. A test FILE belongs to EXACTLY ONE group.

### Group: config-parsing

**Scope:** Config dataclass fields, ConfigFacade parsing, rate-limit parsing utility, shared conftest fixtures, and TOML fixture files.

| Test File | Scenarios Count | Action |
|---|---|---|
| tests/config/test_model.py | 10 | MODIFY |
| tests/config/test_facade.py | 5 | MODIFY |
| tests/utils/test_parsing.py | 3 | MODIFY |
| tests/conftest.py | 0 (fixture support) | MODIFY |
| tests/fixtures/configs/rate_limit_global.toml | 0 (fixture) | NEW |
| tests/fixtures/configs/rate_limit_target_override.toml | 0 (fixture) | NEW |
| tests/fixtures/configs/rate_limit_invalid.toml | 0 (fixture) | NEW |
| tests/fixtures/configs/deferred_thresholds.toml | 0 (fixture) | NEW |

**Notes:**
- `test_model.py` gains tests for `rate_limit` default `"no"`, frozen immutability, deferred threshold defaults (`"5"`, `"10"`, `"7d"`, `"14d"`), and deferred threshold frozen immutability. Existing `test_global_config_defaults` and `test_global_config_immutable` must be updated to assert the new fields.
- `test_facade.py` gains tests for parsing `rate_limit = "100M"` at global scope, invalid format raising `ConfigError`, target override, target inheritance, and deferred threshold overrides. New TOML fixture files provide the config data.
- `test_parsing.py` gains a `parse_rate_limit()` function test for `"500K"` → 512000 bytes/s, `"100M"` → 104857600 bytes/s, and `rate_limit_to_kib("100M")` → 102400. Invalid formats (`"abc"`, `"100X"`) raise `ValueError`.
- `conftest.py` `make_global_config` and `make_target` factory fixtures must accept `rate_limit`, `deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age` kwargs.

### Group: backup-rsync

**Scope:** FileCopyBackupProvider rsync transfer logic, BitmapBackupProvider rate-limit exclusion.

| Test File | Scenarios Count | Action |
|---|---|---|
| tests/modules/backup/test_copy.py | 13 | MODIFY |
| tests/modules/backup/test_bitmap.py | 1 | MODIFY |

**Notes:**
- `test_copy.py` gains new tests for rsync transfer with `--bwlimit=102400 --partial --progress`, cp fallback when `which rsync` fails, partial-file resume, pre/post-transfer INFO logging, DEBUG command logging, anomalous-throughput WARNING, and full-backup ignoring rate_limit. Existing tests that assert `cp` commands must be updated to handle the `rate_limit="no"` default path (which still uses `cp`).
- `test_bitmap.py` gains one test verifying `BitmapBackupProvider.transfer_missing()` uses `qemu-img convert` without `--bwlimit` even when `rate_limit` is set.
- The `transfer_missing()` method signature gains a `rate_limit: str = "no"` parameter (or reads from `TargetConfig.rate_limit`). Tests must construct `TargetConfig(rate_limit="100M")` for rsync scenarios.

### Group: deferred-model

**Scope:** DeferredBlockcommit dataclass field, JsonStateManager serialization, InMemoryStateManager mock, state manager contract.

| Test File | Scenarios Count | Action |
|---|---|---|
| tests/models/test_results.py | 2 | MODIFY |
| tests/state/test_manager.py | 7 | MODIFY |
| tests/mocks/mock_state.py | 0 (mock support) | MODIFY |
| tests/mocks/test_mock_state.py | 0 (mock contract) | MODIFY |
| tests/interfaces/test_state_manager.py | 0 (contract) | MODIFY |

**Notes:**
- `test_results.py` gains tests for `DeferredBlockcommit` with `last_warned_at=None` default and `last_warned_at=datetime(...)` explicit value. The existing `test_deferred_blockcommit_dataclass_fields` must be updated to assert the new field exists.
- `test_manager.py` gains tests for `last_warned_at` round-trip through JSON and backward compatibility with old state files lacking the key. Existing `test_add_and_retrieve_deferred_blockcommit` must assert `last_warned_at=None` on the retrieved entry. `_deferred_to_dict` and `_dict_to_deferred` must serialize/deserialize `last_warned_at`.
- `mock_state.py` `InMemoryStateManager.add_deferred_blockcommit` must construct `DeferredBlockcommit` with `last_warned_at=None` (or accept it).
- `test_mock_state.py` must verify the mock correctly stores and returns `last_warned_at`.
- `test_state_manager.py` contract test verifies `DeferredBlockcommit` field set includes `last_warned_at`.

### Group: core-monitoring

**Scope:** Core deferred threshold checks, Core.list_deferred(), Core.check() with deferred remediation, pipeline step order.

| Test File | Scenarios Count | Action |
|---|---|---|
| tests/core/test_deferred.py | 13 | MODIFY |
| tests/core/test_list_commands.py | 8 | MODIFY |
| tests/core/test_pipeline.py | 2 | MODIFY |

**Notes:**
- `test_deferred.py` gains tests for `_check_deferred_thresholds()`: count below warn (silent), count at warn (WARNING log), count at crit (CRITICAL log), age at warn (WARNING), age at crit (CRITICAL), exit code unchanged (0), and severity-level classification (OK/WARNING/CRITICAL). Existing deferred tests that use `MockConfigFacade` must pass `GlobalConfig` with deferred threshold fields.
- `test_list_commands.py` gains tests for `Core.list_deferred()` returning per-VM summaries (vm_name, count, reason, age), filtering by VM name, returning empty list, and `Core.check()` including deferred status with apparmor/selinux remediation guidance and no remediation for healthy VMs.
- `test_pipeline.py` existing `test_pipeline_always_mode_creates_snapshot` and `test_pipeline_onchange_no_changes_skips_snapshot` must be updated to verify `_check_deferred_thresholds()` is called after pipeline completion (per MODIFIED pipeline step order spec).

### Group: cli-deferred

**Scope:** CLI `list deferred` subcommand dispatch, argument parsing, output formatting.

| Test File | Scenarios Count | Action |
|---|---|---|
| tests/cli/test_commands.py | 6 | MODIFY |
| tests/cli/test_app.py | 2 | MODIFY |
| tests/cli/test_format.py | 0 (format support) | MODIFY |

**Notes:**
- `test_commands.py` gains `handle_list_deferred` dispatch tests: no filter → `core.list_deferred(None)`, VM filter → `core.list_deferred("vm-home")`, `--format raw` → key=value output, no deferred ops → "No deferred blockcommit operations" message. The existing `handle_list` must be updated to dispatch `list_subcommand == "deferred"`.
- `test_app.py` `build_argparser()` must include a `deferred` sub-subcommand under `list`. `test_help_text_lists_subcommands_and_flags` must verify `deferred` appears in help text. New test `test_list_deferred_sub_subcommand` parses `["list", "deferred"]`.
- `test_format.py` may gain a deferred-specific formatter test if a `format_deferred()` function is added to `cli/format.py`.

### Group: validation-rsync

**Scope:** Pre-flight environment validation rsync availability check.

| Test File | Scenarios Count | Action |
|---|---|---|
| tests/core/test_validation.py | 7 | MODIFY |

**Notes:**
- `test_validation.py` gains 3 new tests for rsync availability: `which rsync` succeeds → no warning, `which rsync` fails → WARNING logged but validation passes, `rate_limit="no"` → `which rsync` never called. The existing 4 validation tests (`all_pass`, `snapshot_dir_missing`, `virsh_not_in_path`, `vm_not_defined`) must be updated to handle the new rsync check step in `_validate_environment()`. The `conftest.py` `_setup_validation_expectations` may need a `which rsync` success expectation.

---

## 3. Test Modifications

Existing tests that need updating, with the spec scenario or design decision driving the change.

### tests/modules/backup/test_copy.py — cp to rsync assertions

**Spec scenarios:** #8 Transfer with rate limit uses rsync, #9 Transfer without rate limit uses cp, #53 New snapshot copied to empty target, #55 Snapshot already exists on target, #56 Incremental backup — rebase backing path, #57 Rebase to FULL anchor.

**Design decision:** D1 (replace cp with rsync --bwlimit), D3 (binary suffix format).

**Changes:**
- `test_transfer_missing_new_snapshot_empty_target` — currently asserts `cp` command. Must be updated to handle the default `rate_limit="no"` path (still uses `cp`) AND add a new variant with `rate_limit="100M"` that asserts `rsync --bwlimit=102400 --partial --progress`.
- `test_transfer_incremental_rebase_backing_path` — must work with both `rate_limit="no"` (cp) and `rate_limit="100M"` (rsync). The rebase logic is unchanged.
- `test_transfer_missing_rebases_to_full_anchor` — same: must work with both transfer modes.
- All existing `cp` assertion tests remain valid when `rate_limit` defaults to `"no"`, but the `TargetConfig` constructor must accept the new `rate_limit` field.
- New tests: `test_transfer_with_rate_limit_uses_rsync`, `test_transfer_without_rate_limit_uses_cp`, `test_partial_file_resumes_with_rsync`, `test_rsync_not_found_falls_back_to_cp`, `test_pre_transfer_info_log`, `test_post_transfer_info_log_throughput`, `test_debug_log_contains_rsync_command`, `test_slow_transfer_triggers_warning`, `test_full_backup_ignores_rate_limit`.

### tests/models/test_results.py — DeferredBlockcommit last_warned_at field

**Spec scenarios:** #37 DeferredBlockcommit defaults last_warned_at to None, #38 DeferredBlockcommit with explicit last_warned_at.

**Design decision:** D5 (last_warned_at field for future notification deduplication).

**Changes:**
- `test_deferred_blockcommit_dataclass_fields` — currently asserts fields `{snapshots, reason, since}`. Must be updated to also assert `last_warned_at` exists, defaults to `None`, and is frozen.
- New tests: `test_deferred_blockcommit_defaults_last_warned_at_none`, `test_deferred_blockcommit_explicit_last_warned_at`.

### tests/state/test_manager.py — last_warned_at persistence

**Spec scenarios:** #39 State file round-trips last_warned_at, #40 Old state file backward-compatible, #59 Add and retrieve deferred blockcommit, #60 Clear deferred operations, #61 No deferred operations for VM.

**Design decision:** D5, Risk: old state files missing last_warned_at.

**Changes:**
- `test_add_and_retrieve_deferred_blockcommit` — must assert `last_warned_at is None` on the retrieved entry (since `add_deferred_blockcommit` creates without `last_warned_at`).
- `_deferred_to_dict` must serialize `last_warned_at` as ISO format string (or omit if None).
- `_dict_to_deferred` must use `.get("last_warned_at")` returning `None` for missing keys.
- New tests: `test_state_round_trips_last_warned_at` (write with explicit `last_warned_at`, reload, assert equal), `test_old_state_file_backward_compatible` (write raw JSON without `last_warned_at` key, load via `_dict_to_deferred`, assert `last_warned_at is None`).

### tests/core/test_list_commands.py — list_deferred and check remediation

**Spec scenarios:** #41 Check shows deferred with apparmor remediation, #42 Check shows deferred with selinux remediation, #43 Check shows healthy VM with no remediation, #66 list_deferred returns summaries for all VMs, #67 list_deferred with VM filter, #68 Check includes deferred status, #77-79 list_deferred method.

**Design decision:** D6 (check integrates deferred status with remediation).

**Changes:**
- `test_check_healthy_backing_chain_reports_ok` — must be updated to also verify no remediation guidance is displayed when deferred count is 0.
- New tests: `test_list_deferred_returns_all_vm_summaries`, `test_list_deferred_with_vm_filter`, `test_list_deferred_no_deferred_operations`, `test_list_deferred_filtered_by_vm_name`, `test_check_deferred_apparmor_remediation`, `test_check_deferred_selinux_remediation`, `test_check_healthy_vm_no_remediation`, `test_check_includes_deferred_status`.
- `Core.check()` return type or output must include deferred status. The `CheckResult` dataclass may gain a `deferred_status` field, or `check()` may return enriched output.

### tests/core/test_deferred.py — threshold monitoring

**Spec scenarios:** #27-32 Post-pipeline deferred threshold check, #44-46 Deferred severity levels, #64-65 Core-orchestrator threshold check.

**Design decision:** D4 (per-VM threshold check at end of pipeline run).

**Changes:**
- Existing tests use `MockConfigFacade` with default `GlobalConfig()`. After the change, `GlobalConfig` gains `deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age` fields with defaults. Existing tests should still pass because defaults are backward-compatible.
- `_run_pipeline()` must call `_check_deferred_thresholds()` after processing all VMs. New tests verify this.
- New tests: `test_deferred_count_below_warn_silent`, `test_deferred_count_meets_warn_threshold`, `test_deferred_count_meets_crit_threshold`, `test_deferred_age_meets_warn_threshold`, `test_deferred_age_meets_crit_threshold`, `test_threshold_check_exit_code_unchanged`, `test_deferred_status_ok_below_thresholds`, `test_deferred_status_warning_count`, `test_deferred_status_critical_age`, `test_deferred_threshold_warning_logged`, `test_deferred_threshold_critical_logged`.

### tests/core/test_validation.py — rsync availability check

**Spec scenarios:** #21-23 Pre-flight rsync availability check, #83-86 Environment validation.

**Design decision:** D1 (fallback to cp), env-validation MODIFIED requirement.

**Changes:**
- `test_validate_environment_all_pass` — must be updated to also verify rsync check passes (or is skipped when `rate_limit="no"`).
- `conftest.py` `_setup_validation_expectations` may need a `which rsync` success expectation so validation passes by default.
- New tests: `test_rsync_available_no_warning`, `test_rsync_unavailable_warning`, `test_rsync_check_skipped_when_rate_limit_no`.
- The `_validate_environment()` method gains a check (e): if any target has `rate_limit != "no"`, run `which rsync`. Missing rsync logs WARNING but does not block.

### tests/core/test_pipeline.py — pipeline step order with deferred check

**Spec scenarios:** #69 Pipeline with always mode, #70 Pipeline with onchange mode, no changes.

**Design decision:** D4, core-orchestrator MODIFIED pipeline step order.

**Changes:**
- `test_pipeline_always_mode_creates_snapshot` — must verify `_check_deferred_thresholds()` is called after pipeline completion. Can spy on the method or check log output.
- `test_pipeline_onchange_no_changes_skips_snapshot` — same: verify deferred threshold check runs even when no snapshot is created.

### tests/cli/test_commands.py — list deferred dispatch

**Spec scenarios:** #33-36 CLI list deferred command, #71-73 CLI list deferred subcommand, #75 Subcommand dispatch.

**Changes:**
- `handle_list` must gain a `elif sub == "deferred":` branch that calls `core.list_deferred(vm_filter)` and formats the result.
- New tests: `test_list_deferred_dispatches_to_core`, `test_list_deferred_with_vm_filter_dispatches`, `test_list_deferred_format_raw`, `test_list_deferred_all_operations`, `test_list_deferred_filtered_by_vm`, `test_list_deferred_no_operations`.
- `_make_list_args` helper must support `list_subcommand="deferred"`.

### tests/cli/test_app.py — list deferred subparser

**Spec scenarios:** #74 Help text, #76 list deferred sub-subcommand.

**Changes:**
- `build_argparser()` must add a `deferred` sub-subcommand under `list`.
- `test_help_text_lists_subcommands_and_flags` — must verify `deferred` appears in help output.
- New test: `test_list_deferred_sub_subcommand` — parses `["list", "deferred"]` and verifies `list_subcommand == "deferred"`.

### tests/config/test_model.py — new config fields

**Spec scenarios:** #2 Global rate_limit defaults to "no", #6 GlobalConfig with rate_limit is frozen, #7 TargetConfig with rate_limit is frozen, #24 All deferred thresholds have defaults, #26 Attempted mutation raises FrozenInstanceError, #50-52 config-model dataclass scenarios.

**Changes:**
- `test_global_config_defaults` — must assert `rate_limit == "no"`, `deferred_warn_count == "5"`, `deferred_crit_count == "10"`, `deferred_warn_age == "7d"`, `deferred_crit_age == "14d"`.
- `test_global_config_immutable` — must verify mutating `rate_limit` raises `FrozenInstanceError`.
- `test_target_config_incremental` — must assert `rate_limit` defaults to `"no"` on `TargetConfig`.
- New tests: `test_global_config_rate_limit_defaults_no`, `test_global_config_rate_limit_frozen`, `test_target_config_rate_limit_frozen`, `test_global_config_deferred_thresholds_defaults`, `test_global_config_deferred_thresholds_frozen`, `test_global_config_has_rate_limit_field`, `test_global_config_has_deferred_threshold_fields`, `test_target_config_has_rate_limit_field`.

### tests/config/test_facade.py — rate_limit and deferred threshold parsing

**Spec scenarios:** #1 Global rate_limit is parsed, #3 Invalid rate_limit format raises ConfigError, #4 Target overrides global rate_limit, #5 Target inherits global rate_limit, #25 Deferred thresholds can be overridden.

**Changes:**
- New TOML fixture files: `rate_limit_global.toml` (with `rate_limit = "100M"`), `rate_limit_target_override.toml` (global `"100M"`, target `"500M"`), `rate_limit_invalid.toml` (with `rate_limit = "abc"`), `deferred_thresholds.toml` (with `deferred_warn_count = "3"`, `deferred_crit_age = "30d"`).
- New tests: `test_global_rate_limit_parsed`, `test_invalid_rate_limit_raises_config_error`, `test_target_overrides_global_rate_limit`, `test_target_inherits_global_rate_limit`, `test_deferred_thresholds_overridden`.
- `ConfigFacade._parse()` must validate `rate_limit` format and raise `ConfigError` on invalid values.
- `ConfigFacade._build_target()` must resolve `rate_limit` inheritance (target → VM → global).

### tests/utils/test_parsing.py — rate_limit parsing function

**Spec scenarios:** #16 "500K" parsed correctly, #17 "100M" parsed correctly, #18 100M rate_limit becomes rsync bwlimit 102400.

**Changes:**
- New function `parse_rate_limit(value: str) -> int` in `qsnap/utils/parsing.py` that converts `"500K"` → 512000, `"100M"` → 104857600, `"no"`/`"0"` → 0, invalid → raise `ValueError`.
- New function `rate_limit_to_kib(value: str) -> int` that returns `parse_rate_limit(value) // 1024`.
- New tests: `test_parse_rate_limit_500k`, `test_parse_rate_limit_100m`, `test_rate_limit_to_kib_100m`.

### tests/mocks/mock_state.py — InMemoryStateManager last_warned_at

**Spec scenarios:** Supports #59-63 (deferred-operations scenarios tested via test_manager.py).

**Changes:**
- `add_deferred_blockcommit` must construct `DeferredBlockcommit` with `last_warned_at=None`.
- `get_deferred_operations` must return entries that carry `last_warned_at`.

### tests/conftest.py — fixture factory updates

**Spec scenarios:** Supports all groups (shared fixtures).

**Changes:**
- `make_global_config` must accept `rate_limit`, `deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age` kwargs with defaults matching `GlobalConfig`.
- `make_target` must accept `rate_limit` kwarg with default `"no"`.
- `_setup_validation_expectations` may need a `which rsync` success expectation so validation passes by default for tests that don't override it.

---

## 4. Risks & Edge Cases

From `design.md` Risks section and spec boundary conditions, the following edge cases require dedicated test coverage.

### 4.1 Rsync Fallback to cp When Missing

**Risk:** Rsync adds a new required dependency. Mitigation: graceful fallback to `cp` with WARNING log.

**Test coverage:**
- `test_rsync_not_found_falls_back_to_cp` (tests/modules/backup/test_copy.py) — `rate_limit="100M"`, `which rsync` returns non-zero, assert WARNING logged with "rsync not found", assert `cp` command executed, assert `BackupResult(success=True)`.
- `test_rsync_unavailable_warning` (tests/core/test_validation.py) — pre-flight validation: `which rsync` fails, assert WARNING logged, assert validation status is `"ok"` (non-blocking).
- **Edge case:** `which rsync` returns non-zero but `cp` also fails — assert `BackupResult(success=False)` with cp error.

### 4.2 Old State File Without last_warned_at Backward Compatibility

**Risk:** Users upgrading from old state files will have missing `last_warned_at` in DeferredBlockcommit. Mitigation: `last_warned_at` defaults to `None`; `_dict_to_deferred()` uses `.get("last_warned_at")`.

**Test coverage:**
- `test_old_state_file_backward_compatible` (tests/state/test_manager.py) — write a raw JSON state file with a `deferred_operations` entry that lacks the `last_warned_at` key. Load via `JsonStateManager.get_deferred_operations()`. Assert the returned `DeferredBlockcommit` has `last_warned_at is None`. Assert no exception is raised.
- **Edge case:** State file with `last_warned_at` set to `null` in JSON — assert `None` is returned (not a string `"null"`).
- **Edge case:** State file with `last_warned_at` set to an invalid date string — assert `datetime.fromisoformat` raises are caught or the field defaults to `None`.

### 4.3 Rate Limit Parsing Edge Cases (Invalid Formats)

**Risk:** Invalid `rate_limit` format could cause runtime failures during transfer. Mitigation: fail-fast at config parse time with `ConfigError`.

**Test coverage:**
- `test_invalid_rate_limit_raises_config_error` (tests/config/test_facade.py) — `rate_limit = "abc"` in TOML, assert `ConfigError` raised during `ConfigFacade.__init__`.
- `test_parse_rate_limit_500k` and `test_parse_rate_limit_100m` (tests/utils/test_parsing.py) — verify correct byte values.
- **Edge case:** `rate_limit = "0"` — assert treated as unlimited (same as `"no"`), `parse_rate_limit("0")` returns 0, no rsync `--bwlimit` flag.
- **Edge case:** `rate_limit = "100"` (no suffix) — assert `ValueError` or `ConfigError` (suffix is required).
- **Edge case:** `rate_limit = "100X"` (invalid suffix) — assert `ValueError` or `ConfigError`.
- **Edge case:** `rate_limit = ""` (empty string) — assert `ConfigError`.
- **Edge case:** `rate_limit = "100m"` (lowercase) — assert accepted and treated same as `"100M"` (case-insensitive suffix), OR assert rejected (spec uses uppercase). Tests must match implementation choice.
- **Edge case:** `rate_limit = "-100M"` (negative) — assert `ValueError` or `ConfigError`.

### 4.4 Deferred Thresholds Boundary Conditions

**Risk:** Threshold checks at exact boundary values could be off-by-one. Mitigation: spec says `>=` for thresholds.

**Test coverage:**
- `test_deferred_count_meets_warn_threshold` — exactly 5 deferred ops with `deferred_warn_count="5"`, assert WARNING logged (boundary: `>=` means 5 triggers).
- `test_deferred_count_meets_crit_threshold` — exactly 10 deferred ops with `deferred_crit_count="10"`, assert CRITICAL logged.
- `test_deferred_count_below_warn_silent` — 4 deferred ops with `deferred_warn_count="5"`, assert no WARNING/CRITICAL (boundary: 4 < 5 is silent).
- `test_deferred_age_meets_warn_threshold` — 1 deferred op aged exactly 7 days with `deferred_warn_age="7d"`, assert WARNING logged.
- `test_deferred_age_meets_crit_threshold` — 1 deferred op aged exactly 14 days with `deferred_crit_age="14d"`, assert CRITICAL logged.
- **Edge case:** Count at warn but age below warn — assert WARNING logged (either count OR age triggers).
- **Edge case:** Count below warn but age at crit — assert CRITICAL logged (age crit overrides count warn).
- **Edge case:** Count at crit AND age at crit — assert CRITICAL logged once (not duplicated).
- **Edge case:** Zero deferred operations — assert no threshold messages, severity OK.
- **Edge case:** `deferred_warn_count` set to `"0"` — every deferred op triggers WARNING (boundary: `>= 0` is always true for count > 0).

### 4.5 rsync --partial Resume After Interruption

**Risk:** `rsync --partial` leaves incomplete files on target disk. Mitigation: `transfer_missing()` checks file existence and size; rsync resumes.

**Test coverage:**
- `test_partial_file_resumes_with_rsync` (tests/modules/backup/test_copy.py) — pre-create a partial file smaller than source on target. Run `transfer_missing()`. Assert `rsync --partial` is called (not `cp`), assert the file is completed, assert `BackupResult(success=True)`.
- **Edge case:** Partial file is exactly the same size as source (already complete) — assert `list()` finds it and skips transfer.
- **Edge case:** Partial file is larger than source (corrupt) — assert rsync handles it (overwrites or errors).

### 4.6 Anomalous Throughput Detection

**Risk:** Transfer speed far below configured rate limit indicates target disk health issues.

**Test coverage:**
- `test_slow_transfer_triggers_warning` (tests/modules/backup/test_copy.py) — `rate_limit="100M"` (100 MiB/s limit), actual throughput 5 MiB/s (< 10% of limit). Assert WARNING logged with "slower than expected" and "Check target disk health".
- **Edge case:** Throughput exactly at 10% of limit (10 MiB/s for 100M limit) — assert no WARNING (boundary: `< 10%` is strict less-than).
- **Edge case:** Throughput at 10.1% of limit — assert no WARNING.
- **Edge case:** `rate_limit="no"` — no throughput comparison, no anomalous-throughput WARNING.

### 4.7 Exit Code Unchanged for Threshold Violations

**Risk:** Deferred threshold breaches are operational alerts, not pipeline failures.

**Test coverage:**
- `test_threshold_check_exit_code_unchanged` (tests/core/test_deferred.py) — deferred CRITICAL threshold breached during `run()`, assert `PipelineResult.success is True`, assert exit code is 0 (not 1 or 10).
- **Edge case:** CRITICAL threshold breached AND a backup transfer failed — assert exit code is `EXIT_BACKUP_ABORT` (10) due to backup failure, NOT due to threshold. The threshold check does not add to the exit code.

### 4.8 Config Inheritance: rate_limit Target → Global

**Risk:** Target-level `rate_limit` must correctly override or inherit the global default.

**Test coverage:**
- `test_target_overrides_global_rate_limit` — global `"100M"`, target `"500M"`, assert resolved rate limit for target is `"500M"`.
- `test_target_inherits_global_rate_limit` — global `"100M"`, target `"no"` (default/unset), assert resolved rate limit is `"100M"`.
- **Edge case:** Global `"no"`, target `"100M"` — assert resolved is `"100M"` (target overrides).
- **Edge case:** Global `"100M"`, target `"0"` — assert resolved is unlimited (same as `"no"`).
- **Edge case:** Global `"100M"`, target `"no"` — assert resolved is `"100M"` (target `"no"` means unset/inherit, NOT unlimited). This is a critical semantic distinction: `"no"` as a default value means "inherit", while `"no"` as an explicit override means "unlimited". Tests must verify the implementation matches the spec's intent (spec says: "A missing value SHALL inherit from the global level").
