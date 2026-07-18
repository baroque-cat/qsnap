# Test Plan: Fix Silent Failures & btrbk-Style Logging

This test plan covers all spec scenarios from the change

```
openspec/changes/fix-silent-failures-and-btrbk-logging/
```

across six spec domains. Every `#### Scenario:` in every spec file has a corresponding test row below.

---

## Section 1: Coverage Map

### Spec: action-audit-trail

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| action-audit-trail | ActionRecord dataclass | ActionRecord is immutable | `tests/models/test_results.py` | `test_action_record_is_immutable` | `models-action-record` |
| action-audit-trail | ActionRecord dataclass | ActionRecord size and duration default to zero | `tests/models/test_results.py` | `test_action_record_defaults_zero` | `models-action-record` |
| action-audit-trail | ActionRecord accumulation in Core | Core clears actions at start of run | `tests/core/test_engine.py` | `test_actions_cleared_at_run_start` | `core-audit-trail` |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on snapshot create | `tests/core/test_engine.py` | `test_action_appended_on_snapshot_create` | `core-audit-trail` |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on snapshot delete (blockcommit) | `tests/core/test_engine.py` | `test_action_appended_on_snapshot_delete` | `core-audit-trail` |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on backup transfer | `tests/core/test_engine.py` | `test_action_appended_on_backup_transfer` | `core-audit-trail` |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on FULL backup creation | `tests/core/test_engine.py` | `test_action_appended_on_full_backup` | `core-audit-trail` |
| action-audit-trail | ActionRecord accumulation in Core | Core appends action on backup deletion | `tests/core/test_engine.py` | `test_action_appended_on_backup_delete` | `core-audit-trail` |
| action-audit-trail | ActionRecord accumulation in Core | Core appends error action on failure | `tests/core/test_engine.py` | `test_error_action_appended_on_failure` | `core-audit-trail` |
| action-audit-trail | ActionRecord accumulation in Core | Core does not append actions in dry-run for mutations | `tests/core/test_engine.py` | `test_no_actions_in_dry_run_mutations` | `core-audit-trail` |
| action-audit-trail | PipelineResult carries actions | PipelineResult includes actions after successful run | `tests/core/test_engine.py` | `test_pipeline_result_includes_actions_success` | `core-audit-trail` |
| action-audit-trail | PipelineResult carries actions | PipelineResult includes error actions | `tests/core/test_engine.py` | `test_pipeline_result_includes_error_actions` | `core-audit-trail` |

### Spec: backup-summary

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-summary | Summary table output after run command | Summary table with created and deleted snapshots | `tests/cli/test_summary.py` | `test_summary_table_created_and_deleted_snapshots` | `cli-summary-output` |
| backup-summary | Summary table output after run command | Summary table with backup transfers | `tests/cli/test_summary.py` | `test_summary_table_backup_transfers` | `cli-summary-output` |
| backup-summary | Summary table output after run command | Summary table with errors | `tests/cli/test_summary.py` | `test_summary_table_with_errors` | `cli-summary-output` |
| backup-summary | Summary table output after run command | Summary table legend | `tests/cli/test_summary.py` | `test_summary_table_includes_legend` | `cli-summary-output` |
| backup-summary | Dry-run summary table | Dry-run summary header | `tests/cli/test_summary.py` | `test_dry_run_summary_header` | `cli-summary-output` |
| backup-summary | Dry-run summary table | Dry-run summary footer | `tests/cli/test_summary.py` | `test_dry_run_summary_footer` | `cli-summary-output` |
| backup-summary | Dry-run summary table | Dry-run shows predicted actions from retention evaluation | `tests/cli/test_summary.py` | `test_dry_run_shows_predicted_actions` | `cli-summary-output` |
| backup-summary | Summary formatter as pure function | Formatter has no side effects | `tests/cli/test_summary.py` | `test_formatter_no_side_effects` | `cli-summary-output` |
| backup-summary | Summary formatter as pure function | Formatter reads from PipelineResult.actions only | `tests/cli/test_summary.py` | `test_formatter_reads_from_pipeline_result_only` | `cli-summary-output` |
| backup-summary | Summary table respects --quiet mode | Quiet mode still prints summary | `tests/cli/test_summary.py` | `test_quiet_mode_still_prints_summary` | `cli-summary-output` |
| backup-summary | Summary table groups actions by VM | VM with no actions is omitted | `tests/cli/test_summary.py` | `test_vm_with_no_actions_omitted` | `cli-summary-output` |
| backup-summary | Summary table groups actions by VM | Actions sorted by pipeline order | `tests/cli/test_summary.py` | `test_actions_sorted_by_pipeline_order` | `cli-summary-output` |

