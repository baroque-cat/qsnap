# Tasks: hysteresis-snapshot-retention

All code, comments, log messages, and documentation written during implementation MUST be in
English. No Cyrillic characters in source files. Follow AGENTS.md paradigm rules (DI via ABCs,
modules never inherit Core, result objects instead of exceptions, immutable frozen config
dataclasses, factory-only instantiation).

## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b feat/hysteresis-snapshot-retention`
- [x] 1.2 Verify all existing tests pass before starting: run the full test suite per TESTING.md (`poetry run pytest tests/ -m "not integration and not stress and not e2e"`, then `-m integration`, `-m stress`, `-m e2e`). Note: the full suite takes ~15 minutes; pipe buffering can make it look hung — run directory subsets if needed.

## 2. Config model and parsing (specs: config-model; design D1, D8)

- [x] 2.1 Add `snapshot_retention_mode: str` (default `"steady"`) as an inheritable VM-level option and a frozen field on `VMConfig`; add `max_commits_per_run: int` (default `12`) to `GlobalConfig` — follow the existing `blockcommit_timeout` pattern in `qsnap/models/config.py`
- [x] 2.2 Implement parsing and global→VM inheritance in `qsnap/config/facade.py`; validation: mode must be `steady` or `hysteresis` (else `ConfigError`); in hysteresis mode, AFTER inheritance, require `snapshot_chain_length > snapshot_preserve_min >= 1` (else `ConfigError` naming the VM); `max_commits_per_run < 0` → `ConfigError`
- [x] 2.3 Document both options in `qsnap.toml.example` (commented defaults) and the README configuration section, including the hysteresis band semantics (grow threshold H = `snapshot_chain_length`, collapse floor L = `snapshot_preserve_min`) and migration note for existing deep chains (convergence takes `ceil((N − L) / max_commits_per_run)` runs)

## 3. State manager: collapse phase key (specs: state-management; design D2)

- [x] 3.1 Extend `IStateManager` with additive methods for the `collapse_in_progress` key (list of disk target names per VM): getter returning `[]` when absent, setter persisting atomically; extend `reset_vm_state` to clear it and `reset_vm_disk_state` to remove one disk; implement in `qsnap/state/json_manager.py` (atomic tmp+replace write; unknown extra keys from older/newer code must be tolerated on read)
- [x] 3.2 Mirror the new methods in `InMemoryStateManager` and `tests/mocks/mock_state.py` so all DI mocks stay interface-complete (ABC isinstance checks must keep passing)

## 4. Shared block-job classifier and probe fix (specs: blockjob-protocol, backup-provider; design D5)

- [x] 4.1 Create `qsnap/utils/blockjob.py` with a pure helper `classify_blockjob_output(...) -> Literal["none", "active", "error"]` (no I/O, fully unit-testable); classification: active job text → `active`; "No current block job" → `none`; failed command / unclassifiable output → `error`
- [x] 4.2 Refactor `Core._probe_blockjob` (qsnap/core/__init__.py) to consume the shared classifier; keep addressing by disk TARGET device name (e.g. `vda`), 30 s timeout
- [x] 4.3 Fix `BitmapBackupProvider.run_backup` pre-backup probe (`qsnap/modules/backup/bitmap.py` ~lines 295–323): pass the disk TARGET device name as `--path` instead of `disk.base_image`; use the shared classifier; semantics: `active` → defer the disk backup (existing deferred result), `none` → proceed, `error` → log a WARNING naming VM and disk and proceed (fail-open; document rationale in a comment)
- [x] 4.4 Grep the codebase for any remaining `virsh blockjob` invocations addressed by file path and convert them to target-name addressing; verify libvirtd-noise scenario is gone by inspecting the mocked command lists in backup tests

## 5. Hysteresis retention evaluation (specs: hysteresis-retention, count-based-retention, snapshot-preserve-min, core-orchestrator; design D3, D4, D7)

- [x] 5.1 In `Core._evaluate_disk_retention` (qsnap/core/__init__.py ~4165): branch on `vm_config.snapshot_retention_mode`; steady mode keeps the current logic byte-identical; hysteresis mode reads the persisted phase, and with `N <= chain_length` and no phase returns an empty remove set (grow phase); otherwise evaluates via the unchanged pure `TimeBasedRetention` engine with effective keep = `snapshot_preserve_min`, applies the preserve_min floor trim, then truncates the remove set to the OLDEST `max_commits_per_run` entries (`0` = unlimited)
- [x] 5.2 Phase lifecycle: when a hysteresis collapse is triggered or continues, persist `collapse_in_progress` containing the disk BEFORE the commit step executes; after commit convergence re-read the snapshot count for the disk: `N <= chain_length` is impossible mid-collapse — clear the phase when `N <= snapshot_preserve_min` floor is reached OR defensively when `N <= chain_length` AND no remove set remains; deferred or failed commits keep the phase; dry-run never writes the phase
- [x] 5.3 Emit observability INFO lines per design D7: `[retention] <vm>/<disk>: collapse phase started (N=…, floor=…)`, `… collapse phase active (N=…, committing … of …)`, `… collapse phase complete (N=…)`; log grow-phase status at DEBUG
- [x] 5.4 Verify the newest L snapshots are NEVER marked for removal in any code path (floor invariant), including when `max_commits_per_run` truncation applies

