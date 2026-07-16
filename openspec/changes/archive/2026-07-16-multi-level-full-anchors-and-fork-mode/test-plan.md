# QA Strategy & Test Plan

## Coverage Map

### Spec: multi-level-full-anchors

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `multi-level-full-anchors` | FULL backups triggered at all active bucket boundaries | All active buckets produce FULLs on period change | `tests/core/test_full_anchor.py` | `test_all_active_buckets_trigger_fulls_on_period_change` | `full-anchor-unit` |
| `multi-level-full-anchors` | FULL backups triggered at all active bucket boundaries | First backup creates FULL at each active bucket | `tests/core/test_full_anchor.py` | `test_first_backup_checks_all_active_buckets` | `full-anchor-unit` |
| `multi-level-full-anchors` | FULL backups triggered at all active bucket boundaries | Same bucket period skips FULL | `tests/core/test_full_anchor.py` | `test_same_period_all_buckets_skips_full` | `full-anchor-unit` |
| `multi-level-full-anchors` | FULL backups triggered at all active bucket boundaries | Single active bucket preserves highest-only behavior | `tests/core/test_full_anchor.py` | `test_single_active_bucket_behaves_like_highest_only` | `full-anchor-unit` |
| `multi-level-full-anchors` | Core._backup_target passes all FULLs to bucket check | get_full_backups used for per-bucket comparison | `tests/core/test_full_anchor.py` | `test_backup_target_passes_full_list_to_bucket_check` | `full-anchor-unit` |
| `multi-level-full-anchors` | Periodic FULL frequency is limited by policy granularity | One FULL created per snapshot despite multiple period changes | `tests/core/test_full_anchor.py` | `test_one_full_per_snapshot_despite_multiple_period_changes` | `full-anchor-unit` |

### Spec: full-anchor-syntax

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `full-anchor-syntax` | F-prefix syntax for FULL anchor specification | F-anchor on a single bucket | `tests/core/test_preserve.py` | `test_parse_preserve_f_anchor_single_bucket` | `f-syntax-unit` |
| `full-anchor-syntax` | F-prefix syntax for FULL anchor specification | F-anchor on all buckets | `tests/core/test_preserve.py` | `test_parse_preserve_f_anchor_all_buckets` | `f-syntax-unit` |
| `full-anchor-syntax` | F-prefix syntax for FULL anchor specification | F-anchor requires count > 0 | `tests/config/test_facade.py` | `test_f_anchor_zero_count_raises_config_error` | `f-syntax-unit` |
| `full-anchor-syntax` | F-syntax disables automatic multi-level behavior | F-anchor present — only F-marked buckets checked | `tests/core/test_full_anchor.py` | `test_f_anchor_disables_auto_multi_level_non_f_buckets_ignored` | `full-anchor-unit` |
| `full-anchor-syntax` | F-syntax disables automatic multi-level behavior | Multiple F-anchors — all checked | `tests/core/test_full_anchor.py` | `test_multiple_f_anchors_all_checked_highest_first` | `full-anchor-unit` |
| `full-anchor-syntax` | F-syntax is valid in both snapshot_preserve and target_preserve | F-anchor in snapshot_preserve parses without error | `tests/core/test_preserve.py` | `test_parse_preserve_f_anchor_in_snapshot_preserve_no_error` | `f-syntax-unit` |
| `full-anchor-syntax` | RetentionPolicy gains anchor boolean fields | Anchor fields default to False | `tests/config/test_model.py` | `test_retention_policy_anchor_fields_default_false` | `config-model-unit` |
| `full-anchor-syntax` | RetentionPolicy gains anchor boolean fields | Anchor fields set from parsed F-syntax | `tests/core/test_preserve.py` | `test_parse_preserve_sets_anchor_fields_from_f_syntax` | `f-syntax-unit` |

