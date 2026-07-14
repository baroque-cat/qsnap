## Context

qsnap v0.1 has a complete functional core: external disk-only snapshots, file-copy and bitmap backups, retention, blockcommit, restore, and check --deep. The `consolidate-and-bitmap-backup` change (archived) closed all P0-P2 gaps. However, five production-critical concerns remain unaddressed:

1. **Zero pre-flight validation**: `Core._execute_pipeline()` launches virsh/qemu-img commands without checking prerequisites — a missing `snapshot_dir` is only discovered when the first snapshot fails, after retention evaluation and change detection have already run.
2. **MAC (Mandatory Access Control) blind spot**: `virsh blockcommit` fails with `Permission denied` under AppArmor (Ubuntu/Debian) and `Operation not permitted` under SELinux (RHEL/Fedora) when the VM is running. qsnap logs the error and moves on — snapshots accumulate, retention breaks, disk fills.
3. **No backup integrity assurance**: `FileCopyBackupProvider` copies files with `cp`, `BitmapBackupProvider` uses `qemu-img convert --bitmap`. Neither verifies the target after transfer. A silent I/O error on the backup disk produces an unrecoverable corruption discovered only during restore.
4. **Fragile bitmap backup**: The current `BitmapBackupProvider` (`qemu-img convert --bitmap`) accesses the qcow2 image while QEMU is writing — this causes crashes on some QEMU versions. The correct API is `virsh backup-begin` with pull-model NBD.
5. **Missing btrbk parity features**: `snapshot_preserve_min = "latest"`, tree listing, `--quiesce`.

## Goals / Non-Goals

**Goals:**
- Fail fast: validate environment before pipeline execution
- Never crash on MAC denials: deferred operations with automatic retry on VM shutdown
- Trust but verify: automatic post-transfer backup verification, configurable per-target
- Replace bitmap backup with native NBD pull-model via `virsh backup-begin`
- Parity with btrbk: `--long`, `--tree`, `"latest"` retention, `--quiesce`

**Non-Goals:**
- Active AppArmor/SELinux policy modification (no `aa-disable`, no `setenforce 0`)
- Asynchronous block-job monitoring (no `virsh event` or polling loops)
- Full-disk `qemu-img compare` as default verification (too slow; opt-in via `verify = "full"`)
- SSH target support (separate change)
- `qsnap archive` command (separate change)
- Compression/encryption of backups (separate change)

## Decisions

### D1 — Pre-flight validation before pipeline, not in individual modules

**Choice**: `Core._validate_environment(vm_config) -> CheckResult` runs before `_execute_pipeline()` and checks: (a) `snapshot_dir` exists and is writable, (b) each `target.path` exists (or is gracefully skipped for ondemand), (c) `base_image` exists, (d) `virsh` and `qemu-img` are in PATH, (e) VM is defined in libvirt (`virsh dominfo --domain` returns 0). Failure returns immediately — no partial pipeline execution.

**Rationale**: Individual modules already return `*Result(success=False)` on failures, but by then Core has done useless work (change detection, retention evaluation). Centralized validation avoids wasted work and gives a clear "your environment is broken" message.

**Alternatives**: Per-module validation (more granular but Core still wastes work on earlier steps). Chose centralized because validation failures are "stop everything" events — there is no recovery path if `snapshot_dir` doesn't exist.

### D2 — Deferred operations as first-class IStateManager concept

**Choice**: `IStateManager` gains `get_deferred_operations(vm_name) -> list[DeferredBlockcommit]`, `add_deferred_blockcommit(vm_name, snapshots, reason)`, `clear_deferred_operations(vm_name)`. `DeferredBlockcommit` is a frozen dataclass: `snapshots: list[str]`, `reason: str` (`"apparmor"` | `"selinux"`), `since: datetime`. `Core._execute_snapshot_steps()` checks deferred queue **before** creating new snapshots: if VM is shut off → execute pending blockcommits → clear queue. If VM is running → skip, log INFO.

