# Design: bitmap-dirty-block-transfer

## Context

`BitmapBackupProvider.transfer_missing` (qsnap/modules/backup/bitmap.py) starts a
correct incremental NBD export — `<incremental>` element naming the prior qsnap
checkpoint, successor checkpoint created atomically as the third positional
argument of `virsh backup-begin` — but then pulls data with
`qemu-img convert nbd:unix:<sock> <target>`. `qemu-img convert` negotiates only
`base:allocation`; it never requests the `qemu:dirty-bitmap:<name>`
meta-context, so libvirt serves the full disk content and every incremental
backup is a full copy (observed in production: 33 GB+ incremental after a 44 GB
FULL, killed by the user mid-transfer).

The vendored `examples/virtnbdbackup/` proves the correct client behavior
against the exact same libvirt NBD server: negotiate
`qemu:dirty-bitmap:backup-<disk>` (+ `base:allocation` for sparse filtering),
query `block_status`, unify extents, intersect dirty extents with allocated
extents, and `pread` only dirty blocks (`libvirtnbdbackup/nbdcli/client.py`,
`extenthandler/extenthandler.py`, `block.py`, `chunk.py`). It does this through
the `libnbd` Python bindings (`python3-libnbd` system package).

Constraints from AGENTS.md that this design must honor:

- Modules are stateless workers implementing one ABC, constructor-injected with
  infrastructure dependencies only (`IShell`, optionally `IStateManager`);
  config arrives as frozen dataclasses in method parameters.
- Expected failures return result objects, never exceptions.
- All external process execution goes through `IShell`.
- Pure logic (no I/O, deterministic) is extracted into testable utilities
  (IRetentionEngine-style).
- Every module instantiation goes through `IVMModuleFactory`.
- `pyproject.toml` runtime dependencies remain empty (pure stdlib).

Current protective mechanisms that must survive the refactor: atomic
checkpoint creation at the export freeze point, create-then-delete checkpoint
rotation, prior-checkpoint preservation + successor best-effort deletion on
failure, partial-file cleanup, `.tmp`→`mv` atomic rename (FULL path today),
stall detection, Core-level retry with pattern-matched error classification,
M1/M2/M3 verification tiers for FULL backups, transaction log, and
newest-wins checkpoint discovery.

## Goals / Non-Goals

**Goals:**

- Bitmap incremental backups transfer only blocks dirtied since the prior
  checkpoint (true delta), verified end-to-end by a regression barrier that
  would have caught the current bug.
- Bitmap incrementals become real COW deltas: qcow2 files whose
  `backing-filename` points at the previous backup on the same target — making
  restore (chain copy + `qemu-img rebase -u`), retention cascade-deletion, and
  `check` correct for bitmap mode without changes to those subsystems.
- The incremental→FULL dependency is recorded in state by Core after each
  successful verified bitmap transfer.
- All existing protective mechanisms are preserved or ported (same error
  strings where Core/retry pattern-matches them).
- The new NBD transport is mockable end-to-end; unit tests never open a real
  NBD connection (TESTING.md parity with `IShell`).
- Obsolete code and tests tied to the `qemu-img convert` incremental path are
  explicitly identified and removed.

**Non-Goals:**

- Changing the FULL backup path (`nbd_full_export` + `qemu-img convert`
  remains optimal: a FULL copies everything anyway).
- Changing `FileCopyBackupProvider` (file-copy mode is untouched).
- Multi-disk VM support (current single-first-disk limitation is preserved
  and documented; not made worse).
- Compressed bitmap incrementals (impossible with random-access writes — see
  Trade-off T1). May be revisited as a separate change.
- Push-mode backups, nbdkit plugins, restore-side changes.

## Decisions

### D1 — libnbd behind a new `INbdClient` ABC (dependency justification)

**Decision:** Adopt the system package `python3-libnbd` as the NBD transport,
hidden behind a new infrastructure ABC `qsnap.interfaces.nbd.INbdClient`
(sibling of `IShell`). Production injects `LibnbdClient`
(`qsnap/utils/nbd_client.py`); tests inject `MockNbdClient`
(`tests/mocks/mock_nbd.py`). The `import nbd` happens lazily inside
`connect()`, so the rest of the application (file-copy mode, all other
commands) runs fine without the package.

**Interface (result objects, no exceptions for expected failures):**

