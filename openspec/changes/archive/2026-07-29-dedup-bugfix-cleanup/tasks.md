# Tasks: dedup-bugfix-cleanup

## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b dedup-bugfix-cleanup`
- [x] 1.2 Verify all existing tests pass before starting: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -x --timeout=60`

## 2. Test Suite Cleanup (PRE-REQUISITE — must complete first)

This section modifies conftest.py and test helpers that all other tasks depend on. Complete before any bug fixes or deduplication work.

- [x] 2.1 Add `success_result` and `failure_result` factory fixtures to `tests/conftest.py` (return lambdas that construct `ShellResult` with default parameters)
- [x] 2.2 Add `clean_shell` fixture to `tests/conftest.py` (returns `MockShell()` without validation expectations)
- [x] 2.3 Create `tests/helpers.py` with `add_deferred_with_since()` extracted from `tests/core/test_deferred.py:59-81` and `tests/core/test_list_commands.py:28-51`
- [x] 2.4 Remove duplicated `_success_result`/`_ok_result`/`_failure_result`/`_fail_result` helper definitions from ALL test files: `tests/core/test_reconcile.py:34-54`, `tests/core/test_check_targets.py:30-41`, `tests/core/test_reconcile_targets.py:29-54`, `tests/core/test_reconcile_snapshots.py:30-46`, `tests/core/test_check_snapshots.py:23-38`, `tests/modules/backup/test_bitmap.py:58-60`, `tests/modules/backup/test_bitmap_convert.py:27-29`, `tests/modules/backup/test_bitmap_incremental.py:35-37`, `tests/modules/backup/test_full_verification.py:52-60`, `tests/modules/backup/test_full_verification.py:63-71`, `tests/utils/test_verification.py:21-28`, `tests/utils/test_nbd.py:27-29` — replace all usages with `success_result(stdout=...)` / `failure_result(stderr=...)` fixture calls
- [x] 2.5 Replace `_add_deferred_with_since` in `tests/core/test_deferred.py` and `tests/core/test_list_commands.py` with imports from `tests/helpers.py`
- [x] 2.6 Delete duplicated test sections from `tests/core/test_pipeline.py`: RECONCIRE TESTS (26 tests, lines ~5570-6250), INCREMENTAL GHOST RETENTION (lines ~6281-6700), AUTO-RECOVERY (lines ~7400-7507), Chain Integrity G7 (lines ~7509-7699). Verify the same tests exist in dedicated files before deleting.
- [x] 2.7 Move `test_mock_shell_implements_full_interface()` from `tests/mocks/__init__.py:34-50` to `tests/mocks/test_mock_validity.py` (relocate, do NOT delete the test logic)
- [x] 2.8 Delete `MockBackupProvider` class from `tests/mocks/mock_modules.py:62-123`. Remove from `tests/mocks/__init__.py` exports. Update `tests/mocks/mock_factory.py` to not reference it. Remove `MockBackupProvider` from `tests/interfaces/test_backup_provider.py` parametrization (keep `MockBitmapBackupProvider`).
- [x] 2.9 Delete `MockShell.expect_ordered()` from `tests/mocks/mock_shell.py:85-103` (never called by any test)
- [x] 2.10 Fix `test_check_xml_backingstore_chain_mismatch` in `tests/core/test_check_snapshots.py:580` — replace `assert result["testvm"].status in ("broken", "ok")` with a specific expected status based on the scenario
- [x] 2.11 Fix `test_reconcile_last_allocation_mismatch` in `tests/core/test_reconcile_snapshots.py:458` — update docstring (code already fixes the mismatch, `allocation_fixed=True` is set at core:1828,1840), change assertion to `assert r.allocation_fixed is True`
- [x] 2.12 Fix `test_check_multiple_checkpoints` in `tests/core/test_check_targets.py:765` — either implement the detection logic or mark the test `@pytest.mark.xfail(reason="Implementation gap: multiple checkpoints per target not yet detected")`
- [x] 2.13 Verify all existing tests still pass after cleanup: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -x --timeout=60`

