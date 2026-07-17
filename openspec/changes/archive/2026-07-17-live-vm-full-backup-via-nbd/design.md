## Context

qsnap creates FULL backups via `FileCopyBackupProvider.create_full_backup()`, which runs `qemu-img convert` on the most-recent snapshot. For a running VM, the most-recent snapshot IS the active layer — the qcow2 file QEMU is actively writing to. QEMU holds an exclusive write lock on this file. When `qemu-img convert` tries to open it for reading, it fails with `Failed to get shared "write" lock`.

This is not a first-run-only problem. The bucket-driven FULL model (`_should_create_bucket_full()`) triggers FULL creation at every F-anchor boundary (e.g., weekly for `1Fw`, daily for `7Fd`). Each trigger selects `most_recent = max(snapshots, key=lambda s: s.timestamp)` — always the active layer for a running VM. The lock conflict recurs on every FULL creation.

The existing `BitmapBackupProvider` already solves this for incremental transfers using the libvirt pull-model NBD API (`virsh backup-begin` + `qemu-img convert -n nbd:unix:<socket>`). The NBD server runs inside the QEMU process, serving a frozen point-in-time view of the disk. No external process opens the qcow2 file directly, so there is no lock conflict. However, `BitmapBackupProvider.create_full_backup()` currently raises `NotImplementedError`, and the bucket-driven FULL path in Core calls it unconditionally — meaning bitmap-mode targets crash when a bucket boundary triggers.

Additionally, dry-run mode (`--dry-run` / `-n`) skips both environment validation (`_validate_environment()`) and FULL backup creation. This makes dry-run a misleading preview: it reports success even when the real run will fail due to permission issues, missing VMs, or lock conflicts.

Five other `qemu-img` call sites also lack `--force-share` on active-layer files, causing the same lock-conflict class of bug in: snapshot creation post-info, allocation-map change detection, integrity check listing, fork chain estimation, and fork convert.

## Goals / Non-Goals

**Goals:**

- G1: Enable FULL backup creation for running VMs without lock conflicts, using NBD as the transport when the VM is live.
- G2: Implement `BitmapBackupProvider.create_full_backup()` so bucket-driven FULLs work for bitmap-mode targets.
- G3: Fix all metadata-only `qemu-img` calls missing `--force-share` on active-layer files (5 confirmed bugs).
- G4: Make dry-run a truthful preview — run validation, report FULL-would-be-created, detect VM running state.
- G5: Document the NBD FULL mechanism, `--force-share` safety classification, and dry-run behavior in README.

**Non-Goals:**

- NG1: Adding `--force-share` to data-copying operations (`qemu-img convert`, `qemu-img compare`, `qemu-img commit`). These use NBD instead — `--force-share` on data-copying produces silently corrupted backups due to race conditions (missed writes, stale data, partial writes).
- NG2: Changing the `IBackupProvider` ABC interface. The `create_full_backup()` signature stays the same — the behavior change is internal.
- NG3: Adding new config fields. NBD is automatic — if the VM is running, NBD is used; if stopped, direct convert is used. No operator configuration needed.
- NG4: Checkpoint tracking for file-copy mode. NBD is used only for the frozen view, not for dirty-block tracking. No checkpoint is created or deleted in file-copy FULL backups.
- NG5: Changing the bucket-driven FULL trigger logic (`_should_create_bucket_full()`). The WHEN is unchanged — only the HOW changes.
- NG6: Fixing the `qemu-img commit` / `qemu-img check` calls in lifecycle managers (`BlockCommitManager`, `QemuImgCommitManager`). These are intentionally offline operations — the VM must be shut down. Adding `--force-share` would mask a dangerous state.

## Decisions

### D1: Hybrid NBD/direct-convert in `create_full_backup()`

**Decision:** `FileCopyBackupProvider.create_full_backup()` detects VM running state via `virsh dominfo`. If the VM is running, it uses the NBD pull-model (`virsh backup-begin` + `qemu-img convert -n nbd:unix:<socket>`). If the VM is stopped, it uses the existing direct `qemu-img convert` path.

