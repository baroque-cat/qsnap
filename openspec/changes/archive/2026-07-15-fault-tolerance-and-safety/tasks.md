## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b feat/fault-tolerance-and-safety`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Config Model — New Fields

- [x] 2.1 Add `auto_cleanup: bool = True`, `state_backup_count: int = 2`, `chain_verify_before_commit: bool = True`, `chain_verify_after_commit: bool = True`, `deep_check_schedule: str = "off"` to `GlobalConfig` in `qsnap/models/config.py`
- [x] 2.2 Add `blockcommit_deep_verify: bool = False`, `snapshot_deep_verify: bool = False` to `VMConfig` in `qsnap/models/config.py`
- [x] 2.3 Add `backup_retry_max: int = 3`, `backup_retry_base: str = "2s"` to `TargetConfig` in `qsnap/models/config.py`
- [x] 2.4 Verify all dataclasses remain `frozen=True` and immutable after adding new fields

## 3. Config Parsing — Parse New Fields

- [x] 3.1 Update `ConfigFacade` in `qsnap/config/facade.py` to parse `auto_cleanup`, `state_backup_count`, `chain_verify_before_commit`, `chain_verify_after_commit`, `deep_check_schedule` from the global TOML section
- [x] 3.2 Update `ConfigFacade._build_vm()` to parse `blockcommit_deep_verify` and `snapshot_deep_verify` from each `[[vm]]` section
- [x] 3.3 Update `ConfigFacade._build_target()` to parse `backup_retry_max` and `backup_retry_base` from each `[[vm.target]]` section
- [x] 3.4 Add validation for `backup_retry_base` — must be a valid duration string (e.g. `"1s"`, `"5s"`, `"10s"`); raise `ConfigError` on invalid format
- [x] 3.5 Add validation for `deep_check_schedule` — accepted values: `"off"`, `"weekly"`, `"monthly"`; raise `ConfigError` on invalid value
- [x] 3.6 Verify all new fields use their documented defaults when absent from the TOML file

## 4. State Manager — Corruption Recovery & Rotation

- [x] 4.1 Modify `JsonStateManager._load()` in `qsnap/state/json_manager.py` to catch `json.JSONDecodeError`. On corruption: rename file to `{vm_name}.json.broken.{timestamp}`, log CRITICAL, return empty state dict
- [x] 4.2 Add `state_backup_count` parameter to `JsonStateManager.__init__()` (default `2`, sourced from `GlobalConfig.state_backup_count`)
- [x] 4.3 Modify `JsonStateManager._save()` to rotate previous versions before writing: shift `vm.json` → `vm.json.1` → `vm.json.2` up to `state_backup_count` limit. Use `shutil.move()` for atomic shifts. Discard oldest when count exceeded
- [x] 4.4 Handle `state_backup_count = 0` — write directly, no rotation
- [x] 4.5 Handle first-save case (no previous state file exists) — create `vm.json` only, no rotation

## 5. Pre-Flight Cleanup — Stale Files & Orphan Detection

- [x] 5.1 Add `_preflight_cleanup(vm_config)` method to Core in `qsnap/core/__init__.py` that: (a) removes `*.tmp` and `*.partial` files in `snapshot_dir` and all `target.path` directories via `rm -f`, (b) removes `/tmp/qsnap-backup-*.sock` stale NBD sockets, (c) detects orphan `.qcow2` files in `snapshot_dir` (match naming pattern, not in `IStateManager.get_snapshots()`) — logs WARNING, does NOT delete
- [x] 5.2 Integrate `_preflight_cleanup()` as the very first step of `_validate_environment()`, before any existence checks
- [x] 5.3 Honor `GlobalConfig.auto_cleanup` — when `False`, skip cleanup entirely and log INFO
- [x] 5.4 Ensure cleanup failures do NOT block pipeline execution (defensive step only)

## 6. Chain Integrity Verification — Pre-Commit & Post-Commit

