## Why

The rsync/file-copy provider was removed in the previous change (`remove-rsync-filecopy`), leaving `BitmapBackupProvider` as the sole backup provider. However, the FULL backup path still uses `qemu-img convert` via `nbd_full_export()` — a completely separate code path from the incremental `_copy_dirty_blocks()` / `_transfer_extents()` libnbd loop. This creates **15 points of duplication** (D1–D15) between FULL and incremental transfer, plus dead code left over from the file-copy era (`content_hash`, `file_sha256()`, `full_verify_before_rebase` parameter). The virtnbdbackup reference project proves that a single unified NBD transfer engine handles both FULL and incremental — the only difference is meta-contexts and extent filtering. This change unifies the engine, deletes all duplicate/dead code, and adds the missing safety primitives (`flush()`, connect-retry, compress-driver) identified in the deep-exploration report (`plan_nbd.md`).

## What Changes

- **BREAKING**: Unified transfer engine — `_transfer_extents()` is generalized to handle both FULL (meta-context: `base:allocation` only, zero-skip=True) and incremental (meta-contexts: `base:allocation` + `qemu:dirty-bitmap`, zero-skip=False). `nbd_full_export()` and `_full_pull_via_convert()` are **deleted** — `create_full_backup()` and the full-pull branch of `transfer_missing()` now use the unified engine.
- **BREAKING**: Remove `content_hash` (SHA-256) entirely — field on `SnapshotResult`/`SnapshotInfo`, computation in `ExternalSnapshotProvider.create()`, persistence in `JsonStateManager`, `file_sha256()` utility. Semantically incorrect for NBD-created backups (qcow2 internal structure differs from source snapshot; SHA-256 of raw files never matches). The `"hash"` verify tier was already repurposed to `qemu-img compare` — `content_hash` has zero consumers.
- **BREAKING**: Remove `full_verify_before_rebase` parameter from `IBackupProvider.transfer_missing()` — dead plumbing (the only implementation ignores it; rebase died with file-copy). Core's `GlobalConfig.full_verify_before_rebase` survives for FULL-lifecycle verification at the Core level.
- **BREAKING**: Simplify verify modes — `"hash"` and `"full"` are semantically identical (both run `qemu-img compare`). Replace with a single `"compare"` mode. `TargetConfig.verify` values: `"off"` / `"metadata"` / `"compare"`. `GlobalConfig.full_verify_after_create` values: `"off"` / `"metadata"` / `"check"` / `"compare"`.
- Add `flush()` and `can_flush()` to `INbdClient` ABC — guarantees durable writes before closing the write-side qemu-nbd server. Implemented in `LibnbdClient` via `nbd.can_flush()` / `nbd.flush()`.
- Add connect-retry to `LibnbdClient.connect()` — 20 attempts with 1-second sleep, fresh `nbd.NBD()` handle on each failure (ported from virtnbdbackup `nbdcli/client.py:93-118`).
- Add qemu-nbd compress-driver support — when `compress=True`, the write-side qemu-nbd is started with `--image-opts "driver=compress,file.driver=qcow2,..."` instead of `--format=qcow2`. This enables `pwrite` to create compressed qcow2 clusters (refutes design D6: "qcow2 compressed clusters can only be produced by qemu-img convert").
- Add zero-skip for standalone FULL — in the unified engine, when `zero_skip=True` (FULL, no backing), all-zero chunks are skipped (no `pwrite`). Safe because unwritten qcow2 clusters without backing read as zeros. Never applied to incrementals (zero dirty-block ≠ backing data).

## Capabilities

### New Capabilities

(none — all changes are modifications to existing capabilities)

### Modified Capabilities

