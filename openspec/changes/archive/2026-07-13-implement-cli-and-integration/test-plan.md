# QA Strategy & Test Plan

## Overview

This plan covers the `implement-cli-and-integration` change: adding the CLI
entry point, lockfile mechanism, timestamp formatting, informational list
commands, preserve flags, systemd integration, and activation of unused
`GlobalConfig` fields.

**Testing paradigm:** All tests follow TESTING.md. Unit tests use zero real
I/O (mocked `IShell`, `MockVMModuleFactory`, `InMemoryStateManager`). CLI
tests dispatch to Core methods and verify parameters, not business logic.
Retention tests are pure-function with fixed timestamps. Locking tests use
real `fcntl.flock` on temp files (Linux-native, no mock needed for the
system call itself, but CLI-level lock-held scenarios use mocked
`LockManager`).

**Current test count:** 120 tests across 35 files. This plan adds ~55 new
test functions and modifies ~10 existing files.

---

## Coverage Map

Every `#### Scenario:` from every spec file is mapped to a concrete test
file and test function name, following the TESTING.md directory structure.

| # | Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|---|
| 1 | cli-interface | CLI entry point | Help text | `tests/cli/test_app.py` | `test_help_text_lists_subcommands_and_flags` | cli-app |
| 2 | cli-interface | CLI entry point | Subcommand dispatch | `tests/cli/test_commands.py` | `test_run_subcommand_dispatches_to_core_run` | cli-commands |
| 3 | cli-interface | Global flag --config / -c | Explicit config path | `tests/cli/test_app.py` | `test_explicit_config_path_passed_to_configfacade` | cli-app |
| 4 | cli-interface | Global flag --config / -c | Default config path | `tests/cli/test_app.py` | `test_default_config_path_is_etc_qsnap_toml` | cli-app |
| 5 | cli-interface | Global flag --dry-run / -n | Dry-run logs actions without executing | `tests/cli/test_commands.py` | `test_dry_run_flag_sets_core_dry_run_true` | cli-commands |
| 6 | cli-interface | Global flags --preserve / --preserve-snapshots / --preserve-backups | --preserve skips all deletion | `tests/cli/test_commands.py` | `test_preserve_flag_sets_both_preserve_properties` | cli-commands |
| 7 | cli-interface | Global flags --preserve / --preserve-snapshots / --preserve-backups | --preserve-snapshots skips only snapshot deletion | `tests/cli/test_commands.py` | `test_preserve_snapshots_flag_sets_only_preserve_snapshots` | cli-commands |
| 8 | cli-interface | Global flags --verbose / --quiet / --loglevel | Verbose logging | `tests/cli/test_app.py` | `test_verbose_flag_sets_loglevel_debug` | cli-app |
| 9 | cli-interface | Global flags --verbose / --quiet / --loglevel | Quiet logging | `tests/cli/test_app.py` | `test_quiet_flag_sets_loglevel_error` | cli-app |
| 10 | cli-interface | Global flag --print-schedule / -S | Schedule output | `tests/cli/test_commands.py` | `test_print_schedule_flag_dispatches_to_core_print_schedule` | cli-commands |
| 11 | cli-interface | Global flag --format | Table output | `tests/cli/test_format.py` | `test_format_table_produces_aligned_columns_uppercase_headers` | cli-format |
| 12 | cli-interface | Global flag --format | Raw output | `tests/cli/test_format.py` | `test_format_raw_produces_space_separated_key_value_pairs` | cli-format |
| 13 | cli-interface | Global flag --lockfile | Lockfile override | `tests/cli/test_app.py` | `test_lockfile_flag_overrides_config_lockfile_path` | cli-app |
| 14 | cli-interface | VM filter positional argument | Filter by VM name | `tests/cli/test_commands.py` | `test_vm_filter_positional_passed_to_core_method` | cli-commands |
| 15 | cli-interface | VM filter positional argument | No filter processes all VMs | `tests/cli/test_commands.py` | `test_no_vm_filter_passes_none_to_core_method` | cli-commands |
| 16 | cli-interface | Exit codes | Success exit code | `tests/cli/test_app.py` | `test_success_returns_exit_code_zero` | cli-app |
| 17 | cli-interface | Exit codes | Lockfile error exit code | `tests/cli/test_app.py` | `test_lockfile_held_returns_exit_code_three` | cli-app |
| 18 | cli-interface | CLI is a thin layer | No business logic in CLI | `tests/cli/test_thin_layer.py` | `test_commands_py_has_no_business_logic_imports` | cli-thin-layer |
| 19 | locking | Lockfile acquisition on startup | Successful lock acquisition | `tests/utils/test_locking.py` | `test_acquire_lock_when_free_returns_true` | locking |
| 20 | locking | Lockfile acquisition on startup | Lock already held | `tests/utils/test_locking.py` | `test_acquire_lock_when_held_returns_false` | locking |
| 21 | locking | Lockfile release on exit | Lock released on normal exit | `tests/utils/test_locking.py` | `test_release_lock_allows_reacquisition` | locking |
| 22 | locking | Lockfile release on exit | Lock released on crash | `tests/utils/test_locking.py` | `test_lock_auto_released_on_process_termination` | locking |
| 23 | locking | Lockfile path resolution | Lockfile from CLI overrides config | `tests/utils/test_locking.py` | `test_lockfile_path_resolution_cli_overrides_config` | locking |
| 24 | locking | Lockfile path resolution | No lockfile means no locking | `tests/utils/test_locking.py` | `test_none_lockfile_path_means_no_locking` | locking |
| 25 | timestamp-formatting | Timestamp format from config | Short format | `tests/utils/test_time.py` | `test_short_format_produces_yyyymmdd` | timestamp-utils |
| 26 | timestamp-formatting | Timestamp format from config | Long format (default) | `tests/utils/test_time.py` | `test_long_format_produces_yyyymmdd_thhmm` | timestamp-utils |
| 27 | timestamp-formatting | Timestamp format from config | Long-iso format | `tests/utils/test_time.py` | `test_long_iso_format_produces_yyyymmdd_thhmmss_offset` | timestamp-utils |
| 28 | timestamp-formatting | Collision suffix for duplicate timestamps | Duplicate timestamp resolution | `tests/core/test_engine.py` | `test_generate_snapshot_name_appends_collision_suffix` | core-engine |
| 29 | timestamp-formatting | preserve_day_of_week in retention | Weekly retention with Tuesday boundary | `tests/modules/retention/test_time_based.py` | `test_weekly_retention_tuesday_boundary_keeps_four` | retention |
| 30 | timestamp-formatting | preserve_day_of_week in retention | Weekly retention with default Monday | `tests/modules/retention/test_time_based.py` | `test_weekly_retention_default_monday_boundary_keeps_two` | retention |
| 31 | list-commands | Core.list_snapshots() | List snapshots for all VMs | `tests/core/test_list_commands.py` | `test_list_snapshots_returns_all_vms_sorted_ascending` | core-list |
| 32 | list-commands | Core.list_snapshots() | List snapshots for filtered VM | `tests/core/test_list_commands.py` | `test_list_snapshots_filtered_vm_returns_only_matching` | core-list |
| 33 | list-commands | Core.list_backups() | List backups for a VM with one target | `tests/core/test_list_commands.py` | `test_list_backups_returns_sorted_backup_infos` | core-list |
| 34 | list-commands | Core.list_backups() | List backups when no backups exist | `tests/core/test_list_commands.py` | `test_list_backups_empty_when_no_backups_exist` | core-list |
| 35 | list-commands | Core.list_config() | List configuration | `tests/core/test_list_commands.py` | `test_list_config_returns_all_vmconfigs_from_facade` | core-list |
| 36 | list-commands | Core.list_latest() | Latest snapshot found | `tests/core/test_list_commands.py` | `test_list_latest_returns_newest_snapshot_per_vm` | core-list |
| 37 | list-commands | Core.list_latest() | No snapshots | `tests/core/test_list_commands.py` | `test_list_latest_returns_none_for_vm_without_snapshots` | core-list |
| 38 | list-commands | Core.print_schedule() | Schedule shows keep/remove decisions | `tests/core/test_list_commands.py` | `test_print_schedule_shows_keep_remove_counts` | core-list |
| 39 | list-commands | Core.print_schedule() | Schedule does not mutate | `tests/core/test_list_commands.py` | `test_print_schedule_does_not_call_mutating_shell_commands` | core-list |
| 40 | list-commands | Core.check() | Healthy backing chain | `tests/core/test_list_commands.py` | `test_check_healthy_backing_chain_reports_ok` | core-list |
| 41 | list-commands | Core.check() | Broken backing chain | `tests/core/test_list_commands.py` | `test_check_broken_chain_reports_broken_status` | core-list |
| 42 | systemd-integration | Systemd service unit | Service runs qsnap | `tests/systemd/test_units.py` | `test_service_unit_execstart_runs_qsnap_run_with_config` | systemd |
| 43 | systemd-integration | Systemd timer unit | Timer triggers service | `tests/systemd/test_units.py` | `test_timer_unit_triggers_service_on_hourly_calendar` | systemd |
| 44 | systemd-integration | Systemd timer unit | Persistent timer catches up after sleep | `tests/systemd/test_units.py` | `test_timer_unit_has_persistent_true` | systemd |
| 45 | systemd-integration | Multiple timer instances with different configs | Separate hourly and weekly timers | `tests/systemd/test_units.py` | `test_multiple_timer_instances_pattern_documented` | systemd |
| 46 | systemd-integration | Example config file | Example config is parseable | `tests/systemd/test_units.py` | `test_example_config_is_parseable_by_configfacade` | systemd |
| 47 | core-orchestrator | Dry-run mode | Dry-run logs planned actions | `tests/core/test_pipeline.py` | `test_dry_run_logs_planned_actions_no_mutation` | core-pipeline |
| 48 | core-orchestrator | Dry-run mode | Dry-run activated from CLI | `tests/cli/test_commands.py` | `test_dry_run_flag_sets_core_dry_run_before_run_called` | cli-commands |
| 49 | core-orchestrator | Preserve flags on Core | Preserve snapshots skips blockcommit | `tests/core/test_preserve.py` | `test_preserve_snapshots_skips_blockcommit_call` | core-preserve |
| 50 | core-orchestrator | Preserve flags on Core | Preserve backups skips backup deletion | `tests/core/test_preserve.py` | `test_preserve_backups_skips_provider_delete_calls` | core-preserve |
| 51 | core-orchestrator | Core.print_schedule() method | Schedule shows keep/remove decisions | `tests/core/test_list_commands.py` | `test_print_schedule_with_vm_filter_shows_keep_remove` | core-list |
| 52 | core-orchestrator | Core.print_schedule() method | Schedule does not mutate filesystem | `tests/core/test_list_commands.py` | `test_print_schedule_does_not_execute_mutating_commands` | core-list |
| 53 | core-orchestrator | Error result collection across pipeline steps | Preserve mode with failed backup | `tests/core/test_preserve.py` | `test_preserve_mode_failed_backup_error_reported_no_deletion` | core-preserve |
| 54 | config-model | GlobalConfig lockfile field is consumed | Lockfile from config is used | `tests/config/test_parser.py` | `test_config_parser_reads_lockfile_field_into_globalconfig` | config-parser |
| 55 | config-model | GlobalConfig timestamp_format field is consumed | timestamp_format controls snapshot naming | `tests/core/test_engine.py` | `test_core_uses_config_timestamp_format_for_snapshot_name` | core-engine |
| 56 | config-model | GlobalConfig preserve_day_of_week field is consumed | preserve_day_of_week controls weekly grouping | `tests/modules/retention/test_time_based.py` | `test_preserve_day_of_week_sunday_boundary_keeps_two` | retention |
| 57 | config-model | GlobalConfig preserve_day_of_week validation | Valid day of week | `tests/config/test_facade.py` | `test_preserve_day_of_week_valid_value_accepted` | config-facade |
| 58 | config-model | GlobalConfig preserve_day_of_week validation | Invalid day of week | `tests/config/test_facade.py` | `test_preserve_day_of_week_invalid_value_raises_configerror` | config-facade |

