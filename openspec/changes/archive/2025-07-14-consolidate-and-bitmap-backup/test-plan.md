# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `bitmap-backup-provider` | `BitmapBackupProvider implements IBackupProvider` | `Constructor accepts IShell` | `tests/modules/backup/test_bitmap.py` | `test_constructor_accepts_ishell_and_implements_abc` | `snapshot-bitmap-unit` |
| `bitmap-backup-provider` | `Transfer missing snapshots via dirty bitmap extraction` | `First backup — full copy (no prior checkpoint)` | `tests/modules/backup/test_bitmap.py` | `test_first_backup_full_copy_no_prior_checkpoint` | `snapshot-bitmap-unit` |
| `bitmap-backup-provider` | `Transfer missing snapshots via dirty bitmap extraction` | `Incremental backup — dirty blocks only` | `tests/modules/backup/test_bitmap.py` | `test_incremental_backup_extracts_dirty_blocks_only` | `snapshot-bitmap-unit` |
| `bitmap-backup-provider` | `Transfer missing snapshots via dirty bitmap extraction` | `Checkpoint cleanup after successful transfer` | `tests/modules/backup/test_bitmap.py` | `test_checkpoint_cleanup_after_successful_transfer` | `snapshot-bitmap-unit` |
| `bitmap-backup-provider` | `Transfer missing snapshots via dirty bitmap extraction` | `Transfer failure preserves checkpoint` | `tests/modules/backup/test_bitmap.py` | `test_transfer_failure_preserves_checkpoint` | `snapshot-bitmap-unit` |
| `bitmap-backup-provider` | `Rebase error handling in FileCopyBackupProvider` | `Rebase fails due to invalid backing path` | `tests/modules/backup/test_copy.py` | `test_transfer_rebase_failure_returns_backup_result_failure` | `snapshot-bitmap-unit` |
| `bitmap-backup-provider` | `List checkpoints for target` | `Existing qsnap checkpoints found` | `tests/modules/backup/test_bitmap.py` | `test_list_checkpoints_filters_qsnap_prefix` | `snapshot-bitmap-unit` |
| `bitmap-backup-provider` | `Factory selects BitmapBackupProvider for bitmap mode` | `Bitmap mode selected via TargetConfig` | `tests/factory/test_default.py` | `test_factory_selects_bitmap_provider_for_bitmap_mode` | `config-parsing-unit` |
| `bitmap-backup-provider` | `Factory selects BitmapBackupProvider for bitmap mode` | `File-copy mode is the default` | `tests/factory/test_default.py` | `test_factory_selects_file_copy_provider_for_default_mode` | `config-parsing-unit` |
| `restore-command` | `Restore command copies backup chain to target directory` | `Restore a file-copy backup chain` | `tests/cli/test_commands.py` | `test_handle_restore_dispatches_to_core_restore_with_args` | `cli-unit` |
| `restore-command` | `Restore command copies backup chain to target directory` | `Restore a nonexistent backup` | `tests/cli/test_commands.py` | `test_handle_restore_nonexistent_backup_returns_exit_1` | `cli-unit` |
| `restore-command` | `Restore command copies backup chain to target directory` | `Target directory does not exist` | `tests/cli/test_commands.py` | `test_handle_restore_missing_target_dir_returns_exit_1` | `cli-unit` |
| `restore-command` | `Core.restore method` | `Restore from snapshot` | `tests/core/test_engine.py` | `test_core_restore_from_snapshot_returns_restore_result` | `core-orchestrator-unit` |
| `restore-command` | `Core.restore method` | `Restore from backup` | `tests/core/test_engine.py` | `test_core_restore_from_backup_returns_restore_result` | `core-orchestrator-unit` |
| `restore-command` | `RestoreResult type` | `Successful restore result` | `tests/models/test_results.py` | `test_restore_result_success_fields_and_frozen` | `core-orchestrator-unit` |
| `parsing-utils` | `Shared domblklist path parser` | `Parse domblklist output with one disk` | `tests/utils/test_parsing.py` | `test_parse_domblklist_path_one_disk` | `snapshot-bitmap-unit` |
| `parsing-utils` | `Shared domblklist path parser` | `Parse domblklist with multiple lines` | `tests/utils/test_parsing.py` | `test_parse_domblklist_path_multiple_lines_skips_header` | `snapshot-bitmap-unit` |
| `parsing-utils` | `Shared domblklist path parser` | `Parse empty domblklist output` | `tests/utils/test_parsing.py` | `test_parse_domblklist_path_empty_raises_value_error` | `snapshot-bitmap-unit` |
| `parsing-utils` | `Shared domblklist target parser` | `Parse target name from domblklist` | `tests/utils/test_parsing.py` | `test_parse_domblklist_target_returns_target_name` | `snapshot-bitmap-unit` |
| `parsing-utils` | `Shared domblklist all-disks parser` | `Parse multi-disk domblklist output` | `tests/utils/test_parsing.py` | `test_parse_domblklist_disks_returns_all_disks` | `snapshot-bitmap-unit` |
| `parsing-utils` | `Shared timestamp parser` | `Parse long-format timestamp from filename` | `tests/utils/test_parsing.py` | `test_parse_timestamp_long_format_from_filename` | `snapshot-bitmap-unit` |
| `parsing-utils` | `Shared timestamp parser` | `Fall back to file mtime` | `tests/utils/test_parsing.py` | `test_parse_timestamp_falls_back_to_mtime` | `snapshot-bitmap-unit` |
| `parsing-utils` | `Modules use shared parsers` | `ExternalSnapshotProvider uses shared parser` | `tests/modules/snapshot/test_external.py` | `test_external_snapshot_provider_imports_shared_parsers` | `snapshot-bitmap-unit` |
| `config-model` | `TargetConfig incremental_mode field` | `Default incremental_mode is file-copy` | `tests/config/test_model.py` | `test_target_config_default_incremental_mode_is_file_copy` | `config-parsing-unit` |
| `config-model` | `TargetConfig incremental_mode field` | `Explicit bitmap mode` | `tests/config/test_model.py` | `test_target_config_explicit_incremental_mode_bitmap` | `config-parsing-unit` |
| `config-model` | `VMConfig disks field` | `Disks list is None — auto-discovery` | `tests/config/test_model.py` | `test_vm_config_disks_default_none_auto_discovery` | `config-parsing-unit` |
| `config-model` | `VMConfig disks field` | `Explicit disk list` | `tests/config/test_model.py` | `test_vm_config_explicit_disks_list` | `config-parsing-unit` |
| `core-orchestrator` | `Dynamic disk resolution in snapshot creation` | `VM with a single disk named sda` | `tests/core/test_pipeline.py` | `test_create_snapshot_single_disk_sda_not_vda` | `core-orchestrator-unit` |
| `core-orchestrator` | `Dynamic disk resolution in snapshot creation` | `VM with multiple disks (vda, vdb)` | `tests/core/test_pipeline.py` | `test_create_snapshot_multi_disk_vda_vdb_creates_two_with_suffix` | `core-orchestrator-unit` |
| `core-orchestrator` | `Dynamic disk resolution in snapshot creation` | `Explicit disk list in config overrides auto-discovery` | `tests/core/test_pipeline.py` | `test_create_snapshot_explicit_disk_list_overrides_discovery` | `core-orchestrator-unit` |
| `core-orchestrator` | `Multi-disk snapshot result collection` | `vda succeeds, vdb fails` | `tests/core/test_pipeline.py` | `test_multi_disk_vda_succeeds_vdb_fails_continues_pipeline` | `core-orchestrator-unit` |
| `core-orchestrator` | `Backup retention in print_schedule` | `Schedule shows snapshot and backup decisions` | `tests/core/test_list_commands.py` | `test_print_schedule_shows_snapshot_and_backup_retention` | `core-orchestrator-unit` |
| `core-orchestrator` | `check --deep via qemu-img check` | `Deep check finds corruption` | `tests/core/test_list_commands.py` | `test_check_deep_finds_corruption_reports_broken` | `core-orchestrator-unit` |
| `core-orchestrator` | `check --deep via qemu-img check` | `Deep check on clean image` | `tests/core/test_list_commands.py` | `test_check_deep_clean_image_reports_ok` | `core-orchestrator-unit` |
| `core-orchestrator` | `EXIT_BACKUP_ABORT wired into PipelineResult` | `Backup abort exit code` | `tests/core/test_engine.py` | `test_pipeline_backup_abort_returns_exit_code_10` | `core-orchestrator-unit` |
| `core-orchestrator` | `EXIT_BACKUP_ABORT wired into PipelineResult` | `All backups succeed` | `tests/core/test_engine.py` | `test_pipeline_all_backups_succeed_exit_code_not_10` | `core-orchestrator-unit` |
| `core-orchestrator` | `snapshot_create ondemand support` | `Ondemand with reachable target` | `tests/core/test_engine.py` | `test_ondemand_snapshot_created_when_target_reachable` | `core-orchestrator-unit` |
| `core-orchestrator` | `snapshot_create ondemand support` | `Ondemand with no reachable targets` | `tests/core/test_engine.py` | `test_ondemand_snapshot_skipped_when_no_target_reachable` | `core-orchestrator-unit` |
| `change-detection` | `Per-disk change detection` | `Per-disk change detection for vdb` | `tests/modules/change/test_allocation.py` | `test_has_changed_per_disk_vdb_uses_vdb_path` | `snapshot-bitmap-unit` |
| `change-detection` | `Per-disk change detection` | `Backward-compatible no-disk call` | `tests/modules/change/test_allocation.py` | `test_has_changed_no_disk_uses_first_disk_backward_compatible` | `snapshot-bitmap-unit` |
| `cli-interface` | `qsnap restore subcommand` | `Restore command invocation` | `tests/cli/test_commands.py` | `test_handle_restore_dispatches_to_core_restore_with_positional_args` | `cli-unit` |
| `cli-interface` | `qsnap check --deep flag` | `Deep check invocation` | `tests/cli/test_commands.py` | `test_handle_check_deep_passes_deep_true_to_core` | `cli-unit` |
| `cli-interface` | `qsnap check --deep flag` | `Default check without --deep` | `tests/cli/test_commands.py` | `test_handle_check_without_deep_passes_deep_false_to_core` | `cli-unit` |