```
INbdClient (ABC)
    connect(uri, export_name, meta_contexts: list[str]) -> NbdResult
    get_size() -> int
    get_max_request_size() -> int
    block_status(offset, length) -> NbdResult          # payload: dict[str, list[NbdExtent]]
    pread(offset, length) -> NbdResult                 # payload: bytes
    pwrite(offset, data) -> NbdResult
    disconnect() -> None
```

> **Implementation note (post-apply correction):** `block_status` returns its
> payload as a dict keyed by meta-context name (`base:allocation`,
> `qemu:dirty-bitmap:backup-<disk>`), not a flat `list[NbdExtent]` as sketched
> above — libnbd fires the structured-reply extent callback once per negotiated
> meta-context for a single query, and the dict preserves that separation
> without a second server round-trip. Documented in `qsnap/interfaces/nbd.py`
> and the `NbdResult` docstring.

`NbdExtent(offset: int, length: int, data: bool)` and
`NbdResult(success: bool, payload: object | None, error: str | None)` are new
frozen dataclasses in `qsnap/models/results.py`.

**Alternatives considered:**

- *Bare `qemu-img convert` (status quo)* — rejected: provably copies the full
  disk; cannot negotiate dirty-bitmap meta-contexts. This is the bug.
- *Pure-Python NBD protocol over `socket`* (stdlib-only) — rejected: ~600+
  lines of binary protocol state machine (newstyle handshake, structured
  replies, `NBD_OPT_SET_META_CONTEXT`, `NBD_CMD_BLOCK_STATUS` chunk parsing).
  High defect risk and permanent maintenance burden for a solved problem.
- *Push-mode backup (`mode='push'`)* — rejected: qemu writes the destination
  itself and still copies full-allocated data, not the dirty delta; also loses
  our `.tmp`→rename and verification flow.
- *Keep PyPI dependency, e.g. `pip install nbd`* — rejected: there is no
  maintained libnbd binding on PyPI; the bindings ship with the C library.
  The distro package (`apt install python3-libnbd`) is the supported channel,
  consistent with our existing reliance on system `virsh`/`qemu-img`.

**Paradigm integration:** `pyproject.toml` `dependencies = []` is unchanged.
The dependency is *conditional*: only bitmap mode needs it. `env-validation`
checks importability when `incremental_mode = "bitmap"` and fails with an
actionable message; `DefaultFactory` raises the same actionable error if
`LibnbdClient()` is constructed without the package. No silent fallback to
file-copy — the user explicitly selected bitmap mode.

### D2 — Copy loop reads dirty extents only; pure extent logic in utils

**Decision:** The transfer engine inside `transfer_missing` (replacing
bitmap.py step 4) becomes an in-process copy loop:

1. Resolve `previous` = newest existing backup at target (`self.list(target)`
   last element). It becomes the backing file.
2. `qemu-img create -f qcow2 -b <previous> -F qcow2 <target>.tmp` (via IShell).
3. Fork `qemu-nbd --fork --pid-file <pidfile> --socket <wsock> <target>.tmp`
   (via IShell) — the write-side server.
4. Source side: `nbd.connect(nbd:unix:<libvirt sock>, export=<disk>,
   contexts=["base:allocation", "qemu:dirty-bitmap:backup-<disk>"])`.
5. Query `block_status` over the disk in `max_request_size` windows → extent
   list; then `unify_extents()` and `overlap_with_allocation()` — pure
   functions in `qsnap/utils/extents.py` (ported semantics from
   virtnbdbackup `extenthandler.py:130-258`), no I/O, deterministic.
6. For each dirty extent: chunked `pread` (chunk ≤ server max request size) →
   `pwrite` to the qemu-nbd destination at the same offset. A progress
   watchdog records bytes written (see D4).
7. `disconnect()` both clients; kill qemu-nbd by pidfile; `rm -f` write
   socket; `mv <target>.tmp <final>` — the same atomic-rename discipline the
   FULL path already uses.
8. `dirty_bytes` (sum of dirty extent lengths, known *before* copying) feeds
   the verification barrier (see spec delta).

`finally` cleanup mirrors `nbd_full_export`: `virsh domjobabort`, socket
removal, temp XML removal, plus qemu-nbd termination and `.tmp` removal on
failure paths (existing rollback behavior is preserved: partial file deleted,
successor checkpoint deleted best-effort, prior checkpoint kept).

**Provider wiring (post-apply clarification):**

