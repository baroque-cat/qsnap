## Context

After the `remove-rsync-filecopy` change, `BitmapBackupProvider` is the sole backup provider. However, the FULL backup path still uses `qemu-img convert` via `nbd_full_export()` — a 360-line helper in `qsnap/utils/nbd.py` that is completely separate from the incremental `_copy_dirty_blocks()` / `_transfer_extents()` libnbd loop in `bitmap.py`. The deep-exploration report (`plan_nbd.md`) identified **15 points of code duplication** between these two paths (D1–D15), plus dead code from the file-copy era (`content_hash`, `file_sha256()`, `full_verify_before_rebase` parameter).

The virtnbdbackup reference project (`examples/virtnbdbackup/`) proves that a single unified NBD transfer engine handles both FULL and incremental — the only difference is meta-contexts (`base:allocation` only for FULL; `base:allocation` + `qemu:dirty-bitmap` for incremental) and extent filtering (no overlap for FULL; dirty∩allocated for incremental). virtnbdbackup also demonstrates that `qemu-nbd --image-opts driver=compress` enables `pwrite` to create compressed qcow2 clusters — refuting the old design assumption D6 ("qcow2 compressed clusters can only be produced by qemu-img convert").

## Goals / Non-Goals

**Goals:**
- Unify the NBD transfer engine: one parameterized `_transfer()` method handles both FULL and incremental
- Delete all duplicate code: `nbd_full_export()`, `_full_pull_via_convert()`, and the 15 duplication points
- Delete all dead code: `content_hash`, `file_sha256()`, `full_verify_before_rebase` parameter
- Simplify verify modes: `"hash"`/`"full"` → `"compare"` (both ran `qemu-img compare`)
- Add safety primitives: `flush()`/`can_flush()` in `INbdClient`, connect-retry in `LibnbdClient`
- Add qemu-nbd compress-driver support for write-side compression
- Add zero-skip for standalone FULL (safe — no backing chain)

**Non-Goals (deferred to future changes):**
- FS freeze/thaw around `backupBegin` (hardening bundle, plan §6.9)
- Checkpoint validation via `REDEFINE_VALIDATE` (hardening bundle)
- Inc→full fallback on broken bitmap (hardening bundle)
- Active block-job check before backup (hardening bundle)
- Offline-FULL via standalone `qemu-nbd` (plan §8.1)
- Multi-disk support (plan §8.2)
- `aio_pread`/`aio_pwrite` pipelining (plan §8.4)
- Restore-path unification (plan §8.1 — verify if Core uses `nbd_full_export` for restore)
- Compress-driver for incrementals (separate change — design D6 revision opens this, but mixing it here would expand scope)

## Decisions

### D1: Unified transfer engine — FULL = special case of incremental

**Decision:** Generalize `_transfer_extents()` into `_transfer(socket_path, write_socket, disk_target, meta_contexts, zero_skip, compress, compression_type, stall_timeout)` — one method handling both FULL and incremental.

**Rationale:** virtnbdbackup proves this is production-viable. The 15 duplication points (D1–D15) all stem from maintaining two separate code paths. Unifying eliminates them by construction.

**Alternatives considered:**
- Keep separate paths, extract shared helpers → reduces duplication but doesn't eliminate it; two code paths still diverge over time.
- Replace `nbd_full_export` with a new `nbd_full_export_via_libnbd` → still two functions, still duplication.

**Parameter mapping:**
| Parameter | FULL | Incremental |
|---|---|---|
| `meta_contexts` | `["base:allocation"]` | `["base:allocation", "qemu:dirty-bitmap:backup-{disk}"]` |
| `zero_skip` | `True` | `False` |
| `backing` | `None` (standalone qcow2) | `previous_backup_path` (backing-chained delta) |
| `compress` | from `target.compress` | `False` (incrementals uncompressed — separate change) |

### D2: Delete `nbd_full_export()` entirely

**Decision:** Delete the 360-line `nbd_full_export()` function from `qsnap/utils/nbd.py`. Its callers (`create_full_backup()` and the full-pull branch of `transfer_missing()`) will use the unified engine directly.