### Risk-Mitigation & Infrastructure Tests

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `bitmap-backup-provider` | `QEMU version check (Risk: QEMU < 5.1 incompatible)` | `Constructor rejects unsupported QEMU version` | `tests/modules/backup/test_bitmap.py` | `test_constructor_rejects_unsupported_qemu_version` | `snapshot-bitmap-unit` |
| `bitmap-backup-provider` | `Factory fallback (Risk: QEMU < 5.1 incompatible)` | `DefaultFactory falls back to FileCopyBackupProvider on old QEMU` | `tests/factory/test_default.py` | `test_factory_falls_back_to_file_copy_on_old_qemu` | `config-parsing-unit` |
| `restore-command` | `Restore from bitmap backup (Risk: standalone files, no backing chain)` | `Core.restore from bitmap backup produces standalone file` | `tests/core/test_engine.py` | `test_core_restore_from_bitmap_backup_standalone_file` | `core-orchestrator-unit` |
| `core-orchestrator` | `VMRunResult.backup_failed field (R5 infrastructure)` | `VMRunResult with backup_failed=True is frozen and carries field` | `tests/models/test_results.py` | `test_vm_run_result_backup_failed_field` | `core-orchestrator-unit` |

### Contract Test Parametrization Updates

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `bitmap-backup-provider` | `BitmapBackupProvider contract: transfer_missing` | `BitmapBackupProvider in transfer_missing parametrization` | `tests/interfaces/test_backup_provider.py` | `test_backup_provider_transfer_missing_returns_list_of_backup_result[bitmap]` | `contracts-mocks` |
| `bitmap-backup-provider` | `BitmapBackupProvider contract: list` | `BitmapBackupProvider in list parametrization` | `tests/interfaces/test_backup_provider.py` | `test_backup_provider_list_returns_list_of_snapshotinfo[bitmap]` | `contracts-mocks` |
| `bitmap-backup-provider` | `BitmapBackupProvider contract: delete` | `BitmapBackupProvider in delete parametrization` | `tests/interfaces/test_backup_provider.py` | `test_backup_provider_delete_returns_shellresult[bitmap]` | `contracts-mocks` |
| `bitmap-backup-provider` | `BitmapBackupProvider no Core inheritance` | `BitmapBackupProvider does not inherit from Core` | `tests/interfaces/test_backup_provider.py` | `test_bitmap_backup_provider_no_core_inheritance` | `contracts-mocks` |
| `change-detection` | `IChangeDetector contract: disk parameter` | `has_changed accepts optional disk parameter` | `tests/interfaces/test_change_detector.py` | `test_change_detector_has_changed_accepts_disk_parameter` | `contracts-mocks` |