**Rationale:** The NBD path avoids the lock conflict entirely — QEMU is the sole owner of the qcow2 file, and the NBD client reads through the NBD protocol, not through the filesystem. The direct convert path is preserved for stopped VMs because it is simpler, faster (no NBD setup overhead), and the file is not locked when QEMU is not running.

**Alternatives considered:**

- *Add `--force-share` to `qemu-img convert`*: Rejected. `--force-share` on data-copying operations is dangerous — it bypasses QEMU's locking and reads the file while QEMU is writing to it. Race conditions produce silently corrupted backups (structurally valid qcow2 but inconsistent data). Only discovered at restore time.
- *Always use NBD*: Rejected for stopped VMs. NBD requires a running QEMU process to serve the export. For stopped VMs, direct convert is the only option and is safe (no lock holder).
- *Use `virsh snapshot-create-as` to create a new snapshot, then convert the old (now-frozen) layer*: Rejected. This creates an extra snapshot in the chain, requires blockcommit cleanup, and still needs the old layer to be read-only. NBD is cleaner — it provides a frozen view without modifying the chain.

### D2: Shared NBD helper for FULL exports

**Decision:** Extract a shared `_nbd_full_export()` helper method (or module-level function) used by both `FileCopyBackupProvider.create_full_backup()` and `BitmapBackupProvider.create_full_backup()`. This helper: (1) creates the backup XML with a Unix socket path, (2) runs `virsh backup-begin` WITHOUT `--incremental` (full export), (3) runs `qemu-img convert -n nbd:unix:<socket> <target>`, (4) cleans up the socket in `finally`.

**Rationale:** Both providers need the same NBD full-export logic. Duplicating it violates DRY. The helper is a private method on each provider (or a shared utility in `modules/backup/`) — not a new ABC method, because the interface signature (`create_full_backup()`) is unchanged.

**Alternatives considered:**

- *Put the helper in Core*: Rejected. Core mediates between modules but should not contain backup-transfer logic. The NBD mechanism belongs in the backup provider.
- *Create a new `INbdExporter` ABC*: Rejected. Over-engineering for a single shared helper. The helper is an implementation detail, not a strategy.

### D3: No checkpoint for file-copy NBD FULLs

**Decision:** When `FileCopyBackupProvider` uses NBD for a FULL backup, it does NOT create or delete any libvirt checkpoint. The `virsh backup-begin` call is made without `--incremental` and without a checkpoint name. The NBD export is a one-shot frozen view — once `qemu-img convert` finishes, the socket is closed and the export ends.

**Rationale:** Checkpoints are for dirty-block tracking in incremental mode. File-copy mode uses rsync for incrementals (whole-file copy), not dirty-block extraction. Creating a checkpoint would pollute the checkpoint namespace (`qsnap-{hash}-*`) and interfere with `BitmapBackupProvider`'s checkpoint lifecycle if the user later switches modes. The NBD full-export API works without checkpoints — it simply exports the entire disk.

**Alternatives considered:**

- *Create a checkpoint for consistency*: Rejected. The frozen view from `backup-begin` without `--incremental` is already consistent — QEMU serves a point-in-time snapshot of the disk state. A checkpoint would add metadata overhead with no benefit for file-copy mode.

### D4: `BitmapBackupProvider.create_full_backup()` via NBD full export

**Decision:** `BitmapBackupProvider` overrides `create_full_backup()` to use the same NBD full-export path (no `--incremental`). This produces a standalone qcow2 on the target. No checkpoint is created for this FULL — the checkpoint lifecycle remains in `transfer_missing()` for incremental runs.

**Rationale:** The NBD full export (no `--incremental`) is already what `BitmapBackupProvider.transfer_missing()` does for the first backup to a target. Reusing it for bucket-driven FULLs is natural. The result is a standalone qcow2 — identical in structure to a `FileCopyBackupProvider` FULL. This means cascade deletion and retention work the same way for both providers.

**Alternatives considered:**

