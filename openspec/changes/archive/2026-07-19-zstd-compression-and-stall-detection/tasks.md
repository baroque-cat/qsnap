## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b zstd-compression-and-stall-detection`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Config Model & Parsing

- [x] 2.1 Add `compression_type: str = "zstd"` field to `GlobalConfig` in `qsnap/models/config.py` (frozen dataclass)
- [x] 2.2 Add `backup_stall_timeout: str = "30m"` field to `GlobalConfig` in `qsnap/models/config.py`
- [x] 2.3 Add `compression_type: str = "zstd"` field to `TargetConfig` in `qsnap/models/config.py`
- [x] 2.4 Add `backup_stall_timeout: str = "30m"` field to `TargetConfig` in `qsnap/models/config.py`
- [x] 2.5 Update docstrings for `GlobalConfig` and `TargetConfig` to document new fields (compression_type: "zstd" default, "zlib" alternative; backup_stall_timeout: duration string, "0s" disables)
- [x] 2.6 Parse `compression_type` from TOML in `ConfigFacade._build_global()` at `qsnap/config/facade.py` — validate against `{"zstd", "zlib"}`, raise `ConfigError` on invalid value
- [x] 2.7 Parse `backup_stall_timeout` from TOML in `ConfigFacade._build_global()` — validate via `parse_duration()`, raise `ConfigError` on invalid format
- [x] 2.8 Parse `compression_type` from TOML in `ConfigFacade._build_target()` — inherit from global when not set in target
- [x] 2.9 Parse `backup_stall_timeout` from TOML in `ConfigFacade._build_target()` — inherit from global when not set in target
- [x] 2.10 Pass `compression_type` and `backup_stall_timeout` to `TargetConfig` constructor in `_build_target()`

## 3. IShell Stall Detection

- [x] 3.1 Add `run_with_stall_detection(cmd: list[str], output_file: Path | None = None, stall_timeout: int = 1800, check: bool = False) -> ShellResult` abstract method to `IShell` in `qsnap/interfaces/shell.py`
- [x] 3.2 Implement `run_with_stall_detection()` in `SubprocessShell` at `qsnap/shell/subprocess_shell.py` using `subprocess.Popen()` + 60-second polling loop checking `output_file.stat().st_size`
- [x] 3.3 Implement stall kill logic: if file size unchanged for `stall_timeout` seconds, call `proc.kill()` + `proc.wait()`, return `ShellResult(success=False, error="Stall detected: no progress for {N}s")`
- [x] 3.4 Implement `run_with_stall_detection()` in `MockShell` at `tests/mocks/mock_shell.py` — return predefined `ShellResult` based on expectation matching
- [x] 3.5 Ensure NO speed/progress logging in `run_with_stall_detection()` — only DEBUG log for command start and stall/error events

## 4. Compression Type in Backup Providers

- [x] 4.1 Add `compression_type: str = "zstd"` parameter to `IBackupProvider.create_full_backup()` in `qsnap/interfaces/backup.py`
- [x] 4.2 Add `compression_type: str = "zstd"` parameter to `IBackupProvider.transfer_missing()` in `qsnap/interfaces/backup.py` (if signature changes are needed for rsync compression)
- [x] 4.3 Update `FileCopyBackupProvider.create_full_backup()` in `qsnap/modules/backup/file_copy.py` — add `compression_type` parameter, pass to `nbd_full_export()` and direct convert path
- [x] 4.4 Update `FileCopyBackupProvider.transfer_missing()` in `file_copy.py` — add `compression_type` parameter, use `--compress-choice=zstd` or `--compress-choice=zlib` for rsync
- [x] 4.5 Update `BitmapBackupProvider.create_full_backup()` in `qsnap/modules/backup/bitmap.py` — add `compression_type` parameter, pass to `nbd_full_export()`
- [x] 4.6 Update `BitmapBackupProvider.transfer_missing()` in `bitmap.py` — add `compression_type` parameter, pass to NBD convert command
- [x] 4.7 Update `nbd_full_export()` in `qsnap/utils/nbd.py` — add `compression_type: str = "zstd"` parameter, add `-o compression_type=<type>` when `compress=True` and `compression_type="zstd"`

## 5. Stall Detection in Data Transfer Commands

