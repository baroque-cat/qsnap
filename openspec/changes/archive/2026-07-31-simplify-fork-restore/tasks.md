## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b simplify-fork-restore`
- [x] 1.2 Verify all existing tests pass before starting: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 1.3 Read all spec files under `openspec/changes/simplify-fork-restore/specs/` and `openspec/changes/simplify-fork-restore/design.md` before starting implementation

## 2. State Management (IStateManager)

**Specs:** `specs/state-management/spec.md` — ADDED `reset_vm_state` and `reset_target_state` methods
**Design:** D4 — atomic bulk-reset methods for restore cleanup

- [x] 2.1 Add `reset_vm_state(vm_name: str) -> None` abstract method to `IStateManager` in `qsnap/interfaces/state.py`
- [x] 2.2 Add `reset_target_state(target_path: str) -> None` abstract method to `IStateManager` in `qsnap/interfaces/state.py`
- [x] 2.3 Implement `reset_vm_state()` in `JsonStateManager` (`qsnap/state/json_manager.py`) — clear `snapshots`, `last_allocation`, `deferred_operations` keys, save atomically
- [x] 2.4 Implement `reset_target_state()` in `JsonStateManager` — remove target entry from `_full_backups.json`, `_dependencies.json`, `_target_state.json`, save all atomically
- [x] 2.5 Implement `reset_vm_state()` and `reset_target_state()` in `InMemoryStateManager` (`tests/mocks/mock_state.py`) — clear in-memory dicts
- [x] 2.6 Verify: `poetry run pytest tests/state/test_manager.py tests/interfaces/test_state_manager.py tests/mocks/ -v`

## 3. Change Detection Default

**Specs:** `specs/change-detection/spec.md` — MODIFIED default to `allocation-map`
**Design:** D8 — backward compatible, explicit `allocation-size` still works

- [x] 3.1 Change `VMConfig.change_detection_mode` default from `"allocation-size"` to `"allocation-map"` in `qsnap/models/config.py`
- [x] 3.2 Verify: `poetry run pytest tests/config/test_model.py tests/modules/change/ -v`

## 4. Fork Refactoring

**Specs:** `specs/fork-mode/spec.md` — MODIFIED fork to standalone image creation only, REMOVED deploy, XML, UUID, add-to-config
**Design:** D1 — direct `qemu-img convert` for all sources, no NBD

- [x] 4.1 Rewrite `Core.fork()` in `qsnap/core/__init__.py` — new signature: `fork(name: str, output_path: Path, vm_filter: str | None = None) -> RestoreResult`. Steps: resolve snapshot, log chain size estimate, `qemu-img convert --force-share -O qcow2 <source> <output>`, return `RestoreResult(chain_files=[output_path])`. Remove all XML manipulation, `virsh define`, UUID generation, `--add-to-config` logic.
- [x] 4.2 Remove `Core.deploy()` method from `qsnap/core/__init__.py` (was 1:1 wrapper around fork)
- [x] 4.3 Remove `Core._append_vm_to_config()` helper if no longer referenced
- [x] 4.4 Update fork CLI parser in `qsnap/cli/app.py` — remove `--as-vm`, `--storage`, `--add-to-config` flags; add required `--output <path>` flag; remove `deploy` subparser entirely
- [x] 4.5 Update `handle_fork()` in `qsnap/cli/commands.py` — call `core.fork(name, output_path, vm_filter)` with new signature
- [x] 4.6 Remove `handle_deploy()` from `qsnap/cli/commands.py` and remove `"deploy"` from `_DISPATCH` map in `app.py`
- [x] 4.7 Verify: `poetry run pytest tests/core/test_fork.py tests/cli/test_commands.py tests/cli/test_app.py -v`

## 5. Restore Refactoring

**Specs:** `specs/restore-command/spec.md` — MODIFIED restore to VM disk replacement with state cleanup
**Design:** D2 (temp path + atomic replace), D3 (VM must be stopped), D5 (best-effort checkpoint cleanup), D6 (pre-restore chain verification)

