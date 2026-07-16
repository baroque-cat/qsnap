## Why

Backup transfers currently use plain `cp` with no bandwidth control, risking I/O saturation on production hosts. Deferred blockcommit operations accumulate silently — growing backing chains, degrading VM performance, and increasing data-loss risk — with no proactive alerting. Users discover problems only by manually inspecting logs or state.

## What Changes

### Rate Limiting

- **New `rate_limit` config field** in `GlobalConfig` and `TargetConfig` (global default + per-target override, following existing option inheritance model). Format: human-readable binary suffixes (`"100M"`, `"500K"`, `"no"` for unlimited).
- **Rsync replaces `cp`** in `FileCopyBackupProvider` for snapshot transfers. Uses `rsync --bwlimit --partial --progress` for bandwidth control, partial-transfer resilience, and transfer progress reporting.
- **Full backup operations** (`qemu-img convert`) are excluded from rate limiting (rare, already resource-intensive, and not suitable for rsync).
- **Transfer logging** at INFO (limit applied and actual throughput), DEBUG (full command line), and WARNING (rsync not found or anomalous throughput).
- **Pre-flight validation** checks for `rsync` availability when `rate_limit != "no"`, emitting a non-blocking WARNING if missing (falls back to `cp`).

### Deferred Operations Monitoring

- **New `qsnap list deferred` command** — table or raw-format listing of deferred blockcommits per VM with snapshot count, reason (apparmor/selinux), and age.
- **New config thresholds** in `GlobalConfig`: `deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age`. Post-pipeline check compares accumulated deferred operations against these thresholds, logging WARNING or CRITICAL.
- **Integration into `qsnap check`** — includes deferred status in the health report with actionable remediation guidance (e.g., "shut down the VM to allow automatic merge" or "aa-disable /etc/apparmor.d/libvirt/libvirt-<uuid>").
- **New `last_warned_at` field** in `DeferredBlockcommit` state record — enables future notification deduplication (WARNING alerts sent max once per day, CRITICAL on every run, resolution notified).

## Capabilities

### New Capabilities

- `rate-limit`: Bandwidth control for backup transfers via rsync with config inheritance (global default + per-target override).
- `deferred-monitoring`: Proactive deferred-blockcommit alerting with CLI visibility (`list deferred`), configurable thresholds, and remediation guidance in `check`.

### Modified Capabilities

- `config-model`: New fields `rate_limit` on `GlobalConfig` and `TargetConfig`; new fields `deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age` on `GlobalConfig`.
- `backup-provider`: `FileCopyBackupProvider` uses `rsync --bwlimit` instead of `cp` when `rate_limit` is active; respects `--partial` for resume-after-interruption resilience.
- `deferred-operations`: `DeferredBlockcommit` gains `last_warned_at: datetime | None` field; `IStateManager` persists it.
- `core-orchestrator`: Post-pipeline deferred threshold check (`_check_deferred_thresholds`) added to `_run_pipeline`; deferred status integrated into `check()` output.
- `cli-interface`: New `list deferred` subcommand under `qsnap list`.
- `list-commands`: `Core.list_deferred()` returning per-VM deferred summaries.
- `env-validation`: Pre-flight rsync availability check when `rate_limit != "no"`.

## Impact

- **qsnap/models/config.py**: `GlobalConfig` and `TargetConfig` gain new fields (non-breaking — all new fields have defaults).
- **qsnap/models/results.py**: `DeferredBlockcommit` gains optional `last_warned_at` field (default `None` — backward compatible).
- **qsnap/modules/backup/file_copy.py**: `transfer_missing()` switches from `cp` to `rsync --bwlimit` when `rate_limit` is set.
- **qsnap/config/facade.py**: New field parsing in `_parse()` and `_build_target()` with existing inheritance pattern.
- **qsnap/core/__init__.py**: New `_check_deferred_thresholds()` method; `check()` extended with deferred status; `list_deferred()` added.
- **qsnap/cli/app.py**: New `list deferred` sub-parser.
- **qsnap/cli/commands.py**: New `handle_list_deferred` handler.
- **qsnap/cli/format.py**: New deferred table formatter.
- **qsnap/state/json_manager.py**: `DeferredBlockcommit` serialization updated for `last_warned_at`.
- **TESTING.md**: Test categories extend to cover rate-limit and deferred-monitoring. Mr.Programmer MUST pass this file to Mr.Tester when delegating test plan creation.
