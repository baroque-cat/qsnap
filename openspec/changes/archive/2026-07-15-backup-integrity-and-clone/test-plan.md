# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| preserve-min-config | GlobalConfig accepts snapshot_preserve_min and target_preserve_min | Default preserve_min absent | `tests/config/test_model.py` | `test_global_config_preserve_min_defaults_none` | config-unit |
| preserve-min-config | GlobalConfig accepts snapshot_preserve_min and target_preserve_min | Global preserve_min specified | `tests/config/test_model.py` | `test_global_config_preserve_min_set_from_constructor` | config-unit |
| preserve-min-config | VMConfig inherits preserve_min from GlobalConfig | VM inherits global preserve_min | `tests/config/test_facade.py` | `test_vm_inherits_global_snapshot_preserve_min` | config-unit |
| preserve-min-config | VMConfig inherits preserve_min from GlobalConfig | VM overrides global preserve_min | `tests/config/test_facade.py` | `test_vm_overrides_global_snapshot_preserve_min` | config-unit |
| preserve-min-config | TargetConfig inherits target_preserve_min from VMConfig | Target inherits VM preserve_min | `tests/config/test_facade.py` | `test_target_inherits_vm_target_preserve_min` | config-unit |
| preserve-min-config | TargetConfig inherits target_preserve_min from VMConfig | Target overrides VM preserve_min | `tests/config/test_facade.py` | `test_target_overrides_vm_target_preserve_min` | config-unit |
| preserve-min-config | Core._parse_preserve accepts optional preserve_min parameter | Explicit preserve_min overrides default | `tests/core/test_preserve.py` | `test_parse_preserve_explicit_min_overrides_default` | core-unit |
| preserve-min-config | Core._parse_preserve accepts optional preserve_min parameter | No preserve_min uses existing default | `tests/core/test_preserve.py` | `test_parse_preserve_none_uses_default_zero_h` | core-unit |
| preserve-min-config | Retention evaluation uses per-VM and per-target preserve_min | Snapshot retention uses VM preserve_min | `tests/core/test_preserve.py` | `test_evaluate_snapshot_retention_uses_vm_preserve_min` | core-unit |
| schedule-summary | Core.schedule_summary produces a human-readable retention preview | Empty state produces meaningful simulation | `tests/core/test_schedule_summary.py` | `test_schedule_summary_empty_state_produces_simulation` | core-unit |
| schedule-summary | Core.schedule_summary produces a human-readable retention preview | Summary logs at INFO on every timer invocation | `tests/core/test_schedule_summary.py` | `test_schedule_summary_logs_info_on_timer` | core-unit |
| schedule-summary | Core.schedule_summary produces a human-readable retention preview | Summary shows snapshot and backup breakdown | `tests/core/test_schedule_summary.py` | `test_schedule_summary_shows_snapshot_and_backup_breakdown` | core-unit |
| schedule-summary | TimeBasedRetention.explain returns structured bucket metadata | explain returns per-bucket counts | `tests/modules/retention/test_time_based.py` | `test_explain_returns_per_bucket_counts` | modules-unit |
| schedule-summary | TimeBasedRetention.explain returns structured bucket metadata | explain is a pure function | `tests/modules/retention/test_time_based.py` | `test_explain_is_pure_function` | modules-unit |
| backup-hash-verification | SnapshotResult carries content_hash | Hash present for newly created snapshot | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_returns_content_hash` | modules-unit |
| backup-hash-verification | SnapshotResult carries content_hash | Hash is None on creation failure | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_failure_content_hash_none` | modules-unit |
| backup-hash-verification | SnapshotInfo stores content_hash in persistent state | Hash stored and restored from state | `tests/state/test_manager.py` | `test_record_snapshot_with_content_hash_restored` | state-unit |
| backup-hash-verification | verify_backup supports verify="hash" mode | Hash match passes verification | `tests/modules/backup/test_verification.py` | `test_hash_verification_match_passes` | modules-unit |
| backup-hash-verification | verify_backup supports verify="hash" mode | Hash mismatch fails verification | `tests/modules/backup/test_verification.py` | `test_hash_verification_mismatch_fails` | modules-unit |
| backup-hash-verification | verify_backup supports verify="hash" mode | Hash verification skipped when no expected hash | `tests/modules/backup/test_verification.py` | `test_hash_verification_skipped_when_no_expected_hash` | modules-unit |
| backup-hash-verification | _file_sha256 computes binary hash efficiently | Hash computed for a file | `tests/modules/backup/test_verification.py` | `test_file_sha256_computes_hash` | modules-unit |
| periodic-full-backup | TargetConfig supports full_every and full_compress | full_every disabled by default | `tests/config/test_model.py` | `test_target_config_full_every_defaults_zero_d` | config-unit |
| periodic-full-backup | TargetConfig supports full_every and full_compress | full_every set to 7 days | `tests/config/test_facade.py` | `test_facade_parses_target_full_every` | config-unit |
| periodic-full-backup | FileCopyBackupProvider creates full backups via qemu-img convert | Uncompressed full backup | `tests/modules/backup/test_copy.py` | `test_create_full_backup_uncompressed` | modules-unit |
| periodic-full-backup | FileCopyBackupProvider creates full backups via qemu-img convert | Compressed full backup | `tests/modules/backup/test_copy.py` | `test_create_full_backup_compressed` | modules-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | First run creates full backup | `tests/core/test_pipeline.py` | `test_backup_target_first_run_creates_full_backup` | core-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | Interval not elapsed skips full backup | `tests/core/test_pipeline.py` | `test_backup_target_interval_not_elapsed_skips_full` | core-unit |
| periodic-full-backup | Incremental backups rebase to the FULL anchor | New incremental rebased to FULL | `tests/modules/backup/test_copy.py` | `test_transfer_missing_rebases_to_full_anchor` | modules-unit |
| periodic-full-backup | Incremental backups rebase to the FULL anchor | No FULL anchor uses source backing | `tests/modules/backup/test_copy.py` | `test_transfer_missing_no_full_anchor_uses_source_backing` | modules-unit |
| periodic-full-backup | IStateManager tracks last full backup per target | Full backup timestamp saved and restored | `tests/state/test_manager.py` | `test_set_and_get_last_full_backup` | state-unit |
| backup-verification | verify_backup supports verify="hash" mode | Hash match passes | `tests/modules/backup/test_verification.py` | `test_hash_verification_match_passes` | modules-unit |
| backup-verification | verify_backup supports verify="hash" mode | Hash mismatch fails | `tests/modules/backup/test_verification.py` | `test_hash_verification_mismatch_fails` | modules-unit |
| backup-verification | verify_backup supports verify="hash" mode | Metadata mode unchanged | `tests/modules/backup/test_verification.py` | `test_metadata_mode_unchanged_after_hash_addition` | modules-unit |
| state-management | IStateManager tracks SnapshotInfo content_hash | Hash persists across runs | `tests/state/test_manager.py` | `test_snapshot_content_hash_persists_across_runs` | state-unit |
| state-management | IStateManager tracks last full backup per target | Full backup state saved and retrieved | `tests/state/test_manager.py` | `test_full_backup_state_saved_and_retrieved` | state-unit |
| state-management | IStateManager tracks last full backup per target | No full backup returns None | `tests/state/test_manager.py` | `test_get_last_full_backup_returns_none_when_empty` | state-unit |
| core-orchestrator | Core._parse_preserve accepts optional preserve_min parameter | Explicit preserve_min overrides default | `tests/core/test_preserve.py` | `test_parse_preserve_explicit_min_overrides_default` | core-unit |
| core-orchestrator | Core._parse_preserve accepts optional preserve_min parameter | No preserve_min uses existing default | `tests/core/test_preserve.py` | `test_parse_preserve_none_uses_default_zero_h` | core-unit |
| core-orchestrator | Core._evaluate_snapshot_retention uses vm_config.snapshot_preserve_min | Snapshot retention with preserve_min | `tests/core/test_preserve.py` | `test_evaluate_snapshot_retention_uses_vm_preserve_min` | core-unit |
| core-orchestrator | Core._evaluate_backup_retention uses target.target_preserve_min | Backup retention with preserve_min | `tests/core/test_preserve.py` | `test_evaluate_backup_retention_uses_target_preserve_min` | core-unit |
| core-orchestrator | Core._backup_target triggers full backup when due | First run creates full backup | `tests/core/test_pipeline.py` | `test_backup_target_first_run_creates_full_backup` | core-unit |
| core-orchestrator | Core._backup_target triggers full backup when due | Interval not elapsed skips full backup | `tests/core/test_pipeline.py` | `test_backup_target_interval_not_elapsed_skips_full` | core-unit |
| core-orchestrator | Core.schedule_summary produces retention simulation | Summary includes all VMs when no filter | `tests/core/test_schedule_summary.py` | `test_schedule_summary_includes_all_vms` | core-unit |
| core-orchestrator | Core.schedule_summary produces retention simulation | Summary filters by VM name | `tests/core/test_schedule_summary.py` | `test_schedule_summary_filters_by_vm_name` | core-unit |
| cli-interface | CLI supports --print-schedule flag | --print-schedule with qsnap run | `tests/cli/test_commands.py` | `test_print_schedule_with_run_prints_before_pipeline` | cli-unit |
| cli-interface | CLI supports --print-schedule flag | Standalone --print-schedule | `tests/cli/test_commands.py` | `test_standalone_print_schedule_exits_without_snapshots` | cli-unit |
| cli-interface | Schedule summary logged at INFO during timer invocation | Timer invocation logs summary | `tests/cli/test_app.py` | `test_timer_flag_parsed_and_logs_schedule_at_info` | cli-unit |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | Uncompressed full backup succeeds | `tests/modules/backup/test_copy.py` | `test_create_full_backup_uncompressed` | modules-unit |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | Compressed full backup succeeds | `tests/modules/backup/test_copy.py` | `test_create_full_backup_compressed` | modules-unit |
| backup-provider | transfer_missing rebases incrementals to FULL anchor when present | Rebase to FULL anchor | `tests/modules/backup/test_copy.py` | `test_transfer_missing_rebases_to_full_anchor` | modules-unit |
| backup-provider | transfer_missing rebases incrementals to FULL anchor when present | No FULL anchor preserves existing behavior | `tests/modules/backup/test_copy.py` | `test_transfer_missing_no_full_anchor_uses_source_backing` | modules-unit |
| config-model | GlobalConfig contains snapshot_preserve_min and target_preserve_min | Defaults are None | `tests/config/test_model.py` | `test_global_config_preserve_min_defaults_none` | config-unit |
| config-model | VMConfig contains snapshot_preserve_min and target_preserve_min | VM inherits from global | `tests/config/test_facade.py` | `test_vm_inherits_global_snapshot_preserve_min` | config-unit |
| config-model | TargetConfig contains target_preserve_min, full_every, and full_compress | Target inherits from VM | `tests/config/test_facade.py` | `test_target_inherits_vm_target_preserve_min` | config-unit |
| config-model | TargetConfig contains target_preserve_min, full_every, and full_compress | full_every disabled by default | `tests/config/test_model.py` | `test_target_config_full_every_defaults_zero_d` | config-unit |