**Total scenarios mapped: 58**

---

## Delegation Groups

Each group owns exactly one test file (or a set of tightly-coupled files in
the same directory). Groups are non-overlapping.

---

### Group: cli-app

**Scope:** Argument parsing, global flag resolution, exit codes, help text
output. Tests use `unittest.mock` to mock Core and LockManager — no real
pipeline execution. The argparser is constructed via `build_argparser()` and
tested by parsing `argv` lists and asserting on the resulting `Namespace`.

**Test File:** `tests/cli/test_app.py` (NEW)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/cli/test_app.py` | #1 Help text, #3 Explicit config path, #4 Default config path, #8 Verbose logging, #9 Quiet logging, #13 Lockfile override, #16 Success exit code, #17 Lockfile error exit code | NEW |

**Key test details:**

- `test_help_text_lists_subcommands_and_flags`: Call `build_argparser().parse_args(["--help"])`, capture stdout, assert all subcommand names (`run`, `snapshot`, `backup`, `prune`, `list`, `stats`, `check`) and global flags (`--config`, `--dry-run`, `--preserve`, `--verbose`, `--quiet`, `--loglevel`, `--print-schedule`, `--format`, `--lockfile`) appear.
- `test_explicit_config_path_passed_to_configfacade`: Parse `["-c", "/path/to/custom.toml", "run"]`, assert `ns.config == "/path/to/custom.toml"`.
- `test_default_config_path_is_etc_qsnap_toml`: Parse `["run"]`, assert `ns.config == "/etc/qsnap/qsnap.toml"`.
- `test_verbose_flag_sets_loglevel_debug`: Parse `["-v", "run"]`, assert `ns.verbose is True`; verify the logging setup function receives `DEBUG`.
- `test_quiet_flag_sets_loglevel_error`: Parse `["-q", "run"]`, assert `ns.quiet is True`; verify logging setup receives `ERROR`.
- `test_lockfile_flag_overrides_config_lockfile_path`: Parse `["--lockfile", "/run/qsnap.lock", "run"]`, assert `ns.lockfile == "/run/qsnap.lock"`.
- `test_success_returns_exit_code_zero`: Mock Core.run() to return `PipelineResult(success=True)`, call `main()`, assert `sys.exit` called with 0.
- `test_lockfile_held_returns_exit_code_three`: Mock LockManager.acquire() to return `False`, call `main()`, assert exit code 3 and stderr contains "Lockfile is held by another qsnap instance".

**Additional tests (beyond spec scenarios):**
- `test_loglevel_flag_sets_explicit_level`: Parse `["-l", "warn", "run"]`, assert logging level is `WARN`.
- `test_format_flag_default_is_table`: Parse `["run"]`, assert `ns.format == "table"`.
- `test_format_col_custom_columns`: Parse `["--format", "col:name,path", "list", "snapshots"]`, assert `ns.format == "col:name,path"`.
- `test_unknown_subcommand_returns_parse_error_exit_code_2`: Parse `["bogus"]`, assert exit code 2.

---

### Group: cli-commands

**Scope:** Subcommand dispatch — each CLI subcommand maps to the correct
Core method with the correct arguments. Tests mock Core (via `unittest.mock`
or `MockConfigFacade` + `MockVMModuleFactory`) and assert which Core method
was called and with what parameters. No business logic is executed.

**Test File:** `tests/cli/test_commands.py` (NEW)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/cli/test_commands.py` | #2 Subcommand dispatch, #5 Dry-run logs actions without executing, #6 --preserve skips all deletion, #7 --preserve-snapshots skips only snapshot deletion, #10 Schedule output, #14 Filter by VM name, #15 No filter processes all VMs, #48 Dry-run activated from CLI | NEW |

