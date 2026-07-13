## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b feat/implement-cli-and-integration`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`

## 2. CLI Entry Point & Global Flags

- [x] 2.1 Create `qsnap/cli/` package (`__init__.py`) with re-exports
- [x] 2.2 Create `qsnap/cli/errors.py` — exit code constants (0=success, 1=generic, 2=parse error, 3=lockfile, 10=backup abort)
- [x] 2.3 Create `qsnap/cli/format.py` — output formatters: `table`, `long`, `raw`, `col:<columns>` (spec `cli-interface`, D3)
- [x] 2.4 Create `qsnap/cli/app.py` — `main()`, `build_argparser()` with subparsers for `run`, `snapshot`, `backup`, `prune`, `list`, `stats`, `check`, and all global flags (`--config`, `--dry-run`, `--preserve*`, `--verbose`, `--quiet`, `--loglevel`, `--print-schedule`, `--format`, `--lockfile`) (spec `cli-interface`, D1)
- [x] 2.5 Create `qsnap/cli/commands.py` — handler functions for each subcommand: parse args → create ConfigFacade/SubprocessShell/JsonStateManager/DefaultFactory/Core → set dry_run + preserve properties → call Core method → format output (spec `cli-interface` last requirement, D2)
- [x] 2.6 Create `qsnap/__main__.py` — calls `qsnap.cli.app.main()`
- [x] 2.7 Update `pyproject.toml` — add `[project.scripts]` entry: `qsnap = "qsnap.cli.app:main"` (D9)
- [x] 2.8 Wire VM filter positional arguments: action subcommands (`run`, `snapshot`, `backup`, `prune`) accept optional VM names, passed to `Core.run(vm_filter=...)` (spec `cli-interface`)
- [x] 2.9 Wire dry-run flag: `--dry-run` / `-n` sets `Core.dry_run = True` (spec `cli-interface`, spec `core-orchestrator` MODIFIED dry-run)
- [x] 2.10 Wire verbose/quiet/loglevel flags: configure `logging.basicConfig()` level from CLI flags (spec `cli-interface`)

## 3. Preserve Flags

- [x] 3.1 Add `preserve_snapshots: bool = False` and `preserve_backups: bool = False` properties to `Core` (spec `core-orchestrator` ADDED, D4)
- [x] 3.2 Modify `Core._blockcommit_snapshots()` — when `self.preserve_snapshots` is True, log and skip blockcommit instead of executing it (spec `core-orchestrator`)
- [x] 3.3 Modify `Core._backup_target()` and `Core._execute_prune_steps()` — when `self.preserve_backups` is True, skip backup deletion loop, log planned deletions instead (spec `core-orchestrator`)
- [x] 3.4 Wire CLI `--preserve` flag to set both properties, `--preserve-snapshots` / `--preserve-backups` to set individually (spec `cli-interface`)

## 4. Informational Commands (Core + CLI)

- [x] 4.1 Add `Core.list_snapshots(vm_filter=None) -> dict[str, list[SnapshotInfo]]` — reads from `IStateManager.get_snapshots()` (spec `list-commands`, D5)
- [x] 4.2 Add `Core.list_backups(vm_filter=None) -> dict[str, list[SnapshotInfo]]` — calls `IBackupProvider.list()` per target (spec `list-commands`, D5)
- [x] 4.3 Add `Core.list_config() -> list[VMConfig]` — returns `IConfigFacade.get_vms()` (spec `list-commands`, D5)
- [x] 4.4 Add `Core.list_latest(vm_filter=None) -> dict[str, SnapshotInfo | None]` — returns most recent snapshot per VM by timestamp (spec `list-commands`, D5)
- [x] 4.5 Add `Core.print_schedule(vm_filter=None) -> dict[str, RetentionResult]` — evaluates retention via `IRetentionEngine.evaluate()` without executing any deletion (spec `list-commands`, spec `core-orchestrator`, D5, R5)
- [x] 4.6 Add `Core.check(vm_filter=None) -> dict[str, CheckResult]` — verifies backing chain integrity via `qemu-img info --backing-chain` (spec `list-commands`, D5)
- [x] 4.7 Create `qsnap/models/results.py` additions — `CheckResult` dataclass (vm_name, status, broken_snapshots)
- [x] 4.8 Wire CLI `list` subcommand with sub-subcommands: `snapshots`, `backups`, `config`, `latest` (spec `cli-interface`)
- [x] 4.9 Wire CLI `stats` subcommand — delegating to `list_snapshots()` + `list_backups()` and formatting counts/sizes
- [x] 4.10 Wire CLI `check` subcommand — delegating to `Core.check()` and formatting results
- [x] 4.11 Wire CLI `--print-schedule` / `-S` flag on action commands — calls `Core.print_schedule()`, prints keep/remove per VM before executing (spec `cli-interface`)