## 3. Bug Fixes

- [x] 3.1 **HIGH: Fix `target_chain_length or 0` bug** — In `qsnap/core/__init__.py:4131`, change `chain_length = target.target_chain_length or 0` to `chain_length = target.target_chain_length`. At line 4132, change `should_full = incremental_count > chain_length` to `should_full = chain_length is not None and incremental_count > chain_length`. Same for dry-run path at line 4140. See `specs/periodic-full-backup/spec.md`.
- [x] 3.2 **HIGH: Fix `deep_verify` not passed in main blockcommit path** — In `qsnap/core/__init__.py:3614`, change `result = manager.blockcommit(vm_config, committable)` to `result = manager.blockcommit(vm_config, committable, deep_verify=vm_config.blockcommit_deep_verify)`. See `specs/deep-verification-circuit/spec.md`.
- [x] 3.3 **MEDIUM: Fix `backup_retry_max=0` empty loop** — In `qsnap/core/__init__.py` around line 4153, add a guard before the FULL creation retry loop: `if max_retries <= 0: max_retries = 1`. Alternatively, implement the `_execute_with_retry()` refactoring first (task 4.4) which handles this. See `specs/periodic-full-backup/spec.md`.

## 4. Code Deduplication

### 4.1 Shared Verification Helpers
- [x] 4.1.1 Create `ChainScanResult` frozen dataclass in `qsnap/models/results.py` with fields: `paths: set[str]`, `broken_files: list[str]`, `success: bool`, `error: str | None`
- [x] 4.1.2 Create `deep_verify_base_image(shell: IShell, base_image: Path) -> CommitResult | None` in `qsnap/utils/verification.py`. Use `shell.run(["qemu-img", "check", "--output=json", str(base_image)], timeout=3600)` — do NOT pass `check=True`. Parse JSON, check `corruptions`/`errors`/`leaks`. Return `CommitResult` on failure, `None` on success. See `specs/verification-helpers/spec.md`.
- [x] 4.1.3 Create `scan_backing_chain(shell: IShell, entry_path: Path) -> ChainScanResult` in `qsnap/utils/verification.py`. Run `qemu-img info --force-share --backing-chain --output=json`. Parse JSON (accept both "image" and "filename" keys), verify existence/format qcow2/backing-filename consistency/cycles. See `specs/verification-helpers/spec.md`.
- [x] 4.1.4 Replace the inline `deep_verify` block in `BlockCommitManager.blockcommit()` (`blockcommit_manager.py:110-153`) with: `if deep_verify: fail = deep_verify_base_image(self._shell, vm_config.base_image); if fail is not None: return fail`
- [x] 4.1.5 Replace the inline `deep_verify` block in `QemuImgCommitManager.blockcommit()` (`qemu_img_commit.py:135-175`) with the same 2-line pattern as 4.1.4
- [x] 4.1.6 Replace `Core._verify_backing_chain()` implementation (`core:3044-3180`) with a call to `scan_backing_chain()`, converting `ChainScanResult` → `ChainVerifyResult` (map `broken_files[0]` to `broken_file`)
- [x] 4.1.7 Replace `Core._check_snapshot_chain()` implementation (`core:606-672`) with a call to `scan_backing_chain()`, returning `result.paths` and appending `result.broken_files` to `broken` list
- [x] 4.1.8 Replace inline `qemu-img info --backing-chain` calls in `Core._check_target_consistency()` (core:865-881) and post-cleanup in `Core._cleanup_backups()` (core:4585-4608, core:4718-4738) with `scan_backing_chain()`. Extract duplicated post-cleanup logic into `_verify_keep_set_chains()` private method.

### 4.2 Shared Retry
- [x] 4.2.1 Add `_execute_with_retry(operation, target, *, is_retryable_fn=is_retryable)` private method to Core in `qsnap/core/__init__.py`. Handle `max_retries <= 0` (single attempt), exponential backoff, retryable check, early exit on non-retryable. See `specs/retry-abstraction/spec.md`.
- [x] 4.2.2 Replace `Core._transfer_with_retry()` implementation with delegation to `self._execute_with_retry()`.
- [x] 4.2.3 Replace the inline FULL backup creation retry loop in `Core._backup_target()` (~lines 4153-4252) with calls to `self._execute_with_retry()`. Apply `is_retryable()` filtering so "No space left on device" is not retried.

