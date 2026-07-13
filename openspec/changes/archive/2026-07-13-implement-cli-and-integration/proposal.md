## Why

qsnap currently has a fully functional domain layer (snapshot creation, change detection, blockcommit lifecycle, incremental backups, retention engine) — but no user-facing entry point. There is no CLI, no way to specify a config file at runtime, no dry-run mode accessible from the command line, no informational commands to inspect snapshots or backups, no `--preserve` flags, and no systemd integration. The project is a library without an executable. This change bridges that gap, making qsnap a complete btrbk-equivalent tool ready for production deployment via cron/systemd timers.

## What Changes

- **CLI entry point** (`qsnap` command) with subcommands: `run`, `snapshot`, `backup`, `prune`, `list`, `stats`, `check`
- **Global flags**: `--config`/`-c`, `--dry-run`/`-n`, `--preserve`/`-p`, `--preserve-snapshots`, `--preserve-backups`, `--verbose`/`-v`, `--quiet`/`-q`, `--loglevel`/`-l`, `--print-schedule`/`-S`, `--format`, `--lockfile`
- **Informational commands**: `list snapshots`, `list backups`, `list config`, `list latest`, `stats`, `check` (chain integrity)
- **Dry-run support** exposed via CLI flag, reusing existing `Core.dry_run` property
- **`--preserve` flags** wired into Core pipeline to skip retention deletion steps
- **Config file flag** allows runtime selection of TOML config (essential for systemd services with different configs per timer)
- **Unused config fields activated**: `timestamp_format` (short/long/long-iso), `preserve_day_of_week`, `lockfile`
- **Lockfile mechanism** using `fcntl.flock` to prevent concurrent runs
- **Systemd service and timer** units for scheduled execution
- **Example config** file shipped with the package
- **CLI is a thin translation layer**: args → Core methods → formatted output (no business logic in CLI)

## Capabilities

### New Capabilities

- `cli-interface`: Command-line entry point with subcommands (`run`, `snapshot`, `backup`, `prune`, `list`, `stats`, `check`), global flags (`--config`, `--dry-run`, `--preserve*`, `--verbose`, `--quiet`, `--loglevel`, `--print-schedule`, `--format`, `--lockfile`), exit codes, and machine-readable output formats. Thin translation layer: CLI args → Core methods → formatted output. No business logic.

- `locking`: Lockfile mechanism using `fcntl.flock` to prevent concurrent pipeline execution. Lockfile path configurable via `GlobalConfig.lockfile` and `--lockfile` CLI flag. Releases lock on normal exit and crashes. Provides clear error message when lock is held.

- `timestamp-formatting`: Timestamp format resolution from `GlobalConfig.timestamp_format` (short/long/long-iso). `Core._generate_snapshot_name()` uses the configured format instead of hardcoded `"%Y%m%dT%H%M%S"`. `TimeBasedRetention` respects `preserve_day_of_week` for weekly bucket grouping.

- `list-commands`: `Core.list_snapshots()`, `Core.list_backups()`, `Core.list_config()`, `Core.print_schedule()` methods providing informational output. `Core.check()` verifies backing chain integrity. Exposed via `qsnap list <subcommand>` and `qsnap stats` CLI commands.

- `systemd-integration`: Systemd service unit (`qsnap.service`) and timer unit (`qsnap.timer`) for scheduled pipeline execution. Supports `--config` flag to allow different timers with different configs for different conditions (e.g., hourly snapshots vs. weekly backups).

### Modified Capabilities

- `core-orchestrator`: Core gains `--preserve` flag support — new boolean properties `preserve_snapshots` and `preserve_backups` that suppress deletion steps in `_execute_snapshot_steps()` and `_execute_backup_steps()`. `dry_run` property already exists but is activated via CLI. New public methods: `list_snapshots()`, `list_backups()`, `list_config()`, `print_schedule()`, `check()`. Schedule printing reuses retention engine in evaluate-only mode.

- `config-model`: `GlobalConfig.lockfile` and `GlobalConfig.timestamp_format` and `GlobalConfig.preserve_day_of_week` fields already exist in the dataclass but are unused. No schema changes needed — this change activates existing fields.

## Impact

- **New files**: `qsnap/cli/` package (app.py, commands.py, format.py, errors.py), `qsnap/__main__.py`, systemd units (`qsnap.service`, `qsnap.timer`), example config (`qsnap.toml.example`), locking module (`qsnap/locking.py`)
- **Modified files**: `qsnap/core/__init__.py` (preserve flags, list/check/schedule methods, timestamp format usage), `qsnap/retention/time_based.py` (preserve_day_of_week support), `pyproject.toml` (add `[project.scripts]` entry point)
- **New dependencies**: `argparse` (stdlib, no external), `logging.config` (stdlib)
- **Tests**: `tests/cli/` (commands, app), `tests/utils/test_locking.py`, `tests/utils/test_time.py`, contract tests for new Core methods
- **No breaking changes**: All existing APIs remain compatible; CLI is additive
