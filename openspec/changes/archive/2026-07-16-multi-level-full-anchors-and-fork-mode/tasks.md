## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b multi-level-full-anchors-and-fork-mode`
- [x] 1.2 Run the full test suite to establish a passing baseline: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Config Model: RetentionPolicy anchor fields

- [x] 2.1 Add five boolean fields to `RetentionPolicy` frozen dataclass in `qsnap/models/config.py`: `anchor_hourly: bool = False`, `anchor_daily: bool = False`, `anchor_weekly: bool = False`, `anchor_monthly: bool = False`, `anchor_yearly: bool = False`
- [x] 2.2 Verify immutability: ensure existing `FrozenInstanceError` tests still pass with the new fields

## 3. Config Parsing: F-syntax in _parse_preserve

- [x] 3.1 Extend `Core._parse_preserve()` regex in `qsnap/core/__init__.py` from `(\d+)([hdwmy])` to `(\d+)(F?)([hdwmy])`
- [x] 3.2 When group 2 is `"F"`, set the corresponding `anchor_*` field on the returned `RetentionPolicy`
- [x] 3.3 Handle invalid bucket characters: tokens like `"7Fx"` that don't match `[hdwmy]` are silently ignored (existing skip behavior)
- [x] 3.4 Update `_parse_preserve` signature: accept optional `preserve_min_str` parameter (already exists, verify it works with F-syntax)

## 4. Config Validation: F-anchor rules in ConfigFacade

- [x] 4.1 Add validation in `ConfigFacade._build_target()` (`qsnap/config/facade.py`): if any token has `F` prefix AND `count == 0`, raise `ConfigError("F-anchor on bucket '<bucket>' requires count > 0")`
- [x] 4.2 Update preserve_min-without-buckets validation: now permits buckets if F-anchors are present regardless of count. Only reject if ALL bucket counts are 0 AND NO F-anchors are present
- [x] 4.3 Verify `F` in `snapshot_preserve` parses without error (anchors are stored but have no effect on snapshot logic)

## 5. Core: _should_create_bucket_full multi-level logic

- [x] 5.1 Change `_should_create_bucket_full()` signature in `qsnap/core/__init__.py` from `(target, policy, last_full, snapshot_ts)` to `(target, policy, all_fulls: list, snapshot_ts)` where `all_fulls` is `list[FullBackupInfo]`
- [x] 5.2 Implement `_active_buckets(policy)` helper that returns buckets where `policy.{bucket} > 0` in descending order (yearly, monthly, weekly, daily, hourly)
- [x] 5.3 Implement `_f_anchor_buckets(policy)` helper that returns buckets where `policy.anchor_{bucket} == True` in descending order
- [x] 5.4 Implement anchor selection: if any `anchor_*` is `True`, use F-anchor buckets; otherwise use all active buckets
- [x] 5.5 For each checked bucket, find most recent FULL from `all_fulls` with matching `bucket_level`. Compare period keys. Short-circuit on first match — at most ONE FULL per snapshot
- [x] 5.6 Return `(True, bucket_level)` on period change or no prior FULL for that bucket; `(False, "")` if no bucket triggers
- [x] 5.7 Period key computation: reuse existing `_period_key(ts, bucket)` logic. No changes needed

## 6. Core: _backup_target integration

- [x] 6.1 In `Core._backup_target()`, replace `state.get_last_full_backup(target.path)` with `state.get_full_backups(target.path)`
- [x] 6.2 Pass the full list to `_should_create_bucket_full(target, policy, all_fulls, most_recent_snapshot_ts)`
- [x] 6.3 Ensure existing rebase logic (rebase to most recent FULL by timestamp) is unchanged — it already works with multiple FULLs
- [x] 6.4 Verify first-backup-to-target path: when `all_fulls` is empty, `_should_create_bucket_full` returns `(True, bucket_level)` for the first active/F-marked bucket

## 7. Core: Snapshot resolution primitive for fork

- [x] 7.1 Extract `_resolve_snapshot(snapshot_name: str, vm_filter: str | None = None) -> tuple[SnapshotInfo, VMConfig]` as a reusable private method on Core
- [x] 7.2 Search `IStateManager` across all configured VMs (filtered by `vm_filter`). Search name matches by snapshot filename basename
- [x] 7.3 If not found in state, search all backup providers via `provider.list(target)` for each VM's targets
- [x] 7.4 Raise `FileNotFoundError` with message `"Snapshot not found: {name}"` if not found in either source
- [x] 7.5 Refactor `Core.restore()` to use `_resolve_snapshot()` internally instead of duplicate resolution logic

## 8. Core: fork method

