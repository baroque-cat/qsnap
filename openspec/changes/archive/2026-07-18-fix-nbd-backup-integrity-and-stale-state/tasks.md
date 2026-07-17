## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fix-nbd-backup-integrity-and-stale-state`
- [x] 1.2 Run the full test suite to establish a passing baseline: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`
- [x] 1.3 Verify all external binaries are available: `which virsh qemu-img rsync`

## 2. Config Layer — New GlobalConfig Fields

- [x] 2.1 Add `full_verify_after_create: str = "check"` to `GlobalConfig` dataclass in `qsnap/models/config.py`
- [x] 2.2 Add `full_verify_before_rebase: str = "metadata"` to `GlobalConfig` in `qsnap/models/config.py`
- [x] 2.3 Add `full_verify_before_delete: str = "check"` to `GlobalConfig` in `qsnap/models/config.py`
- [x] 2.4 Add `deep_check_targets: bool = False` to `GlobalConfig` in `qsnap/models/config.py`
- [x] 2.5 Update `qsnap/config/facade.py` to parse the new fields from TOML `[global]` section
- [x] 2.6 Add `full_verify_after_create`, `full_verify_before_rebase`, `full_verify_before_delete` parameters to `make_global_config()` factory function in `tests/conftest.py`

## 3. Backup Verification — verify_full_backup Function

- [x] 3.1 Implement `verify_full_backup(shell, target_path, verify_mode, expected_virtual_size=None, expected_hash=None) -> str | None` in `qsnap/modules/backup/verification.py`
- [x] 3.2 Implement M1 (metadata): `qemu-img info --output=json` check for format="qcow2" and corrupt-bit detection (incompatible_features bit 1)
- [x] 3.3 Implement M2 (check): `qemu-img check --output=json` check for errors=0 and leaks=0 (only when verify_mode is "check" or "hash")
- [x] 3.4 Implement M3 (hash): SHA-256 comparison via `_file_sha256()` against `expected_hash` (only when verify_mode is "hash" and expected_hash is not None)
- [x] 3.5 Handle "off" mode: return None immediately, no shell commands
- [x] 3.6 Export `verify_full_backup` from `qsnap/modules/backup/__init__.py`

## 4. P0 Fix — qemu-img rebase -F qcow2 Flag

- [x] 4.1 Add `"-F", "qcow2"` to `rebase_cmd` at `qsnap/modules/backup/file_copy.py` lines 208–215 (FULL anchor rebase path)
- [x] 4.2 Add `"-F", "qcow2"` to `rebase_cmd` at `qsnap/modules/backup/file_copy.py` lines 251–258 (source backing fallback path)
- [x] 4.3 Verify: `grep -n "rebase" qsnap/modules/backup/file_copy.py` shows `-F qcow2` at both sites

## 5. P0 Fix — NBD Backup Job Cleanup (domjobabort)

- [x] 5.1 Add `virsh domjobabort --domain <vm_name>` to the `finally` block in `nbd_full_export()` in `qsnap/modules/backup/nbd_helper.py`, BEFORE the socket `rm -f`
- [x] 5.2 Use a 30-second timeout for `domjobabort`. On failure, log a WARNING but do NOT propagate the error — the socket cleanup still proceeds.
- [x] 5.3 Verify: `grep -n "domjobabort" qsnap/modules/backup/nbd_helper.py` returns the new call in the finally block

## 6. P0 Fix — FULL Backup Verification Pipeline

- [x] 6.1 In `Core._backup_target()` (`qsnap/core/__init__.py`), after `provider.create_full_backup()` returns success and the atomic rename completes, call `verify_full_backup()` with mode from `global_cfg.full_verify_after_create`
- [x] 6.2 On verification failure: `rm -f` the FULL file, do NOT call `record_full_backup()`, return `BackupResult(success=False, error=...)`
- [x] 6.3 On verification success: proceed with `record_full_backup()` as normal
- [x] 6.4 In `Core._cleanup_backups()`, before cascade-deletion of a FULL, call M1 verification (always, non-configurable) via `verify_full_backup(shell, full_path, "metadata")`
- [x] 6.5 If M1 fails at pre-deletion: skip deletion of the FULL AND all dependent incrementals, log CRITICAL with paths and remediation guidance
- [x] 6.6 If `global_cfg.full_verify_before_delete == "check"`, additionally run M2 (`verify_full_backup` with `"check"`) and block on failure
- [x] 6.7 In `FileCopyBackupProvider.transfer_missing()` (`qsnap/modules/backup/file_copy.py`), before `qemu-img rebase` to a FULL anchor, call M1 verification on the FULL anchor
- [x] 6.8 If M1 fails at pre-rebase: log WARNING, search for alternative FULL anchor (previous by timestamp), retry M1, skip rebase if no valid anchor exists

## 7. P0 Fix — Stale State Self-Healing

- [x] 7.1 In `Core._blockcommit_snapshots()` (`qsnap/core/__init__.py`), before passing `to_merge` to the lifecycle manager, iterate and check `os.path.exists(snapshot.path)` for each entry
- [x] 7.2 For entries where the file does not exist: call `self._state.remove_snapshot(vm_config.name, sn.name)`, log WARNING, remove from `to_merge`
- [x] 7.3 If `to_merge` becomes empty after filtering: log INFO and skip the blockcommit step entirely
- [x] 7.4 In `FileCopyBackupProvider.transfer_missing()` (`qsnap/modules/backup/file_copy.py`), before `rsync`, check `os.path.exists(snapshot.path)`. If missing, call `self._state.remove_snapshot()`, log WARNING, skip the transfer
- [x] 7.5 Verify: the stale removal guard MUST NOT access `IStateManager` inside the module — the `transfer_missing()` fix uses `self._state` (which is already an optional constructor parameter for FileCopyBackupProvider)

## 8. P1 Fix — Snapshot Lock-Conflict Retry

- [x] 8.1 In `ExternalSnapshotProvider.create()` (`qsnap/modules/snapshot/external.py`), add a retry loop around `virsh snapshot-create-as` for lock-conflict errors
- [x] 8.2 Retry parameters: max 3 total attempts (1 initial + 2 retries), backoff 2s/4s
- [x] 8.3 Detection: check if error string contains "cannot acquire state change lock"
- [x] 8.4 Non-lock errors SHALL NOT be retried — fail immediately
- [x] 8.5 Import `time` for `time.sleep()` (stdlib, no new dependency)

## 9. P2 Fix — Partial rsync qcow2 Cleanup in Preflight

- [x] 9.1 In `Core._preflight_cleanup()` (`qsnap/core/__init__.py`), after cleaning `*.tmp` and `*.partial`, scan backup target directories for `.qcow2` files that are NOT `*.FULL.*.qcow2`
- [x] 9.2 For each candidate `.qcow2` file: run `qemu-img info --output=json` with a short timeout (10s)
- [x] 9.3 If `qemu-img info` returns non-zero exit code: the file is a truncated rsync artifact. Delete it (`rm -f`) and log WARNING with the path.
- [x] 9.4 If `qemu-img info` succeeds: the file is valid — do NOT delete
- [x] 9.5 This is gated behind `GlobalConfig.auto_cleanup` (existing flag) — if disabled, skip

## 10. Testing

**IMPORTANT: Before delegating ANY test group to @Mr.Tester, the implementing agent MUST pass the contents of `TESTING.md` (the project's testing paradigm document at the workspace root) to every @Mr.Tester subagent. This ensures all testers follow the same conventions: zero real I/O in unit tests, MockShell with .expect().returns(), test location mirrors production hierarchy, contract test parametrization, test_risk_ prefix for risk tests, no pytest-mock.**

The implementing agent MUST also ensure that integration tests leverage the real libvirt, virsh, and qemu-img available on this system — simulate real-world scenarios like stale state recovery, NBD job abort, and qemu-img rebase on actual qcow2 files.

- [x] 10.1 Read `test-plan.md` Delegation Groups section
- [x] 10.2 Delegate group `backup-full-verify-unit` to @Mr.Tester — scope: `tests/modules/backup/test_full_verification.py` (NEW file, ~12 tests for `verify_full_backup()`)
- [x] 10.3 Delegate group `core-full-verify` to @Mr.Tester — scope: `tests/core/test_full_verification_pipeline.py` (NEW file, ~18 tests for FULL verification pipeline)
- [x] 10.4 Delegate group `backup-provider-mod` to @Mr.Tester — scope: `tests/modules/backup/test_copy.py` (MODIFY: add ~8 tests, update rebase assertions)
- [x] 10.5 Delegate group `bitmap-mod` to @Mr.Tester — scope: `tests/modules/backup/test_bitmap.py` (MODIFY: add ~4 tests, update NBD cleanup assertions)
- [x] 10.6 Delegate group `snapshot-mod` to @Mr.Tester — scope: `tests/modules/snapshot/test_external.py` (MODIFY: add ~4 tests for lock-conflict retry)
- [x] 10.7 Delegate group `core-pipeline-mod` to @Mr.Tester — scope: `tests/core/test_pipeline.py` (MODIFY: add ~4 tests for stale state self-healing)
- [x] 10.8 Delegate group `core-validation` to @Mr.Tester — scope: `tests/core/test_validation.py` (MODIFY: add ~2 tests for truncated qcow2 detection)
- [x] 10.9 Delegate group `config-mod` to @Mr.Tester — scope: `tests/config/test_model.py`, `tests/config/test_facade.py` (MODIFY: add ~12 tests for new GlobalConfig fields)
- [x] 10.10 Delegate group `integration-tests` to @Mr.Tester — scope: `tests/integration/` (MODIFY existing + CREATE `test_stale_state_recovery.py`. All integration tests MUST use real libvirt/virsh/qemu-img. Mark with `@pytest.mark.integration`.)
- [x] 10.11 Review ALL @Mr.Tester reports and fix any source-level bugs discovered during testing
- [x] 10.12 Re-delegate any groups affected by source fixes
- [x] 10.13 Run the full test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`
- [x] 10.14 Run integration tests: `poetry run pytest tests/integration/ -m integration -v`
- [x] 10.15 Verify coverage: `poetry run pytest tests/ -m "not integration and not stress and not e2e" --cov=qsnap --cov-report=html`
- [x] 10.16 Run linting: `poetry run ruff check qsnap/ tests/` and `poetry run pyright qsnap/`

