## Context

qsnap currently uses `FileCopyBackupProvider` for all backups. This copies entire qcow2 snapshot files via `cp` and adjusts backing paths with `qemu-img rebase -u`. For large disks (100+ GB) with small daily churn this wastes time, I/O bandwidth, and target disk space. The qcow2 format natively supports dirty tracking bitmaps — a mechanism to record which blocks have changed since a named checkpoint — and `qemu-img convert` can extract only those blocks.

Additionally, the codebase has several correctness issues from the v0.1-beta: the active disk name is hardcoded (`"vda"`), `qemu-img rebase` errors are silently swallowed in `FileCopyBackupProvider.transfer_missing()`, parser functions are duplicated across modules, `EXIT_BACKUP_ABORT` is defined but unused, and `--print-schedule` skips backup retention evaluation.

The project follows a strict Dependency Injection paradigm with ABC interfaces (see `AGENTS.md`). Every domain module implements exactly one ABC, receives config as immutable method parameters, and returns `*Result` dataclasses. Core is the only coordinator. Tests mirror production with mocks for every ABC.

## Goals / Non-Goals

**Goals:**
- Fix hardcoded `disk = "vda"` → dynamic disk resolution via `virsh domblklist`, iterating all disks
- Surface `qemu-img rebase` failures properly in `FileCopyBackupProvider`
- Eliminate duplicated parser code (`_parse_domblklist_path`, `_parse_timestamp`) into shared utils
- Wire `EXIT_BACKUP_ABORT` into `Core` → `PipelineResult`
- Extend `--print-schedule` to show backup retention decisions
- Fill missing `daily_set.json` and `mixed_set.json` test timestamp fixtures
- Implement `BitmapBackupProvider` as a new `IBackupProvider` strategy using `virsh checkpoint-create-as` + `qemu-img convert --bitmap`
- Add `incremental_mode` to `TargetConfig` (`"file-copy"` default, `"bitmap"` new)
- Implement `qsnap restore` command (file-level: copy + rebase + define)
- Implement `qsnap check --deep` (add `qemu-img check` per snapshot)
- Implement `snapshot_create = "ondemand"` (skip snapshot when no target reachable)

**Non-Goals:**
- `restore` does NOT boot the VM (that requires VM lifecycle management — P3 scope)
- `restore` does NOT handle incremental restore from bitmap-based backups (requires checkpoint replay — P3+)
- No SSH remote backup support (P5 scope)
- No quiesce/guest-agent integration (P5 scope)
- No compression or encryption of backups (P5 scope)

## Decisions

### D1: `BitmapBackupProvider` as separate `IBackupProvider` implementation

**Decision:** Implement `BitmapBackupProvider` in `modules/backup/bitmap.py`, implementing `IBackupProvider`. Select via `DefaultFactory` when `target.incremental_mode == "bitmap"`.

**Rationale:** Follows the existing factory pattern. Adding a new backup strategy means (a) implement `IBackupProvider`, (b) add a branch in `DefaultFactory`. Nothing else changes. `FileCopyBackupProvider` remains as the default when `incremental_mode` is `"file-copy"` or absent.

**Alternatives considered:**
- Extend `FileCopyBackupProvider` with bitmap logic → rejected: would violate separation of concerns; the two approaches use fundamentally different commands (`cp` vs `qemu-img convert`) and failure modes.
- Make `incremental_mode` a global setting → rejected: different targets may want different strategies (local target uses `file-copy`, remote target uses `bitmap` for bandwidth efficiency).

### D2: Multi-disk support design

**Decision:** When `VMConfig.disks` is absent, `Core._create_snapshot()` auto-discovers all disks via `virsh domblklist --domain <name>`. When present (opt-in explicit list), uses the provided disk names. Iterate over each disk, creating one snapshot per disk with the same timestamp name but distinct paths.

**Rationale:** Most VMs have one disk and should work without explicit configuration. Multi-disk VMs can optionally list disks in config for predictability. The `--diskspec` syntax of `virsh snapshot-create-as` already supports a per-disk specification.

**Disk naming convention:** Snapshot files for disk `vdb` are named `{vm_name}.{timestamp}_vdb.qcow2` (distinct from `vda` snapshots). Path: `{snapshot_dir}/{vm_name}.{timestamp}_{disk}.qcow2`.

**IChangeDetector per-disk:** `has_changed()` now accepts optional `disk: str`. If any disk has changed, snapshot is created for all disks (atomic decision — all or none).

### D3: Bitmap checkpoint lifecycle

**Decision:** `BitmapBackupProvider` manages its own checkpoint namespace: prefixes all checkpoint names with `qsnap-`. It creates a checkpoint before each transfer, compares the current bitmap data against the previous backup's checkpoint, extracts only dirty blocks via `qemu-img convert --bitmap <name>`, and deletes the checkpoint after successful transfer.