### 4.3 Shared Check/Reconcile Detectors
- [x] 4.3.1 Add `_detect_phantom_snapshots(vm)` private method to Core — returns `list[SnapshotInfo]` of snapshots in state whose files don't exist on disk. Pure data, no side effects.
- [x] 4.3.2 Add `_detect_phantom_fulls(vm)` private method to Core — returns `list[tuple[TargetConfig, FullBackupInfo]]`. Pure data, no side effects.
- [x] 4.3.3 Add `_detect_stale_deps(vm)` private method to Core — returns `list[tuple[str, str, TargetConfig]]`. Pure data, no side effects.
- [x] 4.3.4 Add `_detect_broken_chains(vm)` private method to Core — returns `list[str]` of non-FULL backup names with broken backing chains (uses `scan_backing_chain()`). Pure data, no side effects.
- [x] 4.3.5 Replace phantom snapshot/FULL/stale dep/broken chain detection in `Core.check_state()` (core:1449-1498) with calls to the shared detector methods. Keep the `StateCheckResult` formatting logic.
- [x] 4.3.6 Replace the same detection logic in `Core.reconcile()` (core:1669-2019) with calls to shared detectors. Keep the repair logic (state mutation, XML refresh, file deletion) in reconcile.

### 4.4 Architectural Cleanup
- [x] 4.4.1 Move `parse_duration()` and `parse_stall_timeout()` from `qsnap/retention/time_based.py:40-79` to `qsnap/utils/time.py`. Update imports in `qsnap/core/__init__.py` and `qsnap/retention/time_based.py`.
- [x] 4.4.2 Fix `is_retryable` pattern string in `qsnap/utils/retry.py:25`: change `"verification failed: hash mismatch"` to `"verification failed: content comparison mismatch"` (hash was renamed to compare). See `specs/backup-retry/spec.md`.

## 5. Dead Code Removal

- [x] 5.1 Remove `_build_backing_refs()` method from `qsnap/core/__init__.py:4528-4563` (36 lines, never called — confirmed by spec `per-chain-retention/spec.md:61`)
- [x] 5.2 Remove `deep_check_targets` field from `GlobalConfig` in `qsnap/models/config.py:105-108`. Remove its parsing from `qsnap/config/facade.py:154-155`. Remove from `tests/conftest.py` `make_global_config` defaults.
- [x] 5.3 Remove `incremental` field from `TargetConfig` in `qsnap/models/config.py:158`. Add deprecation WARNING in `qsnap/config/facade.py:419` when `incremental` key appears in TOML: `"incremental is deprecated and ignored — all backups are now bitmap-based"`. Do not store the value. See `specs/config-model/spec.md`.

## 6. Documentation & Spec Fixes

- [x] 6.1 Fix `qsnap.toml.example` — remove `snapshot_deep_verify = false` comment (lines 160-162, field removed from VMConfig)
- [x] 6.2 Fix `qsnap.toml.example` — update `snapshot_chain_length = 0` comment (lines 37-38): change "Set to 0 to disable blockcommit (keep all snapshots)" to "Set to a positive integer. Minimum: 1. Default: 24."
- [x] 6.3 Fix `qsnap.toml.example` — remove `incremental = true` line (line 169) since the field is removed from TargetConfig
- [x] 6.4 Update `openspec/specs/config-model/spec.md` — remove `rate_limit` (GlobalConfig + TargetConfig) and `incremental_mode` requirements (fields removed). Remove `deep_check_targets` requirement (field removed). Update `TargetConfig` requirement to exclude `incremental` field.
- [x] 6.5 Update `openspec/specs/backup-provider/spec.md:158` — remove `bucket_level` parameter from `create_full_backup()` signature. Update `transfer_missing` requirement to document safety-net behavior when `prior=None`.
- [x] 6.6 Update `openspec/specs/backup-retry/spec.md:8` — replace `"verification failed: hash mismatch"` with `"verification failed: content comparison mismatch"`.
- [x] 6.7 Update `openspec/specs/cli-interface/spec.md:235-239` — remove `snapshot_deep_verify` from list-config columns.
- [x] 6.8 Update `openspec/specs/live-vm-full-backup/spec.md:39` — replace `_should_create_bucket_full()` reference with the inline logic in `_backup_target()`.