- [x] 8.1 Implement `Core.fork(snapshot_name: str, new_vm_name: str, storage_dir: Path, add_to_config: bool = False, vm_filter: str | None = None) -> RestoreResult`
- [x] 8.2 Call `_resolve_snapshot(snapshot_name, vm_filter)` to locate the snapshot and source VM
- [x] 8.3 Resolve backing chain via `qemu-img info --backing-chain --output=json <snapshot.path>` to estimate total chain size
- [x] 8.4 Log estimated chain size at INFO level: `"Converting snapshot {name} (chain size: ~{size}) to standalone qcow2..."`
- [x] 8.5 Create target directory `storage_dir / new_vm_name`
- [x] 8.6 Execute `qemu-img convert -O qcow2 <snapshot.path> <storage_dir>/<new_vm_name>/<new_vm_name>.qcow2` via `IShell` (timeout: 7200s for large disks)
- [x] 8.7 Obtain source VM XML: `virsh dumpxml --domain <source_vm_name>` via `IShell`
- [x] 8.8 Modify XML: replace `<name>` with `new_vm_name`, generate new `<uuid>` via `uuid.uuid4()`, replace `<source file="...">` paths to point to the new standalone qcow2, remove `<mac address="...">` to avoid MAC conflicts
- [x] 8.9 Write modified XML to `storage_dir / new_vm_name / new_vm_name.xml`
- [x] 8.10 Execute `virsh define <xml-path>` via `IShell`
- [x] 8.11 If `add_to_config=True`: append a minimal `[[vm]]` block to the config file with `name`, `base_image`, `snapshot_dir`, `snapshot_create = "always"`
- [x] 8.12 Return `RestoreResult(success=True, snapshot_name=..., restored_path=..., chain_files=[restored_path])`

## 9. Core: deploy method

- [x] 9.1 Implement `Core.deploy(backup_name: str, new_vm_name: str, storage_dir: Path, add_to_config: bool = False, vm_filter: str | None = None) -> RestoreResult`
- [x] 9.2 Delegate to `self.fork(backup_name, new_vm_name, storage_dir, add_to_config, vm_filter)` — fork already handles resolution from both snapshots and backups
- [x] 9.3 Return the `RestoreResult` from `fork()`

## 10. CLI: fork and deploy subcommands

- [x] 10.1 Add `fork` subcommand to `qsnap` CLI in `qsnap/cli/app.py`: positional `SNAPSHOT_NAME`, flags `--as-vm` (required, str), `--storage` (default: `/var/lib/libvirt/images`), `--add-to-config` (flag), optional VM filter positional arg
- [x] 10.2 Implement `handle_fork()` in `qsnap/cli/commands.py`: calls `core.fork(snapshot_name, as_vm, storage_dir, add_to_config, vm_filter)` and formats `RestoreResult` output
- [x] 10.3 Add `deploy` subcommand: positional `BACKUP_NAME`, flags `--as-vm` (required), `--storage`, `--add-to-config`
- [x] 10.4 Implement `handle_deploy()`: calls `core.deploy(backup_name, as_vm, storage_dir, add_to_config)` and formats output
- [x] 10.5 Add `fork` and `deploy` to CLI help text
- [x] 10.6 Wire exit codes: 0 on success, 1 on failure

## 11. Testing

**CRITICAL — TEST ORCHESTRATION PROTOCOL (followed by the @Mr.Programmer agent during the apply phase):**

The @Mr.Programmer agent responsible for implementation MUST follow this protocol when delegating tests:

1. Read `test-plan.md` → Delegation Groups section
2. For EACH group listed, launch one @Mr.Tester subagent with:
   - The group's scope (file paths from the group's Scope line)
   - The group's scenario list from the Coverage Map table in `test-plan.md`
   - Instruction: "Write or fix ONLY these specific tests. Report source bugs, don't fix them."
   - **MUST pass the `TESTING.md` document** (`/home/openuser/vm/qsnap/TESTING.md`) to EACH @Mr.Tester subagent — it describes the testing paradigm (factory injection, result objects, isolated dependencies, mock conventions)
3. Launch ALL groups IN PARALLEL (single message)
4. After all testers return: fix any reported source bugs, re-delegate affected groups
5. Repeat until all groups pass

- [x] 11.1 Read `test-plan.md` Delegation Groups section
- [x] 11.2 Delegate group `full-anchor-unit` to @Mr.Tester (scope: `tests/core/test_full_anchor.py`, **NEW** — 14 scenarios). Pass `TESTING.md` to the tester.
- [x] 11.3 Delegate group `f-syntax-unit` to @Mr.Tester (scope: `tests/core/test_preserve.py`, `tests/config/test_facade.py`, **MODIFY** — 8 scenarios). Pass `TESTING.md` to the tester.
- [x] 11.4 Delegate group `config-model-unit` to @Mr.Tester (scope: `tests/config/test_model.py`, **MODIFY** — 4 scenarios). Pass `TESTING.md` to the tester.
- [x] 11.5 Delegate group `fork-core-unit` to @Mr.Tester (scope: `tests/core/test_fork.py`, **NEW** — 15 scenarios). Pass `TESTING.md` to the tester.
- [x] 11.6 Delegate group `fork-cli-unit` to @Mr.Tester (scope: `tests/cli/test_commands.py`, **MODIFY** — 5 scenarios). Pass `TESTING.md` to the tester.
- [x] 11.7 Delegate group `core-bucket-unit` to @Mr.Tester (scope: `tests/core/test_pipeline.py`, `tests/core/test_engine.py`, **MODIFY** — 7 scenarios). Pass `TESTING.md` to the tester.
- [x] 11.8 Delegate group `contract-unit` to @Mr.Tester (scope: `tests/interfaces/test_backup_provider.py`, **MODIFY** — verify parametrization). Pass `TESTING.md` to the tester.
- [x] 11.9 Review all @Mr.Tester reports and fix any source-level bugs discovered
- [x] 11.10 Re-delegate any groups affected by source fixes
- [x] 11.11 Verify all groups pass and coverage matches `test-plan.md`
- [x] 11.12 Run full test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`

## 12. Documentation

- [x] 12.1 Update `qsnap.toml.example` to document `F` syntax in `target_preserve` and `snapshot_preserve` comments
- [x] 12.2 Add `fork` and `deploy` commands to CLI help examples