**Rationale:** After unification, `nbd_full_export()` has no callers. The helper functions it uses (`write_backup_xml()`, `write_checkpoint_xml()`, `get_first_disk_target()`, `is_libvirt_new_enough()`, `is_vm_running()`) survive — they are used by `bitmap.py` directly.

**Caveat:** Must verify that Core's restore/fork path does not call `nbd_full_export()`. If it does, the restore path must be updated to use the unified engine or a separate restore helper. This is a pre-implementation verification step (task in tasks.md).

### D3: Delete `content_hash` / `file_sha256()` — semantically incorrect, not just dead

**Decision:** Remove `content_hash` field from `SnapshotResult` and `SnapshotInfo`, remove `file_sha256()` from `utils/hash.py`, remove computation from `ExternalSnapshotProvider.create()`, remove persistence from `JsonStateManager`.

**Rationale:** `content_hash` was SHA-256 of the **raw qcow2 file**. For file-copy backups (target = byte-identical copy of source), this worked. For NBD-created backups (target created via `pread`/`pwrite` or `qemu-img convert`), the qcow2 internal structure (L1/L2 tables, cluster metadata, header) differs from the source snapshot — SHA-256 of raw files **never matches**. The `"hash"` verify tier was already repurposed to `qemu-img compare` (which traverses backing chains and compares guest-visible content). `content_hash` has zero consumers and is semantically incorrect for the NBD-only world.

**State file compatibility:** `JsonStateManager` already uses `if "content_hash" in d` for deserialization — old state files with `content_hash` will still load (field is silently ignored). New state files will not contain the field. No migration needed.

### D4: Remove `full_verify_before_rebase` from `transfer_missing()` signature

**Decision:** Remove the `full_verify_before_rebase` parameter from `IBackupProvider.transfer_missing()`. The only implementation (`BitmapBackupProvider`) ignores it — the rebase mechanism died with file-copy.

**Rationale:** Dead plumbing. Core's `GlobalConfig.full_verify_before_rebase` survives — Core uses it for FULL-lifecycle verification at its own level (calling `verify_full_backup()` directly), not by threading it through the provider.

### D5: Simplify verify modes — `"hash"`/`"full"` → `"compare"`

**Decision:** Replace `"hash"` and `"full"` verify modes with a single `"compare"` mode. Both ran `qemu-img compare` — they were semantically identical. The `"full"→"hash"` mapping in `bitmap.py:361` was a workaround.

**New mode sets:**
- `TargetConfig.verify`: `"off"` / `"metadata"` / `"compare"` (was: `"off"` / `"metadata"` / `"hash"` / `"full"`)
- `GlobalConfig.full_verify_after_create`: `"off"` / `"metadata"` / `"check"` / `"compare"` (was: `"off"` / `"metadata"` / `"check"` / `"hash"`)
- `GlobalConfig.full_verify_before_rebase`: `"off"` / `"metadata"` (unchanged)
- `GlobalConfig.full_verify_before_delete`: `"off"` / `"metadata"` / `"check"` (unchanged)

**Deprecation:** Existing configs with `verify = "hash"` or `verify = "full"` will log a deprecation WARNING and be treated as `"compare"` (same behavior, just renamed).

### D6 (revised): qemu-nbd compress driver enables pwrite-based compressed clusters

**Decision:** When `compress=True`, start the write-side qemu-nbd with `--image-opts "driver=compress,file.driver=qcow2,file.file.driver=file,file.file.filename={target}"` instead of `--format=qcow2`. This enables `pwrite` to create compressed qcow2 clusters.

**Rationale:** virtnbdbackup proves this in production (`qemu/util.py:48-62`). The old design assumption D6 ("qcow2 compressed clusters can only be produced by qemu-img convert") is **refuted**. The compress driver is a QEMU block-layer driver inserted via `--image-opts` (not `--filter`). The compression algorithm is taken from the qcow2 metadata — set at creation time via `qemu-img create -f qcow2 -o compression_type=zstd`.

**Requirements:** QEMU ≥ 4.1 (compress driver), ≥ 5.1 (zstd in qcow2) — already covered by the `libvirt ≥ 7.2` gate.