**Key test details:**

- `test_run_subcommand_dispatches_to_core_run`: Mock Core, call `handle_run(args)`, assert `core.run()` was called.
- `test_snapshot_subcommand_dispatches_to_core_snapshot`: Assert `core.snapshot()` called.
- `test_backup_subcommand_dispatches_to_core_backup`: Assert `core.backup()` called.
- `test_prune_subcommand_dispatches_to_core_prune`: Assert `core.prune()` called.
- `test_list_snapshots_subcommand_dispatches_to_core_list_snapshots`: Assert `core.list_snapshots()` called.
- `test_list_backups_subcommand_dispatches_to_core_list_backups`: Assert `core.list_backups()` called.
- `test_list_config_subcommand_dispatches_to_core_list_config`: Assert `core.list_config()` called.
- `test_list_latest_subcommand_dispatches_to_core_list_latest`: Assert `core.list_latest()` called.
- `test_stats_subcommand_dispatches_to_core_print_schedule`: Assert `core.print_schedule()` called.
- `test_check_subcommand_dispatches_to_core_check`: Assert `core.check()` called.
- `test_dry_run_flag_sets_core_dry_run_true`: Call `handle_run` with `dry_run=True`, assert `core.dry_run is True` before `core.run()` is called.
- `test_dry_run_flag_sets_core_dry_run_before_run_called`: Use a `unittest.mock.patch` on `core.run` with `side_effect` that asserts `core.dry_run` is True at call time.
- `test_preserve_flag_sets_both_preserve_properties`: Call `handle_run` with `preserve=True`, assert `core.preserve_snapshots is True` and `core.preserve_backups is True`.
- `test_preserve_snapshots_flag_sets_only_preserve_snapshots`: Call `handle_run` with `preserve_snapshots=True`, assert `core.preserve_snapshots is True` and `core.preserve_backups is False`.
- `test_preserve_backups_flag_sets_only_preserve_backups`: Call `handle_run` with `preserve_backups=True`, assert `core.preserve_backups is True` and `core.preserve_snapshots is False`.
- `test_print_schedule_flag_dispatches_to_core_print_schedule`: Call `handle_run` with `print_schedule=True`, assert `core.print_schedule()` called instead of `core.run()`.
- `test_vm_filter_positional_passed_to_core_method`: Call `handle_run` with `vm_filter="debiantest"`, assert `core.run(vm_filter="debiantest")` called.
- `test_no_vm_filter_passes_none_to_core_method`: Call `handle_run` with no VM args, assert `core.run(vm_filter=None)` called.

---

### Group: cli-format

**Scope:** Output formatters — `format_output()` function that renders
dataclass lists in `table`, `long`, `raw`, and `col:` formats. Pure function
tests: given input data, assert output string structure.

**Test File:** `tests/cli/test_format.py` (NEW)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/cli/test_format.py` | #11 Table output, #12 Raw output | NEW |

**Key test details:**

- `test_format_table_produces_aligned_columns_uppercase_headers`: Pass a list of `SnapshotInfo` dataclasses, format as `table`, assert output has uppercase headers (e.g., `NAME`, `PATH`, `TIMESTAMP`), columns are aligned with padding.
- `test_format_raw_produces_space_separated_key_value_pairs`: Pass a list of `SnapshotInfo` dataclasses, format as `raw`, assert each line is `key=value` pairs separated by spaces, one item per line.
- `test_format_long_produces_extended_columns`: Format as `long`, assert more columns than `table` (e.g., includes `ALLOCATION`).
- `test_format_col_selects_custom_columns`: Format as `col:name,timestamp`, assert only those two columns appear.
- `test_format_empty_list_produces_no_output`: Pass empty list, assert output is empty or a "no results" message.

---

### Group: cli-thin-layer

**Scope:** Static analysis test verifying that `qsnap/cli/commands.py`
contains no business logic imports. Uses `ast` module to parse the source
file and check for forbidden import statements.

**Test File:** `tests/cli/test_thin_layer.py` (NEW)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/cli/test_thin_layer.py` | #18 No business logic in CLI | NEW |

**Key test details:**

- `test_commands_py_has_no_business_logic_imports`: Parse `qsnap/cli/commands.py` with `ast.parse`, walk all `ast.Import` and `ast.ImportFrom` nodes, assert no imported module starts with `qsnap.modules`, `qsnap.config`, `qsnap.retention`, or `qsnap.state`. Allowed imports: `qsnap.core`, `qsnap.cli.format`, `qsnap.cli.errors`, `qsnap.models`, `argparse`, `logging`, stdlib.
- `test_app_py_has_no_business_logic_imports`: Same check for `qsnap/cli/app.py` — may import `qsnap.config.facade` (for ConfigFacade construction) and `qsnap.locking` (for LockManager), but NOT `qsnap.modules`, `qsnap.retention`, or `qsnap.state`.