- *Skip bucket-driven FULLs for bitmap mode*: Rejected. The bucket model expects FULL anchors for cascade deletion and incremental dependency tracking. Skipping would leave bitmap targets without anchors, breaking the retention model.
- *Give `BitmapBackupProvider` an `IStateManager` dependency*: Partially. The factory already passes `IStateManager` to `FileCopyBackupProvider` but not to `BitmapBackupProvider`. For FULL tracking, Core records FULLs via `IStateManager.record_full_backup()` — this happens in `_backup_target()`, not in the provider. So the provider itself does not need `IStateManager`. No factory change needed.

### D5: `--force-share` on metadata-only calls only

**Decision:** Add `--force-share` to all metadata-only `qemu-img` calls that may target active-layer files: `qemu-img info`, `qemu-img info --backing-chain`, `qemu-img map`, `qemu-img check`. Do NOT add it to data-copying operations: `qemu-img convert`, `qemu-img compare`, `qemu-img commit`.

**Rationale:** Metadata-only operations read headers and L2 tables — minimal I/O, low risk of reading inconsistent data. `--force-share` is safe and is already used in 7 places. Data-copying operations read ALL clusters — race conditions during concurrent writes produce silently corrupted output. The NBD path replaces `--force-share` for data-copying on live VMs.

**Classification table:**

| Operation | `--force-share`? | Live VM approach |
|---|---|---|
| `qemu-img info` | YES (safe) | `--force-share` |
| `qemu-img info --backing-chain` | YES (safe) | `--force-share` |
| `qemu-img map` | YES (safe) | `--force-share` |
| `qemu-img check` | YES (safe) | `--force-share` |
| `qemu-img rebase -u` | YES (safe, unsafe mode) | `--force-share` (if source is active) |
| `qemu-img convert` | NO (dangerous) | NBD for live, direct for stopped |
| `qemu-img compare` | NO (dangerous) | Use `metadata` verification for live sources |
| `qemu-img commit` | NO (dangerous) | Offline only (VM must be stopped) |

### D6: Dry-run runs validation as non-fatal warnings

**Decision:** In dry-run mode, `_execute_pipeline()` now calls `_validate_environment()` but treats failures as warnings (logs them, does not raise `RuntimeError`). The `if not self._dry_run:` guard is replaced with `validation = self._validate_environment(vm_config)` always, followed by `if not self._dry_run and validation.status != "ok": raise ...`.

**Rationale:** Dry-run should be a truthful preview. Skipping validation entirely means operators miss permission issues, missing VMs, and missing directories until the real run fails. Running validation in dry-run as non-fatal warnings gives operators the information they need without aborting the preview.

**Alternatives considered:**

- *Make dry-run validation fatal*: Rejected. Dry-run should never abort — its purpose is to show what WOULD happen, including what would fail. Aborting on validation failure defeats the preview purpose.
- *Add a separate `--validate-only` flag*: Rejected. Over-engineering. Dry-run already exists and should just be enhanced.

### D7: Dry-run logs FULL-would-be-created and transfer method

**Decision:** In dry-run mode, `_backup_target()` logs whether a FULL backup WOULD be created (based on `_should_create_bucket_full()`) and which transfer method would be used (NBD if VM running, direct convert if stopped). It does NOT actually create the FULL or run NBD.

**Rationale:** Currently dry-run skips the FULL creation block entirely (`if not self._dry_run and snapshots:`). This means operators don't know whether a FULL will be created or whether it will succeed. Logging the intent + method gives operators a truthful preview without side effects.

### D8: VM running-state detection via `virsh dominfo`

**Decision:** VM running state is detected by calling `virsh dominfo --domain <vm_name>` and parsing the `State:` line. This is already done in `_validate_environment()`. For `create_full_backup()`, the provider calls `virsh dominfo` directly (it receives `vm_config.name` via the `SnapshotInfo` or a new parameter).

**Rationale:** `virsh dominfo` is the standard libvirt API for VM state. It returns `State: running` or `State: shut off`. This is already used elsewhere in the codebase. No new dependency.

**Alternatives considered:**

- *Pass VM running state from Core*: Rejected. The provider should be self-contained — it receives `vm_config` and `target` as method parameters. Adding a `vm_running: bool` parameter to `create_full_backup()` changes the ABC interface (breaking change). Instead, the provider calls `virsh dominfo` itself via `IShell`.
- *Use `virsh domstate`*: Rejected. `dominfo` is already used and returns more context. Consistency.