**Rationale**: This is the only approach that (a) doesn't require security policy changes (safe for enterprise), (b) automatically recovers when the VM next shuts down (zero admin intervention), and (c) keeps the architecture clean — deferred state persists across qsnap runs in JSON.

**Alternatives**: (1) `aa-disable` / `setenforce 0` — rejected: violates host security policy. (2) Skip blockcommit silently — rejected: snapshots accumulate, retention breaks. (3) Fail the pipeline — rejected: too aggressive, a transient MAC denial shouldn't block everything.

### D3 — Three-tier backup verification, defaulting to metadata

**Choice**: `TargetConfig.verify: str = "metadata"` with values `"off"` | `"metadata"` | `"full"`. `"metadata"` runs `qemu-img info --output=json` on target after transfer and asserts: format is qcow2, virtual-size matches source, actual-size is within tolerance. `"full"` runs `qemu-img compare -q source target`. `"off"` skips verification. Verification runs inside `FileCopyBackupProvider.transfer_missing()` and `BitmapBackupProvider.transfer_missing()` — not as a separate pipeline step — so each individual transfer result carries its own verification status.

**Rationale**: `"metadata"` catches 80%+ of corruption (broken header, truncated file, wrong backing chain) in milliseconds. `"full"` catches 100% but takes minutes-to-hours on large disks. Making it per-target configurable lets admins choose: `"full"` for slow/reliable backup disks, `"metadata"` for fast local SSD targets.

**Alternatives**: (1) Always verify — rejected: too slow for TB-scale disks. (2) Never verify — rejected: `cp` has no integrity guarantees. (3) Separate verification step in Core — rejected: verification should be atomic with transfer; if verification fails, the BackupResult should reflect that immediately.

### D4 — NBD backup via `virsh backup-begin` with temporary scratch file

**Choice**: `BitmapBackupProvider` v2 (replaces current implementation) uses the libvirt pull-model backup API:
1. `virsh checkpoint-create-as --domain VM --name "qsnap-N"` — create checkpoint with dirty bitmap
2. Generate backup XML with `<disk backup='yes' type='file'>` pointing to NBD Unix socket
3. `virsh backup-begin --domain VM backup.xml` — starts NBD export, returns immediately
4. `qemu-img convert -n nbd:unix:/tmp/qsnap-nbd.sock /mnt/backup/snap.qcow2` — pull dirty blocks
5. `rm /tmp/qsnap-nbd.sock` — cleanup
6. Checkpoint persists for next incremental run.

**Rationale**: `virsh backup-begin` is the official libvirt API for pull-model backups. QEMU exports dirty blocks over NBD while the VM keeps running — no qcow2 file locking conflict, no VM crashes. Checkpoints with `auto` bitmaps track subsequent writes automatically. This is how Proxmox and oVirt do VM backups.

**Alternatives**: (1) Current `qemu-img convert --bitmap` approach — rejected: accesses qcow2 while QEMU writes, causes crashes. (2) `virsh blockcopy` — rejected: designed for disk migration, not incremental backup. (3) External `qemu-nbd` with `--bitmap` — rejected: requires manual NBD server management.

### D5 — `--quiesce` as opt-in flag on snapshot creation

**Choice**: `VMConfig.snapshot_quiesce: bool = False` (default). When `True`, `ExternalSnapshotProvider.create()` passes `--quiesce` to `virsh snapshot-create-as`. If guest agent is not installed or not responding, snapshot creation fails with a clear error — no silent fallback (application consistency is a hard requirement when requested).

**Rationale**: Without `--quiesce`, disk-only snapshots are crash-consistent (like pulling the power cord). For databases and stateful applications, this risks recovery on revert. But `--quiesce` requires `qemu-guest-agent` inside the VM — making it opt-in respects that not all VMs have the agent.

**Alternatives**: (1) Always quiesce — rejected: breaks VMs without guest agent. (2) Auto-detect guest agent — rejected: introduces uncertainty (agent may be installed but unresponsive).

