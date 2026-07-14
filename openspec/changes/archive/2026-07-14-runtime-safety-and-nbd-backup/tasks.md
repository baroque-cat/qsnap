## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b runtime-safety-and-nbd-backup`
- [x] 1.2 Run full test suite to establish passing baseline: `pytest tests/ -m "not integration and not stress and not e2e" -v`
- [x] 1.3 Run linter check: `ruff check qsnap/` and `pyright qsnap/`

## 2. Config Model — New Fields

- [x] 2.1 Add `verify: str = "metadata"` field to `TargetConfig` dataclass in `qsnap/models/config.py`
- [x] 2.2 Add `snapshot_quiesce: bool = False` field to `VMConfig` dataclass in `qsnap/models/config.py`
- [x] 2.3 Add `lifecycle_mode: str = "virsh"` field to `VMConfig` (for qemu-img commit vs virsh blockcommit)
- [x] 2.4 Update `ConfigFacade._build_target()` in `qsnap/config/facade.py` to parse `verify` from TOML
- [x] 2.5 Update `ConfigFacade._build_vm()` to parse `snapshot_quiesce` and `lifecycle_mode` from TOML
- [x] 2.6 Update `qsnap.toml.example` with new fields and comments

## 3. Shell Abstraction — check Parameter

- [x] 3.1 Add `check: bool = False` parameter to `IShell.run()` in `qsnap/interfaces/shell.py`
- [x] 3.2 Update `SubprocessShell.run()` in `qsnap/shell/subprocess_shell.py`: when `check=True`, log command at DEBUG even on failure, not ERROR
- [x] 3.3 Update all mock shells (`tests/mocks/mock_shell.py`) to accept the parameter

## 4. State Management — Deferred Operations

- [x] 4.1 Add `DeferredBlockcommit` frozen dataclass to `qsnap/models/results.py`: fields `snapshots: list[str]`, `reason: str`, `since: datetime`
- [x] 4.2 Add abstract methods to `IStateManager` in `qsnap/interfaces/state.py`: `get_deferred_operations(vm_name)`, `add_deferred_blockcommit(vm_name, snapshots, reason)`, `clear_deferred_operations(vm_name)`
- [x] 4.3 Implement in `JsonStateManager` (`qsnap/state/json_manager.py`): store deferred_operations in per-VM JSON, atomic writes
- [x] 4.4 Update `InMemoryStateManager` (`tests/mocks/mock_state.py`) with deferred operations methods

## 5. Lifecycle Manager — MAC Detection + QemuImgCommitManager

- [x] 5.1 Add AppArmor/SELinux error detection to `BlockCommitManager.blockcommit()` in `qsnap/modules/lifecycle/blockcommit_manager.py`: inspect stderr for "Permission denied" / "apparmor" → reason "apparmor", "Operation not permitted" / "AVC" → reason "selinux". Return `CommitResult(success=False, committed_snapshot="", error="blocked by apparmor|selinux")`.
- [x] 5.2 Create `qsnap/modules/lifecycle/qemu_img_commit.py` with `QemuImgCommitManager` class implementing `ILifecycleManager`. Use `qemu-img commit -b <base> -d <top>`.
- [x] 5.3 Update `DefaultFactory.create_lifecycle_manager(mode)` in `qsnap/factory/default.py`: branch on `mode` returning `BlockCommitManager` or `QemuImgCommitManager`

## 6. Snapshot Provider — Quiesce Support

- [x] 6.1 Add `quiesce: bool = False` parameter to `ISnapshotProvider.create()` in `qsnap/interfaces/snapshot.py`
- [x] 6.2 Update `ExternalSnapshotProvider.create()` in `qsnap/modules/snapshot/external.py`: when `quiesce=True`, pass `--quiesce` to `virsh snapshot-create-as`, extend timeout to 180s
- [x] 6.3 Update `MockSnapshotProvider` (`tests/mocks/mock_modules.py`) to accept the parameter

## 7. Change Detection — MapChangeDetector

- [x] 7.1 Create `qsnap/modules/change/map_detector.py` with `MapChangeDetector` class implementing `IChangeDetector`. Use `qemu-img map --output=json` for allocated-region comparison. Fail-safe: return `changed=True` on any command error.
- [x] 7.2 Update `DefaultFactory.create_change_detector(mode)` in `qsnap/factory/default.py`: return `MapChangeDetector` when `mode == "allocation-map"`

## 8. Backup Provider — NBD Bitmap v2 + Verification

- [x] 8.1 Rewrite `BitmapBackupProvider` in `qsnap/modules/backup/bitmap.py` to use NBD pull-model: `virsh backup-begin` with Unix socket → `qemu-img convert -n nbd:unix:<socket>` → `rm -f <socket>`. NBD socket path: `/tmp/qsnap-backup-{pid}.sock`. Remove stale socket before starting. Libvirt version >= 6.0 check in `__init__` raises `RuntimeError` on older versions.
- [x] 8.2 Add verification step to `FileCopyBackupProvider.transfer_missing()` in `qsnap/modules/backup/file_copy.py`: when `target.verify != "off"`, run `qemu-img info --output=json` on target → assert format="qcow2", virtual-size match, actual-size tolerance ±10%. When `target.verify == "full"`, additionally run `qemu-img compare -q`. All failures → `BackupResult(success=False, error="verification failed: ...")`
- [x] 8.3 Add same verification step to `BitmapBackupProvider.transfer_missing()`
- [x] 8.4 Update `DefaultFactory.create_backup_provider()`: catch `RuntimeError` from `BitmapBackupProvider` → log warning → fall back to `FileCopyBackupProvider`

## 9. Retention Engine — preserve_min="latest"