## 6. Partial-prefix commit reconciliation (specs: commit-reconciliation; design D6)

- [x] 6.1 Extend `Core._reconcile_commit_outcome` (post-`unknown` path): compute `k` = largest contiguous OLDEST prefix of the merge set whose files are absent; classify: `k == n` + chain shrank by n → `late_success` (full verified set); `0 < k < n` + chain shrank by exactly k (or no baseline available) → `late_success` with verified prefix; `k == 0` + chain unchanged → `failure`; any disagreement (chain delta ≠ k, non-prefix deletion pattern, measurement failure) → `inconclusive`; single-snapshot merge sets MUST behave byte-identically to the current implementation
- [x] 6.2 Extend late-success convergence: converge exactly the verified prefix (`set_last_commit_ts`, `remove_snapshot` only for verified snapshots); when the verified set is a strict prefix, rewrite the commit intent record to the remaining suffix instead of clearing it; WARNING log names both converged and still-pending snapshots; full-set case keeps clearing the intent
- [x] 6.3 Confirm the step-0 crash recovery path invokes the same protocol without a pre-commit baseline and that its fail-closed deferral reasons are unchanged

## 7. Dry-run parity (specs: dry-run-prediction; design D2)

- [x] 7.1 Make dry-run evaluate the hysteresis logic read-only: grow phase predicts no commits; triggered/continuing collapse records one per-disk prediction naming the capped oldest merge set; `collapse_in_progress` is read but never set/cleared; the zero-mutation invariant (state byte-identical) holds; add `[dry-run]` INFO note for the grow phase

## 8. Testing

MANDATORY DELEGATION RULE: every tester subagent delegated below MUST receive the full path
`/home/openuser/vm/qsnap/TESTING.md` in its prompt and be instructed to read it FIRST and follow
its hierarchy, markers, mocking rules, and commands. Test-plan details (coverage rows, scopes,
NEW/MODIFY actions) live in `test-plan.md`.

- [x] 8.1 Read `test-plan.md` Delegation Groups and Coverage Map sections
- [x] 8.2 Delegate group `retention-unit` to @Mr.Tester (scope: tests/core/test_hysteresis_retention.py NEW, tests/core/test_preserve.py, tests/interfaces/test_retention_engine.py; `tests/modules/retention/test_time_based.py` is trace-only) — pass TESTING.md
- [x] 8.3 Delegate group `core-pipeline-unit` to @Mr.Tester (scope: tests/core/test_pipeline.py, commit-intent/dispatch/reconciliation test files, dry-run prediction tests) — pass TESTING.md
- [x] 8.4 Delegate group `state-config-unit` to @Mr.Tester (scope: tests/state/*, tests/config/*, tests/mocks/mock_state.py, new TOML fixtures) — pass TESTING.md
- [x] 8.5 Delegate group `backup-probe-unit` to @Mr.Tester (scope: tests/modules/backup/test_bitmap.py, test_bitmap_incremental.py) — pass TESTING.md
- [x] 8.6 Delegate group `utils-classifier-unit` to @Mr.Tester (scope: tests/utils/test_blockjob.py NEW) — pass TESTING.md
- [x] 8.7 Delegate group `integration` to @Mr.Tester (scope: tests/integration/test_hysteresis_retention.py NEW, test_blockcommit_defer.py, test_commit_intent_recovery.py, test_dry_run.py) — pass TESTING.md; run with `-m integration`
- [x] 8.8 Delegate group `stress` to @Mr.Tester (scope: tests/stress/test_long_chain.py capped multi-cycle collapse) — pass TESTING.md; run with `-m stress`
- [x] 8.9 Review all @Mr.Tester reports; fix source-level bugs discovered (testers report bugs, they do not fix source)
- [x] 8.10 Re-delegate any groups affected by source fixes until green
- [x] 8.11 Run the FULL suite (unit + integration + stress + e2e) and verify coverage matches `test-plan.md`; confirm steady-mode behavior is byte-identical for existing configs (default mode)

## 9. Final verification

- [x] 9.1 Run `openspec validate hysteresis-snapshot-retention` (or repo equivalent) and confirm all artifacts consistent
- [x] 9.2 Manual smoke check on a scratch config: steady default unchanged; hysteresis invalid bounds rejected with clear ConfigError; dry-run zero-mutation confirmed by diffing state files before/after
- [x] 9.3 Prepare summary of changed files for review; do NOT archive the change until implementation is merged and verified
