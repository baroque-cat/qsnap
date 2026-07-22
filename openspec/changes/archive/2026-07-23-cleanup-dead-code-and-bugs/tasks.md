## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b cleanup-dead-code-and-bugs`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Dead Code Removal

- [x] 2.1 Remove `full_verify_before_rebase` from `GlobalConfig` dataclass (`qsnap/models/config.py`) — delete the field, its default, and any docstring. (spec: config-model D1)
- [x] 2.2 Remove `full_verify_before_rebase` parsing and validation from `ConfigFacade._build_global()` (`qsnap/config/facade.py`) — delete the field extraction, validation, and deprecation mapping. (spec: config-model D1)
- [x] 2.3 Remove `snapshot_deep_verify` from `VMConfig` dataclass (`qsnap/models/config.py`) — delete the field, its default, and any docstring. (spec: config-model D2, deep-verification-circuit D2)
- [x] 2.4 Remove `snapshot_deep_verify` parsing from `ConfigFacade._build_vm()` (`qsnap/config/facade.py`) — delete the field extraction. (spec: config-model D2)
- [x] 2.5 Remove `snapshot_deep_verify` from CLI status output (`qsnap/cli/commands.py`) — delete any `SNAPSHOT_DEEP_VERIFY` references in list-config output. (spec: config-model D2)
- [x] 2.6 Remove `snapshot_deep_verify` from systemd example config (`qsnap/systemd/`) — delete from any example TOML. (spec: config-model D2)
- [x] 2.7 Remove dead `compression_type` parameter from `_start_write_server()` in `qsnap/modules/backup/bitmap.py` — delete the parameter from the method signature and all call sites. (spec: nbd-bitmap-backup D8)
- [x] 2.8 Remove dead `"hash mismatch"` retry pattern from `is_retryable()` in `qsnap/utils/retry.py` — delete the pattern string from the retryable patterns list. (spec: shared-utilities D8)

## 3. Bug Fixes

- [x] 3.1 Fix `corruptions` field check in `verification.py` M2 (`qsnap/utils/verification.py`) — add `corruptions` check alongside existing `errors` and `leaks` checks. All three fields must be zero for M2 to pass. (spec: backup-full-verification D3)
- [x] 3.2 Fix `errors` and `leaks` field checks in `blockcommit_manager.py` deep_verify (`qsnap/modules/lifecycle/blockcommit_manager.py`) — add `errors` and `leaks` checks alongside existing `corruptions` check. (spec: deep-verification-circuit D3)
- [x] 3.3 Fix `errors` and `leaks` field checks in `qemu_img_commit.py` deep_verify (`qsnap/modules/lifecycle/qemu_img_commit.py`) — add `errors` and `leaks` checks alongside existing `corruptions` check. (spec: deep-verification-circuit D3)
- [x] 3.4 Add `"check"` to allowed `TargetConfig.verify` values in `ConfigFacade._build_target()` (`qsnap/config/facade.py`) — add `"check"` to the validation set alongside `"off"`, `"metadata"`, `"compare"`. (spec: backup-verification D4)
- [x] 3.5 Fix factory violation in `Core._detect_orphan_checkpoints()` (`qsnap/core/__init__.py`) — replace direct `BitmapBackupProvider` instantiation with `self._factory.create_backup_provider(vm_config, target)`. Remove the lazy import. (spec: core-orchestrator D5)
- [x] 3.6 Fix `disk="vda"` hardcoded fallback in `Core._resolve_disks()` (`qsnap/core/__init__.py`) — when `domblklist` fails or returns no disks, return an empty list and log a WARNING. Do NOT fall back to `["vda"]`. (spec: core-orchestrator D6)
- [x] 3.7 Add `snapshot_create` validation in `ConfigFacade._build_vm()` (`qsnap/config/facade.py`) — validate that `snapshot_create` is one of `{"always", "onchange", "ondemand"}`. Raise `ConfigError` on invalid values. (spec: config-model D9)
- [x] 3.8 Implement compress driver validation in `Core._validate_environment()` (`qsnap/core/__init__.py`) — check that `qemu-nbd` supports the compress driver. Fail with actionable error if missing. In dry-run mode, log WARNING. (spec: env-validation D10)

## 4. Scaffolding Deduplication