## 5. Lockfile Mechanism

- [x] 5.1 Create `qsnap/locking.py` — `LockManager` class using `fcntl.flock` (non-blocking), with `acquire()` returning bool and `release()` method (spec `locking`, D6)
- [x] 5.2 Integrate LockManager into CLI `main()`: resolve lockfile path from `--lockfile` flag → `GlobalConfig.lockfile`, acquire before pipeline, exit code 3 if held, release in `finally` block (spec `locking`)
- [x] 5.3 Ensure `GlobalConfig.lockfile` field (already exists, default `None`) is consumed; `None` means no locking (spec `config-model` ADDED)

## 6. Timestamp Formatting & preserve_day_of_week

- [x] 6.1 Modify `Core._generate_snapshot_name()` — read `GlobalConfig.timestamp_format` (default `"long"`), map to Python format string: `short`→`%Y%m%d`, `long`→`%Y%m%dT%H%M`, `long-iso`→`%Y%m%dT%H%M%S%z` (spec `timestamp-formatting`, spec `config-model` ADDED, D7)
- [x] 6.2 Add collision suffix logic: if snapshot/backup file with same timestamp already exists, append `_N` (starting at 1) (spec `timestamp-formatting`)
- [x] 6.3 Modify `TimeBasedRetention.evaluate()` — accept optional `preserve_day_of_week` parameter (default `"monday"`), use in `_bucket_key()` for weekly bucket grouping (spec `timestamp-formatting`, D7)
- [x] 6.4 Modify `Core._evaluate_snapshot_retention()` and `Core._backup_target()` — pass `GlobalConfig.preserve_day_of_week` to `IRetentionEngine.evaluate()` (spec `config-model` ADDED)
- [x] 6.5 Add `preserve_day_of_week` validation in `ConfigFacade._parse()` — must be one of monday..sunday (case-insensitive), raise `ConfigError` on invalid value (spec `config-model` ADDED)

## 7. Systemd Integration & Example Config

- [x] 7.1 Create `systemd/qsnap.service` — oneshot service: `ExecStart=/usr/bin/qsnap -c /etc/qsnap/qsnap.toml run` (spec `systemd-integration`)
- [x] 7.2 Create `systemd/qsnap.timer` — `OnCalendar=hourly`, `Persistent=True`, `RandomizedDelaySec=300` (spec `systemd-integration`)
- [x] 7.3 Create example config `qsnap.toml.example` with commented documentation for every option (spec `systemd-integration`)

## 8. Testing

**CRITICAL PROTOCOL for test delegation:** Before delegating ANY group to a @Mr.Tester subagent, the main programmer agent MUST provide the TESTING.md file (located at the project root) to each @Mr.Tester. TESTING.md defines the project's test paradigm: mocked IShell, factory-based instantiation, contract test patterns, directory structure conventions, and the rule that domain modules do NOT inherit from Core (design D1). Every @Mr.Tester MUST read and follow TESTING.md.