- *Constructor shape.* `BitmapBackupProvider(shell, state=None, nbd=None)` —
  the `INbdClient` is the third constructor dependency as specified, but
  optional: `DefaultFactory` always wires `LibnbdClient`, and `None` is only
  tolerated so paths that never touch the client (FULL backups via
  `nbd_full_export`, `list`, `delete`, checkpoint helpers) can be exercised
  without one. The incremental copy loop fails with an actionable error when
  the client is `None`. Making the parameter mandatory was considered and
  deferred — it would force every FULL-path test call site to construct a
  mock for a dependency they never use, for zero runtime gain.
- *Two simultaneous connections.* The copy loop interleaves
  `pread(source)` → `pwrite(destination)` per chunk, so two client
  connections must be open at once. The injected client serves the source
  (libvirt export); the destination client (qemu-nbd write side) is created
  as a zero-arg sibling of the same concrete class (`type(nbd)()`) — both
  `LibnbdClient` and `MockNbdClient` are zero-arg constructible, so
  production gets a real second connection and tests get a default-success
  mock without a fourth constructor parameter. This resolves the
  "disconnect both clients" requirement with the single injected `INbdClient`
  mandated by the spec.

**Alternatives considered:**

- *Sparse raw intermediate → `qemu-img convert -O qcow2 -c -B <prev>`* —
  rejected: `convert -B` compares source blocks against backing and writes
  zeros where the raw hole reads as zero but backing has data, corrupting
  delta semantics (holes must mean "read through to backing", not "zero").
- *Standalone sparse qcow2 (no backing) + new restore logic* — rejected:
  invasive restore redesign for zero benefit over real backing chains.
- *`qemu-io` for the write side* — rejected: cannot stream arbitrary binary
  payloads portably; qemu-nbd is the clean, debuggable channel.

### D3 — Write side is a backing-chained qcow2 (real COW delta)