## 11. Final Validation

- [x] 11.1 Run all existing tests to verify no regressions: `poetry run pytest tests/ -m "" -v`
- [x] 11.2 Verify `openspec status --change "fix-nbd-backup-integrity-and-stale-state"` shows all artifacts complete
- [x] 11.3 Verify the cascade failure scenario from the original bug report is impossible: stale state self-heal prevents blockcommit short-circuit, domjobabort prevents lock conflicts, -F qcow2 ensures rebase succeeds, M1 at pre-deletion prevents cascade data loss

## 12. Fix 1 — Remove D4 Path + Config Validation (CRITICAL)

- [x] 12.1 Remove the D4 code path from `FileCopyBackupProvider.transfer_missing()` in `qsnap/modules/backup/file_copy.py` — the block at lines 108–130 that calls `create_full_backup()` when `copy_base=False`, target is empty, and snapshots exist
- [x] 12.2 Remove the call to `self._state.record_full_backup()` that was added after the D4 `create_full_backup()` call
- [x] 12.3 Remove the import of `create_full_backup` dependencies from D4 context if no longer needed
- [x] 12.4 In `qsnap/config/facade.py`, add config validation: when a VM has targets configured, verify at least one retention bucket (hourly/daily/weekly/monthly/yearly) has count > 0 OR `preserve_min` is `"all"`. Raise `ConfigError` with clear message
- [x] 12.5 Update `qsnap/config/facade.py` — the validation should run in `_build_target()` or `_build_vm()` after target construction