- [x] 4.1 Extract `_full_pull_lifecycle()` private helper method on `BitmapBackupProvider` (`qsnap/modules/backup/bitmap.py`) — the helper handles: `qemu-img create`, `_start_write_server`, `_transfer`, `_terminate_qemu_nbd`, `mv .tmp → final`, and the `finally` cleanup. (spec: backup-provider D7, nbd-bitmap-backup D7)
- [x] 4.2 Refactor `transfer_missing()` full-pull branch to call `_full_pull_lifecycle()` (`qsnap/modules/backup/bitmap.py`) — replace the inline scaffolding with a call to the helper. (spec: backup-provider D7)
- [x] 4.3 Refactor `create_full_backup()` to call `_full_pull_lifecycle()` (`qsnap/modules/backup/bitmap.py`) — replace the inline scaffolding with a call to the helper. (spec: backup-provider D7)

## 5. Spec Synchronization (Code-Level)

- [x] 5.1 Verify `backup-provider` spec is in sync — no `qemu-img convert` references in the data path. The spec delta already updates the requirement. No code changes needed — just verify. (spec: backup-provider D12)
- [x] 5.2 Verify `periodic-full-backup` spec is in sync — `IBucketFullStrategy` / `BucketFullStrategy` is used, not `Core._should_create_bucket_full()`. The spec delta already updates the requirement. No code changes needed — just verify. (spec: periodic-full-backup D12)
- [x] 5.3 Verify `backup-hash-verification` spec is rewritten as historical record. The spec delta already handles this. No code changes needed. (spec: backup-hash-verification D11)

## 6. Testing

<!-- 
  TEST ORCHESTRATION PROTOCOL (followed by the apply phase agent):

  The main programmer agent (@Mr.Programmer) is responsible for delegating test
  work to @Mr.Tester subagents. The programmer agent MUST follow these rules:

  1. Read `test-plan.md` Delegation Groups section for the full list of groups.
  2. Read the project's TESTING.md file at the repository root — it describes
     the testing paradigm, directory structure, test categories, and rules.
  3. For EACH delegation group, launch one @Mr.Tester subagent with:
     - The group's scope (file paths from test-plan.md)
     - The group's scenario list from the Coverage Map in test-plan.md
     - The full content of TESTING.md (the testing paradigm document)
     - Instruction: "Write or fix ONLY these specific tests following the
       testing paradigm described in TESTING.md. Report source bugs, don't
       fix them."
  4. Launch ALL groups IN PARALLEL (single message, multiple @Mr.Tester calls).
  5. After all testers return: fix any reported source bugs, re-delegate
     affected groups.
  6. Repeat until all groups pass.

  CRITICAL: The programmer agent MUST pass the TESTING.md document to EVERY
  @Mr.Tester subagent. The TESTING.md file is at the repository root
  (/home/openuser/vm/qsnap/TESTING.md) and describes:
  - Test categories (unit, mock, contract, integration, stress, e2e)
  - Directory structure (tests/ mirrors production hierarchy)
  - Testing paradigm (factory injection, result objects, isolated deps)
  - Mock strategy (custom mock classes implementing ABCs, no pytest-mock)
  - Running tests (poetry run pytest, markers, --strict-markers)
  - Adding new module checklist

  Without TESTING.md, testers may not follow the project's testing conventions.
-->