### Spec: transaction-log

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| transaction-log | Transaction log config field | transaction_log not configured | `tests/config/test_model.py` | `test_transaction_log_defaults_to_none` | `config-transaction-log` |
| transaction-log | Transaction log config field | transaction_log path is absolute | `tests/config/test_model.py` | `test_transaction_log_validates_absolute_path` | `config-transaction-log` |
| transaction-log | Transaction log file format | Snapshot creation log line | `tests/utils/test_transaction.py` | `test_write_snapshot_create_line` | `utils-transaction-log` |
| transaction-log | Transaction log file format | Snapshot deletion log line | `tests/utils/test_transaction.py` | `test_write_snapshot_delete_line` | `utils-transaction-log` |
| transaction-log | Transaction log file format | Backup transfer log line | `tests/utils/test_transaction.py` | `test_write_backup_transfer_line` | `utils-transaction-log` |
| transaction-log | Transaction log file format | FULL backup log line | `tests/utils/test_transaction.py` | `test_write_full_backup_line` | `utils-transaction-log` |
| transaction-log | Transaction log file format | Error log line | `tests/utils/test_transaction.py` | `test_write_error_line` | `utils-transaction-log` |
| transaction-log | Transaction log file format | Finished log line | `tests/utils/test_transaction.py` | `test_write_finished_line` | `utils-transaction-log` |
| transaction-log | Transaction log file format | Transaction log not written in dry-run | `tests/core/test_engine.py` | `test_transaction_log_not_written_in_dry_run` | `core-audit-trail` |
| transaction-log | TransactionWriter as stateless utility | Writer appends to existing file | `tests/utils/test_transaction.py` | `test_writer_appends_to_existing_file` | `utils-transaction-log` |
| transaction-log | TransactionWriter as stateless utility | Writer creates file if it does not exist | `tests/utils/test_transaction.py` | `test_writer_creates_file_if_not_exists` | `utils-transaction-log` |
| transaction-log | TransactionWriter as stateless utility | Writer has no dependency on Core | `tests/utils/test_transaction.py` | `test_writer_has_no_core_dependency` | `utils-transaction-log` |