**Scope:** Only for FULL backups (standalone qcow2). Incremental compression is a separate future change (incrementals are backing-chained deltas — compress driver would need testing with backing files).

### D7: `flush()` / `can_flush()` in `INbdClient`

**Decision:** Add `can_flush() -> bool` and `flush() -> NbdResult` to the `INbdClient` ABC. Implement in `LibnbdClient` via `nbd.can_flush()` / `nbd.flush()`. Call `dst.flush()` before `dst.disconnect()` and before terminating qemu-nbd.

**Rationale:** The current code kills qemu-nbd via SIGTERM without explicit flush. `disconnect()` calls `nbd.shutdown()`, but this does not guarantee `fsync` on the underlying qcow2 file. Without flush, the kernel could cache writes and data could be lost if qemu-nbd exits before the page cache flushes. virtnbdbackup calls `nbd.flush()` before closing the write side (`restore/data.py:164-166`).

### D8: Connect-retry in `LibnbdClient.connect()`

**Decision:** Add a retry loop to `LibnbdClient.connect()`: 20 attempts, 1-second sleep between attempts, fresh `nbd.NBD()` handle on each failure. Ported from virtnbdbackup `nbdcli/client.py:93-118`.

**Rationale:** The current `connect()` is single-attempt — if the NBD server (libvirt's `backup-begin` export) isn't ready yet, the connection fails immediately. virtnbdbackup's 20-retry loop handles the race between `virsh backup-begin` (which starts the NBD server asynchronously) and the client connect.

### D9: Zero-skip for standalone FULL only

**Decision:** In the unified engine, when `zero_skip=True` (FULL, no backing), all-zero chunks are skipped (no `pwrite`). Never applied to incrementals.

**Rationale:** Unwritten qcow2 clusters without a backing file read as zeros — so skipping all-zero `pread` chunks is safe and reproduces the zero-detection of `qemu-img convert` for free. For incrementals, a zero dirty-block does NOT mean the backing data is zero — skipping would corrupt the delta.

### D10: No backward compatibility layer

**Decision:** No fallback, no compatibility shim, no old-qemu/libvirt support. `libvirt ≥ 7.2` and `python3-libnbd` are hard requirements (already enforced by `DefaultFactory` and `Core._validate_environment()`).

**Rationale:** The user explicitly stated: "Никакой обратной зависимости, не нужен никакой фулбэк, не будет работы со старыми qemu, libvirt." This simplifies code and maintenance.

## Risks / Trade-offs

| Risk | Severity | Mitigation |
|---|---|---|
| **FULL speed: single-threaded pread/pwrite slower than qemu-img convert** | Medium | FULL backups are rare (bucket-strategy); benchmark 40–100 GB as acceptance step; future `aio_pread`/`aio_pwrite` pipelining (plan §8.4, deferred) |
| **25+ tests expect `qemu-img convert` in shell history** | High | Dedicated test-audit task: rewrite expectations to NBD `pread`/`pwrite` commands; delegate to @Mr.Tester with TESTING.md |
| **Compress-driver unavailable on specific QEMU build** | Low (QEMU ≥ 7.x on target systems) | Pre-flight check in env-validation: trial `qemu-nbd --image-opts driver=compress` → hard-fail with actionable message |
| **Zero-skip masks real data** | Low | Only for standalone (no backing); unit tests: zero chunk → no pwrite; mixed → pwrite |
| **Forgotten flush → tail data loss** | Medium | `flush()` in ABC + terminate only after flush + M1/M2 verify after rename (already exists) |
| **`content_hash` removal breaks state file schema** | Low | `JsonStateManager` uses `if "content_hash" in d` — old files load fine (field ignored); new files omit it |
| **Restore path depends on `nbd_full_export()`** | Unknown | Pre-implementation verification step (task 1.x): grep Core for `nbd_full_export` callers; if found, update restore path |
| **`"hash"`/`"full"` config values in user configs** | Low | Deprecation WARNING + treat as `"compare"` (same behavior, renamed) |