### Mock & Fixture Infrastructure Tests

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `bitmap-backup-provider` | `MockBitmapBackupProvider implements IBackupProvider` | `Mock returns valid BackupResult list` | `tests/mocks/mock_modules.py` | `MockBitmapBackupProvider` (verified by `tests/mocks/test_mock_factory.py`) | `contracts-mocks` |
| `bitmap-backup-provider` | `MockVMModuleFactory creates bitmap provider` | `create_backup_provider returns bitmap mock for bitmap target` | `tests/mocks/mock_factory.py` | `MockVMModuleFactory._bitmap_backup_provider` (verified by `tests/mocks/test_mock_factory.py`) | `contracts-mocks` |
| `parsing-utils` | `AllocationSizeDetector imports shared parser` | `Module imports parse_domblklist_path from qsnap.utils.parsing` | `tests/modules/change/test_allocation.py` | `test_allocation_detector_imports_shared_parsers` | `snapshot-bitmap-unit` |
| `parsing-utils` | `FileCopyBackupProvider imports shared parser` | `Module imports parse_timestamp from qsnap.utils.parsing` | `tests/modules/backup/test_copy.py` | `test_file_copy_provider_imports_shared_parsers` | `snapshot-bitmap-unit` |
| `cli-interface` | `Restore subcommand in argparser` | `qsnap restore SNAP TARGET parses positional args` | `tests/cli/test_app.py` | `test_restore_subcommand_parses_positional_args` | `cli-unit` |
| `cli-interface` | `Check --deep flag in argparser` | `qsnap check --deep sets deep=True` | `tests/cli/test_app.py` | `test_check_deep_flag_sets_deep_true` | `cli-unit` |
| `cli-interface` | `Check without --deep in argparser` | `qsnap check defaults deep=False` | `tests/cli/test_app.py` | `test_check_without_deep_defaults_false` | `cli-unit` |
| `core-orchestrator` | `daily_set.json fixture validation` | `Fixture has 28 items spanning 14 days` | `tests/fixtures/timestamps/daily_set.json` | `test_daily_set_fixture_has_28_items` (in `tests/modules/retention/test_time_based.py`) | `fixtures-missing` |
| `core-orchestrator` | `mixed_set.json fixture validation` | `Fixture has 19 items spanning 8 days` | `tests/fixtures/timestamps/mixed_set.json` | `test_mixed_set_fixture_has_19_items` (in `tests/modules/retention/test_time_based.py`) | `fixtures-missing` |

