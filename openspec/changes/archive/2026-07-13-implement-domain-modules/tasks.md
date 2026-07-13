## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b implement-domain-modules`
- [x] 1.2 Run the full test suite to establish a passing baseline: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`

## 2. ExternalSnapshotProvider (ISnapshotProvider)

Reference: `specs/snapshot-provider/spec.md`, design decisions D1, autosnapcommit.sh:143-176

- [x] 2.1 Create `qsnap/modules/__init__.py` and `qsnap/modules/snapshot/__init__.py`
- [x] 2.2 Implement `qsnap/modules/snapshot/external.py` — `ExternalSnapshotProvider(ISnapshotProvider)` class with constructor accepting `IShell`. Do NOT inherit from Core (design D1).
- [x] 2.3 Implement `create(vm_config, snapshot_name, disk, snapshot_path) -> SnapshotResult`: call `virsh snapshot-create-as --domain {name} --name {name} --diskspec {disk},file={path},snapshot=external --disk-only --atomic --no-metadata`, then `chmod g+rw,o+r`, then `qemu-img info --output=json` to get `actual-size`. Parse JSON with `json.loads()`. Return `SnapshotResult(success=True, name=..., path=..., new_allocation=<actual-size>)` on success, `SnapshotResult(success=False, error=...)` on failure/timeout.
- [x] 2.4 Implement `list(vm_config) -> list[SnapshotInfo]`: run `virsh domblklist --domain {name}` to find the active disk path, then `qemu-img info --force-share --backing-chain --output=json {active_disk}`. Parse the JSON array — skip the first element (base), create `SnapshotInfo` for each subsequent element with name (from filename stem), path, timestamp (parsed from name or mtime), allocation (`actual-size` from JSON).
- [x] 2.5 Implement `delete(snapshot: SnapshotInfo) -> ShellResult`: run `rm -f {snapshot.path}` through shell.
- [x] 2.6 Wire into `qsnap/factory/default.py`: `create_snapshot_provider` returns `ExternalSnapshotProvider(self._shell)`.

## 3. AllocationSizeDetector (IChangeDetector)

Reference: `specs/change-detection/spec.md`, design decisions D1, D2, D3

- [x] 3.1 Create `qsnap/modules/change/__init__.py` and `qsnap/modules/change/allocation_detector.py`
- [x] 3.2 Implement `AllocationSizeDetector(IChangeDetector)` — constructor accepts `IShell` and `IStateManager`. Do NOT inherit from Core (design D1).
- [x] 3.3 Implement `has_changed(vm_config) -> ChangeResult`:
  1. Get `last_alloc = self._state.get_last_allocation(vm_config.name)`. If `None` → return `ChangeResult(has_changed=True, last_allocation=0, current_allocation=0)`.
  2. Get active disk path: `virsh domblklist --domain {vm_config.name}`, parse second column (design D3).
  3. Get current allocation: `qemu-img info --force-share --output=json {active_disk_path}`, parse `actual-size`.
  4. If any command fails → return `ChangeResult(has_changed=True)` (fail-safe).
  5. Return `ChangeResult(has_changed=(current > last_alloc), last_allocation=last_alloc, current_allocation=current)`.
- [x] 3.4 Wire into `qsnap/factory/default.py`: `create_change_detector` returns `AllocationSizeDetector(self._shell, self._state)`.

## 4. BlockCommitManager (ILifecycleManager)

Reference: `specs/lifecycle-manager/spec.md`, design decisions D1, D4, autosnapcommit.sh:101-140

- [x] 4.1 Create `qsnap/modules/lifecycle/__init__.py` and `qsnap/modules/lifecycle/blockcommit_manager.py`
- [x] 4.2 Implement `BlockCommitManager(ILifecycleManager)` — constructor accepts `IShell`. Do NOT inherit from Core (design D1).
- [x] 4.3 Implement `blockcommit(vm_config, snapshots_to_merge) -> CommitResult`:
  1. If `snapshots_to_merge` is empty → return `CommitResult(success=True, committed_snapshot="")`.
  2. Get disk target: `virsh domblklist --domain {vm_config.name}`, extract target (first column, e.g. `vda`).
  3. For each snapshot in `snapshots_to_merge` (oldest first):
     - Run `virsh blockcommit --domain {vm_config.name} --path {target} --base {vm_config.base_image} --top {snapshot.path} --delete --verbose --wait` with timeout=3600.
     - If fails → return `CommitResult(success=False, committed_snapshot=snapshot.name, error=...)`. Do NOT continue to next snapshot (design D4: short-circuit on first failure).
  4. Return `CommitResult(success=True, committed_snapshot=<last_merged>)` on full success.
- [x] 4.4 Wire into `qsnap/factory/default.py`: `create_lifecycle_manager` returns `BlockCommitManager(self._shell)`.

## 5. FileCopyBackupProvider (IBackupProvider)

Reference: `specs/backup-provider/spec.md`, design decisions D1, D5

- [x] 5.1 Create `qsnap/modules/backup/__init__.py` and `qsnap/modules/backup/file_copy.py`
- [x] 5.2 Implement `FileCopyBackupProvider(IBackupProvider)` — constructor accepts `IShell`. Do NOT inherit from Core (design D1).
- [x] 5.3 Implement `transfer_missing(vm_config, target, snapshots) -> list[BackupResult]`:
  1. Get existing backups: `existing = self.list(target)`, extract names to a set.
  2. For each snapshot NOT in existing_names:
     - Copy file: `cp {snapshot.path} {target.path / snapshot.name}.qcow2` (timeout=600).
     - If `target.incremental == True`: get backing filename via `qemu-img info --output=json {snapshot.path}`, extract `backing-filename`, rebase: `qemu-img rebase -u -b {basename_of_backing} {target_file}` (design D5: `-u` updates only metadata).
     - Append `BackupResult(success=True, snapshot_name=..., source_path=..., target_path=..., bytes_transferred=<from cp stderr or stat>)`.
     - If `cp` fails → append `BackupResult(success=False, error=...)`.
  3. Return results list.
- [x] 5.4 Implement `list(target: TargetConfig) -> list[SnapshotInfo]`: if `target.path` does not exist → return `[]`. For each `*.qcow2` in `target.path.glob("*.qcow2")`: run `qemu-img info --output=json {file}`, parse name, path, timestamp (try from filename stem split on `.`, fallback to `mtime`), allocation (`actual-size`). Sort by timestamp.
- [x] 5.5 Implement `delete(backup: SnapshotInfo) -> ShellResult`: run `rm -f {backup.path}` through shell.
- [x] 5.6 Wire into `qsnap/factory/default.py`: `create_backup_provider` returns `FileCopyBackupProvider(self._shell)`.

## 6. Factory Finalization

- [x] 6.1 Verify `qsnap/factory/default.py` has all 5 `create_*` methods returning concrete instances (no more `NotImplementedError`).
- [x] 6.2 Add necessary imports for all 4 new module classes in `qsnap/factory/default.py`.
- [x] 6.3 Run existing tests to ensure no regressions: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`. The Core pipeline tests (`tests/core/test_pipeline.py`, `tests/core/test_engine.py`) should still pass since they use `MockVMModuleFactory`, not `DefaultFactory`.
- [x] 6.4 Update test `tests/factory/test_default.py`: modify `test_default_factory_unimplemented_raises_notimplementederror` — rename to `test_default_factory_returns_correct_interface_types`, change parametrization from asserting `pytest.raises(NotImplementedError)` to `isinstance(result, <ABC>)` for each of the 4 methods. Map: `create_snapshot_provider → ISnapshotProvider`, `create_backup_provider → IBackupProvider`, `create_change_detector → IChangeDetector`, `create_lifecycle_manager → ILifecycleManager`.