### Spec: periodic-full-backup

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `periodic-full-backup` | Bucket-driven FULL creation logic | Highest bucket is yearly with all-buckets mode | `tests/core/test_full_anchor.py` | `test_all_buckets_checked_yearly_monthly_weekly_daily_hourly` | `full-anchor-unit` |
| `periodic-full-backup` | Bucket-driven FULL creation logic | F-anchor overrides to daily-only | `tests/core/test_full_anchor.py` | `test_f_anchor_daily_only_ignores_other_buckets` | `full-anchor-unit` |
| `periodic-full-backup` | Bucket-driven FULL creation logic | No active buckets | `tests/core/test_full_anchor.py` | `test_no_active_buckets_no_f_anchors_returns_false` | `full-anchor-unit` |
| `periodic-full-backup` | Bucket-driven FULL creation logic | First backup to target creates FULL | `tests/core/test_full_anchor.py` | `test_first_backup_empty_fulls_list_creates_first_active_bucket_full` | `full-anchor-unit` |
| `periodic-full-backup` | Core triggers full backup before incremental transfer | First backup to target creates FULL | `tests/core/test_pipeline.py` | `test_first_backup_creates_full_via_bucket` | `core-bucket-unit` |
| `periodic-full-backup` | Core triggers full backup before incremental transfer | New weekly period triggers FULL (all-buckets mode) | `tests/core/test_pipeline.py` | `test_new_weekly_period_triggers_full_all_buckets` | `core-bucket-unit` |
| `periodic-full-backup` | Core triggers full backup before incremental transfer | F-anchor on weekly only triggers FULL at week boundaries | `tests/core/test_pipeline.py` | `test_f_anchor_weekly_only_full_on_week_boundary_not_day` | `core-bucket-unit` |

### Spec: config-model

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `config-model` | RetentionPolicy dataclass | RetentionPolicy with hourly and daily limits | `tests/config/test_model.py` | `test_retention_policy_hourly_daily` | `config-model-unit` |
| `config-model` | RetentionPolicy dataclass | RetentionPolicy defaults | `tests/config/test_model.py` | `test_retention_policy_defaults` | `config-model-unit` |
| `config-model` | RetentionPolicy dataclass | Anchor fields set from F-syntax | `tests/config/test_model.py` | `test_retention_policy_anchor_fields_explicit` | `config-model-unit` |
| `config-model` | RetentionPolicy dataclass | preserve_min = "latest" | `tests/config/test_model.py` | `test_retention_policy_preserve_min_latest` | `config-model-unit` |

### Spec: config-parsing

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `config-parsing` | Config validation forbids preserve_min without buckets | preserve_min without buckets rejected | `tests/config/test_facade.py` | `test_preserve_min_without_buckets_raises_config_error` | `f-syntax-unit` |
| `config-parsing` | Config validation forbids preserve_min without buckets | preserve_min=all without buckets allowed | `tests/config/test_facade.py` | `test_preserve_min_all_without_buckets_allowed` | `f-syntax-unit` |
| `config-parsing` | Config validation forbids preserve_min without buckets | F-anchor with count=0 rejected | `tests/config/test_facade.py` | `test_f_anchor_zero_count_raises_config_error` | `f-syntax-unit` |
| `config-parsing` | F-syntax parsing in _parse_preserve | F-syntax parsed correctly | `tests/core/test_preserve.py` | `test_parse_preserve_sets_anchor_fields_from_f_syntax` | `f-syntax-unit` |
| `config-parsing` | F-syntax parsing in _parse_preserve | No F-prefix — anchors remain False | `tests/core/test_preserve.py` | `test_parse_preserve_no_f_prefix_anchors_false` | `f-syntax-unit` |
| `config-parsing` | F-syntax parsing in _parse_preserve | F-prefix with invalid bucket character | `tests/core/test_preserve.py` | `test_parse_preserve_f_prefix_invalid_bucket_ignored` | `f-syntax-unit` |