---

## Delegation Groups

### Group: snapshot-bitmap-unit
**Scope:** `tests/modules/backup/test_bitmap.py`, `tests/modules/backup/test_copy.py`, `tests/modules/snapshot/test_external.py`, `tests/utils/test_parsing.py`, `tests/modules/change/test_allocation.py`

**New files:**
- `tests/modules/backup/test_bitmap.py` — BitmapBackupProvider unit tests (constructor, first backup, incremental, checkpoint cleanup, transfer failure, list checkpoints, QEMU version check)
- `tests/utils/test_parsing.py` — Shared parsing utility tests (parse_domblklist_path, parse_domblklist_target, parse_domblklist_disks, parse_timestamp)

**Modified files:**
- `tests/modules/backup/test_copy.py` — Add `test_transfer_rebase_failure_returns_backup_result_failure` (rebase error surfaces as BackupResult failure), add `test_file_copy_provider_imports_shared_parsers` (import verification)
- `tests/modules/snapshot/test_external.py` — Add `test_external_snapshot_provider_imports_shared_parsers` (verify imports from `qsnap.utils.parsing`, no module-level `_parse_*` functions remain)
- `tests/modules/change/test_allocation.py` — Add `test_has_changed_per_disk_vdb_uses_vdb_path` (per-disk change detection for vdb), `test_has_changed_no_disk_uses_first_disk_backward_compatible` (backward-compatible no-disk call), `test_allocation_detector_imports_shared_parsers` (import verification)

**Spec coverage:** `bitmap-backup-provider` (R1–R4), `parsing-utils` (R1–R5), `change-detection` (R1)

**Dependencies:** Requires `qsnap/modules/backup/bitmap.py` and `qsnap/utils/parsing.py` to exist in source. All tests use `MockShell` — zero real I/O.

---

### Group: core-orchestrator-unit
**Scope:** `tests/core/test_engine.py`, `tests/core/test_pipeline.py`, `tests/core/test_list_commands.py`, `tests/models/test_results.py`

**Modified files:**
- `tests/core/test_engine.py` — Add `test_core_restore_from_snapshot_returns_restore_result` (Core.restore from snapshot), `test_core_restore_from_backup_returns_restore_result` (Core.restore from backup), `test_core_restore_from_bitmap_backup_standalone_file` (restore from bitmap backup — risk mitigation), `test_pipeline_backup_abort_returns_exit_code_10` (EXIT_BACKUP_ABORT wired), `test_pipeline_all_backups_succeed_exit_code_not_10` (all backups succeed — exit code not 10), `test_ondemand_snapshot_created_when_target_reachable` (ondemand with reachable target), `test_ondemand_snapshot_skipped_when_no_target_reachable` (ondemand with no reachable targets)
- `tests/core/test_pipeline.py` — Add `test_create_snapshot_single_disk_sda_not_vda` (dynamic disk resolution — sda), `test_create_snapshot_multi_disk_vda_vdb_creates_two_with_suffix` (multi-disk — vda+vdb with `_vda`/`_vdb` suffix), `test_create_snapshot_explicit_disk_list_overrides_discovery` (explicit override), `test_multi_disk_vda_succeeds_vdb_fails_continues_pipeline` (partial failure — vda succeeds, vdb fails)
- `tests/core/test_list_commands.py` — Add `test_print_schedule_shows_snapshot_and_backup_retention` (backup retention in print_schedule), `test_check_deep_finds_corruption_reports_broken` (deep check — corruption found), `test_check_deep_clean_image_reports_ok` (deep check — clean image)
- `tests/models/test_results.py` — Add `test_restore_result_success_fields_and_frozen` (RestoreResult dataclass), `test_vm_run_result_backup_failed_field` (VMRunResult.backup_failed field)

**Spec coverage:** `restore-command` (R2–R3), `core-orchestrator` (R1–R6)

**Dependencies:** Requires updated `Core` class with `restore()`, `check(deep=)`, ondemand support, EXIT_BACKUP_ABORT wiring. All tests use `MockVMModuleFactory` — zero real virsh/qemu-img calls.

---

### Group: config-parsing-unit
**Scope:** `tests/config/test_model.py`, `tests/factory/test_default.py`

**Modified files:**
- `tests/config/test_model.py` — Add `test_target_config_default_incremental_mode_is_file_copy` (default incremental_mode), `test_target_config_explicit_incremental_mode_bitmap` (explicit bitmap mode), `test_vm_config_disks_default_none_auto_discovery` (disks None — auto-discovery), `test_vm_config_explicit_disks_list` (explicit disk list)
- `tests/factory/test_default.py` — Add `test_factory_selects_bitmap_provider_for_bitmap_mode` (bitmap mode → BitmapBackupProvider), `test_factory_selects_file_copy_provider_for_default_mode` (default → FileCopyBackupProvider), `test_factory_falls_back_to_file_copy_on_old_qemu` (QEMU version fallback)

