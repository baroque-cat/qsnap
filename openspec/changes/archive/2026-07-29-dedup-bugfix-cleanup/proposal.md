## Why

A deep code audit revealed 4 active bugs (2 HIGH severity), ~6 significant code duplications across lifecycle managers, verification logic, and check/reconcile utilities, 2 dead config fields, 1 dead method (36 lines), and extensive spec drift where documentation describes fields/behaviors removed from code. These issues cause unnecessary FULL backup creation on every run, silently skipped deep verification, potential crashes on `qemu-img check` failures, and a maintenance burden from duplicated logic that diverges over time.

## What Changes

### Bug Fixes
- **FIX**: `target_chain_length or 0` causes unnecessary FULL backup creation when `target_chain_length` is unconfigured (`None`). When unset, FULL creation should not be triggered by incremental count.
- **FIX**: `deep_verify` is not passed to `ILifecycleManager.blockcommit()` in the main blockcommit path — only in the deferred path. `blockcommit_deep_verify = True` silently does nothing for normal pipeline blockcommits.
- **FIX**: `BlockCommitManager` passes `check=True` to `shell.run()` for `qemu-img check` in deep_verify, while `QemuImgCommitManager` does not. With `check=True`, the shell may raise an exception instead of returning a `ShellResult(success=False)`, causing a crash instead of a graceful `CommitResult`.
- **FIX**: `backup_retry_max = 0` produces an empty retry loop (`range(1, 1)`) in the FULL backup creation path, causing FULL to never be attempted. The incremental transfer path handles this correctly with a `max_retries <= 0` guard.

### Deduplication
- Extract the ~44-line identical `deep_verify` block from `BlockCommitManager` and `QemuImgCommitManager` into a shared `deep_verify_base_image()` function in `qsnap/utils/verification.py`.
- Unify 4 independent backing-chain verification implementations (`_verify_backing_chain`, `_check_snapshot_chain`, `_check_target_consistency`, post-cleanup in `_cleanup_backups`) into a single `scan_backing_chain()` function.
- Extract the duplicated retry loop pattern from `_transfer_with_retry()` and the inline FULL creation loop into a generic `_execute_with_retry()` method.
- Extract the duplicated post-cleanup keep-set verification (appears twice in `_cleanup_backups`) into a `_verify_keep_set_chains()` method.
- Extract 4 identical detection blocks (phantom snapshots, phantom FULLs, stale deps, broken chains) shared between `check_state()` and `reconcile()` into shared private detector methods.
- Move `parse_duration` / `parse_stall_timeout` from `qsnap/retention/time_based.py` to `qsnap/utils/time.py` (architectural — they are utilities, not retention logic).

### Dead Code Removal
- Remove `_build_backing_refs()` (36 lines, never called — confirmed by spec).
- **BREAKING**: Remove `deep_check_targets` field from `GlobalConfig` (parsed, stored, never consumed).
- **BREAKING**: Remove `incremental` field from `TargetConfig` (parsed, stored, never consumed — `FileCopyBackupProvider` was removed, only `BitmapBackupProvider` remains). Facade will log a deprecation WARNING if the key appears in TOML.
- Remove dead test elements: orphaned `test_mock_shell_implements_full_interface()` in `__init__.py`, unused `MockBackupProvider`, unused `MockShell.expect_ordered()`.

### Documentation & Spec Drift
- Fix `qsnap.toml.example`: remove `snapshot_deep_verify` (field removed from code), fix `snapshot_chain_length = 0` comment (facade rejects 0), remove `incremental = true`.
- Update specs: remove `rate_limit` and `incremental_mode` from `config-model/spec.md`, remove `snapshot_deep_verify` from `cli-interface/spec.md`, fix retryable pattern string in `backup-retry/spec.md`, update `backup-provider/spec.md` for `transfer_missing` safety-net behavior and remove `bucket_level`.

### Test Suite Cleanup
- Centralize 16 duplicated `_success_result`/`_ok_result`/`_failure_result` helper definitions into conftest fixtures.
- Split `test_pipeline.py` (7699 lines, 139 tests) — remove sections that duplicate dedicated test files.
- Fix tautological tests: `test_check_xml_backingstore_chain_mismatch` (accepts both "broken" and "ok"), `test_reconcile_last_allocation_mismatch` (outdated docstring — code already fixes the mismatch), `test_check_multiple_checkpoints` (tests a known gap).
- Add `clean_shell` fixture for tests needing a MockShell without validation expectations.

## Capabilities