### Spec: backup-provider

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-provider | BitmapBackupProvider domjobabort after NBD incremental transfer | Domjobabort called after successful transfer | `tests/modules/backup/test_bitmap.py` | `test_domjobabort_called_after_successful_transfer` | `backup-bitmap-enhancements` |
| backup-provider | BitmapBackupProvider domjobabort after NBD incremental transfer | Domjobabort called after failed transfer | `tests/modules/backup/test_bitmap.py` | `test_domjobabort_called_after_failed_transfer` | `backup-bitmap-enhancements` |
| backup-provider | BitmapBackupProvider domjobabort after NBD incremental transfer | Domjobabort failure is non-fatal | `tests/modules/backup/test_bitmap.py` | `test_domjobabort_failure_is_non_fatal` | `backup-bitmap-enhancements` |
| backup-provider | BitmapBackupProvider accepts IStateManager | Constructor accepts IStateManager | `tests/modules/backup/test_bitmap.py` | `test_constructor_accepts_state_manager` | `backup-bitmap-enhancements` |
| backup-provider | BitmapBackupProvider accepts IStateManager | Constructor works without IStateManager | `tests/modules/backup/test_bitmap.py` | `test_constructor_works_without_state_manager` | `backup-bitmap-enhancements` |
| backup-provider | BitmapBackupProvider accepts IStateManager | create_full_backup records FULL in state | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_records_in_state` | `backup-bitmap-enhancements` |
| backup-provider | BitmapBackupProvider accepts IStateManager | create_full_backup skips state recording when state is None | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_skips_state_when_none` | `backup-bitmap-enhancements` |
| backup-provider | Factory passes IStateManager to BitmapBackupProvider | Factory constructs BitmapBackupProvider with state | `tests/factory/test_default.py` | `test_factory_passes_state_to_bitmap_provider` | `factory-bitmap-state` |
| backup-provider | Rebase error handling in FileCopyBackupProvider | Rebase fails due to invalid backing path | `tests/modules/backup/test_copy.py` | `test_transfer_rebase_failure_returns_backup_result_failure` (MODIFY) | `backup-filecopy-silent-failures` |
| backup-provider | FileCopyBackupProvider verify_backup failure logging | Verification failure logged | `tests/modules/backup/test_copy.py` | `test_transfer_verify_failure_logs_warning` (NEW) | `backup-filecopy-silent-failures` |
| backup-provider | FileCopyBackupProvider verify_backup failure logging | JSON decode failure logged | `tests/modules/backup/test_copy.py` | `test_transfer_json_decode_failure_logs_warning` (NEW) | `backup-filecopy-silent-failures` |
| backup-provider | FileCopyBackupProvider rsync failure logging | Rsync failure logged | `tests/modules/backup/test_copy.py` | `test_transfer_rsync_fails_disk_full` (MODIFY) | `backup-filecopy-silent-failures` |
| backup-provider | FileCopyBackupProvider rsync failure logging | Rsync failure logged | `tests/modules/backup/test_copy.py` | `test_rsync_unavailable_transfer_fails_no_cp_fallback` (MODIFY) | `backup-filecopy-silent-failures` |

### Spec: core-orchestrator

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| core-orchestrator | backup_failed WARNING in Core._backup_target | backup_failed warning with transfer failures | `tests/core/test_engine.py` | `test_backup_failed_warning_with_transfer_failures` | `core-audit-trail` |
| core-orchestrator | backup_failed WARNING in Core._backup_target | No warning when all transfers succeed | `tests/core/test_engine.py` | `test_no_backup_failed_warning_when_all_succeed` | `core-audit-trail` |
| core-orchestrator | ActionRecord accumulation in Core pipeline | Actions attached to PipelineResult | `tests/core/test_engine.py` | `test_actions_attached_to_pipeline_result` | `core-audit-trail` |
| core-orchestrator | Per-operation INFO logging in Core | Snapshot creation INFO | `tests/core/test_engine.py` | `test_snapshot_create_info_log` | `core-audit-trail` |
| core-orchestrator | Per-operation INFO logging in Core | Snapshot deletion INFO | `tests/core/test_engine.py` | `test_snapshot_delete_info_log` | `core-audit-trail` |
| core-orchestrator | Per-operation INFO logging in Core | Backup transfer INFO | `tests/core/test_engine.py` | `test_backup_transfer_info_log` | `core-audit-trail` |
| core-orchestrator | Per-operation INFO logging in Core | FULL backup creation INFO | `tests/core/test_engine.py` | `test_full_backup_create_info_log` | `core-audit-trail` |
| core-orchestrator | Per-operation INFO logging in Core | Backup deletion INFO | `tests/core/test_engine.py` | `test_backup_delete_info_log` | `core-audit-trail` |
| core-orchestrator | Per-operation INFO logging in Core | Ghost retention INFO | `tests/core/test_engine.py` | `test_ghost_retention_info_log` | `core-audit-trail` |
| core-orchestrator | Post-commit chain verification after blockcommit | Post-commit chain check passes | `tests/core/test_pipeline.py` | `test_post_commit_chain_check_passes` (MODIFY existing) | `core-pipeline` |
| core-orchestrator | Post-commit chain verification after blockcommit | Post-commit skipped when chain_length_before is None | `tests/core/test_pipeline.py` | `test_post_commit_skipped_when_pre_commit_unavailable` (MODIFY) | `core-pipeline` |
| core-orchestrator | Dry-run mode | Dry-run logs planned actions | `tests/core/test_pipeline.py` | `test_dry_run_logs_planned_actions` | `core-pipeline` |
| core-orchestrator | Dry-run mode | Dry-run activated from CLI | `tests/core/test_pipeline.py` | `test_dry_run_activated_from_cli` | `core-pipeline` |