**Spec coverage:** `config-model` (R1–R2), `bitmap-backup-provider` (R5)

**Dependencies:** Requires `TargetConfig.incremental_mode` and `VMConfig.disks` fields in `qsnap/models/config.py`. Requires `DefaultFactory` branch for bitmap mode in `qsnap/factory/default.py`.

---

### Group: cli-unit
**Scope:** `tests/cli/test_commands.py`, `tests/cli/test_app.py`

**Modified files:**
- `tests/cli/test_commands.py` — Add `test_handle_restore_dispatches_to_core_restore_with_positional_args` (restore handler dispatch), `test_handle_restore_nonexistent_backup_returns_exit_1` (nonexistent backup), `test_handle_restore_missing_target_dir_returns_exit_1` (missing target dir), `test_handle_check_deep_passes_deep_true_to_core` (check --deep handler), `test_handle_check_without_deep_passes_deep_false_to_core` (check without --deep handler)
- `tests/cli/test_app.py` — Add `test_restore_subcommand_parses_positional_args` (restore subcommand in argparser), `test_check_deep_flag_sets_deep_true` (--deep flag parsing), `test_check_without_deep_defaults_false` (default deep=False)

**Spec coverage:** `restore-command` (R1), `cli-interface` (R1–R2)

**Dependencies:** Requires `handle_restore` function in `qsnap/cli/commands.py`, `restore` subparser and `--deep` flag in `qsnap/cli/app.py`. Tests use `Mock` Core — no real config or shell.

---

### Group: contracts-mocks
**Scope:** `tests/interfaces/test_backup_provider.py`, `tests/interfaces/test_change_detector.py`, `tests/mocks/mock_modules.py`, `tests/mocks/mock_factory.py`

**Modified files:**
- `tests/interfaces/test_backup_provider.py` — Add `BitmapBackupProvider` to parametrization in `test_backup_provider_transfer_missing_returns_list_of_backup_result`, `test_backup_provider_list_returns_list_of_snapshotinfo`, `test_backup_provider_delete_returns_shellresult`. Add `test_bitmap_backup_provider_no_core_inheritance` (D1 verification). Add `test_bitmap_backup_provider_is_ibackup_provider` (issubclass check).
- `tests/interfaces/test_change_detector.py` — Add `test_change_detector_has_changed_accepts_disk_parameter` (verify `has_changed()` accepts optional `disk: str` parameter). Update existing parametrized contract test to pass `disk="vda"` to verify the new signature works for all implementations.
- `tests/mocks/mock_modules.py` — Add `MockBitmapBackupProvider` class implementing `IBackupProvider` with standalone-file semantics (returns `BackupResult` with `target_path` pointing to a standalone qcow2, no backing chain).
- `tests/mocks/mock_factory.py` — Add `_bitmap_backup_provider` instance and update `create_backup_provider` to return it when `target.incremental_mode == "bitmap"`.

**Spec coverage:** `bitmap-backup-provider` (R1 contract), `change-detection` (R1 contract)

**Dependencies:** Requires `BitmapBackupProvider` class in `qsnap/modules/backup/bitmap.py`. Requires `IChangeDetector.has_changed()` signature update with optional `disk` parameter. Mock tests verified by existing `tests/mocks/test_mock_factory.py`.

---

### Group: fixtures-missing
**Scope:** `tests/fixtures/timestamps/daily_set.json`, `tests/fixtures/timestamps/mixed_set.json`

**Status:** Files already exist on disk. Validation needed to confirm structure matches retention test expectations.

**Validation:**
- `daily_set.json` — 28 items, two per day (00:00 and 12:00), spanning 14 days from 2025-01-01 to 2025-01-14. Validated by `test_daily_retention_first_per_day` in `tests/modules/retention/test_time_based.py` (asserts `len(items) == 28`).
- `mixed_set.json` — 19 items with irregular intervals over 8 days from 2025-01-01 to 2025-01-08. Validated by `test_evaluate_is_deterministic` in `tests/modules/retention/test_time_based.py` (uses fixture for determinism + boundary tests).

**No new test files needed** — the existing retention tests in `tests/modules/retention/test_time_based.py` already load and validate these fixtures (assertions on item count and timestamp ranges). If the fixtures were missing, those tests would fail with `FileNotFoundError`. The fixtures are confirmed present and correctly structured.

**Spec coverage:** `core-orchestrator` (fixture support for retention tests referenced in proposal P1)

---

## Test Modifications

### New Test Files