### New Capabilities
- `verification-helpers`: Shared verification utility functions (`deep_verify_base_image`, `scan_backing_chain`) extracted from duplicated code in lifecycle managers and Core. These functions consolidate 4 independent chain-verification implementations and 2 identical deep-verify blocks into single reusable functions in `qsnap/utils/verification.py`.
- `retry-abstraction`: Generic retry wrapper (`_execute_with_retry`) that replaces the duplicated retry-loop pattern in `_transfer_with_retry()` and the inline FULL-creation loop. Handles `backup_retry_max <= 0` correctly and applies `is_retryable()` filtering uniformly.

### Modified Capabilities
- `config-model`: Remove dead fields `deep_check_targets` (GlobalConfig) and `incremental` (TargetConfig). Document that `rate_limit` and `incremental_mode` are deprecated-only (WARNING, not stored). Remove `snapshot_deep_verify` references.
- `periodic-full-backup`: Fix `target_chain_length or 0` bug — when `target_chain_length` is `None` (unconfigured), FULL creation must not be triggered by incremental count. Fix `backup_retry_max = 0` producing an empty retry loop.
- `deep-verification-circuit`: Fix `deep_verify` not being passed to `manager.blockcommit()` in the main blockcommit path (only deferred path passes it). `blockcommit_deep_verify = True` must take effect for all blockcommit paths.
- `lifecycle-manager`: Replace inline `deep_verify` blocks in `BlockCommitManager` and `QemuImgCommitManager` with calls to shared `deep_verify_base_image()`. Fix `check=True` inconsistency in `BlockCommitManager`.
- `chain-integrity-verification`: Replace 4 independent backing-chain verification implementations with calls to shared `scan_backing_chain()`. Preserve all existing return types and side effects.
- `state-reconciliation`: Extract shared detector methods (`_detect_phantom_snapshots`, `_detect_phantom_fulls`, `_detect_stale_deps`, `_detect_broken_chains`) used by both `reconcile()` and `check_state()`. Reconcile continues to own repair logic; detectors return data only.
- `state-consistency-check`: Same shared detectors as `state-reconciliation` — `check_state()` calls detectors and formats `StateCheckResult`, `reconcile()` calls detectors and performs repair.
- `backup-retry`: Unify retry logic via `_execute_with_retry()`. Fix retryable pattern string from `"verification failed: hash mismatch"` to `"verification failed: content comparison mismatch"`.
- `backup-provider`: Fix spec drift — document `transfer_missing()` safety-net behavior when `prior=None` (creates FULL export as fallback). Remove `bucket_level` parameter from `create_full_backup()` spec signature.

## Impact

**Affected production code:**
- `qsnap/core/__init__.py` — bug fixes (lines 4131, 4140, 3614, 4153), deduplication (check_state, reconcile, _cleanup_backups, _transfer_with_retry, FULL creation loop), dead code removal (_build_backing_refs)
- `qsnap/modules/lifecycle/blockcommit_manager.py` — deep_verify extraction, check=True fix
- `qsnap/modules/lifecycle/qemu_img_commit.py` — deep_verify extraction
- `qsnap/utils/verification.py` — new functions: `deep_verify_base_image()`, `scan_backing_chain()`
- `qsnap/utils/time.py` — relocated `parse_duration`, `parse_stall_timeout`
- `qsnap/retention/time_based.py` — remove relocated functions
- `qsnap/models/config.py` — remove `deep_check_targets`, `incremental` fields
- `qsnap/config/facade.py` — remove parsing for removed fields, add deprecation WARNING for `incremental`
- `qsnap.toml.example` — documentation fixes

**Affected specs (delta):**
- `config-model`, `periodic-full-backup`, `deep-verification-circuit`, `lifecycle-manager`, `chain-integrity-verification`, `state-reconciliation`, `state-consistency-check`, `backup-retry`, `backup-provider`

**Affected tests:**
- `tests/conftest.py` — new fixtures (`success_result`, `failure_result`, `clean_shell`)
- `tests/core/test_pipeline.py` — split/remove duplicated sections
- `tests/core/test_reconcile*.py`, `test_check_*.py` — remove duplicated helpers
- `tests/mocks/` — remove dead elements
- New integration tests for bug fixes (target_chain_length=None, deep_verify in main path, backup_retry_max=0)

**Breaking changes:** `deep_check_targets` and `incremental` config fields are removed. Users with these keys in TOML will see a deprecation WARNING but no crash. The `incremental = true` line in `qsnap.toml.example` will be removed.