## Delegation Groups

### Group: config-unit

**Scope:** `tests/config/` — Unit tests for config dataclasses (immutability, defaults, field presence) and ConfigFacade integration (TOML parsing, option inheritance global → VM → target).

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | 5 | MODIFY |
| `tests/config/test_facade.py` | 7 | MODIFY |

### Group: core-unit

**Scope:** `tests/core/` — Unit tests for Core orchestration: `_parse_preserve` with preserve_min, retention evaluation wiring, full-backup trigger in `_backup_target`, and the new `schedule_summary` simulation method. All tests use `MockVMModuleFactory` — zero real virsh/qemu-img calls.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_preserve.py` | 7 | MODIFY |
| `tests/core/test_pipeline.py` | 4 | MODIFY |
| `tests/core/test_schedule_summary.py` | 5 | NEW |

### Group: modules-unit

**Scope:** `tests/modules/` — Unit tests for individual domain modules in isolation with mocked `IShell`. Covers `ExternalSnapshotProvider` content_hash computation, `FileCopyBackupProvider` full backup creation and FULL-anchor rebase, `verify_backup` hash mode, `_file_sha256`, and `TimeBasedRetention.explain`.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/snapshot/test_external.py` | 2 | MODIFY |
| `tests/modules/backup/test_copy.py` | 8 | MODIFY |
| `tests/modules/backup/test_verification.py` | 7 | MODIFY |
| `tests/modules/retention/test_time_based.py` | 2 | MODIFY |

