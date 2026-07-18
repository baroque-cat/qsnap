## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fix-verification-and-bitmap-issues`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Verification Fixes (qsnap/utils/verification.py)

- [x] 2.1 Remove the actual-size tolerance check from `verify_backup()` metadata mode (lines 255-264). Delete the entire block that computes `source_asize`, `target_asize`, `tolerance`, and the `abs(target_asize - source_asize) > tolerance` comparison. Keep the format check (a) and virtual-size check (b).
- [x] 2.2 Add `--force-share` to the `qemu-img compare` command in `verify_backup()` "full" mode (lines 289-295). Change the command from `["qemu-img", "compare", "-q", ...]` to `["qemu-img", "compare", "-q", "--force-share", ...]`.
- [x] 2.3 Update the WARNING message in `verify_backup()` "full" mode for live source lock conflicts to recommend `verify=metadata` or `verify=hash` (instead of just `verify=metadata`).
- [x] 2.4 Update the docstring of `verify_backup()` to reflect that actual-size is no longer checked and that `--force-share` is now used in full mode.

Reference: `specs/backup-verification/spec.md` — MODIFIED Requirements: "Metadata verification after transfer" and "Full verification via qemu-img compare"

## 3. Failed Backup File Deletion (qsnap/modules/backup/)

- [x] 3.1 In `FileCopyBackupProvider.transfer_missing()` (file_copy.py, after line 356): add `self._shell.run(["rm", "-f", str(target_file)], timeout=10)` immediately after the WARNING log and before appending `BackupResult(success=False)`. Wrap in try/except for `subprocess.TimeoutExpired` and `FileNotFoundError` — log a WARNING if rm fails but do not propagate the error (best-effort cleanup).
- [x] 3.2 In `FileCopyBackupProvider.transfer_missing()` rsync failure path (file_copy.py, after line 178): add the same `rm -f` cleanup for the partial file left by `rsync --partial`.
- [x] 3.3 In `BitmapBackupProvider.transfer_missing()` (bitmap.py, after line 182): add `self._shell.run(["rm", "-f", str(target_file)], timeout=10)` immediately after the WARNING log and before appending `BackupResult(success=False)`.
- [x] 3.4 In `BitmapBackupProvider.transfer_missing()` NBD convert failure path: add `rm -f` cleanup for the partial target file before returning `BackupResult(success=False)`.

Reference: `specs/backup-provider/spec.md` — ADDED Requirements: "Immediate deletion of failed backup files after verification failure"

## 4. Bitmap Double-FULL Fix (qsnap/modules/backup/bitmap.py)

- [x] 4.1 In `BitmapBackupProvider.transfer_missing()` (bitmap.py, around line 97): when `prior` is `None` (no prior checkpoint), check `self._state.get_full_backups(str(target.path))` if `self._state` is not `None`. If FULLs exist, create a checkpoint via `virsh checkpoint-create-as --domain <vm_name> --name qsnap-{target_hash}-{snapshot.name}` without performing a data transfer, log an INFO message, and `continue` to the next snapshot.
- [x] 4.2 Add a helper method `_create_checkpoint_only(vm_name, target_hash, snapshot_name) -> bool` that calls `virsh checkpoint-create-as` and returns success/failure. This keeps the checkpoint creation logic DRY.
- [x] 4.3 Ensure the checkpoint-only path does NOT trigger when `self._state` is `None` (fall through to existing full NBD export behavior).
- [x] 4.4 Ensure the checkpoint-only path does NOT trigger when the snapshot name already exists on the target (the existing `existing_names` check at line 89 short-circuits before this logic).

Reference: `specs/nbd-bitmap-backup/spec.md` — ADDED Requirements: "Checkpoint-only creation when FULL exists and no prior checkpoint"

## 5. Mode-Dependent Default Verification (qsnap/config/facade.py)

- [x] 5.1 In `ConfigFacade._build_target()` (facade.py, around line 361): change the `verify` resolution logic. If the user does not explicitly set `verify` in the TOML config (`tgt_raw.get("verify")` returns `None`), resolve the default based on `incremental_mode`: `"hash"` for `"file-copy"`, `"metadata"` for `"bitmap"`. If the user explicitly sets `verify`, validate and use the explicit value.
- [x] 5.2 Update the `TargetConfig` dataclass docstring in `qsnap/models/config.py` to document that the effective default is mode-dependent (resolved by ConfigFacade, not by the dataclass field default).
- [x] 5.3 Add a deprecation WARNING if a user explicitly sets `verify="metadata"` for file-copy mode, suggesting `verify="hash"` for stronger verification. This is informational only — the explicit value is still honored.

Reference: `specs/config-model/spec.md` — MODIFIED Requirements: "TargetConfig dataclass"
Reference: `specs/backup-hash-verification/spec.md` — MODIFIED Requirements: "verify_backup supports verify='hash' mode"

## 6. Retry Logic Update (qsnap/utils/retry.py, qsnap/core/__init__.py)