## 7. Testing

**CRITICAL PROTOCOL FOR THE PROGRAMMER AGENT:** When delegating test work to @Mr.Tester subagents, you MUST pass the contents of `TESTING.md` (at `/home/openuser/vm/qsnap/TESTING.md`) as part of every subagent prompt. This document defines the testing paradigm (mirrors production hierarchy, mock strategy, contract tests, markers, fixtures). Every @Mr.Tester MUST understand these rules before writing any test.

**Delegation formula for each group:**
```
Task to @Mr.Tester:
"Read TESTING.md at /home/openuser/vm/qsnap/TESTING.md for testing rules.
 Read the spec files under openspec/changes/dedup-bugfix-cleanup/specs/.
 Read the design.md.
 Your task: write/modify tests for group `<group-name>`.
 Scope: <file paths from test-plan.md>
 Tests to write: <list from test-plan.md Coverage Map for this group>
 Constraints from TESTING.md: use custom mocks (no unittest.mock.patch except for spying/datetime), use markers, follow test file hierarchy."
```

- [x] 7.1 Read `test-plan.md` Delegation Groups section thoroughly
- [x] 7.2 Launch ALL @Mr.Tester subagents IN PARALLEL (single message, multiple tool calls):
  - [x] 7.2.1 Delegate group `test-suite-cleanup` (conftest, helpers, test_pipeline deletions, dead mock removal, tautological test fixes)
  - [x] 7.2.2 Delegate group `verification-helpers-unit` (new `tests/utils/test_verification.py`)
  - [x] 7.2.3 Delegate group `retry-abstraction-unit` (`tests/core/test_pipeline.py`, `tests/utils/test_retry.py`)
  - [x] 7.2.4 Delegate group `bugfix-unit` (`tests/core/test_full_anchor.py`, `tests/core/test_full_verification_pipeline.py`, `tests/config/test_model.py`, `tests/core/test_pipeline.py`, `tests/core/test_deferred.py`)
  - [x] 7.2.5 Delegate group `dedup-lifecycle-unit` (`tests/modules/lifecycle/`, `tests/modules/backup/`)
  - [x] 7.2.6 Delegate group `dedup-chain-verify-unit` (`tests/core/test_pipeline.py`, `tests/core/test_check_snapshots.py`, `tests/core/test_check_targets.py`)
  - [x] 7.2.7 Delegate group `dedup-check-reconcile-unit` (`tests/core/test_reconcile*.py`, `tests/core/test_check_*.py`, `tests/core/test_pipeline.py`)
  - [x] 7.2.8 Delegate group `config-cleanup-unit` (`tests/config/`, `tests/utils/test_time.py`)
- [x] 7.3 Await all @Mr.Tester reports. For any reported source-level bugs, fix them in the production code, then re-delegate affected test groups.
- [x] 7.4 Delegate group `integration-tests` last (requires libvirt) — 4 new test files: `tests/integration/test_target_chain_length_none.py`, `tests/integration/test_deep_verify_main_path.py`, `tests/integration/test_backup_retry_max_zero.py`, `tests/integration/test_scan_backing_chain_real_chain.py`. Note: integration tests require `@pytest.mark.integration` marker and a running libvirt daemon.
- [x] 7.5 Verify all test groups pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -x --timeout=60`
- [x] 7.6 Verify integration tests pass (if libvirt available): `poetry run pytest tests/integration/ -m integration`
- [x] 7.7 Verify test_pipeline.py is reduced from ~7699 lines to ~5100 lines (26 duplicated tests removed)