---

### Group: locking

**Scope:** `LockManager` unit tests — acquire, release, path resolution.
Uses real `fcntl.flock` on temp files in `tmp_path`. The "lock already held"
scenario uses a subprocess or `os.fork` to hold the lock concurrently.

**Test File:** `tests/utils/test_locking.py` (NEW)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_locking.py` | #19 Successful lock acquisition, #20 Lock already held, #21 Lock released on normal exit, #22 Lock released on crash, #23 Lockfile from CLI overrides config, #24 No lockfile means no locking | NEW |

**Key test details:**

- `test_acquire_lock_when_free_returns_true`: Create `LockManager(tmp_path / "lock")`, call `acquire()`, assert returns `True`.
- `test_acquire_lock_when_held_returns_false`: Use `os.fork()` or `multiprocessing` to hold the lock in a child process, then call `acquire()` in the parent, assert returns `False`. (Alternative: open a second fd and `fcntl.flock` it before calling `LockManager.acquire()`.)
- `test_release_lock_allows_reacquisition`: Acquire, release, acquire again — second acquire returns `True`.
- `test_lock_auto_released_on_process_termination`: Fork a child that acquires the lock and exits immediately. After child exit (waitpid), acquire in the parent — should succeed because `flock` is released on process death.
- `test_lockfile_path_resolution_cli_overrides_config`: Test the path resolution function (e.g., `resolve_lockfile_path(cli_path="/run/qsnap.lock", config_path="/var/lock/qsnap.lock")`) returns `"/run/qsnap.lock"`.
- `test_none_lockfile_path_means_no_locking`: `LockManager(None).acquire()` returns `True` (no-op) and does not create any file.
- `test_lockfile_path_resolution_config_when_no_cli`: `resolve_lockfile_path(cli_path=None, config_path="/var/lock/qsnap.lock")` returns `"/var/lock/qsnap.lock"`.
- `test_lockfile_path_resolution_none_when_both_none`: `resolve_lockfile_path(cli_path=None, config_path=None)` returns `None`.

---

### Group: timestamp-utils

**Scope:** Pure-function tests for timestamp format string resolution and
formatting. Tests a utility function (e.g., in `qsnap/utils/time.py`) that
maps `timestamp_format` config values to `strftime` format strings and
formats `datetime` objects. No I/O, no side effects.

**Test File:** `tests/utils/test_time.py` (NEW)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_time.py` | #25 Short format, #26 Long format (default), #27 Long-iso format | NEW |

**Key test details:**

- `test_short_format_produces_yyyymmdd`: `format_snapshot_timestamp(datetime(2025, 7, 13, 15, 31), "short")` returns `"20250713"`.
- `test_long_format_produces_yyyymmdd_thhmm`: `format_snapshot_timestamp(datetime(2025, 7, 13, 15, 31), "long")` returns `"20250713T1531"`.
- `test_long_iso_format_produces_yyyymmdd_thhmmss_offset`: `format_snapshot_timestamp(datetime(2025, 7, 13, 15, 31, 23, tzinfo=...), "long-iso")` returns `"20250713T153123+0200"` (or matching offset).
- `test_unknown_format_defaults_to_long`: `format_snapshot_timestamp(dt, "bogus")` returns same as `"long"` format.
- `test_resolve_format_short_returns_pctY_pctm_pctd`: `resolve_format("short")` returns `"%Y%m%d"`.
- `test_resolve_format_long_returns_pctY_pctm_pctdT_pctH_pctM`: `resolve_format("long")` returns `"%Y%m%dT%H%M"`.
- `test_resolve_format_long_iso_returns_full_iso_format`: `resolve_format("long-iso")` returns `"%Y%m%dT%H%M%S%z"`.

---

### Group: core-list

**Scope:** Core informational methods — `list_snapshots()`, `list_backups()`,
`list_config()`, `list_latest()`, `print_schedule()`, `check()`. Tests use
`MockConfigFacade`, `MockVMModuleFactory`, `InMemoryStateManager`, and
`MockShell`. Pre-populate state and mock return values, then assert on
return types and contents.

**Test File:** `tests/core/test_list_commands.py` (NEW)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_list_commands.py` | #31 List snapshots for all VMs, #32 List snapshots for filtered VM, #33 List backups for a VM with one target, #34 List backups when no backups exist, #35 List configuration, #36 Latest snapshot found, #37 No snapshots, #38 Schedule shows keep/remove decisions (list), #39 Schedule does not mutate (list), #40 Healthy backing chain, #41 Broken backing chain, #51 Schedule shows keep/remove decisions (core), #52 Schedule does not mutate filesystem (core) | NEW |

**Key test details:**

- `test_list_snapshots_returns_all_vms_sorted_ascending`: Pre-populate `mock_state` with 3 snapshots for "vm1" and 2 for "vm2" (unsorted). Call `core.list_snapshots()`. Assert result is `{"vm1": [3 items], "vm2": [2 items]}`, each list sorted by timestamp ascending.
- `test_list_snapshots_filtered_vm_returns_only_matching`: Call `core.list_snapshots(vm_filter="vm1")`. Assert only `"vm1"` key present.
- `test_list_backups_returns_sorted_backup_infos`: Configure `MockBackupProvider.list()` to return 3 `SnapshotInfo` items (unsorted). Call `core.list_backups()`. Assert 3 items sorted by timestamp ascending.
- `test_list_backups_empty_when_no_backups_exist`: `MockBackupProvider.list()` returns `[]`. Call `core.list_backups()`. Assert empty list per VM.
- `test_list_config_returns_all_vmconfigs_from_facade`: Configure `MockConfigFacade` with 2 VMs. Call `core.list_config()`. Assert returns `list[VMConfig]` with 2 items matching the configured VMs.
- `test_list_latest_returns_newest_snapshot_per_vm`: Pre-populate 3 snapshots with different timestamps. Call `core.list_latest()`. Assert the newest `SnapshotInfo` is returned.
- `test_list_latest_returns_none_for_vm_without_snapshots`: No snapshots in state. Call `core.list_latest()`. Assert `None` for that VM.
- `test_print_schedule_shows_keep_remove_counts`: Pre-populate 10 snapshots, configure policy `hourly=6`. Call `core.print_schedule()`. Assert result contains 6 keep and 4 remove decisions, oldest-first order.
- `test_print_schedule_does_not_call_mutating_shell_commands`: Spy on `mock_shell.run`. Call `core.print_schedule()`. Assert no shell command matching `snapshot-create-as`, `blockcommit`, `cp`, or `rm` was executed.
- `test_print_schedule_with_vm_filter_shows_keep_remove`: Call `core.print_schedule(vm_filter="vm1")`. Assert only "vm1" appears in the result.
- `test_print_schedule_does_not_execute_mutating_commands`: Spy on `IShell.run` and `ILifecycleManager.blockcommit` and `IBackupProvider.delete`. Call `core.print_schedule()`. Assert none were called.
- `test_check_healthy_backing_chain_reports_ok`: Configure `MockShell` to return success for `qemu-img info` commands (backing files exist). Call `core.check()`. Assert each VM reports status `"ok"`.
- `test_check_broken_chain_reports_broken_status`: Configure `MockShell` to return failure for one snapshot's backing file check. Call `core.check()`. Assert that snapshot reports `"broken: backing file not found"`.
- `test_check_filtered_vm`: Call `core.check(vm_filter="vm1")`. Assert only "vm1" in result.

---

### Group: core-preserve

**Scope:** Core preserve flags — `preserve_snapshots` and `preserve_backups`
properties. Tests verify that when these flags are `True`, deletion steps are
skipped but retention evaluation still runs. Uses `MockVMModuleFactory` with
spies on `ILifecycleManager.blockcommit` and `IBackupProvider.delete`.

**Test File:** `tests/core/test_preserve.py` (NEW)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_preserve.py` | #49 Preserve snapshots skips blockcommit, #50 Preserve backups skips backup deletion, #53 Preserve mode with failed backup | NEW |