## 13. Fix 2 — Replace M3 SHA-256 with qemu-img compare (CRITICAL)

- [x] 13.1 Change `verify_full_backup()` signature in `qsnap/modules/backup/verification.py`: replace `expected_hash: str | None` parameter with `source_path: Path | None`, update docstring
- [x] 13.2 Replace M3 logic: instead of `_file_sha256()` + hash comparison, run `qemu-img compare -q --force-share <source_path> <target_path>` with 7200s timeout
- [x] 13.3 On compare failure, return `"verification failed: content comparison mismatch"` (with stderr detail)
- [x] 13.4 When `source_path` is None in "hash" mode, skip M3 (return None, same as before)
- [x] 13.5 In `Core._backup_target()` (`qsnap/core/__init__.py`), change `verify_full_backup()` call: pass `source_path=most_recent.path` instead of `expected_hash=most_recent.content_hash`
- [x] 13.6 Update `qsnap/modules/backup/__init__.py` export docstring if needed

## 14. Fix 3 — Add remove_full_backup to IStateManager (HIGH)

- [x] 14.1 Add `remove_full_backup(target_path: str, name: str) -> bool` abstract method to `IStateManager` ABC in `qsnap/interfaces/state.py`
- [x] 14.2 Implement `remove_full_backup()` in `InMemoryStateManager` (`tests/mocks/mock_state.py`) — remove the `FullBackupInfo` entry matching `target_path` and `name`, return True if found/removed
- [x] 14.3 Implement `remove_full_backup()` in `JsonStateManager` (production state manager) — find and remove from JSON state file
- [x] 14.4 In `Core._cleanup_backups()` (`qsnap/core/__init__.py`), after `provider.delete(backup)` for a FULL, call `self._state.remove_full_backup(str(target.path), full_name)`
- [x] 14.5 Call `remove_full_backup()` only when M1 verification passed and deletion succeeded

