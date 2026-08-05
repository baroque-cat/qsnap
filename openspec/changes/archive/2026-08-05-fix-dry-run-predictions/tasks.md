# Tasks: fix-dry-run-predictions

All code, comments, and documentation MUST be written in English. No Cyrillic characters in source files.

Implementation references: `proposal.md` (what/why), `design.md` (decisions D1–D10), `specs/**` (requirements), `test-plan.md` (verification).

## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fix-dry-run-predictions`
- [x] 1.2 Verify all existing tests pass before starting: run `poetry run pytest tests/ -m "not integration and not stress and not e2e"` to establish a passing baseline

## 2. Result Model Extensions (design D7, spec `action-audit-trail`)

- [x] 2.1 Add `disk: str | None = None` field to `SnapshotResult` in `qsnap/models/results.py` (optional, backward-compatible; needed so Core can tag simulated snapshot results per design D1)
- [x] 2.2 Add `predictions: list[ActionRecord] = field(default_factory=list)` field to `PipelineResult` in `qsnap/core/__init__.py` (default empty so all existing constructions remain valid per test-plan.md risk "Test churn breaks unrelated suites")
- [x] 2.3 Extend the documented `ActionRecord.action` vocabulary with `blockcommit` wherever the vocabulary is enumerated (docstrings/comments in `qsnap/models/results.py` and the `action-audit-trail` contract tests' expectations if they enumerate the vocabulary)

## 3. Core Prediction Infrastructure (design D1, D2, D5, D7)

- [x] 3.1 Add `self._predictions: list[ActionRecord]` to `Core.__init__` and reset it at the start of `_run_pipeline`, parallel to `self._actions`; never write predictions to the transaction log (guard at core:~2312 must remain `actions`-only) per spec `action-audit-trail` scenario "Predictions never written to transaction log"
- [x] 3.2 Extract `Core._estimate_chain_size(path: Path) -> int | None` from the existing fork chain-size estimation code (core:1408-1431) into a reusable read-only helper (`qemu-img info --force-share --backing-chain --output=json`, sum `actual-size`); refactor `Core.fork()` to call the helper — behavior must remain byte-identical (design D5)
- [x] 3.3 Implement `Core._simulate_snapshots(vm_config) -> list[SnapshotInfo]` (design D1/D2): in dry-run mode only, build one simulated `SnapshotInfo` per disk that would be snapshotted — name via the real `_generate_snapshot_name()`, path in the disk's snapshot dir, timestamp=now, disk=disk.target, allocation from the read-only `IChangeDetector.has_changed()` result (`ChangeResult.current_allocation`); respect the onchange gate (no simulated snapshots when the gate is closed); never write to `IStateManager`

## 4. Deferred Drain Dry-Run Guard — BUG FIX (design D8, spec `deferred-operations`)

- [x] 4.1 Add a dry-run guard to `Core._check_deferred_operations()` (core:2979-3122): when `self._dry_run`, compute the drain plan read-only (`virsh domstate` allowed) and record one `blockcommit` prediction per disk that would be drained, then return WITHOUT executing `manager.blockcommit()`, WITHOUT `self._state.remove_snapshot()`, WITHOUT rewriting the deferred queue, WITHOUT `_refresh_domain_backing_store()` — this fixes the bug where `qsnap -n run` performs real mutations
- [x] 4.2 If `virsh domstate` fails during dry-run planning, degrade gracefully: still predict the drain with unknown-VM-state handling per spec `deferred-operations` scenario "Dry-run with unknown VM state" (no exception, no mutation)

## 5. Snapshot Simulation Threading (design D3, spec `dry-run-prediction`)

- [x] 5.1 In `_create_snapshot` dry-run branch (core:3136-3141): replace the per-VM log with per-disk logs (design D9) and return the simulated snapshots from `_simulate_snapshots()` instead of `[]`; record one `snapshot_create` prediction per simulated snapshot into `self._predictions`
- [x] 5.2 Add optional `extra_snapshots: list[SnapshotInfo] = ()` parameter to `_evaluate_snapshot_retention` (core:3218-3266): merge extra snapshots with `self._state.get_snapshots()` before per-disk grouping/evaluation; real-run callers pass nothing — behavior unchanged (spec `dry-run-prediction` requirement "Retention prediction against post-run state")
- [x] 5.3 Thread simulated snapshots from `_execute_snapshot_steps` into `_execute_backup_steps` via an optional `extra_snapshots` parameter (core:4185 reads `self._state.get_snapshots()` — merge extras there); record `snapshot_delete` predictions for snapshots retention would remove in dry-run mode

## 6. Backup Prediction (design D4, D6, spec `dry-run-prediction`)

- [x] 6.1 In `_backup_target` FULL dry-run branch (core:4498-4511): enrich the per-disk log with the chain size estimate from `_estimate_chain_size()` (format via `_format_bytes()`, `~` prefix to mark approximation) and record a `backup_full` prediction per disk with the size estimate; on estimation failure degrade gracefully (size unknown, prediction still recorded) per scenario "Estimation failure degrades gracefully"
- [x] 6.2 Implement incremental transfer prediction in the dry-run path of `_backup_target` (the block silently skipped at core:4620-4692): predicted transfer list = snapshots for this target − `full_source_names` − snapshots already present on the target (read-only `provider.list(target)`); for each, record a `backup_transfer` prediction with size = source file actual-size if the file exists else the simulated allocation, logged with `~` upper-bound marker (design D4)
- [x] 6.3 In `_evaluate_backup_retention` dry-run path (core:4851-4908): simulate predicted FULLs as an additional newest chain (timestamp=now) before generation grouping so keep_generations rollover is predicted correctly (design D6); record `backup_delete` predictions for would-be deletions, and log conditional deletions with explicit wording that deletion happens only after the new FULL passes verification (spec `dry-run-prediction` requirement "Backup retention prediction includes predicted FULLs")
- [x] 6.4 Skip per-disk baseline updates in dry-run exactly as today (core:4700-4704) — verify the guard still holds after refactoring

## 7. Blockcommit Prediction (design D9, spec `dry-run-prediction`)

- [x] 7.1 Replace the per-VM counter log in `_blockcommit_snapshots` dry-run branch (core:3699-3705) with per-disk predictions: for each disk group that would be merged, log the disk and snapshot names and record a `blockcommit` prediction per disk (spec scenario "Two disks produce two per-disk predictions")

## 8. Pipeline Result & CLI Summary (design D7, D10, specs `core-orchestrator`, `backup-summary`)

- [x] 8.1 Populate `PipelineResult.predictions` from `self._predictions` in `_run_pipeline` (core:~2333 where `dry_run` is set); ensure real runs always yield `predictions == []` and dry-run never accumulates `ActionRecord` entries in `actions` (spec `core-orchestrator` "Dry-run mode")
- [x] 8.2 In `qsnap/cli/summary.py` `format_summary`: when `result.dry_run` and `result.predictions` is non-empty, render a "Planned actions (dry-run)" section per VM with per-disk prefixes (reuse the existing `[disk]` prefix convention from `_format_action`, summary.py:82); when predictions is empty, render header/footer only (spec `backup-summary` scenarios "Dry-run shows predicted actions per VM and disk", "Dry-run with empty predictions")

## 9. Verification & Quality Gates

- [x] 9.1 Run `poetry run ruff check qsnap/ tests/` and `poetry run ruff format --check qsnap/ tests/` — fix any violations
- [x] 9.2 Run `poetry run pyright qsnap/` — fix any type errors (strict mode)
- [x] 9.3 Run `poetry run pytest tests/ -m "not integration and not stress and not e2e"` — full non-integration suite must pass
- [x] 9.4 Run `openspec validate "fix-dry-run-predictions"` — must report valid

## 10. Testing

MANDATORY DELEGATION RULE: every @Mr.Tester subagent launched in this section MUST receive the FULL content of `/home/openuser/vm/qsnap/TESTING.md` in its prompt (read the file and paste it into each delegation prompt), plus the instruction: "Conform strictly to TESTING.md — markers, mock strategy (no pytest-mock), fixtures, and test hierarchy." Each tester writes or fixes ONLY the tests of its assigned group and reports source-level bugs instead of fixing them.

- [x] 10.1 Read `test-plan.md` Delegation Groups and Coverage Map sections
- [x] 10.2 Delegate group `core-simulated-snapshots` to @Mr.Tester (scope: `tests/core/test_dry_run_prediction.py`, NEW, 17 scenarios; attach TESTING.md)
- [x] 10.3 Delegate group `core-pipeline-updates` to @Mr.Tester (scope: `tests/core/test_pipeline.py`, MODIFY incl. stale-comment cleanup at ~line 462 and adding `virsh domstate` to the read-only allowlist; attach TESTING.md)
- [x] 10.4 Delegate group `core-engine-updates` to @Mr.Tester (scope: `tests/core/test_engine.py`, MODIFY `test_no_actions_in_dry_run_mutations`; attach TESTING.md)
- [x] 10.5 Delegate group `core-full-anchor-updates` to @Mr.Tester (scope: `tests/core/test_full_anchor.py`, MODIFY; attach TESTING.md)
- [x] 10.6 Delegate group `cli-summary-updates` to @Mr.Tester (scope: `tests/cli/test_summary.py`, MODIFY + 1 NEW; attach TESTING.md)
- [x] 10.7 Delegate group `models-updates` to @Mr.Tester (scope: `tests/models/test_results.py`, 2 NEW tests; attach TESTING.md)
- [x] 10.8 Delegate group `integration-dry-run` to @Mr.Tester (scope: `tests/integration/test_dry_run.py`, NEW, 6 tests on real libvirt/qemu using `test_vm` / `test_vm_multi_disk` fixtures; attach TESTING.md)
- [x] 10.9 Delegate group `integration-existing-updates` to @Mr.Tester (scope: `tests/integration/test_count_based_full.py` MODIFY + `tests/integration/test_blockcommit_defer.py` MODIFY with 1 NEW test; attach TESTING.md)
- [x] 10.10 Launch tasks 10.2–10.9 IN PARALLEL (all @Mr.Tester delegations in a single message)
- [x] 10.11 Review all @Mr.Tester reports; fix any source-level bugs discovered in `qsnap/` (never let testers patch production code)
- [x] 10.12 Re-delegate any groups affected by source fixes (again with TESTING.md attached)
- [x] 10.13 Verify all groups pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` then `poetry run pytest tests/integration/ -m integration` (libvirt available); confirm coverage matches the `test-plan.md` Coverage Map (39 scenarios, 40 rows)

## 11. Post-verification follow-up: dry-run state-hygiene zero-mutation (design D11)

Verification (WARNING #1 + SUGGESTION #1/#2) found that state-hygiene self-healing wrote to `IStateManager` during dry-run, and two test artifacts needed corrections. Scope: two source sites (`_validate_state_at_startup`, `_backup_target` phantom filter), spec/design/test-plan amendments, one test-plan row fix, and one integration-test rewrite.

- [x] 11.1 Source: add `Core._healing_logged: set[str]` (init + reset in `_run_pipeline`); gate the three `IStateManager` writes in `_validate_state_at_startup()` (stale-baseline clear, phantom-FULL removal + dependency cascade, post-cascade baseline re-check) behind `if not self._dry_run`, each replaced by a `[dry-run] Would ...` log with per-run dedupe; cascade count via read-only `get_incremental_dependencies()`; post-cascade baseline decision computed in memory in dry-run
- [x] 11.2 Source: gate the three `IStateManager` writes in the `_backup_target()` phantom-FULL filter (same pattern, shared dedupe keys); keep the in-memory `filtered_fulls` filtering active in dry-run
- [x] 11.3 Local gates: ruff check/format, `pyright qsnap/`, and phantom regression anchors (`tests/core/test_pipeline.py` phantom section, `tests/core/test_check_targets.py`, `tests/core/test_dry_run_prediction.py`) all green
- [x] 11.4 Artifacts: amend `specs/dry-run-prediction/spec.md` zero-mutation requirement (2 new scenarios: phantom-FULL prediction without state writes; stale-baseline prediction without state writes; extended read-only command list incl. `virsh domblklist` / `virsh dumpxml` / `virsh checkpoint-list` / `virsh --version`); add design decision D11
- [x] 11.5 Artifacts: fix `test-plan.md` coverage row for scenario "Real run behavior unchanged" (nonexistent `test_retention_real_run_unchanged` replaced by the real VERIFY-unchanged tests); add 2 coverage rows for the new zero-mutation scenarios; add delegation group `dry-run-state-hygiene`; add delegation group `integration-allowlist` (denylist → allowlist rewrite per SUGGESTION #2)
- [x] 11.6 Delegate groups `dry-run-state-hygiene` (4 NEW unit tests in `tests/core/test_dry_run_prediction.py`) and `integration-allowlist` (rewrite `test_dry_run_shell_calls_are_all_read_only` in `tests/integration/test_dry_run.py`) to @Mr.Tester IN PARALLEL with TESTING.md attached; review reports; fix any source bugs; re-run full gates (`ruff`, `pyright`, unit suite, integration suite, `openspec validate`)