- [x] 8.1 Read `test-plan.md` Delegation Groups section to understand group structure and scope
- [x] 8.2 Verify `tests/conftest.py` is updated with new fixtures (LockManager mock, preserve-flags Core instance) — group `conftest`
- [x] 8.3 Delegate group `cli-app` to @Mr.Tester (scope: `tests/cli/test_app.py` — 7 scenarios: help text, config path, verbose/quiet, lockfile override, exit codes, lockfile held → code 3). Pass TESTING.md to the agent.
- [x] 8.4 Delegate group `cli-commands` to @Mr.Tester (scope: `tests/cli/test_commands.py` — 8 scenarios: subcommand dispatch, dry-run, preserve flags, print-schedule, VM filter). Pass TESTING.md to the agent.
- [x] 8.5 Delegate group `cli-format` to @Mr.Tester (scope: `tests/cli/test_format.py` — 2 scenarios: table output, raw output). Pass TESTING.md to the agent.
- [x] 8.6 Delegate group `cli-thin-layer` to @Mr.Tester (scope: `tests/cli/test_thin_layer.py` — 1 scenario: no business logic imports). Pass TESTING.md to the agent.
- [x] 8.7 Delegate group `locking` to @Mr.Tester (scope: `tests/utils/test_locking.py` — 3 scenarios: acquire/release/held). Pass TESTING.md to the agent.
- [x] 8.8 Delegate group `timestamp-utils` to @Mr.Tester (scope: `tests/utils/test_time.py` — 4 scenarios: short/long/long-iso/collision suffix). Pass TESTING.md to the agent.
- [x] 8.9 Delegate group `core-list` to @Mr.Tester (scope: `tests/core/` — 8 scenarios: list_snapshots, list_backups, list_config, list_latest, print_schedule, check). Pass TESTING.md to the agent.
- [x] 8.10 Delegate group `core-preserve` to @Mr.Tester (scope: `tests/core/` — 5 scenarios: preserve_snapshots skips blockcommit, preserve_backups skips deletion, preserve_all, preserve with failed backup, dry-run + preserve interaction). Pass TESTING.md to the agent.
- [x] 8.11 Delegate group `core-engine` to @Mr.Tester (scope: `tests/core/test_engine.py` — 4 scenarios: preserve property defaults, timestamp format from config, collision suffix, schedule does not mutate). Pass TESTING.md to the agent.
- [x] 8.12 Delegate group `core-pipeline` to @Mr.Tester (scope: `tests/core/test_pipeline.py` — 3 scenarios: dump-xml verify, caplog dry-run assertions, preserve mode skip deletion). Pass TESTING.md to the agent.
- [x] 8.13 Delegate group `retention` to @Mr.Tester (scope: `tests/modules/retention/test_time_based.py` — 5 scenarios: preserve_day_of_week weekly boundary tests for monday/sunday/tuesday/friday + default). Pass TESTING.md to the agent.
- [x] 8.14 Delegate group `config-model` to @Mr.Tester (scope: `tests/config/test_model.py` — 3 scenarios: timestamp_format default is "long", GlobalConfig lockfile/timestamp/preserve_day_of_week fields exist). Pass TESTING.md to the agent.
- [x] 8.15 Delegate group `config-facade` to @Mr.Tester (scope: `tests/config/test_facade.py` — 2 scenarios: timestamp_format consumed, preserve_day_of_week consumed). Pass TESTING.md to the agent.
- [x] 8.16 Delegate group `config-parser` to @Mr.Tester (scope: `tests/config/test_parser.py` — 2 scenarios: valid day_of_week accepted, invalid day_of_week raises ConfigError). Pass TESTING.md to the agent.
- [x] 8.17 Delegate group `systemd` to @Mr.Tester (scope: manual verification — 2 scenarios: example config is parseable, service unit syntax check). Pass TESTING.md to the agent.
- [x] 8.18 Delegate group `interfaces-retention` to @Mr.Tester (scope: `tests/interfaces/test_retention_engine.py` — 1 scenario: contract test parametrized with new preserve_day_of_week parameter). Pass TESTING.md to the agent.
- [x] 8.19 Delegate group `mocks` to @Mr.Tester (scope: `tests/mocks/` — 2 scenarios: MockRetentionEngine.evaluate accepts preserve_day_of_week, MockVMModuleFactory.create_retention_engine signature). Pass TESTING.md to the agent.
- [x] 8.20 Launch ALL groups IN PARALLEL (single message with all @Mr.Tester subagent calls). Each subagent receives: the group's scope, the group's scenario list from test-plan.md Coverage Map, and TESTING.md.
- [x] 8.21 After all @Mr.Tester subagents return — review reports, fix any source-level bugs discovered during test implementation
- [x] 8.22 Re-delegate any groups affected by source fixes (re-run affected @Mr.Tester agents)
- [x] 8.23 Verify full test suite passes: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`
- [x] 8.24 Verify test coverage matches test-plan.md (every spec scenario has a passing test)
- [x] 8.25 Verify code quality: `poetry run ruff check qsnap/` and `poetry run black --check qsnap/`