### D9: Fork uses the same hybrid NBD/direct approach

**Decision:** `Core.fork()` detects VM running state. If running, it uses NBD to export the disk for `qemu-img convert`. If stopped, it uses the existing direct `qemu-img convert -O qcow2` path.

**Rationale:** Fork has the same lock-conflict problem as FULL backup — it runs `qemu-img convert` on a snapshot that may be the active layer. The NBD path solves it identically. The fork result is a standalone qcow2 either way.

**Alternatives considered:**

- *Only fix FULL backup, leave fork broken*: Rejected. Fork from a running VM's active-layer snapshot is a valid use case (disaster recovery testing, staging). Leaving it broken is inconsistent.

## Risks / Trade-offs

### [Risk] NBD export fails on old libvirt

**Mitigation:** `BitmapBackupProvider` already checks `_MIN_LIBVIRT_MAJOR = 6` in its constructor and raises `RuntimeError` if too old. The factory catches this and falls back to `FileCopyBackupProvider`. For the NBD FULL path in `FileCopyBackupProvider`, the same version check is performed before attempting NBD. If libvirt is too old, the provider falls back to direct convert with `--force-share` (metadata-only safety) and logs a warning that the backup may fail due to lock conflict. The user is informed, not silently corrupted.

### [Risk] NBD FULL exports current disk state, not snapshot state

**Description:** NBD exports the disk state at the moment of `backup-begin`, which may be slightly newer than the last snapshot (writes between snapshot creation and FULL backup creation). The FULL is technically a point-in-time AFTER the snapshot, not AT the snapshot.

**Mitigation:** This is acceptable. A FULL backup is a complete copy — the incremental chain (rsync copies of older snapshots) covers the gap. The FULL serves as an anchor for cascade deletion and retention, not as an exact snapshot replica. The timestamp recorded for the FULL is the snapshot's timestamp (for retention bucket alignment), not the NBD export time.

### [Risk] `qemu-img compare` with `--force-share` on live sources

**Description:** The `verify_backup()` function's `full` verification tier runs `qemu-img compare` on the source (which may be the active layer). Adding `--force-share` to `qemu-img compare` is dangerous — it compares ALL data clusters, and race conditions produce false mismatches or false matches.

**Mitigation:** `--force-share` is NOT added to `qemu-img compare`. Instead, the `metadata` verification tier is recommended for live sources. The `full` tier should only be used when the source is a frozen snapshot (not the active layer) or a backup file. The README documents this recommendation. The code logs a warning if `verify="full"` is used on a running VM's active layer.

### [Risk] Dry-run validation may produce false positives

**Description:** Running `_validate_environment()` in dry-run may report broken checks that would not actually fail in the real run (e.g., a target directory that is auto-created by the pipeline, or a transient libvirt connection issue).

**Mitigation:** Dry-run validation results are logged as warnings, not errors. The real run still performs validation and aborts on failure. Operators are told to investigate warnings but the dry-run does not abort.

### [Risk] NBD socket left behind on crash

**Description:** If qsnap crashes between `virsh backup-begin` and socket cleanup, the Unix socket file remains in `/tmp/`.

**Mitigation:** The NBD helper removes any stale socket before starting (`rm -f /tmp/qsnap-backup-{pid}.sock`) and cleans up in a `finally` block. This is the same pattern already used by `BitmapBackupProvider.transfer_missing()`. The socket is PID-based, so concurrent qsnap runs do not collide.

### [Trade-off] NBD FULL is slower than direct convert for stopped VMs

**Description:** NBD adds setup overhead (XML creation, `backup-begin`, socket lifecycle). For stopped VMs, direct convert is faster.

**Mitigation:** The hybrid approach uses direct convert for stopped VMs. NBD is only used when the VM is running and the file is locked. There is no performance regression for stopped VMs.

### [Trade-off] Two code paths for FULL backup

**Description:** The NBD path and the direct-convert path are two code paths that must be maintained and tested.

**Mitigation:** The shared `_nbd_full_export()` helper minimizes duplication. The direct-convert path is unchanged (existing code). Tests cover both paths. The branch is a single `if vm_running:` check.