- [x] 5.1 In `nbd_full_export()` at `nbd.py:212` — replace `shell.run(convert_cmd, timeout=3600)` with `shell.run_with_stall_detection(convert_cmd, output_file=Path(target_file), stall_timeout=stall_timeout)` where `stall_timeout` is parsed from target config
- [x] 5.2 In `FileCopyBackupProvider.transfer_missing()` at `file_copy.py:193` — replace `shell.run(transfer_cmd, timeout=3600)` with `shell.run_with_stall_detection(transfer_cmd, output_file=Path(target_file), stall_timeout=stall_timeout)`
- [x] 5.3 In `FileCopyBackupProvider.create_full_backup()` direct convert at `file_copy.py:504` — replace `shell.run(convert_cmd, timeout=3600)` with `shell.run_with_stall_detection(convert_cmd, output_file=Path(tmp_file), stall_timeout=stall_timeout)`
- [x] 5.4 In `BitmapBackupProvider.transfer_missing()` at `bitmap.py:204` — replace `shell.run(convert_cmd, timeout=600)` with `shell.run_with_stall_detection(convert_cmd, output_file=Path(target_file), stall_timeout=stall_timeout)`
- [x] 5.5 Add `stall_timeout` parameter passing: Core reads `target.backup_stall_timeout`, parses to seconds via `parse_duration()`, passes to provider methods. When `"0s"`, fall back to `shell.run(timeout=3600)`.
- [x] 5.6 Update `Core._backup_target()` at `core/__init__.py:2529` — pass `compression_type=target.compression_type` and `stall_timeout` to `create_full_backup()` and `transfer_missing()`

## 6. Remove Size Estimation

- [x] 6.1 Delete `_log_size_estimate()` method from `Core` at `qsnap/core/__init__.py` (around line 2357-2469)
- [x] 6.2 Remove the `_log_size_estimate(vm, target)` call from `Core._execute_pipeline()` (pipeline step D5)
- [x] 6.3 Remove `base_size * 0.3` formula from `schedule_summary()` at `core/__init__.py:379` and `core/__init__.py:466`
- [x] 6.4 Simplify `schedule_summary()` to log only factual data: `base_size` (from `qemu-img info actual-size`) and `compression_type` (from config)
- [x] 6.5 Simplify `estimate()` CLI command to print only factual data (base_size, compression_type, compress enabled/disabled) — no projections
- [x] 6.6 Remove design D5 reference from `AGENTS.md` pipeline section (line 94: `_log_size_estimate(vm, target) # design D5`)

## 7. Update Mocks & Factory

- [x] 7.1 Update `MockBackupProvider.create_full_backup()` in `tests/mocks/mock_modules.py` — add `compression_type="zstd"` parameter
- [x] 7.2 Update `MockBitmapBackupProvider.create_full_backup()` in `tests/mocks/mock_modules.py` — add `compression_type="zstd"` parameter
- [x] 7.3 Update `MockBackupProvider.transfer_missing()` and `MockBitmapBackupProvider.transfer_missing()` if signature changes
- [x] 7.4 Update `MockShell` in `tests/mocks/mock_shell.py` — implement `run_with_stall_detection()` method
- [x] 7.5 Update `tests/conftest.py` — add `compression_type="zstd"` and `backup_stall_timeout="30m"` to `make_global_config()` and `make_target()` factory functions

## 8. Systemd & Documentation

- [x] 8.1 Add `TimeoutStartSec=0` to `systemd/qsnap.service` unit file
- [x] 8.2 Update `qsnap.toml.example` — add `compression_type = "zstd"` (commented) to global and target sections, add `backup_stall_timeout = "30m"` (commented)
- [x] 8.3 Update `README.md` — change "zlib" references to "zstd", document `compression_type` field, document `backup_stall_timeout` field, remove size estimation documentation, remove outdated NBD+compress warning
- [x] 8.4 Update `AGENTS.md` — remove design D5 from pipeline, update Shell Abstraction section to mention `run_with_stall_detection()`, update config dataclass descriptions

## 9. Testing

**CRITICAL — TEST ORCHESTRATION PROTOCOL:**

The lead programmer agent (@Mr.Programmer) MUST delegate test work to @Mr.Tester specialist agents. For EACH delegation group below, launch one @Mr.Tester subagent with:
1. The group's scope (file paths from test-plan.md)
2. The group's scenario list from the Coverage Map in `test-plan.md`
3. **MANDATORY**: The contents of `/home/openuser/vm/qsnap/TESTING.md` — this document defines the testing paradigm, directory structure, test categories, and rules. Every tester MUST read and follow it.
4. **MANDATORY**: Instruction to plan tests on real conditions — this system has libvirt, virsh, and qemu-img available. Integration tests should model real-world scenarios (create test qcow2 disks, start qemu-nbd, test zstd vs zlib, test stall detection with real processes).
5. **MANDATORY**: Instruction to not only write NEW tests but also identify and delete/rewrite OUTDATED tests that no longer match the new behavior (see "Outdated Tests to Delete or Rewrite" section in `test-plan.md`).

