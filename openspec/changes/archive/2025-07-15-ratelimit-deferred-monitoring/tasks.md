## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b ratelimit-deferred-monitoring`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `pytest tests/ -m "not integration and not stress and not e2e" -x`

## 2. Config Model — Rate Limit & Deferred Threshold Fields

- [x] 2.1 Add `rate_limit: str = "no"` field to `GlobalConfig` in `qsnap/models/config.py`
- [x] 2.2 Add `rate_limit: str = "no"` field to `TargetConfig` in `qsnap/models/config.py`
- [x] 2.3 Add `deferred_warn_count: str = "5"`, `deferred_crit_count: str = "10"`, `deferred_warn_age: str = "7d"`, `deferred_crit_age: str = "14d"` fields to `GlobalConfig` in `qsnap/models/config.py`
- [x] 2.4 Add `last_warned_at: datetime | None = None` field to `DeferredBlockcommit` in `qsnap/models/results.py`
- [x] 2.5 Update `ConfigFacade._parse()` in `qsnap/config/facade.py` to extract `rate_limit`, `deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age` from raw TOML global context
- [x] 2.6 Update `ConfigFacade._build_target()` in `qsnap/config/facade.py` to resolve `rate_limit` using existing option inheritance pattern (target override → global default)
- [x] 2.7 Update `JsonStateManager._deferred_to_dict()` and `_dict_to_deferred()` in `qsnap/state/json_manager.py` to serialize/deserialize `last_warned_at` (ISO format, `None` for missing key)

## 3. Pre-flight Validation — Rsync Check

- [x] 3.1 Update `Core._validate_environment()` in `qsnap/core/__init__.py` to check `rsync` availability via `which rsync` when any target has `rate_limit != "no"`
- [x] 3.2 Log WARNING (not ERROR) when rsync is missing: `"rsync not found — rate limiting disabled for target <path>"`
- [x] 3.3 Ensure rsync absence does NOT block the pipeline (non-fatal check)

## 4. Rate Limiting — Rsync Integration in FileCopyBackupProvider

- [x] 4.1 Add `_parse_rate_limit(value: str) -> int | None` utility function: parse `"no"` / `"0"` → `None`, `"500K"` → `512000`, `"100M"` → `104857600`, etc. Raise `ValueError` on invalid format
- [x] 4.2 Add `_rate_limit_to_rsync_bwlimit(bytes_per_sec: int) -> int` helper: `bytes_per_sec // 1024`
- [x] 4.3 Modify `FileCopyBackupProvider.transfer_missing()` signature/body to accept `rate_limit: str` parameter from `TargetConfig.rate_limit`
- [x] 4.4 When `rate_limit` is not `"no"` and rsync is available, use `rsync --bwlimit=<kib> --partial --progress <source> <target>` instead of `cp <source> <target>`
- [x] 4.5 When `rate_limit` is `"no"`, use existing `cp` (unchanged behavior)
- [x] 4.6 When `rate_limit` is set but rsync is unavailable, log WARNING and fall back to `cp`
- [x] 4.7 Add INFO log before transfer: `"Transferring <snapshot_name> to <target_path> (rate limit: <human_readable_rate>)"`
- [x] 4.8 Add INFO log after transfer: elapsed time, bytes transferred, computed average speed
- [x] 4.9 Add WARNING log when actual throughput < 10% of configured rate limit: `"Transfer of <snapshot> slower than expected: <actual> (limit: <configured>). Check target disk health."`
- [x] 4.10 Update `Core._backup_target()` to pass `target.rate_limit` to `FileCopyBackupProvider.transfer_missing()` via `IBackupProvider` method parameter

## 5. Deferred Operations Monitoring