- [x] 6.1 Add `_verify_backing_chain(vm_config) -> ChainVerifyResult` method to Core. Call `qemu-img info --backing-chain --output=json` on the active disk. Parse JSON, verify: every file exists (`os.path.exists`), format is `"qcow2"`, backing-filename refs are consistent, no cycles (filename seen twice)
- [x] 6.2 Define `ChainVerifyResult` frozen dataclass in `qsnap/models/results.py`: `success: bool`, `error: str | None`, `broken_file: Path | None`
- [x] 6.3 In `_blockcommit_snapshots()`, call `_verify_backing_chain()` before `lifecycle.blockcommit()` when `chain_verify_before_commit = true`. On failure: skip blockcommit, log CRITICAL with remediation guidance, do NOT defer
- [x] 6.4 Add `_verify_post_commit(vm_config, expected_removed: int)` method. Call `qemu-img info --backing-chain` on base image, compare chain length before/after commit. On failure: log CRITICAL, preserve snapshots in state (do NOT remove from `IStateManager`)
- [x] 6.5 In `_blockcommit_snapshots()`, call `_verify_post_commit()` after successful blockcommit when `chain_verify_after_commit = true`
- [x] 6.6 Honor `chain_verify_before_commit` and `chain_verify_after_commit` config flags — when `false`, skip verification and log INFO

## 7. Backup Retry — Exponential Backoff

- [x] 7.1 Create `qsnap/utils/retry.py` with pure functions: `is_retryable(error: str) -> bool` (checks for "Connection refused", "No route to host", "timed out", "broken pipe", "EOF" — case-insensitive; excludes "No space left on device", "Permission denied"), `compute_backoff(base_seconds: int, attempt: int) -> float` (returns base * 2^(attempt-1)), `parse_retry_duration(raw: str) -> int` (converts duration string to seconds, raises `ValueError` on invalid)
- [x] 7.2 Add `_transfer_with_retry(provider, vm_config, target, snapshots) -> list[BackupResult]` to Core in `qsnap/core/__init__.py`. Loop: attempt 1..N, call `provider.transfer_missing()`, if success → return; if retryable error → sleep backoff, retry; if non-retryable or N exhausted → return last result
- [x] 7.3 Wire `_transfer_with_retry()` in `_backup_target()` — replace direct `transfer_missing()` call with retry wrapper when `target.backup_retry_max > 0`
- [x] 7.4 Log retry attempts: INFO on retry, WARNING on exhaustion

## 8. Lifecycle Manager — deep_verify Flag

- [x] 8.1 Add optional `deep_verify: bool = False` keyword argument to `ILifecycleManager.blockcommit()` in `qsnap/interfaces/lifecycle.py`
- [x] 8.2 Implement `deep_verify` in `BlockCommitManager.blockcommit()` in `qsnap/modules/lifecycle/blockcommit_manager.py`: after successful commit, if `deep_verify=True`, run `qemu-img check --output=json` on base image. Parse JSON, check `corruptions` count. If > 0, return `CommitResult(success=False, error="deep verify: N corruptions in base image")`
- [x] 8.3 In Core's `_check_deferred_operations()`, pass `deep_verify=vm_config.blockcommit_deep_verify` to `lifecycle.blockcommit()` when executing deferred commits on shut-off VM

## 9. Deep Verification Circuit — check --deep & Systemd Timer

- [x] 9.1 Enhance `Core.check(deep=True)` to run `qemu-img check --output=json` on every snapshot in `IStateManager.get_snapshots()` and every backup file on each target. Report `corruptions` count per image. Aggregate per-VM: OK (0), WARNING (>0), CRITICAL (unreadable)
- [x] 9.2 Include `deep_check_schedule` in `qsnap check` output: if not `"off"`, compute days since last check, report OVERDUE if exceeded
- [x] 9.3 Create `qsnap-check.service` systemd unit: `Type=oneshot`, `ExecStart=qsnap -c /etc/qsnap/qsnap.toml check --deep`
- [x] 9.4 Create `qsnap-check.timer` systemd unit: `OnCalendar=Sun *-*-* 03:00:00`, `Persistent=True`, `RandomizedDelaySec=1800`, `Unit=qsnap-check.service`. Timer is NOT enabled by default
- [x] 9.5 Add both unit files to the package