| File | Tests | Group |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | `test_constructor_accepts_ishell_and_implements_abc`, `test_first_backup_full_copy_no_prior_checkpoint`, `test_incremental_backup_extracts_dirty_blocks_only`, `test_checkpoint_cleanup_after_successful_transfer`, `test_transfer_failure_preserves_checkpoint`, `test_list_checkpoints_filters_qsnap_prefix`, `test_constructor_rejects_unsupported_qemu_version` | `snapshot-bitmap-unit` |
| `tests/utils/test_parsing.py` | `test_parse_domblklist_path_one_disk`, `test_parse_domblklist_path_multiple_lines_skips_header`, `test_parse_domblklist_path_empty_raises_value_error`, `test_parse_domblklist_target_returns_target_name`, `test_parse_domblklist_disks_returns_all_disks`, `test_parse_timestamp_long_format_from_filename`, `test_parse_timestamp_falls_back_to_mtime` | `snapshot-bitmap-unit` |

### Modified Test Files

| File | New Tests Added | Group |
|---|---|---|
| `tests/modules/backup/test_copy.py` | `test_transfer_rebase_failure_returns_backup_result_failure`, `test_file_copy_provider_imports_shared_parsers` | `snapshot-bitmap-unit` |
| `tests/modules/snapshot/test_external.py` | `test_external_snapshot_provider_imports_shared_parsers` | `snapshot-bitmap-unit` |
| `tests/modules/change/test_allocation.py` | `test_has_changed_per_disk_vdb_uses_vdb_path`, `test_has_changed_no_disk_uses_first_disk_backward_compatible`, `test_allocation_detector_imports_shared_parsers` | `snapshot-bitmap-unit` |
| `tests/core/test_engine.py` | `test_core_restore_from_snapshot_returns_restore_result`, `test_core_restore_from_backup_returns_restore_result`, `test_core_restore_from_bitmap_backup_standalone_file`, `test_pipeline_backup_abort_returns_exit_code_10`, `test_pipeline_all_backups_succeed_exit_code_not_10`, `test_ondemand_snapshot_created_when_target_reachable`, `test_ondemand_snapshot_skipped_when_no_target_reachable` | `core-orchestrator-unit` |
| `tests/core/test_pipeline.py` | `test_create_snapshot_single_disk_sda_not_vda`, `test_create_snapshot_multi_disk_vda_vdb_creates_two_with_suffix`, `test_create_snapshot_explicit_disk_list_overrides_discovery`, `test_multi_disk_vda_succeeds_vdb_fails_continues_pipeline` | `core-orchestrator-unit` |
| `tests/core/test_list_commands.py` | `test_print_schedule_shows_snapshot_and_backup_retention`, `test_check_deep_finds_corruption_reports_broken`, `test_check_deep_clean_image_reports_ok` | `core-orchestrator-unit` |
| `tests/models/test_results.py` | `test_restore_result_success_fields_and_frozen`, `test_vm_run_result_backup_failed_field` | `core-orchestrator-unit` |
| `tests/config/test_model.py` | `test_target_config_default_incremental_mode_is_file_copy`, `test_target_config_explicit_incremental_mode_bitmap`, `test_vm_config_disks_default_none_auto_discovery`, `test_vm_config_explicit_disks_list` | `config-parsing-unit` |
| `tests/factory/test_default.py` | `test_factory_selects_bitmap_provider_for_bitmap_mode`, `test_factory_selects_file_copy_provider_for_default_mode`, `test_factory_falls_back_to_file_copy_on_old_qemu` | `config-parsing-unit` |
| `tests/cli/test_commands.py` | `test_handle_restore_dispatches_to_core_restore_with_positional_args`, `test_handle_restore_nonexistent_backup_returns_exit_1`, `test_handle_restore_missing_target_dir_returns_exit_1`, `test_handle_check_deep_passes_deep_true_to_core`, `test_handle_check_without_deep_passes_deep_false_to_core` | `cli-unit` |
| `tests/cli/test_app.py` | `test_restore_subcommand_parses_positional_args`, `test_check_deep_flag_sets_deep_true`, `test_check_without_deep_defaults_false` | `cli-unit` |
| `tests/interfaces/test_backup_provider.py` | Add `BitmapBackupProvider` to parametrization (3 existing parametrized tests get `bitmap` id), add `test_bitmap_backup_provider_no_core_inheritance`, add `test_bitmap_backup_provider_is_ibackup_provider` | `contracts-mocks` |
| `tests/interfaces/test_change_detector.py` | Add `test_change_detector_has_changed_accepts_disk_parameter`, update existing parametrized test to pass `disk="vda"` | `contracts-mocks` |
| `tests/mocks/mock_modules.py` | Add `MockBitmapBackupProvider` class | `contracts-mocks` |
| `tests/mocks/mock_factory.py` | Add `_bitmap_backup_provider` instance, update `create_backup_provider` to select based on `target.incremental_mode` | `contracts-mocks` |

### New Source Files Required (implementation dependencies)

| File | Purpose |
|---|---|
| `qsnap/modules/backup/bitmap.py` | `BitmapBackupProvider` implementing `IBackupProvider` via `virsh checkpoint-create-as` + `qemu-img convert --bitmap` |
| `qsnap/utils/parsing.py` | Shared `parse_domblklist_path`, `parse_domblklist_target`, `parse_domblklist_disks`, `parse_timestamp` functions |