## 15. Fix 4 — Phantom FULL Detection (HIGH)

- [x] 15.1 In `Core._backup_target()` (`qsnap/core/__init__.py`), after `self._state.get_full_backups(target.path)`, iterate entries and check `os.path.exists(str(full.path))` for each
- [x] 15.2 For entries where file does not exist: call `self._state.remove_full_backup(str(target.path), full.name)`, log WARNING, remove from list
- [x] 15.3 Pass the filtered list (non-phantom entries only) to `_should_create_bucket_full()`
- [x] 15.4 Handle the case where all FULLs are phantom — empty list passed to `_should_create_bucket_full()` should trigger first FULL creation

## 16. Fix 5 — Add remove_incremental_dependency to IStateManager (MEDIUM)

- [x] 16.1 Add `remove_incremental_dependency(target_path: str, incremental_name: str, full_name: str) -> bool` abstract method to `IStateManager` ABC
- [x] 16.2 Implement in `InMemoryStateManager` — remove the incremental name from the FULL's dependency list
- [x] 16.3 Implement in `JsonStateManager`
- [x] 16.4 In `Core._cleanup_backups()`, when cascade-deleting an orphaned incremental, call `self._state.remove_incremental_dependency(str(target.path), dep_name, backup.name)`

## 17. Fix 6 — New State Consistency Check (LOW)

- [x] 17.1 Add `check_state(vm_filter: str | None = None) -> dict[str, StateCheckResult]` method to `Core` in `qsnap/core/__init__.py`
- [x] 17.2 Define `StateCheckResult` dataclass in `qsnap/models/results.py` with fields: `vm_name`, `status` ("ok"/"stale_snapshots"/"stale_fulls"/"stale_deps"/"corrupt_state"), `phantom_snapshots: list[str]`, `phantom_fulls: list[str]`, `stale_deps: list[str]`, `corrupt_files: list[str]`
- [x] 17.3 Implement phantom snapshot detection: iterate `self._state.get_snapshots(vm_config.name)`, check `os.path.exists()`, report missing
- [x] 17.4 Implement phantom FULL detection: iterate `self._state.get_full_backups(target.path)`, check `os.path.exists()`, report missing
- [x] 17.5 Implement orphaned dependency detection: iterate `self._state.get_incremental_dependencies()`, verify both incremental and FULL files exist
- [x] 17.6 Implement state file integrity: verify JSON files in state_dir are parsable
- [x] 17.7 Add CLI command: `qsnap check --state` wiring in `qsnap/cli/commands.py` and `qsnap/cli/app.py`

## 18. Testing — Phase 2

- [x] 18.1 Delegate group `state-check` to @Mr.Tester — scope: `tests/core/test_state_check.py` (NEW file, ~6 tests for state consistency check)
- [x] 18.2 Delegate re-run of `backup-full-verify-unit` to @Mr.Tester — update M3 hash tests to use qemu-img compare (source_path instead of expected_hash)
- [x] 18.3 Delegate re-run of `core-full-verify` to @Mr.Tester — add phantom FULL tests, state cleanup tests, M3 source_path tests
- [x] 18.4 Delegate re-run of `backup-provider-mod` to @Mr.Tester — remove D4 assertions, add negative test for transfer_missing not calling create_full_backup
- [x] 18.5 Delegate re-run of `config-mod` to @Mr.Tester — add bucket validation tests
- [x] 18.6 Review ALL @Mr.Tester Phase 2 reports and fix any source-level bugs
- [x] 18.7 Re-delegate any groups affected by source fixes
- [x] 18.8 Run the full test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`
- [x] 18.9 Run linting: `poetry run ruff check qsnap/ tests/`

## 19. Final Validation — Phase 2

- [x] 19.1 Run all existing tests to verify no regressions
- [x] 19.2 Verify `openspec status --change "fix-nbd-backup-integrity-and-stale-state"` shows all artifacts complete
- [x] 19.3 Manual verification: create a test config with all-zero retention buckets → verify ConfigError
- [x] 19.4 Manual verification: create a FULL, delete the file externally, run the pipeline → verify phantom FULL detection and new FULL creation
- [x] 19.5 Manual verification: run `qsnap check --state` on a system with stale entries → verify detection report