- `nbd-bitmap-backup`: Unified transfer engine (FULL = special case of incremental with different meta-contexts and zero-skip); `flush()` before closing write-side; connect-retry in `LibnbdClient`; zero-skip for FULL
- `nbd-dirty-block-transfer`: Unified engine description — no longer "incremental-only"; the same `_transfer_extents` handles both FULL and incremental
- `backup-provider`: `create_full_backup()` uses unified engine (not `nbd_full_export`/`qemu-img convert`); `transfer_missing()` full-pull branch uses unified engine (not `_full_pull_via_convert`); `full_verify_before_rebase` parameter removed from `transfer_missing()` signature
- `live-vm-full-backup`: REMOVED `nbd_full_export` helper requirement (replaced by unified engine); MODIFIED atomic creation (`.tmp`→rename via unified engine, not convert)
- `backup-verification`: Verify mode simplification — `"hash"`/`"full"` → `"compare"`; `content_hash` references removed
- `backup-full-verification`: M3 tier trigger changes from `"hash"` to `"compare"`; `GlobalConfig.full_verify_after_create` values updated
- `config-model`: `TargetConfig.verify` values: `"off"`/`"metadata"`/`"compare"` (was `"off"`/`"metadata"`/`"hash"`/`"full"`); `GlobalConfig.full_verify_after_create` values updated
- `result-types`: REMOVED `content_hash` field from `SnapshotResult` and `SnapshotInfo`
- `snapshot-provider`: REMOVED `content_hash` computation from `ExternalSnapshotProvider.create()`
- `state-management`: REMOVED `content_hash` persistence from `JsonStateManager` (read-tolerant: old state files with `content_hash` still load, field is ignored)
- `backup-hash-verification`: REMOVED any remaining `content_hash`/`file_sha256` references
- `shell-abstraction`: MODIFIED `run_with_stall_detection` description — data path is now NBD `pread`/`pwrite` (in-process stall watchdog), not `qemu-img convert`

## Impact

**Production code:**
- `qsnap/modules/backup/bitmap.py` — major refactor: merge `_copy_dirty_blocks()` + `_transfer_extents()` + `_full_pull_via_convert()` into unified `_transfer()`; rewrite `create_full_backup()` and `transfer_missing()` full-pull branch; add `_start_write_server()` helper with compress-driver support; add `flush()` call before terminate
- `qsnap/utils/nbd.py` — DELETE `nbd_full_export()` (360 lines); keep `write_backup_xml()`, `write_checkpoint_xml()`, `get_first_disk_target()`, `is_libvirt_new_enough()`, `is_vm_running()` (used by Core and bitmap.py)
- `qsnap/interfaces/nbd.py` — add `can_flush()` and `flush()` abstract methods
- `qsnap/utils/nbd_client.py` — implement `can_flush()`/`flush()`; add connect-retry loop to `connect()`
- `qsnap/interfaces/backup.py` — remove `full_verify_before_rebase` parameter from `transfer_missing()` signature
- `qsnap/utils/verification.py` — simplify verify modes (`"hash"`/`"full"` → `"compare"`)
- `qsnap/utils/hash.py` — DELETE `file_sha256()` (entire file)
- `qsnap/models/results.py` — remove `content_hash` field from `SnapshotResult` and `SnapshotInfo`
- `qsnap/modules/snapshot/external.py` — remove `content_hash` computation from `create()`
- `qsnap/state/json_manager.py` — remove `content_hash` serialization/deserialization
- `qsnap/models/config.py` — update `TargetConfig.verify` and `GlobalConfig.full_verify_after_create` valid values
- `qsnap/config/facade.py` — update verify validation
- `qsnap/core/__init__.py` — update verify mode references; remove `full_verify_before_rebase` threading to `transfer_missing()` (keep for Core's own FULL-lifecycle verification)
- `qsnap/utils/__init__.py` — remove `file_sha256` export
- `qsnap/modules/backup/__init__.py` — clean up if needed

**Tests:** Major — 25+ tests in `test_bitmap*.py` expect `qemu-img convert` commands in shell history; must be rewritten to expect `pread`/`pwrite` NBD commands. `content_hash` tests in `test_external.py`, `test_results.py`, `test_manager.py` must be deleted. Verify-mode tests must be updated for `"compare"` replacing `"hash"`/`"full"`.

**Dependencies:** No new external dependencies. `python3-libnbd` already a hard requirement. `qemu-nbd` compress driver requires QEMU ≥ 4.1 (zstd in qcow2 ≥ 5.1) — already covered by the `libvirt ≥ 7.2` gate.

**Specs:** 12 capability deltas (listed above). The `live-vm-full-backup` spec loses the `nbd_full_export` helper requirement entirely. The `backup-hash-verification` spec may become empty (all requirements removed across two changes).