### Modified Source Files Required

| File | Changes |
|---|---|
| `qsnap/models/config.py` | Add `incremental_mode: str = "file-copy"` to `TargetConfig`; add `disks: list[str] \| None = None` to `VMConfig` |
| `qsnap/models/results.py` | Add `RestoreResult` frozen dataclass; add `backup_failed: bool = False` to `VMRunResult` |
| `qsnap/core/__init__.py` | Add `Core.restore()` method; add `deep` parameter to `Core.check()`; wire `EXIT_BACKUP_ABORT`; implement ondemand; implement dynamic disk resolution in `_create_snapshot()`; extend `print_schedule()` with backup retention |
| `qsnap/factory/default.py` | Add bitmap mode branch in `create_backup_provider()`; add QEMU version fallback |
| `qsnap/modules/backup/file_copy.py` | Surface `qemu-img rebase` errors as `BackupResult(success=False)`; import from `qsnap.utils.parsing` |
| `qsnap/modules/snapshot/external.py` | Import from `qsnap.utils.parsing` instead of module-level `_parse_*` functions |
| `qsnap/modules/change/allocation_detector.py` | Add optional `disk` parameter to `has_changed()`; import from `qsnap.utils.parsing` |
| `qsnap/interfaces/change.py` | Add optional `disk: str` parameter to `IChangeDetector.has_changed()` signature |
| `qsnap/cli/app.py` | Add `restore` subparser; add `--deep` flag to `check` subparser |
| `qsnap/cli/commands.py` | Add `handle_restore` function; update `handle_check` to pass `deep` flag |
| `qsnap/cli/errors.py` | No changes needed (`EXIT_BACKUP_ABORT = 10` already defined) |

---

## Risks & Edge Cases

### Risk 1: Bitmap backup produces standalone files (no backing chains)

**Source:** `design.md` line 97
**Impact:** Restoring from a bitmap backup yields a point-in-time full image, not a chain of incremental diffs. Users who need chain-based restores must use `incremental_mode = "file-copy"`.
**Mitigation tests:**
- `test_core_restore_from_bitmap_backup_standalone_file` (`tests/core/test_engine.py`) — verifies that `Core.restore()` handles a standalone bitmap backup file (no backing chain to walk, single-file copy).
- `test_first_backup_full_copy_no_prior_checkpoint` (`tests/modules/backup/test_bitmap.py`) — verifies the first bitmap backup is a standalone qcow2 containing the complete virtual disk.
**Complexity:** MODERATE — restore logic must detect whether a backup has a backing chain (file-copy) or is standalone (bitmap) and handle both paths.

### Risk 2: QEMU < 5.1 incompatible with `qemu-img convert --bitmap`

**Source:** `design.md` line 101
**Impact:** On older systems, the bitmap path is unavailable. `qemu-img convert --bitmap` will fail with an unrecognized flag error.
**Mitigation tests:**
- `test_constructor_rejects_unsupported_qemu_version` (`tests/modules/backup/test_bitmap.py`) — verifies `BitmapBackupProvider` constructor checks `qemu-img --version` and returns a clear error if QEMU < 5.1.
- `test_factory_falls_back_to_file_copy_on_old_qemu` (`tests/factory/test_default.py`) — verifies `DefaultFactory` falls back to `FileCopyBackupProvider` with a warning when QEMU version is too old.
**Complexity:** EASY — version string parse + conditional branch in factory.

### Risk 3: Multi-disk partial failure (snapshot atomicity)

**Source:** `design.md` line 103
**Impact:** If snapshot creation succeeds for `vda` but fails for `vdb`, we have a partial snapshot state. Libvirt does not support cross-disk atomic snapshotting.
**Mitigation tests:**
- `test_multi_disk_vda_succeeds_vdb_fails_continues_pipeline` (`tests/core/test_pipeline.py`) — verifies that when `vda` succeeds and `vdb` fails, the `vda` snapshot is recorded in state, the `vdb` error is logged, and the pipeline continues to retention evaluation.
**Complexity:** MODERATE — Core must collect partial results, log warnings, and continue processing.

### Risk 4: Orphaned checkpoints from interrupted bitmap backups

**Source:** `design.md` line 105
**Impact:** If qsnap crashes between `checkpoint-create` and `checkpoint-delete`, the checkpoint remains in the qcow2 file consuming metadata space.
**Mitigation tests:**
- `test_transfer_failure_preserves_checkpoint` (`tests/modules/backup/test_bitmap.py`) — verifies that when `qemu-img convert` fails, the checkpoint is NOT deleted and `BackupResult(success=False)` is returned, allowing the next run to retry using the preserved checkpoint.
**Complexity:** MODERATE — the provider must track checkpoint lifecycle state and only delete after confirmed success.

### Risk 5: Checkpoint namespace collision