Launch ALL groups IN PARALLEL (single message, multiple @Mr.Tester calls). After all testers return: fix any reported source bugs, re-delegate affected groups.

- [x] 9.1 Read `test-plan.md` Delegation Groups section
- [x] 9.2 Delegate group `shell-unit` to @Mr.Tester (scope: `tests/utils/test_shell.py`) — MUST pass TESTING.md to the tester
- [x] 9.3 Delegate group `shell-contract` to @Mr.Tester (scope: `tests/interfaces/test_shell.py`) — MUST pass TESTING.md to the tester
- [x] 9.4 Delegate group `mock-shell` to @Mr.Tester (scope: `tests/mocks/mock_shell.py`) — MUST pass TESTING.md to the tester
- [x] 9.5 Delegate group `mock-modules` to @Mr.Tester (scope: `tests/mocks/mock_modules.py`) — MUST pass TESTING.md to the tester
- [x] 9.6 Delegate group `config-model` to @Mr.Tester (scope: `tests/config/test_model.py`) — MUST pass TESTING.md to the tester
- [x] 9.7 Delegate group `config-facade` to @Mr.Tester (scope: `tests/config/test_facade.py`) — MUST pass TESTING.md to the tester
- [x] 9.8 Delegate group `config-fixtures` to @Mr.Tester (scope: `tests/conftest.py`, `tests/fixtures/configs/`) — MUST pass TESTING.md to the tester
- [x] 9.9 Delegate group `backup-copy-unit` to @Mr.Tester (scope: `tests/modules/backup/test_copy.py`) — MUST pass TESTING.md to the tester
- [x] 9.10 Delegate group `backup-bitmap-unit` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap.py`) — MUST pass TESTING.md to the tester
- [x] 9.11 Delegate group `core-summary` to @Mr.Tester (scope: `tests/core/test_schedule_summary.py`) — MUST pass TESTING.md to the tester
- [x] 9.12 Delegate group `core-unit` to @Mr.Tester (scope: `tests/core/test_engine.py`) — MUST pass TESTING.md to the tester, includes DELETING 9 outdated size estimation tests
- [x] 9.13 Delegate group `systemd-unit` to @Mr.Tester (scope: `tests/systemd/test_units.py`) — MUST pass TESTING.md to the tester
- [x] 9.14 Delegate group `integration-zstd` to @Mr.Tester (scope: `tests/integration/test_zstd_backup.py`) — MUST pass TESTING.md to the tester, MUST use real libvirt/virsh/qemu-img for real-condition tests
- [x] 9.15 Delegate group `integration-stall` to @Mr.Tester (scope: `tests/integration/test_stall_detection.py`) — MUST pass TESTING.md to the tester, MUST use real processes for stall detection testing
- [x] 9.16 Review @Mr.Tester reports and fix any source-level bugs discovered
- [x] 9.17 Re-delegate any groups affected by source fixes
- [x] 9.18 Verify all groups pass and coverage matches `test-plan.md`

<!--
  TEST ORCHESTRATION PROTOCOL (followed by the apply phase agent):

  1. Read test-plan.md → Delegation Groups section
  2. For EACH group listed, launch one @Mr.Tester subagent with:
     - The group's scope (file paths)
     - The group's scenario list from Coverage Map
     - The contents of /home/openuser/vm/qsnap/TESTING.md (the testing paradigm document)
     - Instruction: "Write or fix ONLY these specific tests. Report source bugs, don't fix them."
     - Instruction: "Plan tests on real conditions — libvirt, virsh, qemu-img are available on this system"
     - Instruction: "Identify and delete/rewrite OUTDATED tests that no longer match new behavior"
  3. Launch ALL groups IN PARALLEL (single message)
  4. After all testers return: fix any reported source bugs, re-delegate affected groups
  5. Repeat until all groups pass
-->

## 10. Final Verification

- [x] 10.1 Run full unit test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 10.2 Run integration tests: `poetry run pytest tests/integration/ -m integration`
- [x] 10.3 Run ruff linter: `poetry run ruff check qsnap/`
- [x] 10.4 Run ruff formatter: `poetry run ruff format --check qsnap/`
- [x] 10.5 Run pyright type checker: `poetry run pyright qsnap/`
- [x] 10.6 Verify no Cyrillic characters in any source files: `grep -rn '[\x{0400}-\x{04FF}]' qsnap/` (should return nothing)