- [x] 5.1 Rewrite `Core.restore()` in `qsnap/core/__init__.py` — new signature: `restore(name: str, vm_filter: str | None = None) -> RestoreResult`. Steps: (1) resolve snapshot, (2) verify VM stopped via `is_vm_running()`, (3) pre-verify chain via `scan_backing_chain()`, (4) `qemu-img convert --force-share -O qcow2 <source> <snapshot_dir>/<vm>.restored.qcow2.tmp`, (5) delete old snapshot overlays from `snapshot_dir`, (6) `mv <temp> <vm_config.base_image>`, (7) strip `<backingStore>` from domain XML via existing `_refresh_domain_backing_store()` + update `<source file>`, (8) `virsh define`, (9) `state.reset_vm_state(vm_name)` + `state.reset_target_state(target_path)` for each target, (10) best-effort `virsh checkpoint-delete --metadata` for each `qsnap-*` checkpoint
- [x] 5.2 Add `--dry-run` and `--yes` flags to restore CLI parser in `qsnap/cli/app.py`
- [x] 5.3 Update `handle_restore()` in `qsnap/cli/commands.py` — remove `target_dir` positional, add `--dry-run`/`--yes` handling, call `core.restore(name, vm_filter)`
- [x] 5.4 Implement confirmation prompt in `handle_restore()` — without `--yes`, prompt: "WARNING: This will replace the VM's disk and delete all snapshots. Continue? [y/N]"
- [x] 5.5 Verify: `poetry run pytest tests/core/test_restore.py tests/cli/test_commands.py tests/cli/test_app.py -v`

## 6. List Backups --tree

**Specs:** `specs/list-commands/spec.md` — ADDED tree grouping; `specs/cli-interface/spec.md` — ADDED `--tree` flag for `list backups`
**Design:** D7 — reuse existing `_group_backups_by_chain()` and `_resolve_chain_full_anchor()`

- [x] 6.1 Add `tree: bool = False` parameter to `Core.list_backups()` in `qsnap/core/__init__.py` — when `True`, group by FULL anchor using existing `_group_backups_by_chain()`
- [x] 6.2 Add `--tree` flag to `list backups` subparser in `qsnap/cli/app.py`
- [x] 6.3 Add `_print_backup_tree()` function to `qsnap/cli/commands.py` — display FULL anchors at top level with indented incrementals beneath, grouped by target
- [x] 6.4 Update `handle_list()` in `commands.py` — when `list backups` + `--tree`, call `core.list_backups(tree=True)` and pass to `_print_backup_tree()`
- [x] 6.5 Verify: `poetry run pytest tests/cli/test_tree.py tests/core/test_list_commands.py -v`

## 7. Testing

**Read `test-plan.md` Delegation Groups section before starting.**

**CRITICAL INSTRUCTION FOR THE PROGRAMMER AGENT:** When delegating ANY test group to a @Mr.Tester sub-agent, you MUST pass the file `TESTING.md` (located at the project root) to the tester as part of the task. The tester MUST read `TESTING.md` to understand the project's testing philosophy, directory structure, test categories, mock strategy, and conventions before writing any tests. Without `TESTING.md`, the tester will not follow the correct paradigm.

