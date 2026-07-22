# Tasks: bitmap-dirty-block-transfer

**MANDATORY for the implementing agent:** read `AGENTS.md` (DI paradigm) and `TESTING.md` (testing paradigm) before writing any code. Both documents are the contract for this change. When delegating ANY test group to a @Mr.Tester subagent (Section 7), you MUST include the full path to `TESTING.md` in every delegation prompt and require the tester to read it first — no exceptions.

## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b bitmap-dirty-block-transfer`
- [x] 1.2 Run the full test suite to establish a passing baseline: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 1.3 Verify the host has libnbd for development/integration runs: `python3 -c "import nbd"` (install `python3-libnbd` via apt if missing)

## 2. Infrastructure — result types, ABC, pure extent logic

- [x] 2.1 Add frozen dataclasses `NbdExtent(offset: int, length: int, data: bool)` and `NbdResult(success: bool, payload: object | None, error: str | None)` to `qsnap/models/results.py`; export both in `qsnap/models/__init__.py` `__all__` (spec: nbd-dirty-block-transfer / INbdClient abstraction)
- [x] 2.2 Create `qsnap/interfaces/nbd.py` with ABC `INbdClient`: `connect(uri, export_name, meta_contexts) -> NbdResult`, `get_size() -> int`, `get_max_request_size() -> int`, `block_status(offset, length) -> NbdResult`, `pread(offset, length) -> NbdResult`, `pwrite(offset, data) -> NbdResult`, `disconnect() -> None`; document "result objects, never exceptions for expected failures" (design D1); export in `qsnap/interfaces/__init__.py`
- [x] 2.3 Create `qsnap/utils/extents.py` with pure functions `unify_extents(extents) -> list[NbdExtent]` and `overlap_with_allocation(dirty, allocated) -> list[NbdExtent]` — port semantics from `examples/virtnbdbackup/libvirtnbdbackup/extenthandler/extenthandler.py` (`_unifyExtents`, `overlap`), no I/O, deterministic (design D2)

## 3. LibnbdClient + Mock

- [x] 3.1 Create `qsnap/utils/nbd_client.py` with `LibnbdClient(INbdClient)`: lazy `import nbd` inside `connect()`; missing package → `NbdResult(success=False, error="python3-libnbd is required ... apt install python3-libnbd")`; request exactly the given meta-contexts; honor server max request size (cap 32 MiB); chunk oversized pread/pwrite; normalize libnbd errors to retryable strings ("eof", "timed out", "broken pipe", "connection refused") (design D1)
- [x] 3.2 Create `tests/mocks/mock_nbd.py` with `MockNbdClient(INbdClient)`: configurable `block_status`/`pread`/`pwrite` responses, call history recording, failure injection per method (TESTING.md: every ABC gets a mock)

## 4. BitmapBackupProvider refactor — replace convert with copy loop

- [x] 4.1 Change `BitmapBackupProvider.__init__` to accept third dependency `nbd: INbdClient` (after `shell`, `state`) (design D1; **BREAKING internal**)
- [x] 4.2 In `transfer_missing()`, DELETE the `qemu-img convert` incremental block (step 4, incl. the incremental `-c`/`-o compression_type=zstd` branch and the `run_with_stall_detection` branch for convert) and the `verify_backup()` call for incrementals — this is the old NBD code going under the knife (design D7)
- [x] 4.3 Implement the copy loop in place of the deleted block: resolve previous backup via `self.list(target)` (newest; FULL if none), re-check existence immediately before use (race → retryable error); `qemu-img create -f qcow2 -b <prev> -F qcow2 <name>.qcow2.tmp` via IShell; fork `qemu-nbd --fork --pid-file <pid> --socket /tmp/qsnap-write-{pid}.sock <tmp>` via IShell; connect source `INbdClient` with contexts `["base:allocation", "qemu:dirty-bitmap:backup-<disk>"]`; `block_status` windows → `unify_extents` → `overlap_with_allocation`; compute `dirty_bytes`; chunked `pread`→`pwrite` per dirty extent; disconnect both; kill qemu-nbd via pidfile; keep atomic `mv .tmp final` (design D2, D3)
- [x] 4.4 Add the in-process stall watchdog to the copy loop: monotonic last-progress timestamp updated per chunk; no progress for `stall_timeout` s → abort with `error="Stall detected: no progress for {N}s"` (exact shell-level string); `stall_timeout == 0` disables; no threads (design D4; spec: stall-detection delta)
- [x] 4.5 Extend the `finally` cleanup: terminate qemu-nbd (pidfile), `rm -f` write socket, remove `.tmp` on failure paths — in addition to existing domjobabort / socket / XML cleanup and checkpoint rollback (design D2; spec: write-side lifecycle crash-safe)
- [x] 4.6 Remove the now-dead `compression_type` handling from the incremental path (keep it in `create_full_backup` untouched); log one INFO line that bitmap incrementals are uncompressed when `target.compress=True` (design D6; spec: REMOVED Compression for NBD incremental transfers)

## 5. Verification

- [x] 5.1 Add `verify_bitmap_incremental(shell, source_path, delta_path, expected_backing, dirty_bytes, verify_mode) -> str | None` to `qsnap/utils/verification.py`: (a) format qcow2, (b) virtual-size match, (c) `backing-filename` == `expected_backing`, (d) regression barrier `actual-size ≤ dirty_bytes × 2 + 64 MiB`; `verify="hash"/"full"` add `qemu-img compare -q --force-share` across chains with the existing live-source WARNING; keep `verify_backup()` for file-copy untouched (design D5; spec: verification requirement)

## 6. Wiring — factory, Core, env-validation