**Key test details:**

- `test_preserve_snapshots_defaults_to_false`: Construct Core, assert `core.preserve_snapshots is False` and `core.preserve_backups is False`.
- `test_preserve_snapshots_skips_blockcommit_call`: Set `core.preserve_snapshots = True`. Pre-populate state with snapshots that retention would remove. Call `core.run()`. Spy on `MockLifecycleManager.blockcommit` — assert it was NOT called. Spy on `create_retention_engine` — assert it WAS called (retention still evaluated).
- `test_preserve_backups_skips_provider_delete_calls`: Set `core.preserve_backups = True`. Configure `MockBackupProvider.list()` to return backups that retention would remove. Call `core.run()`. Spy on `MockBackupProvider.delete` — assert NOT called. Spy on `create_retention_engine` — assert WAS called.
- `test_preserve_both_skips_all_deletion`: Set both flags. Call `core.run()`. Assert neither `blockcommit` nor `delete` called.
- `test_preserve_mode_failed_backup_error_reported_no_deletion`: Set `core.preserve_backups = True`. Configure `MockBackupProvider.transfer_missing()` to return a `BackupResult(success=False)`. Call `core.run()`. Assert the `VMRunResult` for that VM has `success=False` with an error, AND `MockBackupProvider.delete` was NOT called (deletion skipped in preserve mode).
- `test_preserve_snapshots_retention_still_evaluated`: Set `core.preserve_snapshots = True`. Call `core.run()`. Spy on `create_retention_engine` — assert it WAS called (retention evaluation runs for schedule printing even in preserve mode).

---

### Group: core-engine

**Scope:** Core initialization, preserve property defaults, snapshot name
generation with timestamp format and collision suffix. This group MODIFIES
the existing `test_engine.py` to add new assertions and new test functions.

**Test File:** `tests/core/test_engine.py` (MODIFY)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_engine.py` | #28 Duplicate timestamp resolution, #55 timestamp_format controls snapshot naming | MODIFY — add new test functions; update existing `test_core_init_stores_dependencies` |

**Modifications to existing tests:**

- `test_core_init_stores_dependencies` (MODIFY): Add assertions that `core.preserve_snapshots is False` and `core.preserve_backups is False` after construction. These are new properties added by this change.

**New test functions:**

- `test_generate_snapshot_name_appends_collision_suffix`: Pre-populate `mock_state` with a snapshot named `"testvm.20250713T1531"`. Call `core._generate_snapshot_name(vm_config)` with a frozen datetime at `2025-07-13T15:31`. Assert the name is `"testvm.20250713T1531_1"`. (Requires mocking `datetime.now` or injecting a clock.)
- `test_generate_snapshot_name_collision_increments_suffix`: Pre-populate state with snapshots `"testvm.20250713T1531"` and `"testvm.20250713T1531_1"`. Generate name at same timestamp. Assert `"testvm.20250713T1531_2"`.
- `test_core_uses_config_timestamp_format_for_snapshot_name`: Configure `MockConfigFacade` with `GlobalConfig(timestamp_format="short")`. Mock `datetime.now` to return `2025-07-13 15:31`. Call `core._generate_snapshot_name(vm_config)`. Assert name matches `"testvm.20250713"` (short format, no time component).
- `test_core_timestamp_format_long_produces_long_name`: Same with `timestamp_format="long"`. Assert name matches `"testvm.20250713T1531"`.
- `test_core_timestamp_format_long_iso_produces_iso_name`: Same with `timestamp_format="long-iso"`. Assert name includes UTC offset.
- `test_core_passes_preserve_day_of_week_to_retention_engine`: Configure `MockConfigFacade` with `GlobalConfig(preserve_day_of_week="tuesday")`. Spy on `MockRetentionEngine.evaluate`. Call `core.run()`. Assert `evaluate` was called with `preserve_day_of_week="tuesday"` (or the parameter appears in the call).

---

### Group: core-pipeline

**Scope:** Dry-run mode pipeline behavior. This group MODIFIES the existing
`test_pipeline.py` to update the dry-run test with stronger assertions about
INFO logging of planned actions.

**Test File:** `tests/core/test_pipeline.py` (MODIFY)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | #47 Dry-run logs planned actions | MODIFY — update existing `test_dry_run_logs_no_mutation` |

**Modifications to existing tests:**

- `test_dry_run_logs_no_mutation` (MODIFY): The existing test already asserts no state writes, no shell calls, no snapshot creation. Add assertions using `caplog` that planned snapshot names are logged at INFO level (e.g., `[dry-run] Would create snapshot ...`). This satisfies the "Dry-run logs planned actions" scenario which requires "each planned action is logged at INFO level."

---

### Group: retention

**Scope:** `TimeBasedRetention.evaluate()` with `preserve_day_of_week`
parameter. This group MODIFIES the existing `test_time_based.py` to add
weekly bucket boundary tests with non-default days.

**Test File:** `tests/modules/retention/test_time_based.py` (MODIFY)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/retention/test_time_based.py` | #29 Weekly retention with Tuesday boundary, #30 Weekly retention with default Monday, #56 preserve_day_of_week controls weekly grouping | MODIFY — add new test functions |

**New test functions:**

