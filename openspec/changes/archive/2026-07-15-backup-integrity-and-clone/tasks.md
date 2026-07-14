## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b feat/backup-integrity-and-clone`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`

## 2. Config Model — preserve_min, full_every, full_compress, content_hash

Reference: specs `preserve-min-config`, `config-model`, `periodic-full-backup`, `backup-hash-verification`
Pattern: AGENTS.md § Immutable Config Dataclasses, § Module Contract

- [x] 2.1 Add `snapshot_preserve_min: str | None = None` and `target_preserve_min: str | None = None` to `GlobalConfig`
- [x] 2.2 Add `snapshot_preserve_min: str | None = None` and `target_preserve_min: str | None = None` to `VMConfig`
- [x] 2.3 Add `target_preserve_min: str | None = None`, `full_every: str = "0d"`, and `full_compress: bool = False` to `TargetConfig`
- [x] 2.4 Add `content_hash: str | None = None` to `SnapshotResult` and `SnapshotInfo` in `qsnap/models/results.py`
- [x] 2.5 Add `FullBackupInfo` dataclass (`name`, `path`, `timestamp`) in `qsnap/models/results.py`

## 3. Config Facade — parse new TOML keys with inheritance

Reference: specs `preserve-min-config`, `config-model`, `periodic-full-backup`
Pattern: AGENTS.md § Immutable Config Dataclasses — ConfigFacade as root of truth

- [x] 3.1 Read `snapshot_preserve_min` and `target_preserve_min` from global section in TOML parser loop
- [x] 3.2 Read `snapshot_preserve_min` and `target_preserve_min` from `[[vm]]` sections
- [x] 3.3 Read `target_preserve_min`, `full_every`, `full_compress` from `[[vm.target]]` sections
- [x] 3.4 Apply inheritance chain in `_build_vm()`: global `snapshot_preserve_min` → VM (if VM value is None)
- [x] 3.5 Apply inheritance chain in `_build_target()`: VM `target_preserve_min` → target (if target value is None)

## 4. IStateManager — full backup tracking

Reference: specs `state-management`, `periodic-full-backup`
Pattern: AGENTS.md § State Manager as Separate Interface

- [x] 4.1 Add `get_last_full_backup(target_path: str) -> FullBackupInfo | None` to `IStateManager` ABC
- [x] 4.2 Add `set_last_full_backup(target_path: str, name: str, timestamp: datetime) -> None` to `IStateManager` ABC
- [x] 4.3 Implement both methods in `JsonStateManager` — persist under `"target_full_backups"` key per VM
- [x] 4.4 Implement both methods in `InMemoryStateManager` (in `tests/mocks/mock_state.py`)
- [x] 4.5 Update `InMemoryStateManager` to persist/restore `SnapshotInfo.content_hash`

## 5. Core — preserve_min wiring

Reference: specs `preserve-min-config`, `core-orchestrator`
Pattern: AGENTS.md § Pipeline — Core owns the sequence

- [x] 5.1 Update `Core._parse_preserve()` signature to accept `preserve_min_str: str | None = None`
- [x] 5.2 When `preserve_min_str` is non-None, override the default `preserve_min` in the returned `RetentionPolicy`
- [x] 5.3 Update `_evaluate_snapshot_retention()` to pass `vm_config.snapshot_preserve_min`
- [x] 5.4 Update `_evaluate_backup_retention()` to pass `target.target_preserve_min`

## 6. Core — schedule_summary

Reference: specs `schedule-summary`, `core-orchestrator`
Pattern: AGENTS.md § Modules are stateless workers — Core calls, modules compute

- [x] 6.1 Add `TimeBasedRetention.explain(items, policy, now, preserve_day_of_week) -> dict` method returning per-bucket breakdown
- [x] 6.2 Implement `Core.schedule_summary(vm_filter=None) -> str` — generate synthetic timestamps per VM, pass through retention, format output
- [x] 6.3 Log schedule summary at INFO level on timer invocation (`--timer` flag present)
- [x] 6.4 Add `--print-schedule` / `-S` CLI flag that calls `schedule_summary()` and prints to stdout

## 7. Snapshot Provider — SHA-256 at creation time

Reference: specs `backup-hash-verification`
Pattern: AGENTS.md § Module Contract — return result objects, no exceptions

- [x] 7.1 Add `_file_sha256(path: Path) -> str` utility function in `qsnap/modules/backup/verification.py` (read 8MB chunks, return hex digest)
- [x] 7.2 In `ExternalSnapshotProvider.create()`, after file creation, compute SHA-256 via `_file_sha256(snapshot_path)`
- [x] 7.3 Return hash in `SnapshotResult.content_hash`
- [x] 7.4 In `Core._create_snapshot()`, store `result.content_hash` in `SnapshotInfo` and persist via `IStateManager.record_snapshot()`

## 8. Backup Verification — verify="hash" tier

Reference: specs `backup-hash-verification`, `backup-verification`
Pattern: AGENTS.md § Anti-Patterns — no broad exceptions