- [x] 6.1 `DefaultFactory.create_backup_provider()`: construct `BitmapBackupProvider(shell, state, LibnbdClient())` for bitmap mode (libvirt ≥ 7.2 gate unchanged); on missing libnbd raise/log the actionable error (no silent fallback) (design D1, R4)
- [x] 6.2 Core: after a bitmap incremental transfer succeeds AND verification passes, call `record_incremental_dependency()` for the incremental and its FULL anchor; failed transfers record nothing (design D3; spec: Core records dependency)
- [x] 6.3 env-validation: when any target has `incremental_mode = "bitmap"`, verify `import nbd` succeeds; failure → validation error naming `python3-libnbd`; skip the check entirely when no bitmap targets exist (spec: env-validation delta)
- [x] 6.4 Update docs (README/install section): `python3-libnbd` system requirement for bitmap mode; note uncompressed incrementals (design D6)

## 7. Testing

> TEST ORCHESTRATION PROTOCOL for the implementing agent:
> 1. Read `test-plan.md` → Delegation Groups.
> 2. For EACH group, launch one @Mr.Tester subagent. EVERY delegation prompt MUST contain: (a) the group scope and its Coverage Map rows, (b) **the absolute path `/home/openuser/vm/qsnap/TESTING.md` with the explicit instruction "read TESTING.md first and follow its paradigm"**, (c) the instruction "Write or fix ONLY these tests. Report source bugs, don't fix them."
> 3. Launch independent groups IN PARALLEL (single message).
> 4. After testers return: fix reported source bugs, re-delegate affected groups, repeat until green.

- [x] 7.1 Read `test-plan.md` Delegation Groups section
- [x] 7.2 Delegate group `extents-unit` to @Mr.Tester (scope: `tests/utils/test_extents.py`, NEW) — prompt MUST include TESTING.md path
- [x] 7.3 Delegate group `nbd-client-unit` to @Mr.Tester (scope: `tests/interfaces/test_nbd.py`, `tests/utils/test_nbd_client.py`, `tests/mocks/mock_nbd.py`, NEW) — prompt MUST include TESTING.md path
- [x] 7.4 Delegate group `bitmap-provider-unit` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap_incremental.py` NEW + `tests/modules/backup/test_bitmap.py` MIXED) — prompt MUST include TESTING.md path
- [x] 7.5 Delegate group `verification-unit` to @Mr.Tester (scope: `tests/utils/test_verification_bitmap.py`, `tests/integration/test_verification_bitmap.py`, NEW) — prompt MUST include TESTING.md path
- [x] 7.6 Delegate group `core-wiring-unit` to @Mr.Tester (scope: `tests/factory/test_default.py` MODIFY, `tests/core/test_bitmap_dependency.py` NEW) — prompt MUST include TESTING.md path
- [x] 7.7 Delegate group `integration-bitmap` to @Mr.Tester (scope: `tests/integration/test_bitmap_dirty_transfer.py`, `test_stall_inprocess.py`, `test_env_validation.py` NEW; `test_bitmap_atomic.py`, `test_bitmap_integration.py` MODIFY; libnbd skip-guard pattern) — prompt MUST include TESTING.md path
- [x] 7.8 Delegate group `test-removal` to @Mr.Tester (scope: DELETE the 9 obsolete tests in `tests/modules/backup/test_bitmap.py` per test-plan.md Test Modifications table: `test_incremental_backup_dirty_blocks_via_nbd`, `test_bitmap_incremental_dirty_blocks_via_nbd`, `test_bitmap_incremental_nbd_with_compression`, `test_bitmap_incremental_nbd_without_compression`, `test_bitmap_compress_metadata_verification_passes`, `test_bitmap_compress_full_verification_passes`, `test_bitmap_transfer_with_zstd_compression`, `test_bitmap_transfer_with_zlib_compression`, `test_bitmap_transfer_uses_stall_detection`; plus the MODIFY list in the same table) — prompt MUST include TESTING.md path
- [x] 7.9 Review @Mr.Tester reports and fix any source-level bugs discovered
- [x] 7.10 Re-delegate any groups affected by source fixes
- [x] 7.11 Verify all groups pass and coverage matches `test-plan.md` (every spec scenario has a test; 9 deletions applied)

## 8. Final verification

- [x] 8.1 Run full unit suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` — green
- [x] 8.2 Run integration suite on a host with libvirt + libnbd: `poetry run pytest tests/integration/ -m integration` — green, incl. the real dirty-bound assertion (10 MiB guest write → delta far below disk size)
- [x] 8.3 Lint + type check: `poetry run ruff check qsnap/ tests/` and `poetry run pyright` — clean
- [x] 8.4 Manual smoke on a real VM — **covered by the automated integration suite** after the venv was switched to Python 3.14 + system-site-packages (libnbd importable): `test_int_full_pipeline_dirty_transfer` (FULL + backing-chained delta, backing-filename, dirty barrier, no stray qemu-nbd/sockets/.tmp), checkpoint rotation to exactly one, and `test_int_no_qemu_nbd_orphan_after_failure` all pass against real libvirt/QEMU. Originally **BLOCKED in this environment**: no VMs are defined on the host and the Poetry venv (Python 3.13) cannot import the system libnbd (Python 3.14). Partial substitute performed: LibnbdClient verified end-to-end against a real qemu-nbd via system python3 (connect with meta-context negotiation, block_status extents, chunked pread/pwrite round-trips, disconnect); the libnbd-dependent integration tests are skip-guarded and will run on a host whose venv sees python3-libnbd: one pipeline run produces FULL + one small backing-chained delta; `qemu-img info` shows `backing-filename`; second run rotates checkpoints to exactly one; no stray `qemu-nbd` processes or `/tmp/qsnap-*` sockets
- [x] 8.5 `openspec validate bitmap-dirty-block-transfer` — valid