- `test_weekly_retention_tuesday_boundary_keeps_four`: Construct items spanning 5 weeks with snapshots on both sides of Tuesday. Set `preserve_day_of_week="tuesday"`, `weekly=4`. Assert exactly 4 weekly snapshots are kept, with the "first of week" being the first snapshot on or after Tuesday. The 5th week's snapshot is removed.
- `test_weekly_retention_default_monday_boundary_keeps_two`: Construct items spanning 3 ISO weeks. Call `evaluate()` without `preserve_day_of_week` (defaults to `"monday"`). Set `weekly=2`. Assert 2 kept, with Monday as the week boundary (ISO week boundary).
- `test_preserve_day_of_week_sunday_boundary_keeps_two`: Construct items spanning 3 weeks where a Sunday-Monday boundary matters. Set `preserve_day_of_week="sunday"`, `weekly=2`. Assert the Sunday boundary shifts the bucket grouping so that a snapshot on Sunday is grouped with the previous week, not the following Monday-based week.
- `test_preserve_day_of_week_case_insensitive`: Pass `preserve_day_of_week="TUESDAY"` (uppercase). Assert it works the same as `"tuesday"`.
- `test_preserve_day_of_week_does_not_affect_other_buckets`: Set `preserve_day_of_week="wednesday"` with `hourly=24, daily=7, weekly=0`. Assert hourly and daily results are identical to calling without the parameter (only weekly bucket is affected).

---

### Group: config-model

**Scope:** `GlobalConfig` dataclass defaults. This group MODIFIES the existing
`test_model.py` to update the `timestamp_format` default assertion from
`"short"` to `"long"` (per spec: "Default behavior SHALL match `"long"`").