**Decision:** The incremental output file chains to the previous backup
(previous incremental, or the FULL for the first incremental). This makes the
bitmap chain physically real on disk: `qemu-img info` shows
`backing-filename`, restore's chain resolution works unchanged, and the
missing dependency registration (P3) is additionally repaired at the file
level. Core still records the dependency in state after verified success
(design D4 of the project: state recording is Core's responsibility).

**Race handling:** between `list(target)` and `qemu-img create -b`, retention
could in theory remove `previous`. Mitigation: existence check immediately
before create; on disappearance, fail the transfer with a retryable-class
error (next run re-discovers the newest). Ghost-retention (design D2) already
prevents deletion of FULLs with recorded dependents during a run.

### D4 — Stall detection moves in-process for the incremental path

**Decision:** The copy loop runs a watchdog: a monotonic timestamp of last
progress (bytes written). If no progress for `stall_timeout` seconds, abort
the loop, run the standard failure path, and return
`error="Stall detected: no progress for {N}s"` — the exact string
`IShell.run_with_stall_detection` produces today, so Core retry
classification is untouched. When `stall_timeout == 0`, the watchdog is
disabled (same semantics as the shell fallback). The watchdog is implemented
as a tiny stateful helper inside the provider (no threads: progress is
checked between chunk writes; a hung `pread` is bounded by libnbd's own
timeout behavior plus the job-abort cleanup).

**Alternatives considered:**

- *Keep `run_with_stall_detection` by spawning a helper subprocess* —
  rejected: the copy loop is in-process Python; wrapping it in a subprocess
  purely for the watchdog adds a process boundary with no benefit.
- *Threads + shared counter* — rejected: unnecessary concurrency for a
  sequential loop; between-chunk checks are sufficient because a single chunk
  is bounded by `max_request_size` (≤ 32 MiB by default).

### D5 — Verification semantics updated; regression barrier added

**Decision:** For bitmap incrementals:

- Metadata tier: qcow2 format check, virtual-size equality, **plus**
  `backing-filename` must equal the resolved previous backup path.
- New regression barrier (all tiers except `off`): `actual-size ≤
  dirty_bytes × K + slack` with K = 2 and slack for qcow2 metadata (default
  64 MiB). Breach ⇒ transfer failed (the engine regressed to full-copy).
- `verify="hash"`/`"full"`: `qemu-img compare -q --force-share
  <snapshot.path> <delta>` — both sides are backing chains whose virtual
  content should match at the freeze point (known live-VM caveats unchanged
  and documented).

`verify_backup()` in `qsnap/utils/verification.py` keeps serving the
file-copy path; bitmap mode gains a dedicated `verify_bitmap_incremental()`
helper in the same module (cross-cutting, stateless — correct home per its
docstring rules).

### D6 — Compression: FULL only (Trade-off T1 accepted)

**Decision:** `target.compress` / `compression_type` continue to apply to
FULL backups (`qemu-img convert -c -o compression_type=zstd`). Bitmap
incrementals are written uncompressed: qcow2 compressed clusters can only be
produced by `qemu-img convert`, and random-access `pwrite` cannot create
them. The delta is small by construction, so the loss is minor; config
acceptance is unchanged (no config migration), and the behavior change is
documented in the spec delta and user docs.

### D7 — Old code and tests are removed in the same change

**Decision:** The refactor explicitly deletes what it replaces — no dead
code, no misleading tests:

**Code removed/changed:**

- `bitmap.py`: the `qemu-img convert` incremental block (step 4, incl. the
  incremental `-c`/zstd branch), the `verify_backup()` call for
  incrementals, and the incremental shell stall-detection branch — replaced
  by the D2 copy loop. Checkpoint lifecycle, rotation, naming, newest-wins,
  `create_full_backup`, `list`, `delete` remain.
- `qsnap/utils/nbd.py`: unchanged (`nbd_full_export` still serves FULL and
  file-copy live full).

**Tests removed (obsolete by design, enumerated for the test-plan):**

- `tests/modules/backup/test_bitmap.py`:
  `test_bitmap_incremental_nbd_with_compression`,
  `test_bitmap_incremental_nbd_without_compression`,
  `test_bitmap_transfer_with_zstd_compression`,
  `test_bitmap_transfer_with_zlib_compression`,
  `test_bitmap_transfer_uses_stall_detection` (shell-level variant),
  `test_incremental_backup_dirty_blocks_via_nbd` and
  `test_bitmap_incremental_dirty_blocks_via_nbd` (assert the convert command;
  superseded by copy-loop tests),
  `test_bitmap_compress_metadata_verification_passes`,
  `test_bitmap_compress_full_verification_passes` (incremental compression
  verification no longer exists).
- Integration: `tests/integration/test_bitmap_atomic.py` and
  `test_bitmap_integration.py` keep their atomicity/rotation scenarios but
  their size assertions (`incr_size > 0` only) are replaced by dirty-bound
  assertions; any scenario asserting full-copy sizes is deleted.

The test-plan artifact (authored with TESTING.md as its contract) contains
the authoritative add/remove list; tasks.md carries the removal steps.

## Risks / Trade-offs

- **T1 — Incrementals lose compression** → Accepted (D6). Delta is small;
  FULL stays zstd-compressed. A future change may add post-hoc compression
  if measurements justify it.
- **R1 — Holes vs backing corruption** → Avoided by construction (D2): we
  only `pwrite` dirty blocks; unallocated clusters read through to backing.
  The rejected `convert -B` alternative would have had this bug.
- **R2 — Previous backup disappears between list and create** → Existence
  re-check + retryable failure class (D3); ghost-retention already guards
  FULLs with dependents.
- **R3 — qemu-nbd process leak (zombie/socket)** → `--pid-file` + kill in
  `finally`, socket `rm -f`, mirroring the existing `nbd_full_export`
  cleanup discipline; integration test asserts no stray processes/sockets.
- **R4 — `python3-libnbd` missing on host** → Fail fast in env-validation
  and factory with "install python3-libnbd" guidance; no silent fallback
  (user explicitly chose bitmap mode).
- **R5 — Regression barrier false positives** → `dirty_bytes` is known
  pre-copy; barrier uses K=2 plus 64 MiB slack, which comfortably absorbs
  qcow2 metadata while remaining ~3 orders of magnitude below a full copy.
- **R6 — Live-VM content drift between freeze point and compare** →
  Unchanged from today; `--force-share` caveat documented in verification
  docs and warning logs.
- **R7 — libvirt < 7.2** → Existing `is_libvirt_new_enough` gate in the
  factory is unchanged.

## Migration Plan

1. Land the change behind the existing `incremental_mode = "bitmap"` config —
   no config schema change, no state-file schema change.
2. First run after upgrade: newest-wins checkpoint discovery picks the last
   pre-upgrade checkpoint as the baseline (legacy names remain parseable), so
   the first post-upgrade incremental is a true delta from that baseline —
   no re-FULL required. Pre-upgrade standalone incrementals (full copies)
   remain restorable standalone files; retention handles them by timestamp
   as before.
3. Rollback: revert the change; the next run falls back to convert-based
   transfer reading the same checkpoint baseline. State files and chains are
   forward/backward compatible (new fields: none).

## Open Questions

- None blocking. (Resolved during planning: compression trade-off accepted;
  first-incremental-after-FULL retained as a now-cheap true delta; OpenSpec
  workflow used for this change.)