### Spec: fork-mode

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `fork-mode` | qsnap fork command creates independent VM from snapshot | Fork creates standalone writable qcow2 | `tests/core/test_fork.py` | `test_fork_creates_standalone_qcow2_via_qemu_img_convert` | `fork-core-unit` |
| `fork-mode` | qsnap fork command creates independent VM from snapshot | Fork defines new libvirt VM | `tests/core/test_fork.py` | `test_fork_defines_new_libvirt_vm_with_modified_xml` | `fork-core-unit` |
| `fork-mode` | qsnap fork command creates independent VM from snapshot | Fork from backup | `tests/core/test_fork.py` | `test_fork_from_backup_resolves_via_backup_provider` | `fork-core-unit` |
| `fork-mode` | qsnap fork command creates independent VM from snapshot | Fork with --add-to-config | `tests/core/test_fork.py` | `test_fork_add_to_config_appends_vm_block` | `fork-core-unit` |
| `fork-mode` | Core.fork method | fork returns RestoreResult on success | `tests/core/test_fork.py` | `test_fork_returns_restore_result_on_success` | `fork-core-unit` |
| `fork-mode` | Core.fork method | fork fails on nonexistent snapshot | `tests/core/test_fork.py` | `test_fork_snapshot_not_found_returns_failure` | `fork-core-unit` |
| `fork-mode` | Fork generates unique VM UUID | Forked VM has different UUID | `tests/core/test_fork.py` | `test_fork_generates_new_uuid_not_source_vm_uuid` | `fork-core-unit` |
| `fork-mode` | Fork logs estimated size before converting | Size estimate logged | `tests/core/test_fork.py` | `test_fork_logs_chain_size_before_convert` | `fork-core-unit` |
| `fork-mode` | qsnap deploy command deploys backup as VM | Deploy FULL backup | `tests/core/test_fork.py` | `test_deploy_full_backup_delegates_to_fork` | `fork-core-unit` |
| `fork-mode` | qsnap deploy command deploys backup as VM | Deploy incremental backup | `tests/core/test_fork.py` | `test_deploy_incremental_backup_flattens_chain` | `fork-core-unit` |

### Spec: core-orchestrator

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `core-orchestrator` | Core._backup_target triggers full backup when due | Full backup list passed to bucket check | `tests/core/test_pipeline.py` | `test_backup_target_passes_full_list_to_bucket_check` | `core-bucket-unit` |
| `core-orchestrator` | Core._backup_target triggers full backup when due | First run creates full backup | `tests/core/test_pipeline.py` | `test_first_backup_creates_full_via_bucket` | `core-bucket-unit` |
| `core-orchestrator` | Core._should_create_bucket_full signature change | Updated signature | `tests/core/test_full_anchor.py` | `test_should_create_bucket_full_accepts_list_not_single` | `full-anchor-unit` |
| `core-orchestrator` | Core.fork method | fork succeeds | `tests/core/test_fork.py` | `test_core_fork_method_succeeds` | `fork-core-unit` |
| `core-orchestrator` | Core.deploy method | deploy delegates to fork | `tests/core/test_fork.py` | `test_deploy_delegates_to_fork` | `fork-core-unit` |

### Spec: cli-interface

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `cli-interface` | qsnap fork subcommand | Fork command succeeds | `tests/cli/test_commands.py` | `test_fork_command_dispatches_to_core_fork` | `fork-cli-unit` |
| `cli-interface` | qsnap fork subcommand | Fork command fails on missing snapshot | `tests/cli/test_commands.py` | `test_fork_command_missing_snapshot_exit_one` | `fork-cli-unit` |
| `cli-interface` | qsnap fork subcommand | Fork with --add-to-config | `tests/cli/test_commands.py` | `test_fork_command_add_to_config_flag` | `fork-cli-unit` |
| `cli-interface` | qsnap deploy subcommand | Deploy command succeeds | `tests/cli/test_commands.py` | `test_deploy_command_dispatches_to_core_deploy` | `fork-cli-unit` |
| `cli-interface` | qsnap deploy subcommand | Deploy with --storage and --add-to-config | `tests/cli/test_commands.py` | `test_deploy_command_storage_and_add_to_config_flags` | `fork-cli-unit` |

### Spec: restore-command

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `restore-command` | Core.restore method | Restore from snapshot | `tests/core/test_engine.py` | `test_core_restore_from_snapshot_returns_restore_result` | `core-bucket-unit` |
| `restore-command` | Core.restore method | Restore from backup | `tests/core/test_engine.py` | `test_core_restore_from_backup_returns_restore_result` | `core-bucket-unit` |
| `restore-command` | Snapshot resolution exposes shared primitives for fork | _resolve_snapshot finds snapshot in state | `tests/core/test_fork.py` | `test_resolve_snapshot_finds_in_state` | `fork-core-unit` |
| `restore-command` | Snapshot resolution exposes shared primitives for fork | _resolve_snapshot finds snapshot in backup | `tests/core/test_fork.py` | `test_resolve_snapshot_finds_in_backup` | `fork-core-unit` |
| `restore-command` | Snapshot resolution exposes shared primitives for fork | _resolve_snapshot raises on not found | `tests/core/test_fork.py` | `test_resolve_snapshot_raises_on_not_found` | `fork-core-unit` |