### Group: state-unit

**Scope:** `tests/state/` — Unit tests for `JsonStateManager` concrete implementation: `SnapshotInfo.content_hash` persistence/restore, and `get_last_full_backup` / `set_last_full_backup` round-trip with `FullBackupInfo`.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/state/test_manager.py` | 5 | MODIFY |

### Group: cli-unit

**Scope:** `tests/cli/` — Unit tests for CLI argument parsing (`--timer` flag) and handler dispatch (`--print-schedule` calling `Core.schedule_summary`, standalone exit behavior, timer INFO logging).

| Test File | Scenarios | Action |
|---|---|---|
| `tests/cli/test_app.py` | 1 | MODIFY |
| `tests/cli/test_commands.py` | 2 | MODIFY |

### Group: models-unit

**Scope:** `tests/models/` — Unit tests for result dataclasses: verifies new `content_hash` field on `SnapshotResult` and `SnapshotInfo`, and the new `FullBackupInfo` dataclass (fields, frozen immutability).

| Test File | Scenarios | Action |
|---|---|---|
| `tests/models/test_results.py` | 0 spec scenarios (4 additional tests) | MODIFY |

### Group: mocks-unit

**Scope:** `tests/mocks/` — Mock verification tests: ensure `InMemoryStateManager` implements new `IStateManager` methods, and `MockBackupProvider` / `MockSnapshotProvider` support new interface methods (`create_full_backup`, `content_hash`).

| Test File | Scenarios | Action |
|---|---|---|
| `tests/mocks/test_mock_state.py` | 0 spec scenarios (2 additional tests) | MODIFY |
| `tests/mocks/test_mock_factory.py` | 0 spec scenarios (2 additional tests) | MODIFY |

### Group: interfaces-unit

**Scope:** `tests/interfaces/` — Contract tests: verify new abstract methods are declared on `IStateManager` (`get_last_full_backup`, `set_last_full_backup`) and `IBackupProvider` (`create_full_backup`), and that all concrete implementations satisfy the expanded contracts.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_state_manager.py` | 0 spec scenarios (1 additional test) | MODIFY |
| `tests/interfaces/test_backup_provider.py` | 0 spec scenarios (2 additional tests) | MODIFY |
| `tests/interfaces/test_snapshot_provider.py` | 0 spec scenarios (1 additional test) | MODIFY |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/config/test_model.py` | Add `test_global_config_preserve_min_defaults_none`, `test_global_config_preserve_min_set_from_constructor`, `test_target_config_full_every_defaults_zero_d`, `test_target_config_full_compress_defaults_false`, `test_vm_config_preserve_min_fields_exist` | Spec `preserve-min-config` scenarios "Default preserve_min absent", "Global preserve_min specified"; spec `config-model` scenario "Defaults are None"; spec `periodic-full-backup` scenario "full_every disabled by default". New fields `snapshot_preserve_min`, `target_preserve_min` on `GlobalConfig`/`VMConfig`, and `full_every`, `full_compress` on `TargetConfig` need constructor/default verification. |
| `tests/config/test_facade.py` | Add `test_vm_inherits_global_snapshot_preserve_min`, `test_vm_overrides_global_snapshot_preserve_min`, `test_target_inherits_vm_target_preserve_min`, `test_target_overrides_vm_target_preserve_min`, `test_facade_parses_target_full_every`, `test_facade_parses_target_full_compress` | Spec `preserve-min-config` scenarios for global→VM→target inheritance of `preserve_min`; spec `config-model` scenarios "VM inherits from global", "Target inherits from VM"; spec `periodic-full-backup` scenario "full_every set to 7 days". `ConfigFacade._build_vm()` and `_build_target()` must resolve the new keys through the same inheritance chain as existing `snapshot_preserve`/`target_preserve`. |
| `tests/core/test_preserve.py` | Add `test_parse_preserve_explicit_min_overrides_default`, `test_parse_preserve_none_uses_default_zero_h`, `test_evaluate_snapshot_retention_uses_vm_preserve_min`, `test_evaluate_backup_retention_uses_target_preserve_min` | Spec `preserve-min-config` scenarios "Explicit preserve_min overrides default", "No preserve_min uses existing default", "Snapshot retention uses VM preserve_min"; spec `core-orchestrator` scenarios "Snapshot retention with preserve_min", "Backup retention with preserve_min". `Core._parse_preserve()` gains optional `preserve_min_str` parameter; `_evaluate_snapshot_retention()` and `_evaluate_backup_retention()` must pass the per-VM/per-target values. |
| `tests/core/test_pipeline.py` | Add `test_backup_target_first_run_creates_full_backup`, `test_backup_target_interval_not_elapsed_skips_full` | Spec `periodic-full-backup` and `core-orchestrator` scenarios "First run creates full backup", "Interval not elapsed skips full backup". `Core._backup_target()` must check `IStateManager.get_last_full_backup()` before the incremental transfer loop and invoke `create_full_backup()` when the `full_every` interval has elapsed. |
| `tests/core/test_schedule_summary.py` | Create new file with `test_schedule_summary_empty_state_produces_simulation`, `test_schedule_summary_logs_info_on_timer`, `test_schedule_summary_shows_snapshot_and_backup_breakdown`, `test_schedule_summary_includes_all_vms`, `test_schedule_summary_filters_by_vm_name` | Spec `schedule-summary` scenarios "Empty state produces meaningful simulation", "Summary logs at INFO on every timer invocation", "Summary shows snapshot and backup breakdown"; spec `core-orchestrator` scenarios "Summary includes all VMs when no filter", "Summary filters by VM name". New `Core.schedule_summary()` method simulates retention against synthetic timestamps and returns a formatted string. |
| `tests/modules/snapshot/test_external.py` | Add `test_create_snapshot_returns_content_hash`, `test_create_snapshot_failure_content_hash_none` | Spec `backup-hash-verification` scenarios "Hash present for newly created snapshot", "Hash is None on creation failure". `ExternalSnapshotProvider.create()` must compute SHA-256 of the created qcow2 file (8MB chunks) and return it in `SnapshotResult.content_hash`; on any failure, `content_hash` must be `None`. |
| `tests/modules/backup/test_copy.py` | Add `test_create_full_backup_uncompressed`, `test_create_full_backup_compressed`, `test_transfer_missing_rebases_to_full_anchor`, `test_transfer_missing_no_full_anchor_uses_source_backing` | Spec `periodic-full-backup` and `backup-provider` scenarios for `create_full_backup` (qemu-img convert with/without `-c`) and `transfer_missing` rebase to FULL anchor. New `create_full_backup()` method on `FileCopyBackupProvider`; `transfer_missing()` must detect `vm.FULL.*.qcow2` and rebase incrementals to the anchor. |
| `tests/modules/backup/test_verification.py` | Add `test_hash_verification_match_passes`, `test_hash_verification_mismatch_fails`, `test_hash_verification_skipped_when_no_expected_hash`, `test_file_sha256_computes_hash`, `test_metadata_mode_unchanged_after_hash_addition` | Spec `backup-hash-verification` and `backup-verification` scenarios for `verify="hash"` mode. `verify_backup()` gains `expected_hash` parameter; new `_file_sha256()` module-level function. Existing `"metadata"` and `"full"` behavior must remain unchanged. |
| `tests/modules/retention/test_time_based.py` | Add `test_explain_returns_per_bucket_counts`, `test_explain_is_pure_function` | Spec `schedule-summary` scenarios "explain returns per-bucket counts", "explain is a pure function". New `TimeBasedRetention.explain()` method returns a dict mapping bucket names to `{"count": N, "range": (start, end)}`. Must be deterministic (pure function, same as `evaluate()`). |
| `tests/state/test_manager.py` | Add `test_record_snapshot_with_content_hash_restored`, `test_snapshot_content_hash_persists_across_runs`, `test_set_and_get_last_full_backup`, `test_full_backup_state_saved_and_retrieved`, `test_get_last_full_backup_returns_none_when_empty` | Spec `backup-hash-verification` scenario "Hash stored and restored from state"; spec `state-management` scenarios "Hash persists across runs", "Full backup state saved and retrieved", "No full backup returns None"; spec `periodic-full-backup` scenario "Full backup timestamp saved and restored". `JsonStateManager` must persist/restore `SnapshotInfo.content_hash` and implement `get_last_full_backup`/`set_last_full_backup` under the `"target_full_backups"` JSON key. |
| `tests/cli/test_app.py` | Add `test_timer_flag_parsed`, `test_timer_flag_defaults_false`, `test_print_schedule_short_flag_S_parsed` | Spec `cli-interface` scenario "Timer invocation logs summary". New `--timer` flag must be added to the argparser on action subcommands (run, snapshot, backup, prune). The `-S` short flag for `--print-schedule` already exists but needs an explicit parsing test. |
| `tests/cli/test_commands.py` | Modify `test_print_schedule_flag_dispatches_to_core_print_schedule` to verify `core.schedule_summary()` is called instead of `core.print_schedule()`. Add `test_print_schedule_with_run_prints_before_pipeline`, `test_standalone_print_schedule_exits_without_snapshots`, `test_timer_invocation_logs_schedule_at_info` | Spec `cli-interface` scenarios "--print-schedule with qsnap run", "Standalone --print-schedule", "Timer invocation logs summary". CLI `--print-schedule` now calls `Core.schedule_summary()` (returns str) instead of `Core.print_schedule()` (returns dict). Standalone `--print-schedule` (without `--dry-run`) must exit without running the pipeline. `--timer` flag triggers INFO-level logging of the schedule summary. |
| `tests/models/test_results.py` | Add `test_snapshot_result_content_hash_defaults_none`, `test_snapshot_result_content_hash_set`, `test_snapshot_info_content_hash_defaults_none`, `test_full_backup_info_dataclass_fields_and_frozen` | Spec `backup-hash-verification` requirement "SnapshotResult carries content_hash"; spec `state-management` requirement "IStateManager tracks SnapshotInfo content_hash". New `content_hash: str \| None = None` field on `SnapshotResult` and `SnapshotInfo`; new `FullBackupInfo` frozen dataclass with `name: str` and `timestamp: datetime` fields. |
| `tests/mocks/test_mock_state.py` | Add `test_inmemory_state_manager_full_backup_methods`, `test_inmemory_state_manager_content_hash_persists` | `IStateManager` ABC gains `get_last_full_backup` and `set_last_full_backup` abstract methods; `InMemoryStateManager` must implement them. `record_snapshot` must preserve `SnapshotInfo.content_hash` through the in-memory dict. |
| `tests/mocks/test_mock_factory.py` | Add `test_mock_backup_provider_has_create_full_backup`, `test_mock_snapshot_provider_returns_content_hash` | `IBackupProvider` ABC gains `create_full_backup` abstract method; `MockBackupProvider` must implement it and return a valid `BackupResult`. `MockSnapshotProvider.create()` must return a `SnapshotResult` with `content_hash` field populated. |
| `tests/interfaces/test_state_manager.py` | Add `test_istate_manager_full_backup_methods_abstract` | `IStateManager.__abstractmethods__` must include `get_last_full_backup` and `set_last_full_backup` so every concrete implementation is forced to provide them. |
| `tests/interfaces/test_backup_provider.py` | Add `test_ibackup_provider_create_full_backup_abstract`, `test_backup_provider_create_full_backup_returns_backup_result` | `IBackupProvider.__abstractmethods__` must include `create_full_backup`. Contract test parametrized over `FileCopyBackupProvider`, `MockBackupProvider` verifying `create_full_backup()` returns a `BackupResult`. |
| `tests/interfaces/test_snapshot_provider.py` | Add `test_snapshot_provider_create_returns_content_hash` | Contract test parametrized over `ExternalSnapshotProvider` and `MockSnapshotProvider` verifying that `SnapshotResult.content_hash` field exists and is either `None` or a 64-char hex string. |
| `tests/conftest.py` | Update `make_vm_config` to pass through `snapshot_preserve_min` and `target_preserve_min` kwargs. Update `make_target` to pass through `target_preserve_min`, `full_every`, `full_compress` kwargs. Update `make_global_config` to pass through `snapshot_preserve_min` and `target_preserve_min` kwargs. | All factory fixtures use `**kwargs` forwarding to the dataclass constructor, so the new fields will be accepted automatically once the dataclasses have them. However, `make_global_config` has explicit parameters and must be updated to forward the new fields. |
| `tests/mocks/mock_state.py` | Add `get_last_full_backup` and `set_last_full_backup` methods. Update `record_snapshot` to preserve `content_hash` from `SnapshotInfo`. | `IStateManager` ABC gains these abstract methods; `InMemoryStateManager` must implement them or fail `isinstance` checks. |
| `tests/mocks/mock_modules.py` | Add `create_full_backup` method to `MockBackupProvider` and `MockBitmapBackupProvider`. Update `MockSnapshotProvider.create()` to return `SnapshotResult` with `content_hash` field. | `IBackupProvider` ABC gains `create_full_backup`; all mock implementations must satisfy the expanded contract. `SnapshotResult` gains `content_hash` field; mock must populate it. |
| `tests/fixtures/configs/preserve_min.toml` | Create new TOML fixture with `snapshot_preserve_min` and `target_preserve_min` at global, VM-override, and target-override levels. | ConfigFacade inheritance tests need a fixture exercising all three levels of `preserve_min` inheritance, mirroring the existing `inheritance.toml` pattern. |
| `tests/fixtures/configs/full_backup.toml` | Create new TOML fixture with `full_every = "7d"` and `full_compress = true` under `[[vm.target]]`. | ConfigFacade parsing tests for `full_every` and `full_compress` need a TOML fixture with these keys set. |

## Risks & Edge Cases

- **preserve_min="0h" with empty time-bucket policy — all snapshots removed immediately (Data loss)** → `test_preserve_min_zero_h_with_empty_buckets_removes_all_except_now` in `tests/core/test_preserve.py`: construct a `RetentionPolicy(hourly=0, daily=0, weekly=0, monthly=0, yearly=0, preserve_min="0h")`, feed 10 items spanning 10 hours, and assert that only the single item at `now` survives. Additionally, `test_schedule_summary_warns_when_total_kept_zero` in `tests/core/test_schedule_summary.py`: verify that `schedule_summary()` output includes a warning string when the simulated retention keeps zero items.

- **SHA-256 of multi-GB overlay on slow disk — snapshot creation stalls for 30-60s (Pipeline latency)** → `test_file_sha256_reads_in_8mb_chunks` in `tests/modules/backup/test_verification.py`: verify that `_file_sha256()` uses an 8MB read buffer (not `file.read()` without size) by mocking `open` and asserting `read(8 * 1024 * 1024)` is called. Additionally, `test_create_snapshot_content_hash_computed_only_when_needed` in `tests/modules/snapshot/test_external.py`: verify that the hash computation step is only invoked when at least one target uses `verify="hash"` (lazy computation per design D3 rationale).

- **qemu-img convert fails mid-transfer — partial FULL file on target, no anchor (Next cycle retries)** → `test_create_full_backup_convert_failure_returns_failure_result` in `tests/modules/backup/test_copy.py`: configure `MockShell` so `qemu-img convert` returns failure, then assert `BackupResult(success=False)` is returned and no `vm.FULL.*.qcow2` file exists on the target (atomicity: convert to `.tmp`, rename on success). Additionally, `test_create_full_backup_atomic_temp_file` in `tests/modules/backup/test_copy.py`: verify the convert command writes to a `.tmp` path and only renames to the final name on success (spy on shell commands for `.tmp` suffix).

- **FULL + new incrementals uses MORE space than old chain temporarily (Target disk pressure during transition)** → `test_backup_target_creates_full_before_deleting_old_chain` in `tests/core/test_pipeline.py`: verify that `Core._backup_target()` calls `create_full_backup()` BEFORE any `provider.delete()` calls — the old chain must survive until the FULL is created. Use `MockVMModuleFactory` and spy on method call ordering.

- **FULL file naming collision — two FULLs on same day (Second FULL overwrites first)** → `test_create_full_backup_same_day_appends_suffix` in `tests/modules/backup/test_copy.py`: pre-create a `vm.FULL.20250714.qcow2` file in the target directory, then call `create_full_backup()` and verify the new file is named `vm.FULL.20250714_1.qcow2` (collision suffix `_N`, same pattern as snapshot naming in `Core._generate_snapshot_name`).