**Dirty bitmap extraction pipeline:**
```
1. qemu-img convert -f qcow2 -O qcow2 \
     --bitmap "qsnap-{target_hash}-{prev_snapshot}" \
     /path/to/active.qcow2 /mnt/backup/snap.qcow2
2. virsh checkpoint-delete --domain VM \
     "qsnap-{target_hash}-{prev_snapshot}" --metadata
3. virsh checkpoint-create-as --domain VM \
     --name "qsnap-{target_hash}-{current_snapshot}"
```

The pipeline uses the **prior** backup's checkpoint (step 1) to extract only blocks changed since that checkpoint, then deletes it (step 2) and creates a new checkpoint (step 3) for the next incremental run. This is the standard correct approach — a freshly-created checkpoint has zero dirty blocks, so the bitmap must come from the previous cycle.

**First backup (no prior checkpoint):** Full copy via `qemu-img convert` (no `--bitmap` flag), then create checkpoint for subsequent incremental runs.

**Checkpoint cleanup:** Checkpoints are deleted after successful transfer. If transfer fails, checkpoints are preserved for retry. A `qsnap clean` operation (future) can purge orphaned checkpoints.

**Target path:** On the target, bitmap-derived backups are standalone qcow2 files (no backing chain). Each file contains the full virtual disk content, but was created efficiently from only dirty blocks. This avoids the complexity of maintaining backing chains on the target.

### D4: `qsnap restore` design

**Decision:** `restore` is a Core-level command (not a provider method) that calls `IShell` directly for `cp`, `qemu-img rebase`, and optionally `virsh define`. Output: a directory containing a restorable VM image with rebuilt backing chain.

**Signature:** `Core.restore(snapshot_name: str, target_dir: Path, vm_filter: str | None) -> RestoreResult`

**Flow:**
1. Identify the snapshot/backup by name across all VMs
2. Copy the snapshot file and its entire backing chain to `target_dir`
3. Execute `qemu-img rebase -u` on each copied file to use relative `./basename` paths
4. Optionally generate or accept a VM XML definition
5. Return `RestoreResult` with the final active image path and XML path

**Non-goal for this change:** Automatic VM boot after restore. The user is responsible for `virsh define` + `virsh start`.

### D5: `check --deep` via `qemu-img check`

**Decision:** When `--deep` is passed, `Core.check()` calls `qemu-img check --output=json` on each snapshot and backup file. Parse the JSON to extract `"corruptions"` count. Report any file with `corruptions > 0` as broken.

**Rationale:** `qemu-img check` reads all metadata and can detect leaked clusters, refcount errors, and corrupt L1/L2 tables. This is significantly more thorough than the current `check` which only verifies file existence.

**Performance note:** `qemu-img check` is I/O-intensive for large images. It should not be run via the default timer; it's a manual diagnostic command.

## Risks / Trade-offs

- **[Risk] Bitmap backup produces standalone files, not backing chains** → Restoring from a bitmap backup gives you a point-in-time full image, not a chain of incremental diffs. This is simpler but means you lose the ability to restore intermediate points from a single backup chain. **Mitigation:** This is an explicit trade-off for simplicity. Users who need chain-based restores should use `incremental_mode = "file-copy"`.

- **[Risk] Checkpoint namespace collision** → Multiple qsnap instances or manual `virsh checkpoint-create` could collide on the `qsnap-` prefix. **Mitigation:** Include a target-path hash in the checkpoint name to distinguish per-target checkpoints.

- **[Risk] `qemu-img convert --bitmap` requires QEMU >= 5.1** → On older systems the bitmap path is unavailable. **Mitigation:** `BitmapBackupProvider` constructor checks QEMU version via `qemu-img --version` and returns a clear error if unsupported. `DefaultFactory` falls back to `FileCopyBackupProvider` with a warning.

- **[Risk] Multi-disk snapshot atomicity** → If snapshot creation succeeds for `vda` but fails for `vdb`, we have a partial snapshot state. **Mitigation:** This is an inherent limitation of external snapshots — libvirt does not support cross-disk atomic snapshotting. qsnap logs a warning and returns partial success. Retention and lifecycle for the failed disk are skipped.

- **[Risk] Orphaned checkpoints from interrupted bitmap backups** → If qsnap crashes between `checkpoint-create` and `checkpoint-delete`, the checkpoint remains in the qcow2 file consuming metadata space. **Mitigation:** A future `qsnap clean --checkpoints` command should purge qsnap-owned checkpoints. Until then, the risk is low (checkpoints are metadata-only, negligible space).

- **[Trade-off] Extracting `_parse_domblklist_path` to shared utils** → This changes the import graph of 3 modules. **Mitigation:** The change is mechanical. All three modules already import from `qsnap.models`; adding `qsnap.utils.parsing` is a 0-risk refactor.