- [x] 6.1 In `qsnap/utils/retry.py`: add `"verification failed: hash mismatch"` to the retryable error patterns. This is the only verification error that is retryable — other verification errors (format mismatch, virtual-size mismatch) are deterministic and must not be retried.
- [x] 6.2 In `qsnap/core/__init__.py` `_transfer_with_retry()` (lines 2280-2367): verify that the retry loop correctly picks up the new retryable pattern. No code change needed if `is_retryable()` is called on the error string — just verify the pattern is matched.

Reference: `specs/backup-retry/spec.md` — MODIFIED Requirements: "Retry wrapper for backup transfers on transient errors"

## 7. NBD as Default Mode (qsnap/models/config.py, qsnap/config/facade.py)

- [x] 7.1 In `qsnap/models/config.py`: change `TargetConfig.incremental_mode` default from `"file-copy"` to `"bitmap"` (line 124).
- [x] 7.2 In `qsnap/config/facade.py` `_build_target()`: when `incremental_mode` is not explicitly set, resolve the default to `"bitmap"`. The factory already falls back to `FileCopyBackupProvider` when `is_libvirt_new_enough()` returns `False`, so old systems are unaffected.
- [x] 7.3 Update the `TargetConfig` dataclass docstring to document that the default is `"bitmap"` and that the factory handles fallback to `"file-copy"` for old libvirt.
- [x] 7.4 Verify that existing tests using `make_target()` without explicit `incremental_mode` are updated to account for the new default.

Reference: `specs/config-model/spec.md` — MODIFIED Requirements: "TargetConfig dataclass" (default incremental_mode = "bitmap")

## 8. Bitmap+Hash Warning and Auto-Downgrade (qsnap/config/facade.py)

- [x] 8.1 In `ConfigFacade._build_target()`: when `incremental_mode="bitmap"` and `verify="hash"` are configured together, log a WARNING: "verify='hash' is not supported in bitmap mode (NBD-converted qcow2 has different internal structure). Downgrading to verify='metadata'. Use verify='full' for content-level verification."
- [x] 8.2 After the WARNING, automatically set `verify` to `"metadata"` for the resulting `TargetConfig`. Do NOT raise an error — the configuration is valid but suboptimal.
- [x] 8.3 Ensure this check runs AFTER the mode-dependent default resolution (so it only triggers when the user explicitly sets `verify="hash"` for bitmap mode, not when the default `"metadata"` is resolved).

Reference: `specs/backup-hash-verification/spec.md` — MODIFIED Requirements: "verify_backup supports verify='hash' mode" (bitmap+hash warning scenario)
Reference: `specs/config-model/spec.md` — MODIFIED Requirements: "TargetConfig dataclass" (bitmap+hash downgrade scenario)

## 9. Compression for Incremental Transfers (qsnap/modules/backup/)

- [x] 9.1 In `BitmapBackupProvider.transfer_missing()` (bitmap.py, lines 144-151): add `-c` flag to `qemu-img convert` command when `target.compress=True`. Change from `["qemu-img", "convert", "-O", "qcow2", nbd_uri, str(target_file)]` to conditionally append `-c` before the NBD URI.
- [x] 9.2 In `FileCopyBackupProvider.transfer_missing()` (file_copy.py, lines 128-151): add `--compress` flag to rsync command when `target.compress=True`. For rate-limited transfers: `rsync --bwlimit=<kib> --compress --partial --progress ...`. For non-rate-limited: `rsync --compress --partial --progress ...`.
- [x] 9.3 Verify that compression does not break hash verification for rsync mode (rsync `--compress` is transfer-level, file bytes are identical on target).
- [x] 9.4 Verify that compression does not break metadata verification for NBD mode (`qemu-img info` reports same format and virtual-size regardless of compression).

Reference: `specs/nbd-bitmap-backup/spec.md` — ADDED Requirements: "Compression for NBD incremental transfers"
Reference: `specs/backup-provider/spec.md` — ADDED Requirements: "Compression for rsync incremental transfers"

## 10. README Update

- [x] 10.1 Update the README "Configuration" section to document NBD bitmap as the default `incremental_mode`, with automatic fallback to file-copy for old libvirt.
- [x] 10.2 Document the mode-dependent default for `verify`: `"hash"` for file-copy mode, `"metadata"` for bitmap mode.
- [x] 10.3 Add a note that `verify="hash"` is NOT supported in bitmap mode. When configured with bitmap mode, it auto-downgrades to `"metadata"` with a WARNING. Recommend `verify="full"` for content-level verification in bitmap mode.
- [x] 10.4 Document compression support: FULL backups compress via `qemu-img convert -c` (both modes). NBD incrementals compress via `-c` flag. rsync incrementals compress via `--compress` flag (transfer-level). Controlled by `target.compress` (default `True`).
- [x] 10.5 Update the README "Bitmap Mode" section to document the fixed first-run behavior: only one FULL is created (via bucket strategy), and a checkpoint is created without data transfer for subsequent incrementals.
- [x] 10.6 Update the README "Verification" section to document that actual-size is no longer checked in metadata mode (only format + virtual-size).
- [x] 10.7 Add a "Migration from rsync to NBD" section documenting: existing rsync backups remain valid, new NBD backups coexist as standalone files, transition is graceful, users can set `incremental_mode = "file-copy"` to keep rsync mode.