---

## Delegation Groups

### Group: full-anchor-unit

**Scope:** `tests/core/test_full_anchor.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_full_anchor.py` | 14 | NEW |

Contains all unit tests for `Core._should_create_bucket_full()` with multi-bucket all-active logic, F-anchor bucket filtering, short-circuit behavior, and single-bucket highest-only equivalence. Tests use mocked `RetentionPolicy` and `FullBackupInfo` objects — zero real I/O.

### Group: f-syntax-unit

**Scope:** `tests/core/test_preserve.py`, `tests/config/test_facade.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_preserve.py` | 5 | MODIFY |
| `tests/config/test_facade.py` | 3 | MODIFY |

Contains unit tests for `_parse_preserve()` F-syntax regex parsing (single F, all F, no F, F in snapshot_preserve, invalid bucket char Fx), plus `ConfigFacade` validation tests for F-anchor-with-zero-count rejection and preserve_min-without-buckets validation. `test_preserve.py` already has base `_parse_preserve` tests — these are additive.

### Group: config-model-unit

**Scope:** `tests/config/test_model.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | 4 | MODIFY |

Adds `RetentionPolicy` anchor boolean field tests: defaults to `False`, explicit `True` from F-syntax `make_retention_policy()` helper, and `preserve_min = "latest"` acceptance. Extends existing `test_retention_policy_*` test family. The `test_retention_policy_anchor_fields_explicit` test validates that `anchor_daily=True, anchor_weekly=True` is accepted by the frozen dataclass constructor.

### Group: fork-core-unit

**Scope:** `tests/core/test_fork.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_fork.py` | 15 | NEW |

Contains all unit tests for `Core.fork()`, `Core.deploy()`, and `Core._resolve_snapshot()`. Tests use `MockShell` with pre-configured expectations for `qemu-img convert`, `virsh dumpxml`, `virsh define`, `uuidgen`, and `qemu-img info --backing-chain`. Also tests `_resolve_snapshot` resolution from both `IStateManager` and backup providers, plus not-found error handling.

### Group: fork-cli-unit

**Scope:** `tests/cli/test_commands.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/cli/test_commands.py` | 5 | MODIFY |

Adds CLI dispatch tests for `qsnap fork` and `qsnap deploy` subcommands. Validates that `handle_fork()` and `handle_deploy()` call the correct `Core` methods with the right arguments (positional snapshot/backup name, `--as-vm`, `--storage`, `--add-to-config` flags). Tests exit code 0 on success, exit code 1 on missing snapshot.

### Group: core-bucket-unit

**Scope:** `tests/core/test_pipeline.py`, `tests/core/test_engine.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | 5 | MODIFY |
| `tests/core/test_engine.py` | 2 | MODIFY |

Modifies existing pipeline tests to verify the new `all_fulls` list signature of `_should_create_bucket_full`. Updates `test_first_backup_creates_full_via_bucket` to use `get_full_backups()` (list) instead of the old `get_last_full_backup()` path. Adds tests for weekly period triggers in all-buckets mode and F-anchor weekly-only filtering. Engine tests verify existing `restore()` still works — unchanged but kept in coverage map for completeness.

### Group: contract-unit

**Scope:** `tests/interfaces/test_backup_provider.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_backup_provider.py` | 0 | MODIFY |

Existing `test_ibackup_provider_create_full_backup_bucket_level_parameter` already validates the `bucket_level` parameter on `create_full_backup()`. No new scenarios added — this group ensures the contract test parametrization includes all backup provider implementations and that the `bucket_level` default remains `"monthly"`.

