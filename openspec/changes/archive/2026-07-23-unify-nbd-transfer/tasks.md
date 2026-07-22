## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b unify-nbd-transfer`
- [x] 1.2 Run the full test suite to establish a passing baseline: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` (baseline must be green; the suite will go temporarily RED after code changes and is brought back to green by the delegated test work in section 6)
- [x] 1.3 Read `openspec/changes/unify-nbd-transfer/proposal.md`, `design.md`, all 12 spec deltas under `specs/`, and `test-plan.md`. Also read `AGENTS.md` (architecture paradigm) and `TESTING.md` (testing paradigm — binding for all test work).

## 2. Pre-Implementation Verification

- [x] 2.1 Verify `nbd_full_export()` has no callers in Core's restore/fork path: `rg -n "nbd_full_export" qsnap/` — if callers exist outside `bitmap.py`, document them (the restore path may need updating in section 3)
- [x] 2.2 Verify `content_hash` has zero consumers: `rg -n "content_hash" qsnap/` — confirm all hits are computation/persistence, not consumption
- [x] 2.3 Verify `file_sha256()` has zero consumers: `rg -n "file_sha256" qsnap/` — confirm only `external.py` and `hash.py` reference it
- [x] 2.4 Verify `full_verify_before_rebase` is ignored by `BitmapBackupProvider.transfer_missing()`: read `bitmap.py` transfer_missing — confirm the parameter is accepted but not used in the copy loop

## 3. INbdClient & LibnbdClient Changes

- [x] 3.1 Add `can_flush() -> bool` and `flush() -> NbdResult` abstract methods to `INbdClient` in `qsnap/interfaces/nbd.py` (see spec: `nbd-bitmap-backup` ADDED "flush() before closing write-side")
- [x] 3.2 Implement `can_flush()` and `flush()` in `LibnbdClient` (`qsnap/utils/nbd_client.py`) via `nbd.can_flush()` / `nbd.flush()` (see design D7)
- [x] 3.3 Add connect-retry to `LibnbdClient.connect()`: 20 attempts, 1-second sleep, fresh `nbd.NBD()` handle on each failure (see design D8, spec: `nbd-bitmap-backup` ADDED "connect-retry in LibnbdClient")
- [x] 3.4 Update `MockNbdClient` in `tests/mocks/mock_modules.py` to implement `can_flush()`/`flush()` (return `True`/`NbdResult(success=True)`)

## 4. Unified NBD Transfer Engine

- [x] 4.1 Generalize `_transfer_extents()` in `qsnap/modules/backup/bitmap.py` into `_transfer(socket_path, write_socket, disk_target, meta_contexts, zero_skip, compress, compression_type, stall_timeout)` — one parameterized method handling both FULL and incremental (see design D1, spec: `nbd-bitmap-backup` MODIFIED "NBD pull-model backup")
- [x] 4.2 Implement zero-skip in the copy loop: when `zero_skip=True`, check `data == b'\x00' * len(data)` before `pwrite` — skip if all-zero (see design D9, spec: `nbd-bitmap-backup` ADDED "zero-skip for standalone FULL")
- [x] 4.3 Implement `_start_write_server(target_file, compress, compression_type)` helper — builds qemu-nbd command with `--image-opts "driver=compress,..."` when `compress=True`, `--format=qcow2` when `compress=False` (see design D6, spec: `nbd-bitmap-backup` ADDED "qemu-nbd compress driver")
- [x] 4.4 Add `dst.flush()` call before `dst.disconnect()` and before terminating qemu-nbd — gated by `dst.can_flush()` (see design D7, spec: `nbd-bitmap-backup` ADDED "flush() before closing write-side")
- [x] 4.5 Rewrite `create_full_backup()` to use the unified engine: `qemu-img create` → fork qemu-nbd (compress driver) → `virsh backup-begin` with checkpoint XML → `_transfer(meta_contexts=["base:allocation"], zero_skip=True)` → flush → terminate → mv .tmp → final (see spec: `nbd-bitmap-backup` MODIFIED "create_full_backup via unified NBD engine", `live-vm-full-backup` MODIFIED "FULL backup requires a running VM")
- [x] 4.6 Rewrite `transfer_missing()` full-pull branch (prior is None) to use the unified engine instead of `_full_pull_via_convert()` (see spec: `backup-provider` MODIFIED "Transfer missing snapshots")
- [x] 4.7 Remove `full_verify_before_rebase` parameter from `IBackupProvider.transfer_missing()` signature in `qsnap/interfaces/backup.py` and from `BitmapBackupProvider.transfer_missing()` (see design D4, spec: `backup-provider` MODIFIED)
- [x] 4.8 Update `Core._transfer_with_retry()` in `qsnap/core/__init__.py` to remove `full_verify_before_rebase` kwarg from `transfer_missing()` calls (keep `GlobalConfig.full_verify_before_rebase` for Core's own FULL-lifecycle verification)

## 5. Dead Code Deletion & Verify Mode Simplification

- [x] 5.1 Delete `nbd_full_export()` from `qsnap/utils/nbd.py` (360 lines) — keep `write_backup_xml()`, `write_checkpoint_xml()`, `get_first_disk_target()`, `is_libvirt_new_enough()`, `is_vm_running()` (see design D2, spec: `live-vm-full-backup` REMOVED "NBD full-export helper")
- [x] 5.2 Delete `_full_pull_via_convert()` from `qsnap/modules/backup/bitmap.py` (see design D1)
- [x] 5.3 Delete `qsnap/utils/hash.py` entirely (`file_sha256()` — zero consumers) (see design D3, spec: `backup-hash-verification` REMOVED)
- [x] 5.4 Remove `content_hash` field from `SnapshotResult` and `SnapshotInfo` in `qsnap/models/results.py` (see design D3, spec: `result-types` REMOVED)
- [x] 5.5 Remove `content_hash` computation from `ExternalSnapshotProvider.create()` in `qsnap/modules/snapshot/external.py` (see spec: `snapshot-provider` MODIFIED)
- [x] 5.6 Remove `content_hash` serialization/deserialization from `JsonStateManager` in `qsnap/state/json_manager.py` — keep read-tolerance: `if "content_hash" in d` still works (see design D3, spec: `state-management` MODIFIED)
- [x] 5.7 Remove `file_sha256` export from `qsnap/utils/__init__.py`
- [x] 5.8 Simplify verify modes in `qsnap/utils/verification.py`: replace `"hash"`/`"full"` with `"compare"` in `verify_full_backup()` and `verify_bitmap_incremental()` (see design D5, spec: `backup-verification` MODIFIED)
- [x] 5.9 Update `TargetConfig.verify` in `qsnap/models/config.py`: valid values `"off"`/`"metadata"`/`"compare"` (was `"off"`/`"metadata"`/`"hash"`/`"full"`) (see design D5, spec: `config-model` MODIFIED)
- [x] 5.10 Update `GlobalConfig.full_verify_after_create` in `qsnap/models/config.py`: valid values `"off"`/`"metadata"`/`"check"`/`"compare"` (was `..."hash"`) (see design D5, spec: `config-model` MODIFIED)
- [x] 5.11 Update verify validation in `qsnap/config/facade.py`: accept `"compare"`, deprecate `"hash"`/`"full"` with WARNING (see design D5, spec: `config-model` MODIFIED)
- [x] 5.12 Update Core verify mode references in `qsnap/core/__init__.py`: replace `"hash"` with `"compare"` in `_backup_target()` and `_cleanup_backups()` (see spec: `backup-full-verification` MODIFIED)
- [x] 5.13 Update `qsnap/interfaces/shell.py` docstring: `run_with_stall_detection` description — data path is now NBD `pread`/`pwrite`, not `qemu-img convert` (see spec: `shell-abstraction` MODIFIED)
- [x] 5.14 Repo-wide sweep: `rg -i "qemu-img convert" qsnap/` — verify zero hits in production code (only in comments/docstrings if any survive)

## 6. Testing

<!-- TEST ORCHESTRATION PROTOCOL — followed by the apply-phase agent (@Mr.Programmer):

  ⚠️ CRITICAL: The programmer agent (@Mr.Programmer) MUST pass the full verbatim contents of
  `/home/openuser/vm/qsnap/TESTING.md` to EVERY @Mr.Tester subagent. TESTING.md describes the
  binding testing paradigm (tests mirror production hierarchy, custom mock classes per ABC,
  contract tests parametrized, markers in pyproject, no pytest-mock, poetry run pytest).
  Every tester prompt MUST include the full TESTING.md content — no exceptions.

  Additionally, the programmer MUST instruct each tester:
  - This is a REMOVAL-FIRST change — the primary job is DELETING and MODIFYING old tests
    (qemu-img convert expectations, content_hash tests, hash/full verify mode tests),
    not writing new ones. NEW tests are written only AFTER cleanup.
  - There is FULL access to a real libvirt + QEMU host for integration tests
    (@pytest.mark.integration). Don't limit to mocks — write real integration tests
    for the unified engine, flush, connect-retry, compress-driver, zero-skip.
  - Report source-level bugs, do NOT fix them — the programmer fixes source bugs.

  Launch ALL groups IN PARALLEL (single message, multiple @Mr.Tester calls).
-->

- [x] 6.1 Read `test-plan.md` Delegation Groups section (12 groups)
- [x] 6.2 Delegate group `nbd-interface-contract` to @Mr.Tester (scope: `tests/interfaces/test_nbd.py`, `tests/interfaces/test_backup_provider.py`, `tests/interfaces/test_shell.py`) — MUST include TESTING.md verbatim
- [x] 6.3 Delegate group `config-model` to @Mr.Tester (scope: `tests/config/test_model.py`, `tests/config/test_facade.py`) — MUST include TESTING.md verbatim
- [x] 6.4 Delegate group `state-management` to @Mr.Tester (scope: `tests/state/test_manager.py`, `tests/utils/test_verification.py`) — MUST include TESTING.md verbatim
- [x] 6.5 Delegate group `models-results` to @Mr.Tester (scope: `tests/models/test_results.py`) — MUST include TESTING.md verbatim
- [x] 6.6 Delegate group `mocks` to @Mr.Tester (scope: `tests/mocks/mock_modules.py`, `tests/mocks/mock_factory.py`, `tests/mocks/test_mock_factory.py`) — MUST include TESTING.md verbatim
- [x] 6.7 Delegate group `snapshot-provider` to @Mr.Tester (scope: `tests/modules/snapshot/test_external.py`) — MUST include TESTING.md verbatim
- [x] 6.8 Delegate group `bitmap-backup` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap.py`, `tests/modules/backup/test_bitmap_incremental.py`, `tests/modules/backup/test_full_verification.py`) — MUST include TESTING.md verbatim — HEAVIEST group (25+ test deletions/rewrites)
- [x] 6.9 Delegate group `core-pipeline` to @Mr.Tester (scope: `tests/core/test_pipeline.py`, `tests/core/test_validation.py`, `tests/core/test_full_verification_pipeline.py`, `tests/core/test_bitmap_dependency.py`) — MUST include TESTING.md verbatim
- [x] 6.10 Delegate group `nbd-utils` to @Mr.Tester (scope: `tests/utils/test_nbd.py`, `tests/utils/test_nbd_client.py`, `tests/utils/test_verification_bitmap.py`, `tests/utils/test_extents.py`) — MUST include TESTING.md verbatim
- [x] 6.11 Delegate group `integration-nbd-unified` to @Mr.Tester (scope: `tests/integration/test_nbd_full_backup.py`, `tests/integration/test_bitmap_atomic.py`, `tests/integration/test_bitmap_dirty_transfer.py`, `tests/integration/test_bitmap_integration.py`, `tests/integration/test_stall_detection.py`, `tests/integration/test_stall_inprocess.py`, `tests/integration/test_verification_bitmap.py`) — MUST include TESTING.md verbatim — REAL libvirt/QEMU available
- [x] 6.12 Delegate group `integration-new` to @Mr.Tester (scope: NEW integration tests for unified engine, flush, connect-retry, compress-driver, zero-skip) — MUST include TESTING.md verbatim — REAL libvirt/QEMU available
- [x] 6.13 Delegate group `fixtures-config` to @Mr.Tester (scope: `tests/fixtures/configs/`, `tests/conftest.py`) — MUST include TESTING.md verbatim
- [x] 6.14 Review all @Mr.Tester reports and fix any source-level bugs discovered (do NOT let testers fix source bugs)
- [x] 6.15 Re-delegate any groups affected by source fixes
- [x] 6.16 Verify all groups pass and coverage matches `test-plan.md`

## 7. Verification Gates

- [x] 7.1 `poetry run ruff check qsnap/ tests/` — clean
- [x] 7.2 `poetry run ruff format --check qsnap/ tests/` — clean
- [x] 7.3 `poetry run pyright qsnap/` — 0 errors (strict execution environment for production code)
- [x] 7.4 `poetry run pytest tests/ -m "not integration and not stress and not e2e"` — all pass
- [x] 7.5 `poetry run pytest tests/integration/ -m integration` — all pass (real libvirt/QEMU host available)
- [x] 7.6 `rg -i "qemu-img convert" qsnap/` — zero hits in production code
- [x] 7.7 `rg "nbd_full_export|_full_pull_via_convert" qsnap/ tests/` — zero hits
- [x] 7.8 `rg "content_hash|file_sha256" qsnap/ tests/` — zero hits
- [x] 7.9 `rg "full_verify_before_rebase" qsnap/interfaces/ qsnap/modules/` — zero hits (Core may still reference GlobalConfig.full_verify_before_rebase for its own use)
- [x] 7.10 `rg '"hash"|"full"' qsnap/utils/verification.py` — zero hits (replaced by `"compare"`)