## 11. Testing

**CRITICAL: The main programmer agent MUST delegate testing to @Mr.Tester subagents. Before delegating, the main programmer MUST pass the TESTING.md document to EACH tester.**

**TEST ORCHESTRATION PROTOCOL:**

1. Read `openspec/changes/fix-verification-and-bitmap-issues/test-plan.md` — Delegation Groups section
2. For EACH group listed below, launch one @Mr.Tester subagent with:
   - The group's scope (file paths from test-plan.md)
   - The group's scenario list from the Coverage Map in test-plan.md
   - The TESTING.md document at `/home/openuser/vm/qsnap/TESTING.md` — the tester MUST read this file to understand the testing paradigm, directory structure, mock strategy, and test categories
   - Instruction: "Write or fix ONLY the tests specified for your group. Follow the TESTING.md paradigm exactly. Use real virsh/qemu-img for integration tests (libvirt is available). Report source bugs, don't fix them."
3. Launch ALL groups IN PARALLEL (single message, multiple @Mr.Tester calls)
4. After all testers return: fix any reported source bugs, re-delegate affected groups
5. Repeat until all groups pass

**The main programmer agent is OBLIGATED to pass TESTING.md to every @Mr.Tester subagent. This is non-negotiable — the TESTING.md document defines the testing paradigm, mock strategy, and directory structure that all testers must follow.**

- [x] 11.1 Read `test-plan.md` Delegation Groups section
- [x] 11.2 Delegate group `verification-unit` to @Mr.Tester (scope: `tests/modules/backup/test_verification.py`). Pass TESTING.md. Tasks: MODIFY 7 tests (flip --force-share assertions, update docstrings), ADD 2 new tests (actual-size-passes, live-source-lock-error).
- [x] 11.3 Delegate group `copy-provider-unit` to @Mr.Tester (scope: `tests/modules/backup/test_copy.py`). Pass TESTING.md. Tasks: REPLACE 1 test (verify failure deletes file), MODIFY 2 tests (rsync failure rm -f, rsync unavailable rm -f), ADD tests for rsync --compress flag.
- [x] 11.4 Delegate group `bitmap-unit` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap.py`). Pass TESTING.md. Tasks: MODIFY 1 test (checkpoint preservation + rm -f), ADD tests for checkpoint-only creation, NBD incremental compression (-c flag), verify failure deletes file.
- [x] 11.5 Delegate group `config-model-unit` to @Mr.Tester (scope: `tests/config/test_resolver.py`, `tests/config/test_model.py`). Pass TESTING.md. Tasks: ADD tests for hash default for file-copy, metadata default for bitmap, explicit override, verify=full for both modes, bitmap+hash warning+downgrade, incremental_mode default=bitmap, explicit file-copy override, factory fallback.
- [x] 11.6 Delegate group `retry-unit` to @Mr.Tester (scope: `tests/utils/test_retry.py`). Pass TESTING.md. Tasks: ADD 4 tests (hash mismatch retryable, format error NOT retryable, retry disabled max=0, exhaustion returns last error).
- [x] 11.7 Delegate group `core-unit` to @Mr.Tester (scope: `tests/core/test_pipeline.py`). Pass TESTING.md. Tasks: ADD 2 tests (retry on hash mismatch in pipeline, immediate halt on format error).
- [x] 11.8 Delegate group `backup-integration` to @Mr.Tester (scope: `tests/integration/` — new files). Pass TESTING.md. Tasks: NEW FILE `test_verification.py` (metadata+hash+full verification with real qemu-img, race condition simulation, failed file deletion), NEW FILE `test_bitmap_integration.py` (checkpoint-only creation with real virsh, NBD incremental compression), NEW FILE `test_retry_integration.py` (retry loop with exponential backoff on hash mismatch).
- [x] 11.9 Review all @Mr.Tester reports and fix any source-level bugs discovered by the tests
- [x] 11.10 Re-delegate any groups affected by source fixes (pass TESTING.md again to each re-delegated tester)
- [x] 11.11 Verify all groups pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` for unit tests
- [x] 11.12 Verify integration tests pass: `poetry run pytest tests/integration/ -m integration` (requires libvirt)
- [x] 11.13 Verify coverage matches test-plan.md — every scenario in every spec file has at least one test

## 12. Final Verification

- [x] 12.1 Run ruff linter: `poetry run ruff check qsnap/ tests/`
- [x] 12.2 Run ruff formatter: `poetry run ruff format qsnap/ tests/`
- [x] 12.3 Run pyright type checker: `poetry run pyright qsnap/`
- [x] 12.4 Run full test suite: `poetry run pytest tests/ -m "not stress and not e2e"`
- [x] 12.5 Verify no Cyrillic characters in any source file: `grep -rn '[а-яА-Я]' qsnap/ tests/` should return nothing
- [x] 12.6 Review all changes with `git diff` — ensure no unintended modifications
