## Context

The current `fork` and `restore` commands were designed with ambitious VM-management capabilities that proved unnecessary in practice:

- `Core.fork()` (`core/__init__.py:1065-1330`) performs 8 steps including `virsh dumpxml`, XML modification (new name, UUID, MAC removal), and `virsh define`. The NBD pull-model is used when the source VM is running. In practice, operators want a simple "flatten this snapshot/backup into a standalone qcow2" operation and manage VM creation themselves.
- `Core.restore()` (`core/__init__.py:949-1063`) copies the entire backing chain to a directory with relative `./` paths. It does not modify the VM. Operators want restore to replace the VM's disk and clean up all snapshot state.
- `Core.deploy()` (`core/__init__.py:1332-1351`) is a 1:1 wrapper around `fork()` — redundant once fork is simplified.
- `change_detection_mode` defaults to `"allocation-size"`, but `"allocation-map"` is a strict superset in sensitivity.
- `list backups` shows a flat list — operators cannot see which increments belong to which FULL chain.

## Goals / Non-Goals

**Goals:**

- Simplify `fork` to a pure `qemu-img convert` operation that produces a standalone qcow2 from any snapshot or backup
- Redesign `restore` as a destructive VM disk replacement operation with full state cleanup
- Remove `deploy` command (redundant wrapper)
- Make `allocation-map` the default change detection mode
- Add `--tree` flag to `list backups` for chain visualization
- Add `reset_vm_state()` and `reset_target_state()` to `IStateManager` for atomic cleanup
- Add safety flags (`--dry-run`, `--yes`) to `restore`
- Add pre-restore chain integrity verification
- Add best-effort checkpoint cleanup during restore

**Non-Goals:**

