## Context

qsnap has a complete domain layer (snapshot creation, change detection, blockcommit lifecycle, incremental backups, retention engine) and a DI-driven Core orchestrator with methods `run()`, `snapshot()`, `backup()`, `prune()` that accept an optional `vm_filter` string. Core already has a `dry_run` boolean property. All module instantiation goes through `IVMModuleFactory`.

What is missing is the user-facing layer: a CLI entry point, informational commands, `--preserve` flags wired into Core, activation of unused config fields (`timestamp_format`, `preserve_day_of_week`, `lockfile`), lockfile mechanism, and systemd integration.

The CLI must follow the btrbk command structure closely to provide a familiar experience for users migrating from btrbk.

## Goals / Non-Goals

**Goals:**
- Provide `qsnap` CLI command with subcommands: `run`, `snapshot`, `backup`, `prune`, `list`, `stats`, `check`
- Support all btrbk-equivalent global flags: `--config`, `--dry-run`, `--preserve*`, `--verbose`/`--quiet`, `--loglevel`, `--print-schedule`, `--format`, `--lockfile`
- CLI is a thin translation layer: argparse → Core methods → formatted output
- Wire `--preserve` flags into Core to skip retention deletion steps
- Activate `timestamp_format`, `preserve_day_of_week`, `lockfile` config fields
- Implement lockfile mechanism via `fcntl.flock`
- Provide systemd service + timer units
- Ship example TOML config file

**Non-Goals:**
- Multi-disk VM support (hardcoded `"vda"` remains — separate change)
- SSH/remote backup targets (separate change)
- Raw backup target format (separate change)
- Integration/e2e/stress tests (unit + contract tests only)
- Packaging (RPM/DEB/PyPI — separate change)
- `qsnap archive` command (separate change)

## Decisions

### D1: argparse (stdlib) for CLI

**Choice:** `argparse` with subparsers.

**Alternatives rejected:**
- `click` — external dependency, decorator-heavy, harder to test argument parsing in isolation
- `typer` — also external, requires `click`
- Manual `sys.argv` parsing — error-prone

**Rationale:** `argparse` is stdlib, matches the project's zero-runtime-dependency philosophy. Subcommands map cleanly to Core methods. Thin translation layer is trivially testable: parse args → assert correct Core method called with correct parameters.

### D2: CLI layer structure

```
qsnap/cli/
  __init__.py
  app.py          # main(), build_argparser(), dispatch to commands
  commands.py     # handle each subcommand: validate args → create Core → call method → format output
  format.py       # output formatters: table, long, raw, col:
  errors.py       # exit codes (0=success, 1=generic, 2=parse error, 3=lockfile, 10=backup abort), error formatting
```

`commands.py` does NOT parse config, create snapshots, or evaluate retention. It is a thin translation layer: CLI args → Core call → formatted output (per AGENTS.md anti-pattern rule).

### D3: Output formats (mirroring btrbk)

| Format | Flag | Description |
|---|---|---|
| `table` | `--format table` (default) | Human-readable columns, uppercase headers |
| `long` | `--format long` / `-L` | Extended table with more columns |
| `raw` | `--format raw` | `key=value` pairs, space-separated, machine-readable |
| `col:` | `--format col:name,path,timestamp` | Custom column selection |

`format.py` provides a `format_output()` function that takes a list of dataclass instances + column definitions and renders in the selected format.

### D4: Preserve flags as Core properties

**Choice:** Add `preserve_snapshots: bool` and `preserve_backups: bool` properties to Core, defaulting to `False`. CLI sets them via `core.preserve_snapshots = True` / `core.preserve_backups = True`. The `--preserve` flag sets both.

**Pipeline behavior when preserve is active:**
- `_evaluate_snapshot_retention()` still runs (needed for schedule printing) but results are ignored in `_blockcommit_snapshots()` — nothing is deleted
- `_backup_target()` skips the retention→delete loop for backups