### D6 — `qemu-img commit` as alternative lifecycle manager

**Choice**: New `QemuImgCommitManager` implementing `ILifecycleManager`. Uses `qemu-img commit -b <base> -d <top>` instead of `virsh blockcommit`. Selected by factory when `vm_config.lifecycle_mode == "qemu-img"` (default remains `"virsh"`). Works offline (no libvirt connection needed for commit step). AppArmor/SELinux: qemu-img operates on files, not libvirt — may or may not be blocked depending on policy.

**Rationale**: Some deployments (containers, minimal libvirt, strict MAC policies) can't use `virsh blockcommit` at all. `qemu-img commit` is strictly file-level, no libvirt dependency. Complementary to D2 (deferred operations): if virsh blockcommit is deferred, Core can try qemu-img commit as fallback.

**Alternatives**: (1) Only support virsh blockcommit — rejected: reduces deployment flexibility. (2) Replace blockcommit entirely — rejected: virsh blockcommit is the standard production path, qemu-img commit is a fallback.

### D7 — `qemu-img map` change detection as alternative strategy

**Choice**: New `MapChangeDetector` implementing `IChangeDetector`. Instead of comparing a single `actual-size` integer, it runs `qemu-img map --output=json` on the active layer and compares the set of allocated offset/length pairs against the last recorded state. Factory selects via `mode = "allocation-map"` (default remains `"allocation-size"`).

**Rationale**: `actual-size` is a coarse metric — zero-fill and trim operations change the allocation map without changing total size, causing false negatives (missed changes). `qemu-img map` provides per-region allocation data, catching these cases. Trade-off: map output can be large (thousands of JSON entries for fragmented disks).

**Alternatives**: (1) `virsh domstats --block` wr.bytes — rejected: requires VM running, doesn't work on shut-off VMs. (2) Always map — rejected: too heavy for simple cases.

### D8 — `preserve_min = "latest"` in retention engine

**Choice**: `_parse_duration()` in `TimeBasedRetention` gains special case handling: string `"latest"` → `timedelta(seconds=0)`. The retention engine then keeps only the single most recent item (the `preserve_min` window of 0 means "keep nothing by age, but the `preserve` policy always keeps the latest snapshot/backup pair"). Applies to both `snapshot_preserve_min` and `target_preserve_min`.

**Rationale**: btrbk supports `preserve_min = latest` for "keep only the most recent backup." This is essential for tight-disk scenarios where you only need the current state.

## Risks / Trade-offs

| Risk | Impact | Mitigation |
|---|---|---|
| **NBD socket cleanup failure** — crash during `backup-begin` leaves stale Unix socket | Next backup fails with "address already in use" | Always `rm -f` socket before `backup-begin`. Socket path uses PID: `/tmp/qsnap-backup-{pid}.sock` |
| **`qemu-img compare` on multi-TB disk** — `verify = "full"` can take hours | Backup pipeline hangs, subsequent runs delayed | Timeout: 7200s (2h). Document as "use only on fast storage, not as default." |
| **Deferred blockcommit accumulation** — VM runs for weeks, dozens of snapshots pile up | Disk fills, VM pauses | `qsnap check` warns when chain length exceeds configurable threshold. `qsnap list` shows deferred count. |
| **`--quiesce` failure on agent timeout** — snapshot creation hangs waiting for frozen FS | Pipeline stalls | `virsh snapshot-create-as` timeout extended to 180s for quiesce. `ShellResult` captures timeout. |
| **`qemu-img map` JSON parsing on fragmented disk** — 100K+ entries | Memory spike, slow change detection | Stream JSON with `ijson` if output exceeds threshold. Fall back to `allocation-size` mode. |
| **`virsh backup-begin` API not available** — older libvirt (<6.0) | NBD backup fails, factory must fall back | `BitmapBackupProvider.__init__` checks libvirt version. Factory catches `RuntimeError` → falls back to `FileCopyBackupProvider` with warning. |