- Changing the NBD pull-model for FULL backup creation (unchanged)
- Changing snapshot creation, retention, or blockcommit logic (unchanged)
- Changing backup transfer or verification logic (unchanged)
- Adding VM creation/management to fork (explicitly removed — operator's responsibility)
- Supporting restore on running VMs (explicitly excluded — requires stopped VM)
- Changing the `IStateManager` JSON file format (new methods are additive)

## Decisions

### D1: Fork uses direct `qemu-img convert` for all sources (no NBD)

**Decision:** Fork always uses `qemu-img convert --force-share -O qcow2 <source> <output>` regardless of source type (snapshot or target) or VM running state.

**Rationale:** NBD pull-model is only necessary when the source is a live VM disk with an exclusive write lock that prevents direct file reads. Snapshots and target backups are files on disk — even the active layer can be read with `--force-share` (shared lock). The data may be inconsistent if the VM is actively writing, but this is an acceptable tradeoff for simplicity. Operators who need consistency can stop the VM or fork a previous (non-active) snapshot.

**Alternatives considered:**
- Keep NBD for active layer: Adds complexity (two code paths, socket management, `virsh backup-begin`/`domjobabort`) for marginal consistency benefit. Rejected — simplicity wins.
- Require VM stopped for fork: Too restrictive — non-active snapshots are safe to read directly. Rejected.

### D2: Restore writes to a temporary path, then atomically replaces base image

**Decision:** Restore creates the standalone image at `<snapshot_dir>/<vm>.restored.qcow2.tmp`, then:
1. Deletes old snapshot overlay files from `snapshot_dir`
2. `mv <temp> <vm_config.base_image>` (atomic replace)
3. Updates domain XML (strip `<backingStore>`, update `<source file>`)
4. `virsh define` the modified XML

**Rationale:** If `qemu-img convert` fails mid-transfer, the original base image and snapshot chain remain intact. The operator can retry without data loss. Only after successful conversion are old files deleted and the new image moved into place.

**Alternatives considered:**
- Overwrite base image in-place: If convert fails, the base image is corrupted. Rejected — unsafe.
- Write to a new path and update XML only: Leaves old snapshots orphaned on disk. Rejected — messy.

### D3: Restore requires VM to be stopped

**Decision:** Restore checks `is_vm_running()` at the start. If the VM is running, it returns `RestoreResult(success=False, error="VM must be stopped for restore")` immediately.

**Rationale:** Replacing a running VM's disk would cause data corruption, libvirt lock conflicts, and potential VM crashes. The VM must be stopped to safely replace the disk, strip XML, and clean up state.

**Alternatives considered:**
- Use NBD for live restore: Extremely complex, risky, and not a real use case. Rejected.

### D4: `reset_vm_state()` and `reset_target_state()` as new IStateManager methods

**Decision:** Add two new methods to `IStateManager`:

```python
def reset_vm_state(self, vm_name: str) -> None:
    """Atomically clear all per-VM state: snapshots, last_allocation, deferred_operations."""

def reset_target_state(self, target_path: str) -> None:
    """Atomically clear all per-target state: full_backups, incremental_dependencies, last_backup_allocation."""
```

**Rationale:** Restore needs to clear ~20 individual state entries (snapshots, allocation, deferred, FULLs, deps, baselines). Doing this with individual `remove_*` calls is error-prone and not atomic. Two bulk-reset methods make the operation clean and testable.

**Implementation in `JsonStateManager`:**
- `reset_vm_state()`: Loads `{vm_name}.json`, clears `snapshots`, `last_allocation`, `deferred_operations` keys, saves atomically.
- `reset_target_state()`: Loads `_full_backups.json`, removes the `target_path` entry. Loads `_dependencies.json`, removes the `target_path` entry. Loads `_target_state.json`, removes the `target_path` entry. All three saved atomically.

**Alternatives considered:**
- Individual `remove_*` calls: 20+ calls, non-atomic, error-prone. Rejected.
- Delete and recreate JSON files: Loses other VMs' state in `_full_backups.json` etc. Rejected.

### D5: Restore performs best-effort checkpoint cleanup

**Decision:** After state reset, restore iterates all libvirt checkpoints with `qsnap-` prefix and deletes them via `virsh checkpoint-delete --domain <vm> --metadata <checkpoint>`. Failures are logged at WARNING level and do not block restore.

**Rationale:** Checkpoints reference the old disk's dirty bitmap and are invalid after disk replacement. Leaving them would cause the next backup to attempt incremental transfer against a stale baseline. However, checkpoint deletion can fail (checkpoint already gone, libvirt error) — this should not block the restore operation. The next `qsnap run` will detect no valid FULL and force a new one regardless.

### D6: Pre-restore chain integrity verification

**Decision:** Before conversion, restore calls `scan_backing_chain()` (from `qsnap/utils/verification.py:404`) on the source path. If the chain is broken, restore aborts with `RestoreResult(success=False, error="Source backing chain is broken: <details>")`.

**Rationale:** `qemu-img convert` will fail mid-transfer if a backing file is missing. Pre-checking gives a clear error message and avoids partial conversions.

### D7: `list backups --tree` uses existing chain grouping

**Decision:** `Core.list_backups()` gains an optional `tree: bool = False` parameter. When `True`, it calls `_group_backups_by_chain()` (already implemented at `core/__init__.py:4470`) and `_resolve_chain_full_anchor()` (at `core/__init__.py:4412`) to group backups by FULL anchor. The CLI handler calls `_print_backup_tree()` (new function in `commands.py`) to display the indented hierarchy.

**Rationale:** The chain grouping logic already exists for retention evaluation. Reusing it for display is zero-cost and ensures consistency between what retention sees and what the operator sees.

### D8: `allocation-map` as default — backward compatibility

**Decision:** Change `VMConfig.change_detection_mode` default from `"allocation-size"` to `"allocation-map"`. The factory logic (`DefaultFactory.create_change_detector()`) is unchanged — explicit `"allocation-size"` in config still works.

**Rationale:** `allocation-map` catches all changes that `allocation-size` catches, plus zero-fill, fstrim, and region redistribution without total size change. The cost is `qemu-img map` being heavier than `qemu-img info`, but for a backup system, sensitivity is more important than speed.

**Migration:** Existing configs without explicit `change_detection_mode` will switch from `allocation-size` to `allocation-map`. This is a behavior change but a strictly safer one (more sensitive). No data migration needed.

## Risks / Trade-offs

- **[Risk] Fork on active layer of running VM may produce inconsistent image** → `--force-share` allows reading while VM writes. **Mitigation**: Document in `--help` and man page that forking the active layer of a running VM may produce an inconsistent image. Recommend stopping the VM or forking a previous snapshot for consistency.

- **[Risk] Restore is destructive — replaces VM disk, deletes snapshots, resets state** → Operator could lose data if they restore from the wrong source. **Mitigation**: `--dry-run` flag shows what would be done. `--yes` flag required to skip confirmation prompt (default behavior prompts for confirmation).

- **[Risk] Restore from target with broken backing chain** → `qemu-img convert` would fail mid-transfer. **Mitigation**: Pre-restore `scan_backing_chain()` check aborts before any modification.

- **[Risk] Checkpoint cleanup fails** → `virsh checkpoint-delete` may fail for stale/invalid checkpoints. **Mitigation**: Best-effort deletion, WARNING log, does not block restore. Next `qsnap run` forces new FULL regardless.

- **[Risk] `reset_vm_state()` clears deferred operations** → If there were deferred blockcommits queued, they are lost. **Mitigation**: This is correct behavior — after disk replacement, old deferred operations are meaningless (the old backing chain is destroyed).

- **[Trade-off] `allocation-map` default is slower** → `qemu-img map` reads the full cluster allocation table, heavier than `qemu-img info`. **Accepted**: Sensitivity > speed for backup systems. Operators can opt back to `allocation-size` explicitly.

- **[Trade-off] `deploy` command removal** → Scripts using `qsnap deploy` will break. **Accepted**: `deploy` was a 1:1 wrapper around `fork` — operators can use `fork` directly. Document in migration notes.