## 10. CLI — Safety Transparency

- [x] 10.1 Extend `qsnap list config` output to show per-VM safety columns: `blockcommit_deep_verify` (ON/OFF), `snapshot_deep_verify` (ON/OFF). Show global safety settings in a header: `auto_cleanup`, `chain_verify_before_commit`, `chain_verify_after_commit`, `deep_check_schedule`
- [x] 10.2 Extend `qsnap check` output to include safety configuration summary: which safety features are ON/OFF

## 11. Config Example — Update qsnap.toml.example

- [x] 11.1 Add `snapshot_preserve_min` and `target_preserve_min` to example with usage comments (these already exist in code but not in example)
- [x] 11.2 Add all new fault-tolerance fields to example with T0/T1 vs T2/T3 sections clearly documented
- [x] 11.3 Add `rate_limit`, `full_every`, `full_compress`, `incremental_mode`, `change_detection_mode`, `disks`, `deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age` — all existing-but-not-shown fields
- [x] 11.4 Add a second commented-out VM example showing `blockcommit_deep_verify = true`, `snapshot_deep_verify = true`, `verify = "full"`, `full_every = "7d"` for critical VMs
- [x] 11.5 Verify the example config is parseable: `poetry run qsnap -c qsnap.toml.example list config`

## 12. Testing

**IMPORTANT**: The implementing agent (@Mr.Programmer) MUST pass `TESTING.md` to every @Mr.Tester subagent delegated below. The TESTING.md document describes the project's test paradigm: directory structure mirroring source, MockShell for zero real I/O, MockFactory for Core tests, InMemoryStateManager, contract tests for every ABC. Each tester must follow this paradigm.

- [x] 12.1 Read `test-plan.md` Delegation Groups section to understand the 7 test groups
- [x] 12.2 Delegate group `state` to @Mr.Tester (scope: tests/state/test_manager.py, tests/mocks/test_mock_state.py, tests/modules/change/test_allocation.py, tests/interfaces/test_state_manager.py). **Pass TESTING.md as context.**
- [x] 12.3 Delegate group `config` to @Mr.Tester (scope: tests/config/test_model.py, tests/config/test_facade.py). **Pass TESTING.md as context.**
- [x] 12.4 Delegate group `core` to @Mr.Tester (scope: tests/core/test_pipeline.py). **Pass TESTING.md as context.**
- [x] 12.5 Delegate group `lifecycle` to @Mr.Tester (scope: tests/modules/lifecycle/test_blockcommit.py, tests/interfaces/test_lifecycle_manager.py, tests/factory/test_default.py). **Pass TESTING.md as context.**
- [x] 12.6 Delegate group `cleanup` to @Mr.Tester (scope: tests/core/test_validation.py). **Pass TESTING.md as context.**
- [x] 12.7 Delegate group `retry` to @Mr.Tester (scope: tests/utils/test_retry.py [NEW], tests/modules/backup/test_copy.py). **Pass TESTING.md as context.**
- [x] 12.8 Delegate group `cli` to @Mr.Tester (scope: tests/cli/test_commands.py, tests/systemd/test_units.py). **Pass TESTING.md as context.**
- [x] 12.9 Review all @Mr.Tester reports and fix any source-level bugs discovered by tests
- [x] 12.10 Re-delegate any groups affected by source fixes
- [x] 12.11 Verify all test groups pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 12.12 Verify coverage matches test-plan.md: all scenarios covered, no overlapping files between groups

## 13. Final Verification

- [x] 13.1 Run the full test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`
- [x] 13.2 Run the example config parse test: `poetry run qsnap -c qsnap.toml.example list config`
- [x] 13.3 Verify `qsnap list config` shows the new safety columns
- [x] 13.4 Verify `qsnap check` shows safety configuration status
- [x] 13.5 Verify `qsnap --help` includes the new `check --deep` option
- [x] 13.6 Verify `qsnap-check.service` and `qsnap-check.timer` files exist with correct content
- [x] 13.7 Manual smoke test (if test environment has libvirt): run `qsnap -n run` in dry-run mode, verify no crashes with the new config fields