**Source:** `design.md` line 99
**Impact:** Multiple qsnap instances or manual `virsh checkpoint-create` could collide on the `qsnap-` prefix.
**Mitigation:** Checkpoint names include a target-path hash: `qsnap-{target_hash}-{timestamp}`. The `test_list_checkpoints_filters_qsnap_prefix` test verifies that only `qsnap-` prefixed checkpoints are returned, preventing accidental manipulation of non-qsnap checkpoints.
**Complexity:** EASY — prefix filtering is a simple string match.

### Risk 6: Extracting `_parse_domblklist_path` to shared utils changes import graph

**Source:** `design.md` line 107
**Impact:** The import graph of 3 modules (`external.py`, `allocation_detector.py`, `file_copy.py`) changes. If the extraction is incomplete, modules will fail to import.
**Mitigation tests:**
- `test_external_snapshot_provider_imports_shared_parsers` (`tests/modules/snapshot/test_external.py`) — verifies `external.py` imports from `qsnap.utils.parsing` and has no module-level `_parse_*` functions.
- `test_allocation_detector_imports_shared_parsers` (`tests/modules/change/test_allocation.py`) — verifies `allocation_detector.py` imports from `qsnap.utils.parsing`.
- `test_file_copy_provider_imports_shared_parsers` (`tests/modules/backup/test_copy.py`) — verifies `file_copy.py` imports from `qsnap.utils.parsing`.
**Complexity:** EASY — mechanical refactor, 0-risk if imports are verified.

### Risk 7: EXIT_BACKUP_ABORT exit code semantics

**Source:** `design.md` line 5 (hardening gap), `qsnap/cli/errors.py` line 8
**Impact:** `EXIT_BACKUP_ABORT` (exit code 10) is defined but currently unused. If wired incorrectly, successful runs could return exit code 10, breaking cron job expectations.
**Mitigation tests:**
- `test_pipeline_backup_abort_returns_exit_code_10` (`tests/core/test_engine.py`) — verifies exit code 10 when at least one backup fails.
- `test_pipeline_all_backups_succeed_exit_code_not_10` (`tests/core/test_engine.py`) — verifies exit code is NOT 10 when all backups succeed.
- `test_vm_run_result_backup_failed_field` (`tests/models/test_results.py`) — verifies `VMRunResult.backup_failed` field exists and is frozen.
**Complexity:** MODERATE — Core must track per-VM backup failure status and aggregate into pipeline exit code.

### Risk 8: Dynamic disk resolution replaces hardcoded "vda"

**Source:** `design.md` line 5, `qsnap/core/__init__.py` line 278
**Impact:** The hardcoded `disk = "vda"` in `Core._create_snapshot()` breaks for VMs whose primary disk is named `sda` or has multiple disks. If dynamic resolution fails, no snapshot is created.
**Mitigation tests:**
- `test_create_snapshot_single_disk_sda_not_vda` (`tests/core/test_pipeline.py`) — verifies snapshot is created for `sda`, not `vda`.
- `test_create_snapshot_multi_disk_vda_vdb_creates_two_with_suffix` (`tests/core/test_pipeline.py`) — verifies two snapshots with `_vda`/`_vdb` suffixes.
- `test_create_snapshot_explicit_disk_list_overrides_discovery` (`tests/core/test_pipeline.py`) — verifies explicit `VMConfig.disks` overrides auto-discovery.
**Complexity:** MODERATE — Core must call `virsh domblklist`, parse output, iterate disks, and generate per-disk snapshot names.

---

## Test Execution Commands

```bash
# Run all unit + contract + mock tests (fast, no I/O):
poetry run pytest tests/ -m "not integration and not stress and not e2e" -v

# Run only the new/modified tests for this change:
poetry run pytest tests/modules/backup/test_bitmap.py tests/utils/test_parsing.py tests/core/ tests/cli/ tests/config/test_model.py tests/factory/test_default.py tests/interfaces/test_backup_provider.py tests/interfaces/test_change_detector.py tests/mocks/ tests/modules/backup/test_copy.py tests/modules/snapshot/test_external.py tests/modules/change/test_allocation.py tests/models/test_results.py -v

# Coverage report:
poetry run pytest tests/ --cov=qsnap --cov-report=html

# Integration tests (needs libvirt):
poetry run pytest tests/ -m integration
```

---

## New Module Checklist (per TESTING.md)

For `BitmapBackupProvider`:
1. Create `tests/modules/backup/test_bitmap.py`
2. Update `MockVMModuleFactory` in `tests/mocks/mock_factory.py` to optionally return `MockBitmapBackupProvider`
3. Add `BitmapBackupProvider` to contract test parametrization in `tests/interfaces/test_backup_provider.py`
4. Add bitmap mode config fixture in `tests/fixtures/configs/` (if needed for integration tests)
5. Verify: `poetry run pytest tests/modules/backup/ tests/interfaces/test_backup_provider.py -v`

For `qsnap.utils.parsing`:
1. Create `tests/utils/test_parsing.py`
2. Verify all three modules (`external.py`, `allocation_detector.py`, `file_copy.py`) import from the shared module
3. Verify: `poetry run pytest tests/utils/ tests/modules/ -v`