### Spec: cli-interface

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| cli-interface | Summary output after run command | Summary printed after successful run | `tests/cli/test_commands.py` | `test_summary_printed_after_successful_run` | `cli-commands-summary` |
| cli-interface | Summary output after run command | Summary printed after run with backup failures | `tests/cli/test_commands.py` | `test_summary_printed_after_run_with_failures` | `cli-commands-summary` |
| cli-interface | Summary output after run command | Summary printed after dry-run | `tests/cli/test_commands.py` | `test_summary_printed_after_dry_run` | `cli-commands-summary` |
| cli-interface | CLI thin-layer constraint for summary | No business logic in summary formatter | `tests/cli/test_thin_layer.py` | `test_summary_formatter_no_business_logic` | `cli-thin-layer` |
| cli-interface | CLI is a thin layer | No business logic in CLI | `tests/cli/test_thin_layer.py` | `test_no_business_logic_in_cli` (MODIFY — add summary.py import check) | `cli-thin-layer` |

---

## Section 2: Delegation Groups

Each group is based on exactly one test file. Tests are non-overlapping across groups.

### Group: `models-action-record`

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/models/test_results.py` | 2 | NEW |

### Group: `core-audit-trail`

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/core/test_engine.py` | 13 | NEW |

### Group: `core-pipeline`

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | 4 | NEW + MODIFY |

### Group: `backup-filecopy-silent-failures`

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/modules/backup/test_copy.py` | 4 | NEW + MODIFY |

### Group: `backup-bitmap-enhancements`

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | 7 | NEW |

### Group: `factory-bitmap-state`

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/factory/test_default.py` | 1 | NEW + MODIFY |

### Group: `cli-summary-output`

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/cli/test_summary.py` | 12 | NEW |

### Group: `cli-commands-summary`

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/cli/test_commands.py` | 3 | NEW + MODIFY |

### Group: `cli-thin-layer`

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/cli/test_thin_layer.py` | 2 | NEW + MODIFY |

### Group: `utils-transaction-log`

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/utils/test_transaction.py` | 9 | NEW |

### Group: `interfaces-backup-contract`

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/interfaces/test_backup_provider.py` | 0 | MODIFY |

### Group: `config-transaction-log`

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/config/test_model.py` | 2 | NEW |

---

## Section 3: Test Modifications

### Tests to MODIFY