### D5: Informational commands as Core methods

New public Core methods:
- `list_snapshots(vm_filter=None) → dict[str, list[SnapshotInfo]]` — calls `IStateManager.get_snapshots()` per VM
- `list_backups(vm_filter=None) → dict[str, list[SnapshotInfo]]` — calls `IBackupProvider.list()` per target per VM
- `list_config() → list[VMConfig]` — returns `IConfigFacade.get_vms()`
- `list_latest(vm_filter=None) → dict[str, SnapshotInfo | None]` — returns most recent snapshot per VM
- `print_schedule(vm_filter=None) → dict[str, RetentionResult]` — evaluates retention without executing blockcommit or backup deletion, formats as schedule
- `check(vm_filter=None) → dict[str, CheckResult]` — verifies backing chain integrity via `qemu-img info --backing-chain` checks (each snapshot's backing file exists, no broken chain links)

### D6: Lockfile via fcntl.flock

**Choice:** `fcntl.flock` on a file descriptor, acquired at process start, released on exit.

```python
class LockManager:
    def __init__(self, lockfile_path: str | None): ...
    def acquire(self) -> bool: ...  # non-blocking; returns False if already held
    def release(self) -> None: ...
```

The lockfile path defaults to `GlobalConfig.lockfile` (which may be `None` → no locking) and can be overridden by `--lockfile` CLI flag. CLI acquires the lock before creating Core, exits with code 3 if already held.

**Rationale:** `flock` is Linux-native, released automatically on process death (no stale lock files), and works with the project's no-external-deps policy.

### D7: Timestamp format resolution

**Choice:** `Core._generate_snapshot_name()` reads `GlobalConfig.timestamp_format` and selects the format string:

| `timestamp_format` | Python format | Example |
|---|---|---|
| `"short"` | `%Y%m%d` | `20250713` |
| `"long"` (default) | `%Y%m%dT%H%M` | `20250713T1531` |
| `"long-iso"` | `%Y%m%dT%H%M%S%z` | `20250713T153123+0200` |

`preserve_day_of_week` is passed to `TimeBasedRetention.evaluate()` as a new optional parameter and used in `_bucket_key()` for the `weekly` bucket to shift the week boundary to the configured day.

### D8: Systemd units

`qsnap.service`: `ExecStart=/usr/bin/qsnap -c /etc/qsnap/qsnap.toml run`
`qsnap.timer`: `OnCalendar=hourly`, `Persistent=True`, `RandomizedDelaySec=300`

Users create additional timer/service pairs with `--config` flag for different schedules (e.g., `qsnap-hourly.timer` → `qsnap run`, `qsnap-weekly.timer` → `qsnap -c /etc/qsnap/weekly.toml backup`).

### D9: Exit codes (mirroring btrbk)

| Code | Meaning |
|---|---|
| 0 | Success — no problems |
| 1 | Generic error |
| 2 | Parse error (CLI args or config file) |
| 3 | Lockfile error (another instance running) |
| 10 | Backup abort (at least one backup task failed) |

## Risks / Trade-offs

- **[R1] CLI surface is large for a single change** — Mitigation: phased task breakdown; informational commands can be implemented incrementally
- **[R2] `argparse` subcommand routing is verbose** — Mitigation: well-structured `app.py` with a dispatch table mapping subcommand names to handler functions; no nested if-else chains
- **[R3] `fcntl.flock` is Linux-only** — Mitigation: qsnap targets Linux (libvirt/KVM requirement); acceptable
- **[R4] `preserve_day_of_week` changes retention bucket grouping** — Mitigation: existing tests parametrize over `RetentionPolicy`; add test cases with non-default `preserve_day_of_week` to `test_time_based.py`
- **[R5] Schedule printing reuses retention engine** — Mitigation: `print_schedule()` calls `IRetentionEngine.evaluate()` but does NOT call `blockcommit()` or `delete()`; safe