- [x] 7.1 Read `test-plan.md` Delegation Groups section
- [x] 7.2 Delegate group `fork-unit` to @Mr.Tester — scope: `tests/core/test_fork.py` (REWRITE), `tests/cli/test_commands.py` (MODIFY fork sections), `tests/cli/test_app.py` (MODIFY fork args). **Pass `TESTING.md` to the tester.** The tester should: (a) read TESTING.md, (b) study existing tests in these files, (c) delete tests for removed functionality (NBD, XML, UUID, deploy, --as-vm, --storage, --add-to-config), (d) write new tests matching the spec scenarios.
- [x] 7.3 Delegate group `restore-unit` to @Mr.Tester — scope: `tests/core/test_restore.py` (NEW), `tests/cli/test_commands.py` (MODIFY restore sections), `tests/cli/test_app.py` (MODIFY restore args). **Pass `TESTING.md` to the tester.** The tester should: (a) read TESTING.md, (b) write new tests for all restore scenarios (disk replacement, state reset, checkpoint cleanup, dry-run, --yes, broken chain pre-check).
- [x] 7.4 Delegate group `cli-app` to @Mr.Tester — scope: `tests/cli/test_app.py` (help text, deploy removal), `tests/cli/test_commands.py` (deploy removal). **Pass `TESTING.md` to the tester.** The tester should: (a) read TESTING.md, (b) verify deploy is removed from help text and dispatch map, (c) verify subcommand dispatch still works.
- [x] 7.5 Delegate group `cli-tree` to @Mr.Tester — scope: `tests/cli/test_tree.py` (MODIFY), `tests/core/test_list_commands.py` (MODIFY). **Pass `TESTING.md` to the tester.** The tester should: (a) read TESTING.md, (b) add backup tree tests, (c) verify tree grouping by FULL anchor, (d) verify orphan handling.
- [x] 7.6 Delegate group `change-detection` to @Mr.Tester — scope: `tests/config/test_model.py` (MODIFY for new default). **Pass `TESTING.md` to the tester.** The tester should: (a) read TESTING.md, (b) verify default is now `allocation-map`, (c) verify explicit `allocation-size` still works.
- [x] 7.7 Delegate group `state-management` to @Mr.Tester — scope: `tests/interfaces/test_state_manager.py` (MODIFY), `tests/state/test_manager.py` (MODIFY), `tests/mocks/mock_state.py` (MODIFY). **Pass `TESTING.md` to the tester.** The tester should: (a) read TESTING.md, (b) add contract tests for `reset_vm_state` and `reset_target_state`, (c) verify atomic writes, (d) verify InMemoryStateManager implementation.
- [x] 7.8 Delegate group `integration` to @Mr.Tester — scope: `tests/integration/test_fork.py` (NEW), `tests/integration/test_restore.py` (NEW), `tests/integration/test_backup_tree.py` (NEW). **Pass `TESTING.md` to the tester.** The tester has FULL access to libvirt and qemu. The tester should: (a) read TESTING.md, (b) write integration tests that use real virsh/qemu-img against disposable test VMs, (c) verify fork produces standalone qcow2 with no backing file, (d) verify restore replaces VM disk and VM boots, (e) verify restore resets all state, (f) verify restore cleans up checkpoints, (g) verify backup tree shows correct chain hierarchy.
- [x] 7.9 Review all @Mr.Tester reports and fix any source-level bugs discovered during testing
- [x] 7.10 Re-delegate any groups affected by source fixes
- [x] 7.11 Verify all groups pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 7.12 Verify integration tests pass: `poetry run pytest tests/integration/ -m integration`
- [x] 7.13 Verify coverage matches `test-plan.md` — every spec scenario has at least one test

## 8. Final Verification

- [x] 8.1 Run full test suite: `poetry run pytest tests/ -m ""`
- [x] 8.2 Run linter: `poetry run ruff check qsnap/ tests/`
- [x] 8.3 Run formatter: `poetry run ruff format --check qsnap/ tests/`
- [x] 8.4 Run type checker: `poetry run pyright qsnap/`
- [x] 8.5 Verify no `deploy` references remain in source code: `grep -r "deploy" qsnap/ --include="*.py"` (should only find unrelated uses)
- [x] 8.6 Verify no `--as-vm` references remain: `grep -r "as.vm\|as_vm" qsnap/ --include="*.py"`
- [x] 8.7 Verify no `target_dir` references in restore: `grep -r "target_dir" qsnap/core/__init__.py`