- [x] 9.1 Update `_parse_duration()` in `qsnap/retention/time_based.py`: handle string `"latest"` → `timedelta(seconds=0)`
- [x] 9.2 Update `evaluate()`: when `preserve_min` window is 0 (from "latest"), the preserve_min step keeps the single most recent item

## 10. Core Orchestrator — Validation, Deferred Ops, Verification Wiring

- [x] 10.1 Add `_validate_environment(vm_config)` method to Core in `qsnap/core/__init__.py`: verify snapshot_dir exists/writable, base_image exists, virsh/qemu-img in PATH, VM defined in libvirt (`virsh dominfo`). Return error if any check fails. Use `shell.run(check=True)` for checks.
- [x] 10.2 Integrate validation into `_execute_pipeline()`: call `_validate_environment()` before change detection. On failure, return `VMRunResult(success=False)` and log error.
- [x] 10.3 Add deferred operations check to `_execute_snapshot_steps()`: before step 2, check `IStateManager.get_deferred_operations()`. If VM is shut off → execute pending blockcommits → clear queue. If VM is running → log INFO, skip.
- [x] 10.4 After blockcommit in step 4, check `CommitResult` for MAC denial (error contains "apparmor" or "selinux"). If detected, call `add_deferred_blockcommit()`.
- [x] 10.5 Update `_create_snapshot()`: pass `quiesce=vm_config.snapshot_quiesce` to `provider.create()`.
- [x] 10.6 Update `_backup_target()`: verification results reflected in `VMRunResult.backup_failed`.
- [x] 10.7 Update `_execute_backup_steps()`: handle verification failures in backup_failed flag.

## 11. CLI — Tree, --long, Verify Config

- [x] 11.1 Add `--tree` flag to `list snapshots` subcommand in `qsnap/cli/app.py`
- [x] 11.2 Implement tree-format output in `qsnap/cli/commands.py`: traverse backing chain and print indented hierarchy
- [x] 11.3 Ensure `--long` / `-L` global flag works as shortcut for `--format long`
- [x] 11.4 Add `lifecycle_mode` and `snapshot_quiesce` and `verify` fields to example config

## 12. README.md

- [x] 12.1 Create `README.md` in project root: project description, installation (pip install from git), basic configuration example with TOML snippet, quick start, commands reference, links to AGENTS.md and TESTING.md

## 13. Missing Fixtures and Minor Cleanup

- [x] 13.1 Create `tests/fixtures/shell_outputs/` directory and populate with canonical domblklist, snapshot-list, qemu-img-info, backing-chain output files for consistent test fixtures
- [x] 13.2 Refactor `blockcommit_manager.py` to import `parse_domblklist_target` from `qsnap.utils.parsing` instead of using local `_parse_domblklist_target`

## 14. Testing — 13 Delegation Groups (parallel @Mr.Tester)

- [x] 14.1 Read `test-plan.md` Delegation Groups section (lines 254-484)
- [x] 14.2 Delegate group `Group A — Core Orchestration` to @Mr.Tester (scope: tests/core/test_engine.py, test_pipeline.py, test_list_commands.py)
- [x] 14.3 Delegate group `Group B — Backup Providers` to @Mr.Tester (scope: tests/modules/backup/test_bitmap.py, test_copy.py)
- [x] 14.4 Delegate group `Group C — Lifecycle Managers` to @Mr.Tester (scope: tests/modules/lifecycle/test_blockcommit.py, test_qemu_img_commit.py NEW)
- [x] 14.5 Delegate group `Group D — Change Detection` to @Mr.Tester (scope: tests/modules/change/test_allocation.py, test_map_detector.py NEW)
- [x] 14.6 Delegate group `Group E — Snapshot Provider` to @Mr.Tester (scope: tests/modules/snapshot/test_external.py)
- [x] 14.7 Delegate group `Group F — State Management` to @Mr.Tester (scope: tests/state/test_manager.py)
- [x] 14.8 Delegate group `Group G — Factory` to @Mr.Tester (scope: tests/factory/test_default.py)
- [x] 14.9 Delegate group `Group H — Config Model` to @Mr.Tester (scope: tests/config/test_model.py, tests/config/test_facade.py)
- [x] 14.10 Delegate group `Group I — Retention Engine` to @Mr.Tester (scope: tests/modules/retention/test_time_based.py)
- [x] 14.11 Delegate group `Group J — Shell Abstraction` to @Mr.Tester (scope: tests/utils/test_shell.py)
- [x] 14.12 Delegate group `Group K — CLI Interface` to @Mr.Tester (scope: tests/cli/test_commands.py, tests/cli/test_app.py)
- [x] 14.13 Delegate group `Group L — Contract Tests` to @Mr.Tester (scope: tests/interfaces/*.py)
- [x] 14.14 Delegate group `Group M — Mock Tests` to @Mr.Tester (scope: tests/mocks/*.py)
- [x] 14.15 Review @Mr.Tester reports and fix any source-level bugs discovered
- [x] 14.16 Re-delegate any groups affected by source fixes
- [x] 14.17 Verify all groups pass and coverage matches `test-plan.md`

<!--
  TEST ORCHESTRATION PROTOCOL (followed by the apply phase agent):

  1. Read test-plan.md → Delegation Groups section
  2. For EACH group A-M, launch one @Mr.Tester subagent with:
     - The group's scope (file paths)
     - The group's scenario list from Coverage Map
     - TESTING.md as mandatory context
     - Instruction: "Write or fix ONLY these specific tests. Report source bugs, don't fix them."
  3. Launch ALL 13 groups IN PARALLEL (single message)
  4. After all testers return: fix any reported source bugs, re-delegate affected groups
  5. Repeat until all groups pass → then verify with: pytest tests/ -v, ruff check qsnap/, pyright qsnap/
-->
