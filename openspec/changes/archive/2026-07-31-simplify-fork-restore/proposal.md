## Why

The current `fork` and `restore` commands are overengineered for their actual use cases. `fork` performs XML manipulation and VM definition that should be left to the operator, while `restore` copies backing chains to a directory instead of replacing the VM's disk. Additionally, `allocation-map` change detection is a strict superset of `allocation-size` in sensitivity and should be the default. Finally, `list backups` lacks a `--tree` view to visualize backup chain structure, making it hard to select the right backup for fork/restore.

## What Changes

- **BREAKING**: `qsnap fork` simplified to standalone image creation only — removes XML manipulation, `virsh define`, `--as-vm`, `--storage`, `--add-to-config` flags. New signature: `qsnap fork <name> --output <path> [vm]`. Uses direct `qemu-img convert --force-share -O qcow2` for all sources (snapshots and targets). NBD pull-model removed from fork entirely.
- **BREAKING**: `qsnap restore` redesigned as VM disk replacement — replaces the VM's base image with a flattened standalone qcow2, strips `<backingStore>` from domain XML, cleans up all state (snapshots, baselines, deferred ops, FULL records, incremental deps, libvirt checkpoints). Requires VM to be stopped. New signature: `qsnap restore <name> [vm]`. Removes `target_dir` positional argument.
- **BREAKING**: `qsnap deploy` command removed entirely — was a thin wrapper around `fork()`, no longer needed.
- **BREAKING**: `change_detection_mode` default changed from `"allocation-size"` to `"allocation-map"` in `VMConfig`. The `allocation-map` mode is a strict superset of `allocation-size` in sensitivity (catches zero-fill, fstrim, region redistribution without total size change).
- `qsnap list backups` gains `--tree` flag — displays backup chains grouped by FULL anchor with indented hierarchy, analogous to `list snapshots --tree`.
- **BREAKING**: `IStateManager` gains `reset_vm_state(vm_name)` and `reset_target_state(target_path)` methods for atomic state cleanup during restore.
- `qsnap restore` gains `--dry-run` flag (show what would be done without executing) and `--yes` flag (skip confirmation prompt for destructive operation).
- `qsnap restore` performs pre-restore chain integrity verification via `scan_backing_chain()` — aborts if the source backing chain is broken.
- `qsnap restore` performs best-effort libvirt checkpoint cleanup (`virsh checkpoint-delete --metadata` for each `qsnap-*` checkpoint) — logs WARNING on failure, does not block restore.
- `qsnap fork` documents that forking the active layer of a running VM via `--force-share` may produce an inconsistent image — operators should stop the VM or fork a previous snapshot for consistency.

## Capabilities

### New Capabilities

_(none — all changes modify existing capabilities)_

### Modified Capabilities

- `fork-mode`: Simplify fork to standalone image creation only — remove XML manipulation, VM definition, NBD pull-model, deploy wrapper. Fork becomes a pure `qemu-img convert` operation.
- `restore-command`: Redesign restore as VM disk replacement — flatten source to standalone qcow2, replace VM base image, strip domain XML backingStore, reset all state, cleanup checkpoints. Requires VM stopped. Add `--dry-run` and `--yes` flags.
- `cli-interface`: Update fork/restore/deploy CLI signatures. Remove `deploy` command. Add `--tree` to `list backups`. Add `--dry-run`/`--yes` to `restore`.
- `change-detection`: Change default `change_detection_mode` from `"allocation-size"` to `"allocation-map"`.
- `list-commands`: Add `--tree` flag to `list backups` — group by FULL anchor, show indented chain hierarchy.
- `state-management`: Add `reset_vm_state(vm_name)` and `reset_target_state(target_path)` methods to `IStateManager` for atomic state cleanup during restore.

## Impact

- **`qsnap/core/__init__.py`**: `Core.fork()` rewritten (~150 lines removed, ~30 lines remain). `Core.restore()` rewritten (~100 lines removed, ~80 lines added). `Core.deploy()` removed. `Core.list_backups()` enhanced with chain grouping for `--tree`.
- **`qsnap/cli/app.py`**: Fork/restore/deploy argument parsers updated. Deploy parser removed. `list backups` gains `--tree` sub-argument.
- **`qsnap/cli/commands.py`**: `handle_fork()`, `handle_restore()`, `handle_deploy()` updated. `handle_list()` enhanced for backup tree. `_print_backup_tree()` added. `handle_deploy()` removed.
- **`qsnap/models/config.py`**: `VMConfig.change_detection_mode` default changed to `"allocation-map"`.
- **`qsnap/interfaces/state.py`**: `IStateManager` gains `reset_vm_state()` and `reset_target_state()` abstract methods.
- **`qsnap/state/json_manager.py`**: `JsonStateManager` implements `reset_vm_state()` and `reset_target_state()`.
- **`qsnap/mocks/mock_state.py`**: `InMemoryStateManager` implements new methods.
- **Tests**: `tests/core/test_fork.py` rewritten. `tests/e2e/test_restore.py` rewritten. `tests/cli/test_commands.py` updated. `tests/cli/test_tree.py` enhanced. New integration tests for restore state cleanup. Existing integration tests for fork/restore updated.