**Test File:** `tests/config/test_model.py` (MODIFY)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | (indirectly supports #55 timestamp_format controls snapshot naming) | MODIFY — update `test_global_config_defaults` |

**Modifications to existing tests:**

- `test_global_config_defaults` (MODIFY): Change `assert cfg.timestamp_format == "short"` to `assert cfg.timestamp_format == "long"`. The spec states "Default SHALL be `"long"`" in timestamp-formatting/spec.md and "Default behavior SHALL match `"long"`" in config-model/spec.md. The current dataclass has `timestamp_format: str = "short"` — this must change to `"long"`.
- `test_global_config_immutable` (MODIFY): Update the constructor call from `GlobalConfig(timestamp_format="long", ...)` to `GlobalConfig(timestamp_format="short", ...)` (to verify a non-default value can still be set) — or leave as-is since it's just testing immutability with any value.

---

### Group: config-facade

**Scope:** `ConfigFacade` validation of `preserve_day_of_week`. This group
MODIFIES the existing `test_facade.py` to add validation tests.

**Test File:** `tests/config/test_facade.py` (MODIFY)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_facade.py` | #57 Valid day of week, #58 Invalid day of week | MODIFY — add new test functions |

**New test functions:**

- `test_preserve_day_of_week_valid_value_accepted`: Write a TOML fixture with `preserve_day_of_week = "friday"`. Parse with `ConfigFacade`. Assert `facade.get_global().preserve_day_of_week == "friday"`.
- `test_preserve_day_of_week_invalid_value_raises_configerror`: Write a TOML fixture with `preserve_day_of_week = "funday"`. Assert `ConfigFacade(fixture)` raises `ConfigError` with a message indicating valid values (monday–sunday).
- `test_preserve_day_of_week_case_insensitive_accepted`: Write a TOML fixture with `preserve_day_of_week = "FRIDAY"`. Assert `ConfigFacade` accepts it and stores `"friday"` (lowercased).
- `test_preserve_day_of_week_all_seven_days_accepted`: Parametrize over all 7 valid day names. Assert each is accepted.

**Fixture files needed:**
- `tests/fixtures/configs/preserve_dow_valid.toml` — `preserve_day_of_week = "friday"`
- `tests/fixtures/configs/preserve_dow_invalid.toml` — `preserve_day_of_week = "funday"`

---

### Group: config-parser

**Scope:** `ConfigFacade` parsing of `lockfile`, `timestamp_format`, and
`preserve_day_of_week` global fields. This group MODIFIES the existing
`test_parser.py` to add parsing tests for newly-activated fields.

**Test File:** `tests/config/test_parser.py` (MODIFY)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_parser.py` | #54 Lockfile from config is used | MODIFY — add new test functions |

**New test functions:**

- `test_config_parser_reads_lockfile_field_into_globalconfig`: Write a TOML fixture with `lockfile = "/var/lock/qsnap.lock"`. Parse with `ConfigFacade`. Assert `facade.get_global().lockfile == "/var/lock/qsnap.lock"`.
- `test_config_parser_reads_timestamp_format_field`: Write a TOML fixture with `timestamp_format = "short"`. Parse. Assert `facade.get_global().timestamp_format == "short"`.
- `test_config_parser_reads_preserve_day_of_week_field`: Write a TOML fixture with `preserve_day_of_week = "wednesday"`. Parse. Assert `facade.get_global().preserve_day_of_week == "wednesday"`.
- `test_config_parser_lockfile_defaults_to_none`: Parse `minimal.toml` (no lockfile field). Assert `facade.get_global().lockfile is None`.
- `test_config_parser_timestamp_format_defaults_to_long`: Parse `minimal.toml` (no timestamp_format field). Assert `facade.get_global().timestamp_format == "long"` (after the default change).

**Fixture files needed:**
- `tests/fixtures/configs/global_fields.toml` — all global fields set: `timestamp_format`, `preserve_day_of_week`, `lockfile`, `snapshot_preserve`, `target_preserve`

---

### Group: systemd

**Scope:** Systemd unit file content verification and example config
parseability. Tests read the shipped `.service` and `.timer` files as text
and assert on key directives. The example config test parses the shipped
`.toml.example` file with `ConfigFacade`.

**Test File:** `tests/systemd/test_units.py` (NEW)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/systemd/test_units.py` | #42 Service runs qsnap, #43 Timer triggers service, #44 Persistent timer catches up after sleep, #45 Separate hourly and weekly timers, #46 Example config is parseable | NEW |

**Key test details:**

- `test_service_unit_execstart_runs_qsnap_run_with_config`: Read `qsnap.service` (or `systemd/qsnap.service`). Assert `ExecStart` contains `qsnap` and `run`. Assert `Type=oneshot`.
- `test_timer_unit_triggers_service_on_hourly_calendar`: Read `qsnap.timer`. Assert `OnCalendar=hourly` (or `OnCalendar=*:0`). Assert `Unit=qsnap.service` (or implied).
- `test_timer_unit_has_persistent_true`: Read `qsnap.timer`. Assert `Persistent=true` appears.
- `test_timer_unit_has_randomized_delay`: Read `qsnap.timer`. Assert `RandomizedDelaySec=300` appears.
- `test_multiple_timer_instances_pattern_documented`: Assert that the documentation or comments in the unit files explain how to create additional timer/service pairs with `--config` for different schedules. (This is a documentation/pattern test — verify the example config or README mentions the pattern.)
- `test_example_config_is_parseable_by_configfacade`: Read `qsnap.toml.example` (or `examples/qsnap.toml`). Construct `ConfigFacade(path)`. Assert `get_vms()` returns at least one VM. Assert no exception.

---

### Group: interfaces-retention

**Scope:** Contract test for `IRetentionEngine` ABC — verify the interface
is updated to accept `preserve_day_of_week` and that both `TimeBasedRetention`
and `MockRetentionEngine` satisfy the updated contract.

**Test File:** `tests/interfaces/test_retention_engine.py` (MODIFY)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_retention_engine.py` | (supports #29, #30, #56 — contract for preserve_day_of_week parameter) | MODIFY — add parametrized contract test |

**Modifications to existing tests:**

- `test_iretention_engine_standalone_no_core` (no change needed — still verifies ABC is standalone, no Core inheritance).

**New test functions:**

- `test_retention_engine_evaluate_accepts_preserve_day_of_week`: Parametrize over `[TimeBasedRetention, MockRetentionEngine]`. Call `evaluate(items, policy, now, preserve_day_of_week="tuesday")`. Assert returns `RetentionResult` (not `None`, not exception). This verifies the ABC contract includes the new parameter.
- `test_retention_engine_evaluate_preserve_day_of_week_defaults_to_monday`: Parametrize over both implementations. Call `evaluate(items, policy, now)` without the parameter. Assert no exception (default value works).

---

### Group: mocks

**Scope:** Mock implementation updates. `MockRetentionEngine.evaluate()`
must accept the new `preserve_day_of_week` parameter. The mock test file
verifies the mock factory still returns correct interface types after the
update.

**Test Files:** `tests/mocks/test_mock_factory.py` (MODIFY), `tests/mocks/mock_modules.py` (MODIFY — mock implementation, not a test file)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/mocks/test_mock_factory.py` | (supports #29, #30, #56 — mock accepts new parameter) | MODIFY — add assertion that mock retention engine accepts preserve_day_of_week |

**Modifications:**

- `tests/mocks/mock_modules.py` (MODIFY): Update `MockRetentionEngine.evaluate()` signature to accept `preserve_day_of_week: str = "monday"` as a new optional parameter. The mock can ignore the value (it already keeps everything), but must accept it without error.
- `tests/mocks/test_mock_factory.py` (MODIFY): Add `test_mock_retention_engine_accepts_preserve_day_of_week` — create `MockVMModuleFactory`, get retention engine, call `evaluate(items, policy, now, preserve_day_of_week="wednesday")`, assert returns `RetentionResult`.

---

### Group: conftest (shared fixtures)

**Scope:** Shared fixture additions in `tests/conftest.py` to support new
test files. Not a delegation group with its own scenarios — supports all
groups.

**Test File:** `tests/conftest.py` (MODIFY)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/conftest.py` | (supports all) | MODIFY — add new fixtures |

**New fixtures:**

- `make_global_config`: Factory function to create `GlobalConfig` instances with custom `timestamp_format`, `preserve_day_of_week`, `lockfile` values for tests.
- `mock_lock_manager`: Returns a mock `LockManager` (using `unittest.mock.Mock`) with `acquire()` returning `True` and `release()` being a no-op. Used by CLI tests.
- `cli_app`: Returns the `build_argparser()` result for CLI tests that need to parse argv lists.
- `frozen_clock`: A fixture or context manager that freezes `datetime.now()` to a fixed value, for deterministic snapshot name tests. (Can use `freezegun` or a simple `unittest.mock.patch` on `datetime.now`.)

---

## Test Modifications

Summary of all existing files that need modification (not new files):

| File | Change | Reason |
|---|---|---|
| `tests/core/test_engine.py` | Add assertions for `preserve_snapshots`/`preserve_backups` defaults in `test_core_init_stores_dependencies`; add 6 new test functions for timestamp format and collision suffix | Core gains preserve properties (core-orchestrator spec) and timestamp format consumption (timestamp-formatting, config-model specs) |
| `tests/core/test_pipeline.py` | Update `test_dry_run_logs_no_mutation` to add `caplog` assertions for INFO-level planned action logs | core-orchestrator spec "Dry-run logs planned actions" requires verifying INFO logging, not just absence of mutation |
| `tests/modules/retention/test_time_based.py` | Add 5 new test functions for `preserve_day_of_week` weekly bucket boundary | timestamp-formatting spec "preserve_day_of_week in retention" and config-model spec "preserve_day_of_week controls weekly grouping" |
| `tests/config/test_model.py` | Update `test_global_config_defaults`: change `assert cfg.timestamp_format == "short"` to `assert cfg.timestamp_format == "long"` | timestamp-formatting spec "Default SHALL be `"long"`" and config-model spec "Default behavior SHALL match `"long"`" — dataclass default changes from `"short"` to `"long"` |
| `tests/config/test_facade.py` | Add 4 new test functions for `preserve_day_of_week` validation (valid, invalid, case-insensitive, all 7 days) | config-model spec "GlobalConfig preserve_day_of_week validation" |
| `tests/config/test_parser.py` | Add 5 new test functions for parsing `lockfile`, `timestamp_format`, `preserve_day_of_week` fields | config-model spec "GlobalConfig lockfile field is consumed" and activation of previously-unused fields |
| `tests/interfaces/test_retention_engine.py` | Add 2 parametrized contract tests verifying `evaluate()` accepts `preserve_day_of_week` | timestamp-formatting spec changes `IRetentionEngine.evaluate()` signature; contract tests must verify both `TimeBasedRetention` and `MockRetentionEngine` |
| `tests/mocks/mock_modules.py` | Update `MockRetentionEngine.evaluate()` to accept `preserve_day_of_week: str = "monday"` parameter | ABC signature change requires mock to accept the new parameter; otherwise CLI/Core tests passing `preserve_day_of_week` to the mock would fail |
| `tests/mocks/test_mock_factory.py` | Add 1 test verifying mock retention engine accepts `preserve_day_of_week` | Verify mock update is correct |
| `tests/conftest.py` | Add `make_global_config`, `mock_lock_manager`, `cli_app`, `frozen_clock` fixtures | New test files need shared fixtures for CLI app, lock manager mocking, and deterministic timestamps |

**New fixture files needed:**
- `tests/fixtures/configs/preserve_dow_valid.toml` — `preserve_day_of_week = "friday"`
- `tests/fixtures/configs/preserve_dow_invalid.toml` — `preserve_day_of_week = "funday"`
- `tests/fixtures/configs/global_fields.toml` — all global fields set

**New `__init__.py` files needed:**
- `tests/cli/__init__.py`
- `tests/systemd/__init__.py`

---

## Risks & Edge Cases

Risks extracted from `design.md` § Risks / Trade-offs, plus additional edge
cases identified during analysis.

- **[R1] CLI surface is large for a single change** — The CLI has 7 subcommands and 10+ global flags. Risk: a flag or subcommand is silently dropped or misrouted.
  - *Test:* `test_help_text_lists_subcommands_and_flags` (cli-app) verifies all subcommands and flags appear in `--help`.
  - *Test:* `test_*_subcommand_dispatches_to_core_*` (cli-commands) — one test per subcommand, each asserting the correct Core method is called.

- **[R2] argparse subcommand routing is verbose** — Risk: nested if-else chains instead of a dispatch table, making it hard to test.
  - *Test:* `test_commands_py_has_no_business_logic_imports` (cli-thin-layer) — uses `ast` to verify no forbidden imports, which also implicitly checks the file is a thin dispatch layer.
  - *Test:* Each subcommand dispatch test verifies the handler function exists and calls the correct Core method.

- **[R3] `fcntl.flock` is Linux-only** — Risk: tests fail on non-Linux CI. Mitigation: qsnap targets Linux (libvirt/KVM requirement).
  - *Test:* `test_acquire_lock_when_free_returns_true` and `test_acquire_lock_when_held_returns_false` (locking) — use real `fcntl.flock` on temp files. These will fail on non-Linux, which is acceptable.
  - *Edge case:* `test_none_lockfile_path_means_no_locking` — verify `LockManager(None)` is a no-op (no file created, acquire returns True). This allows running without locking on any platform.

- **[R4] `preserve_day_of_week` changes retention bucket grouping** — Risk: existing weekly retention tests break because `_bucket_key()` changes behavior.
  - *Test:* `test_weekly_retention_default_monday_boundary_keeps_two` (retention) — verifies the default behavior is unchanged (Monday boundary = ISO week).
  - *Test:* `test_weekly_retention_tuesday_boundary_keeps_four` (retention) — verifies non-default day shifts the boundary.
  - *Test:* `test_preserve_day_of_week_does_not_affect_other_buckets` (retention) — verifies hourly/daily/monthly/yearly buckets are unaffected.
  - *Edge case:* `test_preserve_day_of_week_case_insensitive` — `"TUESDAY"` should work the same as `"tuesday"`.
  - *Edge case:* Snapshots exactly at midnight on the boundary day — verify they land in the correct week.

- **[R5] Schedule printing reuses retention engine** — Risk: `print_schedule()` accidentally calls `blockcommit()` or `delete()`.
  - *Test:* `test_print_schedule_does_not_call_mutating_shell_commands` (core-list) — spies on `IShell.run` and asserts no mutating commands.
  - *Test:* `test_print_schedule_does_not_execute_mutating_commands` (core-list) — spies on `ILifecycleManager.blockcommit` and `IBackupProvider.delete`, asserts neither called.
  - *Edge case:* `print_schedule()` with empty snapshot list — should return empty schedule, not crash.

- **[R-EXTRA-1] Collision suffix increments correctly** — Risk: two snapshots created in the same minute get the same name, causing a file overwrite.
  - *Test:* `test_generate_snapshot_name_appends_collision_suffix` (core-engine) — first duplicate gets `_1`.
  - *Test:* `test_generate_snapshot_name_collision_increments_suffix` (core-engine) — second duplicate gets `_2`.
  - *Edge case:* What if `_99` is reached? (Spec says starting at 1, no upper bound mentioned — test up to `_2` is sufficient.)

- **[R-EXTRA-2] Lockfile path resolution order** — Risk: config lockfile used when CLI lockfile should take precedence.
  - *Test:* `test_lockfile_path_resolution_cli_overrides_config` (locking) — CLI path wins.
  - *Test:* `test_lockfile_path_resolution_config_when_no_cli` (locking) — config path used when no CLI flag.
  - *Test:* `test_lockfile_path_resolution_none_when_both_none` (locking) — no locking when both are None.
  - *Edge case:* CLI `--lockfile` with empty string — should this be treated as None or as a path? (Spec doesn't mention; test should follow implementation.)

- **[R-EXTRA-3] Preserve mode with failed backup** — Risk: in preserve mode, a failed backup transfer might still trigger deletion of old backups (defeating the purpose of preserve).
  - *Test:* `test_preserve_mode_failed_backup_error_reported_no_deletion` (core-preserve) — failed transfer reports error, `delete()` NOT called.
  - *Edge case:* Preserve mode with all backups failing — all errors reported, no deletions.

- **[R-EXTRA-4] Dry-run + preserve interaction** — Risk: dry-run and preserve flags interact in unexpected ways (e.g., dry-run skips retention evaluation, breaking schedule printing).
  - *Test:* `test_dry_run_logs_planned_actions_no_mutation` (core-pipeline) — dry-run alone: no mutation, logs planned actions.
  - *Test:* `test_preserve_snapshots_retention_still_evaluated` (core-preserve) — preserve alone: retention still evaluated.
  - *Edge case:* Both dry-run AND preserve active — retention should still be evaluated (for schedule), no mutations, no deletions. (Covered by combining the two test scenarios; no separate test needed unless behavior differs.)

- **[R-EXTRA-5] `timestamp_format` default change breaks existing configs** — Risk: changing the default from `"short"` to `"long"` changes snapshot naming for configs that don't specify `timestamp_format`.
  - *Test:* `test_config_parser_timestamp_format_defaults_to_long` (config-parser) — verify the default is `"long"` after the change.
  - *Test:* `test_core_timestamp_format_long_produces_long_name` (core-engine) — verify `"long"` format produces `YYYYMMDDThhmm`.
  - *Edge case:* Config with `timestamp_format = "short"` explicitly set — should still produce short format (backward compatible).

- **[R-EXTRA-6] `check()` with multiple VMs, mixed health** — Risk: one broken chain causes the entire check to fail, or errors are silently swallowed.
  - *Test:* `test_check_healthy_backing_chain_reports_ok` and `test_check_broken_chain_reports_broken_status` (core-list) — verify per-VM, per-snapshot status reporting.
  - *Edge case:* VM with no snapshots — `check()` should report "ok" (no chain to break) or "no snapshots" (not broken).

- **[R-EXTRA-7] Exit code 10 (backup abort)** — The cli-interface spec mentions exit code 10 for backup abort, but no scenario tests it explicitly.
  - *Test:* `test_backup_abort_returns_exit_code_10` (cli-app, additional) — when `PipelineResult.success is False` due to backup failure, `main()` exits with code 10.
  - *Edge case:* Mixed success/failure across VMs — if any backup failed, exit code 10 (not 0).

- **[R-EXTRA-8] `--format col:` with invalid column names** — Risk: user specifies a column that doesn't exist on the dataclass.
  - *Test:* `test_format_col_invalid_column_raises_or_ignores` (cli-format, additional) — verify graceful handling (empty column or error message, not a crash).