---

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/core/test_pipeline.py` | Replace `state.get_last_full_backup()` calls with `state.get_full_backups()` (list). Update assertions in `test_first_backup_creates_full_via_bucket` and `test_new_monthly_period_triggers_full` to pass `all_fulls` list. | Decision 7: `_should_create_bucket_full` now accepts `list[FullBackupInfo]` instead of single `last_full`. The pipeline must call `get_full_backups()` not `get_last_full_backup()`. |
| `tests/core/test_pipeline.py` | Add `test_backup_target_passes_full_list_to_bucket_check`: spy on `state.get_full_backups` and verify `_should_create_bucket_full` receives the list. | core-orchestrator spec: `Core._backup_target` must pass the complete full list. |
| `tests/core/test_pipeline.py` | Update `test_should_create_bucket_full_highest_yearly` and `test_should_create_bucket_full_highest_daily` to pass `all_fulls: list[FullBackupInfo]` instead of single `last_full`. When a previous FULL exists, wrap it in a list `[full]`. | core-orchestrator spec: signature change from `last_full: FullBackupInfo \| None` to `all_fulls: list[FullBackupInfo]`. |
| `tests/core/test_pipeline.py` | Add `test_new_weekly_period_triggers_full_all_buckets`: policy `yearly=1, monthly=12, weekly=4` with last FULL monthly=June, weekly=W24, snapshot in W25. Assert bucket_level="weekly". | periodic-full-backup spec: all-buckets mode triggers FULL at weekly boundary even though monthly is active but unchanged. |
| `tests/core/test_pipeline.py` | Add `test_f_anchor_weekly_only_full_on_week_boundary_not_day`: policy `weekly=4, anchor_weekly=True, daily=7, anchor_daily=False`. Snapshot on new day but same week. Assert no FULL created. Snapshot on new week. Assert FULL created with bucket_level="weekly". | periodic-full-backup spec: F-anchor filtering at Core pipeline level. |
| `tests/core/test_pipeline.py` | Modify `test_no_buckets_preserve_min_all_no_full_created` to ensure `_should_create_bucket_full` path is tested with `all_fulls=[]`. | Design Decision 1: no buckets + no F-anchors = no FULL. |
| `tests/core/test_preserve.py` | Add `test_parse_preserve_f_anchor_single_bucket`: `_parse_preserve("24h 7Fd 4w")` returns `anchor_daily=True, anchor_hourly=False, anchor_weekly=False`. | full-anchor-syntax spec: F-prefix on single bucket token. |
| `tests/core/test_preserve.py` | Add `test_parse_preserve_f_anchor_all_buckets`: `_parse_preserve("24Fh 7Fd 4Fw 12Fm 1Fy")` sets all five `anchor_*` fields to `True`. | full-anchor-syntax spec: F-prefix on all buckets. |
| `tests/core/test_preserve.py` | Add `test_parse_preserve_f_anchor_in_snapshot_preserve_no_error`: `_parse_preserve("24Fh 7Fd")` parses without error (snapshot context). | full-anchor-syntax spec: F-syntax valid in both preserve fields. |
| `tests/core/test_preserve.py` | Add `test_parse_preserve_no_f_prefix_anchors_false`: `_parse_preserve("24h 7d")` confirms all anchor fields remain `False`. | full-anchor-syntax spec: backward compatibility for non-F policies. |
| `tests/core/test_preserve.py` | Add `test_parse_preserve_f_prefix_invalid_bucket_ignored`: `_parse_preserve("7Fx")` returns `RetentionPolicy()` with all zeros and no anchors. | config-parsing spec: invalid bucket char after F is non-matching token. |
| `tests/config/test_model.py` | Add `test_retention_policy_anchor_fields_default_false`: `RetentionPolicy(hourly=24, daily=7)` has all `anchor_*` fields `False`. | config-model spec: anchor fields default to `False` when not explicitly set. |
| `tests/config/test_model.py` | Add `test_retention_policy_anchor_fields_explicit`: `RetentionPolicy(daily=7, anchor_daily=True, weekly=4, anchor_weekly=True)` stores `True` for those anchors, `False` for others. | config-model spec: frozen dataclass accepts anchor booleans. |
| `tests/config/test_model.py` | Add `test_retention_policy_anchor_fields_immutable`: mutation raises `FrozenInstanceError`. | Immutability paradigm: all config dataclasses are frozen. |
| `tests/config/test_facade.py` | Add `test_f_anchor_zero_count_raises_config_error`: config file with `target_preserve = "0Fh 7d"` raises `ConfigError("F-anchor on bucket 'h' requires count > 0")`. | config-parsing spec: `ConfigFacade` validates F-anchor + count > 0. |
| `tests/config/test_facade.py` | Add `test_preserve_min_without_buckets_raises_config_error`: config with all-zero buckets + `preserve_min="48h"` + no F-anchors raises `ConfigError`. | config-parsing spec: preserve_min requires at least one active bucket or F-anchor. |
| `tests/config/test_facade.py` | Add `test_preserve_min_all_without_buckets_allowed`: config with all-zero buckets + `preserve_min="all"` + no F-anchors passes validation. | config-parsing spec: `preserve_min="all"` is the safe default with no buckets. |

---

## Risks & Edge Cases

- **[Risk] Policies like "48h 14d 8w 12m 1y" now produce many more FULLs** → `tests/core/test_full_anchor.py`: `test_all_buckets_produce_fulls_no_f_anchors_five_active` — verifies behavior for a 5-bucket policy. `tests/core/test_pipeline.py`: `test_multi_bucket_full_count_logged` — verifies logging that FULLs were produced at each level.
- **[Risk] F-syntax is backward-incompatible with older qsnap versions** → `tests/core/test_preserve.py`: `test_parse_preserve_no_f_prefix_anchors_false` — verifies non-F policies parse identically to old behavior at the policy level. `tests/config/test_facade.py`: `test_non_f_config_parses_without_warning` — verifies no warnings for non-F configs.
- **[Risk] `qemu-img convert` on a large VM is slow (reads entire chain)** → `tests/core/test_fork.py`: `test_fork_logs_chain_size_before_convert` — verifies size estimation is logged before convert begins. `tests/core/test_fork.py`: `test_fork_runs_qemu_img_convert_without_force_share_when_vm_stopped` — edge case: VM running vs stopped.
- **[Risk] Fork produces a file as large as the full virtual disk (not sparse like the chain)** → `tests/core/test_fork.py`: `test_fork_logs_size_warning_for_large_disks` — verifies estimated final size is logged so the user knows the disk cost before committing.
- **[Risk] Deploy from incremental backup requires chain resolution through the backup target** → `tests/core/test_fork.py`: `test_deploy_incremental_backup_flattens_chain` — verifies `qemu-img convert` resolves the backing chain across backup target files. `tests/core/test_fork.py`: `test_deploy_full_backup_convert_is_noop` — verifies FULL backup deploy still calls convert (consistency).
- **[Edge Case] Short-circuit: yearly period change skips lower buckets** → `tests/core/test_full_anchor.py`: `test_one_full_per_snapshot_despite_multiple_period_changes` — verifies only ONE FULL is created when yearly, monthly, weekly ALL change on Jan 1.
- **[Edge Case] Empty full_backups list with F-anchors** → `tests/core/test_full_anchor.py`: `test_first_backup_empty_fulls_list_creates_first_active_bucket_full` — verifies first FULL chooses first active/F-marked bucket.
- **[Edge Case] `_resolve_snapshot` with vm_filter restricts search** → `tests/core/test_fork.py`: `test_resolve_snapshot_vm_filter_restricts_to_matching_vm` — verifies snapshot resolution only searches matching VMs when filter is provided.
- **[Edge Case] Fork when source VM is running (WARNING logged but proceeds)** → `tests/core/test_fork.py`: `test_fork_warns_when_source_vm_running` — per Open Question 3, log WARNING but do not block.
- **[Edge Case] RetentionPolicy with `preserve_min="latest"` and only F-anchors (zero counts)** → `tests/config/test_model.py`: `test_retention_policy_preserve_min_latest_with_f_anchors` — validates that F-anchors are retained even when counts are zero in memory (parsing-time rejection for zero F is done at ConfigFacade level).
- **[Edge Case] F-anchor on all-zero-count bucket rejected at parse time** → `tests/config/test_facade.py`: `test_f_anchor_zero_count_raises_config_error` — validates `0Fh` raises `ConfigError`.
- **[Edge Case] _should_create_bucket_full short-circuits descending** → `tests/core/test_full_anchor.py`: `test_should_create_bucket_full_iterates_descending_yearly_first` — verifies yearly is checked before monthly before weekly before daily before hourly in all-buckets mode.
- **[Edge Case] F-anchor bucket that has never triggered before (no prior FULL for that specific bucket_level)** → `tests/core/test_full_anchor.py`: `test_f_anchor_bucket_no_prior_full_creates_full` — verifies that when a F-anchor bucket has no prior FULL (only other buckets have FULLs), it triggers.