| File | Test Name | What Changes | Driven By |
|---|---|---|---|
| `tests/core/test_pipeline.py` | `test_post_commit_measurement_fails_graceful` | Remove the assertion `"Post-commit chain verification passed" in caplog.text` (line 1751). The spec now says "passed" must NOT be logged when post-commit measurement fails. Verify only WARNING is emitted. | `core-orchestrator/spec.md` — Requirement: Post-commit chain verification after blockcommit — Scenario: Post-commit skipped when chain_length_before is None |
| `tests/core/test_pipeline.py` | `test_post_commit_skipped_when_pre_commit_unavailable` | Add assertion that `"Post-commit chain verification passed" is NOT in caplog.text` when `chain_length_before` is `None`. The current test only checks `"Pre-commit chain length unavailable"`. | `core-orchestrator/spec.md` — Requirement: Post-commit chain verification after blockcommit — Scenario: Post-commit skipped when chain_length_before is None |
| `tests/modules/backup/test_copy.py` | `test_transfer_rsync_fails_disk_full` | Add `caplog` fixture and assert that a `logger.warning` call was made with message matching `"rsync failed for <snapshot>: <error>"` before returning `BackupResult(success=False)`. | `backup-provider/spec.md` — Requirement: FileCopyBackupProvider rsync failure logging — Scenario: Rsync failure logged |
| `tests/modules/backup/test_copy.py` | `test_rsync_unavailable_transfer_fails_no_cp_fallback` | Add `caplog` fixture and assert that a `logger.warning` call was made with message matching `"rsync failed for <snapshot>: <error>"`. | `backup-provider/spec.md` — Requirement: FileCopyBackupProvider rsync failure logging — Scenario: Rsync failure logged |
| `tests/modules/backup/test_copy.py` | `test_transfer_rebase_failure_returns_backup_result_failure` | Add `caplog` fixture and assert that a `logger.warning` call was made with message matching `"rebase to FULL failed for <snapshot>: <error>"` before returning `success=False`. | `backup-provider/spec.md` — Requirement: Rebase error handling in FileCopyBackupProvider — Scenario: Rebase fails due to invalid backing path |
| `tests/factory/test_default.py` | `test_factory_selects_bitmap_provider_for_bitmap_mode` | After `create_backup_provider(make_vm_config(), target)`, assert that `provider._state` is the factory's `mock_state` (verify `state` was injected by the factory). | `backup-provider/spec.md` — Requirement: Factory passes IStateManager to BitmapBackupProvider — Scenario: Factory constructs BitmapBackupProvider with state |
| `tests/factory/test_default.py` | `test_factory_bitmap_mode_new_libvirt_returns_bitmap` | Same as above: assert `provider._state` is the factory's `mock_state`. | `backup-provider/spec.md` — Requirement: Factory passes IStateManager to BitmapBackupProvider |
| `tests/interfaces/test_backup_provider.py` | `test_backup_provider_transfer_missing_returns_list_of_backup_result` (parametrize bitmap case) | The parametrized `BitmapBackupProvider` init uses `{"shell": MockShell()}`. This still works (state is optional), but add a second parametrize entry for `BitmapBackupProvider` with `{"shell": MockShell(), "state": MockState()}` to verify the new signature works in contract. | `backup-provider/spec.md` — Requirement: BitmapBackupProvider accepts IStateManager — Scenario: Constructor accepts IStateManager |
| `tests/cli/test_thin_layer.py` | `test_no_business_logic_in_cli` (if exists) | Add check that `qsnap/cli/summary.py` has no imports from `qsnap.modules`, `qsnap.config`, `qsnap.retention`, or `qsnap.state`. If the test uses import inspection, extend it to check `summary.py` as well. | `cli-interface/spec.md` — Requirement: CLI thin-layer constraint for summary — Scenario: No business logic in summary formatter |
| `tests/cli/test_commands.py` | `test_handle_run_calls_format_summary` (if exists, or new) | Ensure the CLI test for `handle_run()` (or equivalent) verifies that `format_summary(result)` is called after `_format_pipeline_result()` and its output is printed to stdout. | `cli-interface/spec.md` — Requirement: Summary output after run command — Scenario: Summary printed after successful run |

### Tests to REMOVE

The following tests should be removed because the code they test has been obsoleted by this change:

| File | Test Name | Reason |
|---|---|---|
| N/A | N/A | No tests need removal. The removed Core private methods (`_should_create_bucket_full`, `_active_buckets`, `_f_anchor_buckets`, `_period_key`) were already extracted to `BucketFullStrategy` in a prior change, and their tests already migrated to `tests/modules/retention/test_bucket_full_strategy.py`. The `test_get_chain_length_no_use_base_image_param` test is still valid (it validates that the parameter was removed — this is the expected behavior, not a test of removed code). The `BitmapBackupProvider` constructor signature change is backward-compatible (optional `state` parameter). |

---

## Section 4: Risks & Edge Cases

### Risk Scenarios from design.md

| Risk | Mitigation | Test Strategy |
|---|---|---|
| ActionRecord list grows unbounded for long chains | ActionRecord is ephemeral — only accumulated during a single `_run_pipeline()` call and discarded after. Max entries ≈ number of snapshots × targets. | Create a boundary test in `core-audit-trail` group: `test_actions_list_does_not_persist_across_runs` — verify `self._actions` is empty at the start of a second `core.run()` call. |
| Summary table could be confusing if `backup_failed` is True but individual items show as transferred | Items that failed have `action="error"` with error message, and `!!!` symbol in summary table. | Tests in `cli-summary-output`: `test_summary_mixed_success_and_error` — create `PipelineResult` with both successful transfers and error actions, verify error items render with `!!!` and include error text. |
| Transaction log may contain sensitive paths | Paths already logged at INFO level throughout pipeline. Transaction log is opt-in via config. No obfuscation needed. | Test in `utils-transaction-log`: `test_transaction_log_respects_opt_in` — verify log is only written when `transaction_log` is configured, not by default. |
| Summary goes to stdout, per-op logs go to stderr | Matches btrbk's separation. `--quiet` suppresses stderr but not stdout summary. | Test in `cli-summary-output`: `test_quiet_mode_still_prints_summary` — mock stderr suppression, verify summary still appears on stdout. |

### Additional Edge Cases

| Edge Case | Test Strategy |
|---|---|
| Empty `PipelineResult.actions` produces valid empty summary | Test in `cli-summary-output`: `test_format_summary_with_empty_actions` — returns a string (no crash, no empty table artifacts). |
| `PipelineResult.actions` is `None` or missing attribute | Test in `cli-summary-output`: `test_format_summary_handles_missing_actions` — graceful default (empty string or minimal header). |
| Multiple VMs with interleaved actions sort correctly | Test in `cli-summary-output`: `test_actions_grouped_by_vm_then_sorted` — actions from VM A and VM B are interleaved in the list but grouped correctly in output. |
| Transaction log race condition (two writers to same file) | Test in `utils-transaction-log`: `test_concurrent_writes_produce_valid_lines` — stress test: two threads call `TransactionWriter.write()` simultaneously, verify all lines are present and valid. |
| Transaction log directory does not exist | Test in `utils-transaction-log`: `test_write_creating_nonexistent_directory` — verify a WARNING is emitted or an error is handled gracefully (the spec says "logs a WARNING if the directory does not exist"). |
| Domjobabort with 30-second timeout affects subsequent transfer | Test in `backup-bitmap-enhancements`: `test_domjobabort_timeout_does_not_block_next_transfer` — simulate a slow domjobabort, verify socket cleanup still happens and next snapshot can transfer. |
| BitmapBackupProvider state=None does not crash during create_full_backup | Test in `backup-bitmap-enhancements`: `test_create_full_backup_skips_state_when_none` — already covered by spec scenario. |
| ActionRecord with non-ASCII error messages | Test in `models-action-record`: `test_action_record_handles_unicode_error` — construct with Unicode error, verify repr and format_successfully. |
| Transaction log file permissions failure | Test in `utils-transaction-log`: `test_write_permission_denied` — simulate `PermissionError`, verify it is handled (logged as WARNING or raised depending on design). |
| Summary formatter with malicious ActionRecord data (path traversal in path field) | Test in `cli-summary-output`: `test_format_summary_sanitizes_paths` — paths are displayed as-is (they come from trusted config), but verify no shell injection is possible. |
