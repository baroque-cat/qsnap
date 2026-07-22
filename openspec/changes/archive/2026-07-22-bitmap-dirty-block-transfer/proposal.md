# Proposal: bitmap-dirty-block-transfer

## Why

In `incremental_mode = "bitmap"`, the incremental NBD export is started correctly
(`<incremental>` XML + atomic checkpoint), but the data pull is executed by a bare
`qemu-img convert nbd:unix:<sock> <target>`, which never negotiates the
`qemu:dirty-bitmap:<name>` NBD meta-context. Production evidence (iotop, target
file growing to tens of GB) confirms every "incremental" copies the **entire
virtual disk** instead of the dirty delta. The spec requirement "the first
incremental after a FULL is a true delta" (`nbd-bitmap-backup`) has never been
implemented: libvirt only *advertises* the dirty bitmap; the client must request
it via meta-context negotiation and read only dirty extents — exactly what
`virtnbdbackup` (vendored under `examples/virtnbdbackup/`) does through
`libnbd`. Additionally, the bitmap path never records the incremental→FULL
dependency (`record_incremental_dependency` is only called by
`FileCopyBackupProvider`), making bitmap incrementals invisible to
cascade-deletion and `check`.

## What Changes

- **Replace the incremental transfer engine** in
  `BitmapBackupProvider.transfer_missing`: drop the `qemu-img convert` pull
  (bitmap.py step 4) and implement an in-process copy loop over a new
  `INbdClient` abstraction that negotiates `base:allocation` +
  `qemu:dirty-bitmap:backup-<disk>` meta-contexts, queries block status, unifies
  extents, intersects dirty extents with allocated extents (sparse/fstrim
  filtering, per virtnbdbackup's `overlap()`), and `pread`s only dirty blocks.
- **Write side becomes a true COW delta**: create `<name>.qcow2.tmp` with
  `qemu-img create -f qcow2 -b <previous-backup> -F qcow2`, serve it through a
  forked `qemu-nbd`, `pwrite` dirty blocks at their offsets, then atomic
  `mv .tmp → final`. Bitmap incrementals gain **real backing files**, fixing the
  missing incremental→FULL dependency at the file level and making the existing
  restore (chain copy + `rebase -u`) and cascade-deletion correct for bitmap
  mode without changes.
- **New infrastructure ABC `INbdClient`** (sibling of `IShell`): production
  implementation `LibnbdClient` wraps the system `python3-libnbd` package with a
  lazy `import nbd`; tests inject `MockNbdClient`. Extent unification/overlap
  logic lives in `qsnap/utils/extents.py` as pure, I/O-free functions
  (IRetentionEngine-style).
- **Adopt the system dependency `python3-libnbd`** (distro package; PyPI
  `dependencies = []` stays untouched). Bitmap mode fails fast with an
  actionable error when the package is missing; `env-validation` gains the
  check.
- **Stall detection moves in-process** for the incremental path: the copy loop
  carries a progress watchdog (no bytes written for `stall_timeout` seconds →
  abort with the same `"Stall detected: no progress for Ns"` error string, so
  Core retry logic is unaffected). `IShell.run_with_stall_detection` remains for
  the FULL path (`qemu-img convert`).
- **Verification updated**: incremental metadata checks now assert qcow2 format,
  virtual-size match, **and** `backing-filename == <previous backup>`; a new
  regression barrier fails the transfer when
  `actual-size > dirty_bytes × K + slack` (K≈2), which would have caught the
  current bug. `verify="hash"/"full"` compare the snapshot chain against the
  FULL+delta chain via `qemu-img compare` (chain-traversing, semantics now
  meaningful).
- **Compression trade-off**: qcow2 compressed clusters can only be produced by
  `qemu-img convert`; random-access `pwrite` cannot create them. Bitmap
  incrementals become **uncompressed** (`target.compress` keeps applying to
  FULL backups only). Documented behavior change.
- **Core records the dependency**: after a successful verified bitmap
  incremental, Core calls `record_incremental_dependency()` (design D4 — state
  recording is Core's responsibility).
- **Old code and tests removed**: the `qemu-img convert` incremental path, its
  `verify_backup` usage in bitmap mode, compression-on-incremental tests,
  shell-level stall-detection tests for bitmap transfer, and integration size
  assertions that only passed because incrementals were full copies.
- **BREAKING (internal)**: `BitmapBackupProvider.__init__` gains a third
  dependency `nbd: INbdClient`; `DefaultFactory` constructs it. No public ABC
  signature changes (`IBackupProvider.transfer_missing` is unchanged).

## Capabilities

### New Capabilities

- `nbd-dirty-block-transfer`: The `INbdClient` abstraction, its `LibnbdClient`
  production implementation, pure extent-processing functions, the dirty-block
  copy loop with in-process stall watchdog, and the backing-chained qcow2
  write-side lifecycle (qemu-img create + qemu-nbd + atomic rename).

### Modified Capabilities

- `nbd-bitmap-backup`: Incremental transfer SHALL copy only dirty blocks via
  meta-context negotiation; incremental output gains a real backing file;
  compression no longer applies to bitmap incrementals; incremental→FULL
  dependency SHALL be recorded; verification requirements updated (backing-file
  check + dirty-size regression barrier).
- `stall-detection`: Incremental bitmap transfer uses an in-process progress
  watchdog instead of output-file growth polling (the error contract is
  preserved); FULL backup path is unchanged.
- `env-validation`: When `incremental_mode = "bitmap"`, validation SHALL verify
  that the `python3-libnbd` system package is importable and fail with an
  actionable message otherwise.

## Impact

- **Code**: `qsnap/modules/backup/bitmap.py` (transfer engine replaced),
  `qsnap/utils/nbd_client.py` (new), `qsnap/interfaces/nbd.py` (new ABC),
  `qsnap/utils/extents.py` (new pure functions), `qsnap/models/results.py`
  (new `NbdExtent`/`NbdResult` dataclasses), `qsnap/factory/default.py`
  (constructor wiring), `qsnap/core/__init__.py` (dependency recording),
  env-validation module (libnbd check), `qsnap/config/facade.py`
  (bitmap `verify="hash"/"full"` auto-downgrade removed — chain-traversing
  `qemu-img compare` is now meaningful for backing-chained deltas, so the
  explicit tier is preserved instead of downgraded to `"metadata"`).
- **Tests**: new `tests/mocks/mock_nbd.py`, `tests/utils/test_extents.py`,
  `tests/utils/test_nbd_client.py`, rewritten
  `tests/modules/backup/test_bitmap.py` incremental-transfer tests, reworked
  `tests/integration/test_bitmap_atomic.py` and `test_bitmap_integration.py`
  (dirty-size assertions). Removed: compression-on-incremental and
  shell-stall-detection bitmap tests made obsolete by the new engine.
- **Dependencies**: new *system* package `python3-libnbd` (libnbd Python
  bindings) for bitmap mode only; `pyproject.toml` runtime dependencies remain
  empty. FULL backup, file-copy mode, restore, retention, and all other
  providers are untouched.
- **Specs**: one new capability spec, three modified capability specs (deltas).
- **Docs**: installation docs gain the `python3-libnbd` requirement for bitmap
  mode and a note on uncompressed incrementals.