- [x] 6.1 Read `test-plan.md` Delegation Groups section and `TESTING.md` at repository root
- [x] 6.2 Delegate group `verification-unit` to @Mr.Tester — scope: `tests/utils/test_verification.py`, `tests/modules/backup/test_full_verification.py`. Pass TESTING.md content to the tester. (4 scenarios: M2 pass all-zero, corruptions, errors, leaks)
- [x] 6.3 Delegate group `lifecycle-unit` to @Mr.Tester — scope: `tests/modules/lifecycle/test_blockcommit.py`, `tests/modules/lifecycle/test_qemu_img_commit.py`. Pass TESTING.md content to the tester. (7 scenarios: deep_verify pass/fail corruptions/errors/leaks for both managers)
- [x] 6.4 Delegate group `config-unit` to @Mr.Tester — scope: `tests/config/test_model.py`, `tests/config/test_facade.py`, `tests/config/test_parser.py`, `tests/config/test_fixtures.py`. Pass TESTING.md content to the tester. (13 scenarios: verify="check", full_verify_before_rebase removal, snapshot_create validation, snapshot_deep_verify removal)
- [x] 6.5 Delegate group `core-unit` to @Mr.Tester — scope: `tests/core/test_validation.py`, `tests/core/test_pipeline.py`, `tests/core/test_full_verification_pipeline.py`. Pass TESTING.md content to the tester. (9 scenarios: compress driver unskip, factory routing, empty disk list, hash mismatch rename, bucket strategy via factory)
- [x] 6.6 Delegate group `retry-utils-unit` to @Mr.Tester — scope: `tests/utils/test_retry.py`. Pass TESTING.md content to the tester. (1 scenario: is_retryable hash mismatch returns False)
- [x] 6.7 Delegate group `backup-unit` to @Mr.Tester — scope: `tests/modules/backup/test_bitmap.py`, `tests/modules/backup/test_bitmap_incremental.py`. Pass TESTING.md content to the tester. (4 scenarios: _start_write_server signature, _full_pull_lifecycle shared helper, full export, incremental)
- [x] 6.8 Delegate group `cli-systemd-unit` to @Mr.Tester — scope: `tests/cli/test_commands.py`, `tests/systemd/test_units.py`. Pass TESTING.md content to the tester. (0 new scenarios — MODIFY only: remove snapshot_deep_verify references)
- [x] 6.9 Delegate group `state-interfaces-unit` to @Mr.Tester — scope: `tests/models/test_results.py`, `tests/state/test_manager.py`, `tests/interfaces/test_snapshot_provider.py`. Pass TESTING.md content to the tester. (0 new scenarios — keep existing regression guards)
- [x] 6.10 Delegate group `utils-unit` to @Mr.Tester — scope: `tests/utils/test_verification.py`, `tests/utils/test_nbd.py`, `tests/utils/test_verification_bitmap.py`. Pass TESTING.md content to the tester. (1 scenario: Core imports verify_full_backup from utils)
- [x] 6.11 Delegate group `retention-unit` to @Mr.Tester — scope: `tests/modules/retention/test_bucket_full_strategy.py`, `tests/modules/retention/test_time_based.py`. Pass TESTING.md content to the tester. (2 scenarios: weekly bucket, F-anchor — existing tests, no changes)
- [x] 6.12 Delegate group `integration-real` to @Mr.Tester — scope: `tests/integration/test_compress_driver.py`, `tests/integration/test_env_validation.py`, `tests/integration/test_retry_integration.py`. Pass TESTING.md content to the tester. The environment has FULL ACCESS to libvirt and qemu — write real integration tests, not just mocks. (2 scenarios: validation passes, libnbd missing — existing; MODIFY retry integration to update hash mismatch references)
- [x] 6.13 Delegate group `deprecated-fixtures` to @Mr.Tester — scope: `tests/fixtures/configs/safety_fields.toml`, `tests/conftest.py`. Pass TESTING.md content to the tester. (0 new scenarios — MODIFY only: remove snapshot_deep_verify and full_verify_before_rebase from fixtures)
- [x] 6.14 Review all @Mr.Tester reports and fix any source-level bugs discovered
- [x] 6.15 Re-delegate any groups affected by source fixes (pass TESTING.md to each re-delegated tester)
- [x] 6.16 Verify all groups pass and coverage matches `test-plan.md`: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 7. Final Verification

- [x] 7.1 Run the full unit test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 7.2 Run integration tests (if libvirt available): `poetry run pytest tests/integration/ -m integration`
- [x] 7.3 Run ruff linter: `poetry run ruff check qsnap/`
- [x] 7.4 Run ruff formatter: `poetry run ruff format qsnap/`
- [x] 7.5 Run pyright type checker: `poetry run pyright qsnap/`
- [x] 7.6 Verify no references to removed fields: `grep -r "full_verify_before_rebase" qsnap/` and `grep -r "snapshot_deep_verify" qsnap/` should return zero hits
- [x] 7.7 Verify no references to removed patterns: `grep -r "hash mismatch" qsnap/` should return zero hits
- [x] 7.8 Verify no direct BitmapBackupProvider instantiation in Core: `grep -r "BitmapBackupProvider" qsnap/core/` should return zero hits (factory only)

<!--
  TEST ORCHESTRATION PROTOCOL (followed by the apply phase agent):

  1. Read test-plan.md → Delegation Groups section
  2. Read TESTING.md at repository root — the testing paradigm document
  3. For EACH group listed, launch one @Mr.Tester subagent with:
     - The group's scope (file paths)
     - The group's scenario list from Coverage Map
     - The FULL CONTENT of TESTING.md (pass it verbatim to the tester)
     - Instruction: "Write or fix ONLY these specific tests following the
       testing paradigm described in TESTING.md. Report source bugs, don't
       fix them."
  4. Launch ALL groups IN PARALLEL (single message)
  5. After all testers return: fix any reported source bugs, re-delegate
     affected groups (passing TESTING.md again to each re-delegated tester)
  6. Repeat until all groups pass

  CRITICAL: The programmer agent MUST pass the TESTING.md document to EVERY
  @Mr.Tester subagent. TESTING.md is at /home/openuser/vm/qsnap/TESTING.md and
  describes the project's testing paradigm, directory structure, test
  categories, mock strategy, and running instructions. Without it, testers
  may not follow the project's testing conventions.
-->