## 7. Testing

**READ BEFORE STARTING:** The test plan is at `test-plan.md`. It defines 6 non-overlapping Delegation Groups. Each group must be executed by a dedicated `@Mr.Tester` subagent.

**CRITICAL — Test Delegation Protocol:**

When delegating EACH test group to `@Mr.Tester`, the implementing agent MUST:
1. Pass the `TESTING.md` file (project root) as context — it defines the testing paradigm, mock patterns, directory structure, and test categories
2. Pass the full `test-plan.md` — it defines the Coverage Map, Delegation Groups, Test Modifications, and Risks
3. Pass the relevant spec files from `openspec/changes/implement-domain-modules/specs/<capability>/spec.md` for that group's capability
4. Pass `design.md` for architectural context (decisions D1-D5)
5. Instruct `@Mr.Tester` to: "Write or fix ONLY the tests in your group's Scope. Report source-level bugs, do NOT fix them. Every test MUST follow the TESTING.md patterns: constructor DI, MockShell for I/O, result object assertions. Contract tests MUST assert the concrete class does NOT inherit from Core (design D1)."

**Launch ALL groups IN PARALLEL (single message, multiple task tool calls):**

- [x] 7.1 Read `test-plan.md` Delegation Groups section and `TESTING.md` (project root)
- [x] 7.2 Delegate group `snapshot-unit` to @Mr.Tester (scope: `tests/modules/snapshot/`, spec: `specs/snapshot-provider/spec.md`). Pass TESTING.md and design.md. Tests: 7 scenarios (test_external.py + __init__.py).
- [x] 7.3 Delegate group `change-unit` to @Mr.Tester (scope: `tests/modules/change/`, spec: `specs/change-detection/spec.md`). Pass TESTING.md and design.md. Tests: 4 scenarios (test_allocation.py + __init__.py). MUST verify domblklist is called, NOT base_image (design D3 edge case).
- [x] 7.4 Delegate group `lifecycle-unit` to @Mr.Tester (scope: `tests/modules/lifecycle/`, spec: `specs/lifecycle-manager/spec.md`). Pass TESTING.md and design.md. Tests: 5 scenarios (test_blockcommit.py + __init__.py). MUST verify sequential merging with short-circuit on first failure (design D4 edge case).
- [x] 7.5 Delegate group `backup-unit` to @Mr.Tester (scope: `tests/modules/backup/`, spec: `specs/backup-provider/spec.md`). Pass TESTING.md and design.md. Tests: 10 scenarios (test_copy.py + __init__.py). MUST verify `qemu-img rebase` uses `-u` flag and backing path is bare filename (design D5 edge case).
- [x] 7.6 Delegate group `interface-contracts` to @Mr.Tester (scope: `tests/interfaces/` — files: `test_snapshot_provider.py`, `test_change_detector.py`, `test_lifecycle_manager.py`, `test_backup_provider.py`). Pass TESTING.md and design.md. Tests: 24 contract tests across 4 files. ALL contract tests MUST include `test_<concrete>_no_core_inheritance` asserting `not issubclass(ConcreteClass, Core)` (design D1). Parametrize over `[ConcreteClass, MockClass]` per TESTING.md pattern.
- [x] 7.7 Delegate group `factory-unit` to @Mr.Tester (scope: `tests/factory/test_default.py`). Pass TESTING.md and design.md. MODIFY existing test — replace NotImplementedError assertion with return-type verification (see task 6.4).
- [x] 7.8 Review all @Mr.Tester reports. Fix any source-level bugs discovered.
- [x] 7.9 Re-delegate any groups affected by source fixes.
- [x] 7.10 Run full test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`. Verify all 51+ tests pass.
- [x] 7.11 Verify coverage matches test-plan.md: all 26 spec scenarios + 24 contract tests + factory modification are implemented and passing.