- [x] 5.1 Implement `Core._check_deferred_thresholds()` in `qsnap/core/__init__.py`: iterate all VMs, retrieve deferred ops from `IStateManager`, compare count and oldest age against thresholds from `GlobalConfig`
- [x] 5.2 Parse `deferred_warn_count` / `deferred_crit_count` to int; parse `deferred_warn_age` / `deferred_crit_age` using existing `_parse_duration` from retention module
- [x] 5.3 Log WARNING when count or age meets warn threshold for a VM: `"VM <name>: <N> deferred blockcommit operations pending"`
- [x] 5.4 Log CRITICAL when count or age meets crit threshold for a VM
- [x] 5.5 Call `_check_deferred_thresholds()` at the end of `_run_pipeline()` — after all VMs are processed
- [x] 5.6 Ensure threshold violations do NOT change pipeline exit code (remain 0 for success)
- [x] 5.7 Implement `Core.list_deferred(vm_filter=None)` returning list of deferred summaries (vm_name, snapshots count, reason, age) from `IStateManager`
- [x] 5.8 Update `Core.check()` to include deferred status per VM with remediation guidance for apparmor/selinux
- [x] 5.9 Format remediation guidance: `"Merge blocked by AppArmor. Consider: aa-disable /etc/apparmor.d/libvirt/libvirt-<uuid>"` and `"Or: shut down the VM to allow automatic merge."`

## 6. CLI — `list deferred` Subcommand

- [x] 6.1 Add `list deferred` sub-parser to `build_argparser()` in `qsnap/cli/app.py`
- [x] 6.2 Implement `handle_list_deferred(args)` handler in `qsnap/cli/commands.py`: calls `Core.list_deferred(vm_filter)`, formats output
- [x] 6.3 Add `_format_deferred_table(rows)` function to `qsnap/cli/format.py`: columns VM, SNAPSHOTS, REASON, AGE; sorted by age descending
- [x] 6.4 Add `--format raw` support for deferred listing: `vm_name=... snapshots=... reason=... since=...` format per row
- [x] 6.5 Register `list deferred` in CLI dispatch table

## 7. Testing

**TEST ORCHESTRATION PROTOCOL:** @Mr.Programmer, when executing task 7, you MUST:

1. Read `test-plan.md` Delegation Groups section
2. For EACH group listed below, launch one @Mr.Tester subagent with:
   - The group's scope (file paths from test-plan.md)
   - The group's scenario list from Coverage Map
   - **CRITICAL: Pass `TESTING.md` (at `/home/openuser/vm/qsnap/TESTING.md`) to EVERY @Mr.Tester subagent** so they understand the testing paradigm (mocked IShell, frozen dataclasses, contract tests, result-object patterns, fixture conventions)
   - Instruction: "Write or fix ONLY these specific tests. Report source bugs, don't fix them."
3. Launch ALL groups IN PARALLEL (single message with multiple tool calls)
4. After all testers return: fix any reported source bugs, re-delegate affected groups
5. Repeat until all groups pass

- [x] 7.1 Read `test-plan.md` Delegation Groups section
- [x] 7.2 Delegate group `config-parsing` to @Mr.Tester (scope: rate_limit and deferred threshold fields in config model; inherit `TESTING.md` from project root)
- [x] 7.3 Delegate group `backup-rsync` to @Mr.Tester (scope: rsync integration in FileCopyBackupProvider, cp fallback, transfer logging; inherit `TESTING.md` from project root)
- [x] 7.4 Delegate group `deferred-model` to @Mr.Tester (scope: DeferredBlockcommit last_warned_at, JsonStateManager serialization, backward compat; inherit `TESTING.md` from project root)
- [x] 7.5 Delegate group `core-monitoring` to @Mr.Tester (scope: _check_deferred_thresholds, list_deferred, check() integration, pipeline step order; inherit `TESTING.md` from project root)
- [x] 7.6 Delegate group `cli-deferred` to @Mr.Tester (scope: list deferred subcommand, format table/raw, dispatch; inherit `TESTING.md` from project root)
- [x] 7.7 Delegate group `validation-rsync` to @Mr.Tester (scope: pre-flight rsync availability check, non-blocking WARNING, pipeline continuation; inherit `TESTING.md` from project root)
- [x] 7.8 Review @Mr.Tester reports and fix any source-level bugs discovered
- [x] 7.9 Re-delegate any groups affected by source fixes
- [x] 7.10 Verify all groups pass and coverage matches `test-plan.md`
- [x] 7.11 Run full test suite: `pytest tests/ -m "not integration and not stress and not e2e" -v`
- [x] 7.12 Verify no regressions in existing tests
