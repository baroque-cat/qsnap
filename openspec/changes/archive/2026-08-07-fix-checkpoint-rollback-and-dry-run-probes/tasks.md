# Tasks: fix-checkpoint-rollback-and-dry-run-probes

## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fix-checkpoint-rollback-and-dry-run-probes`
- [x] 1.2 Verify all existing tests pass before starting: run the full test suite (`poetry run pytest tests/ -m "not integration and not stress and not e2e"`)

## 2. Result Model & Provider (checkpoint name propagation)

Specs: `specs/result-types/spec.md`, `specs/backup-provider/spec.md`. Design decisions: D1.

- [x] 2.1 Add optional frozen field `checkpoint: str | None = None` to `BackupResult` in `qsnap/models/results.py` (after `disk`); extend the docstring: exact libvirt checkpoint name created during the operation, `None` when none was created
- [x] 2.2 In `qsnap/modules/backup/bitmap.py` `create_full_backup()`: on the running-VM path, populate `checkpoint=checkpoint_name` in every `BackupResult` constructed after `backup-begin` succeeds (success result and any post-begin failure results); keep `checkpoint=None` (or omit) on the `backup-begin`-failure branch and on the stopped-VM path
- [x] 2.3 Verify no positional `BackupResult(...)` construction breaks: grep `BackupResult(` across `qsnap/` — the new field is trailing with a default, so call sites must remain keyword-safe

## 3. Core Rollback (exact-name checkpoint deletion)

Spec: `specs/core-orchestrator/spec.md` (Requirement: `Core._cleanup_failed_checkpoint rollback method`). Design decisions: D1, D2.

- [x] 3.1 Rewrite `Core._cleanup_failed_checkpoint()` in `qsnap/core/__init__.py` (~5288-5327): if `full_result.checkpoint` is `None` → return without any `virsh checkpoint-delete` call (DEBUG log); otherwise delete exactly that checkpoint via `virsh checkpoint-delete --metadata --domain <vm> <checkpoint>` (`check=True`, timeout 30s), WARNING on failure, never raise
- [x] 3.2 Remove the bulk `qsnap-{target_hash}-*` filter, the `provider.list_checkpoints()` call, and the now-unused `target_hash` computation from the method; update its docstring to the exact-name contract
- [x] 3.3 Confirm the rollback call site (`core:~4991`) still passes `full_result` unchanged and that retry iterations each delete only their own attempt's checkpoint

## 4. Dry-Run Estimation (base_image fallback + probe hygiene)

Specs: `specs/dry-run-prediction/spec.md`, `specs/shell-abstraction/spec.md`. Design decisions: D3, D4.

- [x] 4.1 In `qsnap/utils/space.py`: add `check=True` to the `shell.run()` probe calls in `estimate_full_size()` (~line 49) and `estimate_incremental_size()` (~line 101); keep the existing WARNING for genuinely undecidable estimates but ensure the shell layer logs expected failures at DEBUG only
- [x] 4.2 In `qsnap/core/__init__.py` dry-run FULL branch (~4886-4957): add a shared helper (e.g. `_estimate_full_size_for_disk(vm_config, disk_cfg, source_snapshot)`) that estimates from `source_snapshot.path` when the file exists and falls back to `disk_cfg.base_image` when it does not; use it for BOTH the prediction estimate (~4925, via the shared chain-size helper contract with `Core.fork()`) and the free-space gate estimate (~4887) so they never disagree
- [x] 4.3 Ensure "Cannot estimate FULL size" WARNING is not emitted solely because the simulated snapshot file is absent (fallback path must be attempted first); prediction still degrades to "size unknown" only when both source and base_image estimates fail

## 5. Obsolete Test Removal

Source: `test-plan.md` section "Tests To Delete (Refactoring Inventory)".

- [x] 5.1 Delete `test_checkpoint_cleaned_up_after_failed_full` from `tests/core/test_full_verification_pipeline.py` (asserts the removed bulk `qsnap-{target_hash}-*` filter)
- [x] 5.2 Re-scan the inventory section of `test-plan.md` for any additional deletions identified during group work; delete only tests listed there

## 6. Testing

Reference: `test-plan.md` — Coverage Map, Delegation Groups, Test Modifications, Integration Test Review.

MANDATORY DELEGATION RULE: the lead programmer agent orchestrating this section MUST pass the testing paradigm document `/home/openuser/vm/qsnap/TESTING.md` to EVERY @Mr.Tester subagent invoked below — each delegation message must instruct the tester to read `TESTING.md` FIRST and conform all test placement, naming, fixtures, mocks, and markers to it. Testers also receive their group's scope and scenario list from `test-plan.md` and the instruction: "Write or fix ONLY these specific tests. Report source bugs, don't fix them."

- [x] 6.1 Read `test-plan.md` Delegation Groups section
- [x] 6.2 Delegate group `rollback-unit` to @Mr.Tester (scope: `tests/core/test_full_verification_pipeline.py`, `tests/mocks/mock_modules.py`) — include `TESTING.md`
- [x] 6.3 Delegate group `result-model-unit` to @Mr.Tester (scope: `tests/models/test_results.py`) — include `TESTING.md`
- [x] 6.4 Delegate group `backup-provider-unit` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap.py`, `tests/interfaces/test_backup_provider.py`) — include `TESTING.md`
- [x] 6.5 Delegate group `dry-run-prediction-unit` to @Mr.Tester (scope: `tests/core/test_dry_run_prediction.py`, `tests/core/test_full_anchor.py`) — include `TESTING.md`
- [x] 6.6 Delegate group `size-estimation-unit` to @Mr.Tester (scope: `tests/utils/test_space.py`) — include `TESTING.md`
- [x] 6.7 Delegate group `shell-probe-unit` to @Mr.Tester (scope: `tests/utils/test_shell.py`) — include `TESTING.md`
- [x] 6.8 Delegate group `env-validation-unit` to @Mr.Tester (scope: `tests/core/test_validation.py`) — include `TESTING.md`
- [x] 6.9 Delegate group `integration` to @Mr.Tester (scope: `tests/integration/test_dry_run.py`, `tests/integration/test_multi_disk.py`, `tests/integration/test_rollback_retry.py`, `tests/integration/test_full_backup.py`, `tests/integration/test_log_levels.py`) — include `TESTING.md`; apply the "Integration Test Review" section assertions (multi-disk rollback isolation, stopped-VM no-delete, first-run dry-run numeric estimate without ERROR)
- [x] 6.10 Launch all group delegations IN PARALLEL (single message, multiple @Mr.Tester invocations)
- [x] 6.11 Review @Mr.Tester reports and fix any source-level bugs discovered (fixes go into sections 2-4 code, never into test expectations)
- [x] 6.12 Re-delegate any groups affected by source fixes (again with `TESTING.md` attached)
- [x] 6.13 Verify all groups pass and coverage matches `test-plan.md`

## 7. Verification & Spec Sync

- [x] 7.1 Run unit suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` — all green
- [x] 7.2 Run integration suite (requires libvirt): `poetry run pytest tests/integration/ -m integration` — all green (SKIPPED: no libvirt available)
- [x] 7.3 Run `ruff check qsnap/ tests/` and `ruff format --check qsnap/ tests/`; run `pyright` (strict) — no new findings
- [x] 7.4 Manual dry-run smoke check on a scratch config: first-run `qsnap -n run` emits no ERROR/WARNING from size estimation and shows a numeric FULL size estimate (SKIPPED: no VM available)
- [x] 7.5 Validate this change: `openspec validate fix-checkpoint-rollback-and-dry-run-probes` passes; delta specs ready for archive-time sync into `openspec/specs/`