- [x] 8.1 Add `expected_hash: str | None = None` parameter to `verify_backup()`
- [x] 8.2 Implement `verify_mode="hash"` branch: compute `_file_sha256(target_path)`, compare to `expected_hash`
- [x] 8.3 Return `"verification failed: hash mismatch"` on mismatch, `None` on match or when `expected_hash` is `None`
- [x] 8.4 In `Core._backup_target()`, pass `snapshot_info.content_hash` as `expected_hash` to `verify_backup()`

## 9. Full Backup — qemu-img convert + rebase

Reference: specs `periodic-full-backup`, `backup-provider`
Pattern: AGENTS.md § Modules are stateless workers — receive config as method parameters

- [x] 9.1 Implement `FileCopyBackupProvider.create_full_backup(source_snapshot, target, compress=False) -> BackupResult` — calls `qemu-img convert [-c]`
- [x] 9.2 Use atomic pattern: convert to `.tmp`, `mv` to final name on success
- [x] 9.3 In `Core._backup_target()`, check `state.get_last_full_backup(target.path)` before incremental loop
- [x] 9.4 If `full_every` interval elapsed, call `provider.create_full_backup()` on most recent snapshot, then `state.set_last_full_backup()`
- [x] 9.5 Update `FileCopyBackupProvider.transfer_missing()` to detect FULL anchor and rebase new incrementals via `qemu-img rebase -u -b ./FULL...`
- [x] 9.6 When no FULL anchor exists, preserve existing rebase-to-source behavior

## 10. README

Reference: spec `cli-interface`, proposal § Impact

- [x] 10.1 Add Quick Start section with minimal TOML config and first `qsnap run` example
- [x] 10.2 Add full configuration reference table with ALL keys: `snapshot_preserve`, `snapshot_preserve_min`, `target_preserve`, `target_preserve_min`, `full_every`, `full_compress`, `verify`, `incremental`, `snapshot_dir`, `encrypt`
- [x] 10.3 Add retention policy guide with examples for home-host and server scenarios
- [x] 10.4 Add full backup section explaining what it is, when to enable, and the `full_compress` trade-off
- [x] 10.5 Add verification section explaining three tiers: `off`, `metadata`, `hash`, `full`
- [x] 10.6 Add restore section with step-by-step from backup files
- [x] 10.7 Add example configs for: home host with USB backup target, server with persistent network target

## 11. Testing

<!--
  TEST ORCHESTRATION PROTOCOL (mandatory — apply phase agent MUST follow this):

  1. Read test-plan.md → Delegation Groups section
  2. For EACH group listed, launch one @Mr.Tester subagent in parallel (all in one message)
  3. EACH @Mr.Tester subagent MUST receive a copy of TESTING.md (at /home/openuser/vm/qsnap/TESTING.md)
     as part of their instructions. This document describes the testing architecture,
     categories, paradigm, and rules that ALL tests must follow.
     The message to each @Mr.Tester MUST include:
       - "Read TESTING.md at /home/openuser/vm/qsnap/TESTING.md — it describes the test architecture and rules you must follow"
     - The group's scope (file paths from test-plan.md)
     - The group's scenario list from Coverage Map
     - Instruction: "Write or fix ONLY these specific tests. Report source bugs, don't fix them."
  4. After all testers return: fix any reported source bugs, re-delegate affected groups
  5. Repeat until all groups pass
-->

Read the full `test-plan.md` Delegation Groups section before proceeding.

- [x] 11.1 Read `test-plan.md` and all spec files to understand expected behavior
- [x] 11.2 Delegate group `config-unit` to @Mr.Tester **with TESTING.md** (scope: `tests/config/test_model.py`, `tests/config/test_facade.py`)
- [x] 11.3 Delegate group `core-unit` to @Mr.Tester **with TESTING.md** (scope: `tests/core/test_preserve.py`, `tests/core/test_pipeline.py`, `tests/core/test_schedule_summary.py`)
- [x] 11.4 Delegate group `modules-unit` to @Mr.Tester **with TESTING.md** (scope: `tests/modules/snapshot/test_external.py`, `tests/modules/backup/test_copy.py`, `tests/modules/backup/test_verification.py`, `tests/modules/retention/test_time_based.py`)
- [x] 11.5 Delegate group `state-unit` to @Mr.Tester **with TESTING.md** (scope: `tests/state/test_manager.py`)
- [x] 11.6 Delegate group `cli-unit` to @Mr.Tester **with TESTING.md** (scope: `tests/cli/test_app.py`, `tests/cli/test_commands.py`)
- [x] 11.7 Delegate group `models-unit` to @Mr.Tester **with TESTING.md** (scope: `tests/models/test_results.py`)
- [x] 11.8 Delegate group `mocks-unit` to @Mr.Tester **with TESTING.md** (scope: `tests/mocks/test_mock_state.py`, `tests/mocks/test_mock_factory.py`)
- [x] 11.9 Delegate group `interfaces-unit` to @Mr.Tester **with TESTING.md** (scope: `tests/interfaces/test_state_manager.py`, `tests/interfaces/test_backup_provider.py`, `tests/interfaces/test_snapshot_provider.py`)
- [x] 11.10 Review all @Mr.Tester reports and fix any source-level bugs discovered
- [x] 11.11 Re-delegate any groups affected by source fixes
- [x] 11.12 Verify all groups pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`
- [x] 11.13 Verify coverage matches `test-plan.md` Coverage Map
