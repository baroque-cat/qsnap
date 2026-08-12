# Tasks: harden-blockcommit-races

All code, comments, and documentation written during implementation MUST be in English.
Follow `AGENTS.md` (DI paradigm, result objects, no module-to-module imports, config as method
parameters) and `TESTING.md` (test categories, markers, mock strategy).

## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b harden-blockcommit-races`
- [x] 1.2 Verify all existing tests pass before starting: run the full test suite
      (`poetry run pytest -m "not integration and not stress and not e2e"`, then the marked
      suites if libvirt is available) and record the baseline — 1838 passed, 179 deselected

## 2. Models & Interfaces (foundation — everything depends on this)

- [x] 2.1 Add `outcome: str` field to `CommitResult` in `qsnap/models/results.py`
      (values `"success" | "failure" | "unknown"`, default `"failure"`; frozen; re-export
      unchanged) — spec: `result-types`
- [x] 2.2 Add frozen `CommitIntent` dataclass (`disk`, `snapshots: list[str]`, `base`,
      `started_ts`) to `qsnap/models/` with `__all__` re-export — spec: `commit-intent-journal`
- [x] 2.3 Add abstract method `run_with_heartbeat(cmd, timeout, heartbeat_seconds,
      on_heartbeat, check=False) -> ShellResult` to `IShell` in `qsnap/interfaces/shell.py`
      with the exact semantics of spec `shell-abstraction` (hard timeout, continuous pipe
      draining, heartbeat callback) — **BREAKING**
- [x] 2.4 Add abstract methods `set_commit_in_progress`, `get_commit_in_progress`,
      `clear_commit_in_progress` to `IStateManager` in `qsnap/interfaces/state.py` —
      **BREAKING** — spec: `commit-intent-journal`
- [x] 2.5 Add additive keyword parameter `timeout: int = 1800` to
      `ILifecycleManager.blockcommit` in `qsnap/interfaces/lifecycle.py` — spec:
      `lifecycle-manager`

## 3. Infrastructure Implementations

- [x] 3.1 Implement `SubprocessShell.run_with_heartbeat` in `qsnap/shell/subprocess_shell.py`:
      Popen + poll loop in `heartbeat_seconds` slices, daemon reader threads draining
      stdout/stderr continuously, hard kill at `timeout` returning
      `ShellResult(success=False, returncode=-1, error="Command timed out after {timeout}s")`,
      structured logging like `run()` — spec: `shell-abstraction`
- [x] 3.2 Implement the intent journal in `qsnap/state/json_manager.py`: persist
      `commit_in_progress` list under the per-VM state via the existing atomic tmp+`os.replace`
      save; missing key loads as empty list; upsert semantics per disk — specs:
      `state-management`, `commit-intent-journal`
- [x] 3.3 Add `blockcommit_timeout: int = 1800` to `GlobalConfig` in `qsnap/models/config.py`,
      parse it from `[global]` in `ConfigFacade` with validation (positive integer; clear error
      naming the option otherwise), and document it in `qsnap.toml.example` — spec: `config-model`

## 4. Lifecycle Managers

- [x] 4.1 Rework `BlockCommitManager.blockcommit` (`qsnap/modules/lifecycle/blockcommit_manager.py`):
      accept `timeout: int = 1800`, execute the virsh command via `run_with_heartbeat`
      (heartbeat 60 s, callback logs VM/disk/snapshot/elapsed), map exit-0 → `outcome="success"`,
      non-zero → `outcome="failure"`, timeout/kill → `outcome="unknown"`; keep MAC detection and
      short-circuit behavior — specs: `lifecycle-manager`, `commit-observability`
- [x] 4.2 Update `QemuImgCommitManager.blockcommit` (`qsnap/modules/lifecycle/qemu_img_commit.py`):
      accept `timeout: int = 1800` and use it for the `qemu-img commit` call (was hard-coded
      3600); timeout maps to `outcome="unknown"` — spec: `lifecycle-manager`

## 5. Core Orchestration

- [x] 5.1 Add the shared block-job probe helper `_probe_blockjob(vm_config, disk)` returning
      `"none" | "active" | "error"` (30 s timeout; parse "No current block job" → none); refactor
      the existing backup-path probe onto it without behavior change — spec: `blockjob-protocol`
- [x] 5.2 Pre-commit probe in `_blockcommit_one_disk`: only `"none"` proceeds; `"active"` + intent
      record → reconciliation; `"active"` without intent → defer reason `"blockjob_active"`;
      `"error"` → defer reason `"vm_state_unknown"` — spec: `blockjob-protocol`
- [x] 5.3 Pre-snapshot probe in `_execute_snapshot_steps` for running VMs: any `"active"`/`"error"`
      skips snapshot creation for the VM this run (WARNING, baseline untouched, no deferred entry)
      — spec: `blockjob-protocol`
- [x] 5.4 Write/clear the intent journal around commits (main path and deferred drain):
      `set_commit_in_progress` before the manager call; on success order `set_last_commit_ts` →
      `remove_snapshot`(s) → `clear_commit_in_progress`; definitive failure clears intent before
      classification; `unknown` keeps intent until reconciliation finalizes — specs:
      `commit-intent-journal`, `core-orchestrator`
- [x] 5.5 Implement `_reconcile_commit_outcome(vm_config, disk, base_image, snapshots)` returning
      `late_success | job_active | failure | inconclusive` per spec `commit-reconciliation`
      (blockjob probe first; file existence AND chain-length must agree; contradictions →
      inconclusive)
- [x] 5.6 Dispatch `unknown` outcomes through reconciliation in `_blockcommit_one_disk`:
      `late_success` → WARNING + state convergence + continue; `job_active` → defer
      `"blockjob_active"` + continue; `failure` → clear intent + `RuntimeError` with
      `virsh blockjob`/libvirtd-journal hint; `inconclusive` → defer `"vm_state_unknown"` —
      spec: `commit-reconciliation`
- [x] 5.7 Step-0 crash recovery of stale intent records (with the deferred-operations check):
      probe → live job keeps intent + defers; no job → reconcile (converge late success /
      clear no-effect with WARNING / keep on inconclusive); dry-run predicts only — spec:
      `commit-intent-journal`
- [x] 5.8 Fail-closed offline race guard: `domstate` re-check failure before
      `QemuImgCommitManager` defers with reason `"vm_state_unknown"` (never proceed) — spec:
      `core-orchestrator` "Fail-closed offline race guard"
- [x] 5.9 Pass `GlobalConfig.blockcommit_timeout` as `timeout=` on every lifecycle-manager
      invocation (main path + drain); remove any hard-coded commit timeout — spec:
      `core-orchestrator` "Configurable commit timeout pass-through"
- [x] 5.10 Make `_find_broken_chain_file` bound dynamic: `max(64, measured_chain_length + 2)`
      (measured from the failing scan's parsed chain, else state count + 8) — spec:
      `blockcommit-recovery`
- [x] 5.11 Observability: INFO intent line before every commit (`[blockcommit] {vm}/{disk}:
      committing N snapshot(s) into {base} (mode=..., timeout=...s)`) on main and drain paths;
      WARNING/ERROR lines per reconciliation and recovery outcome — spec: `commit-observability`

## 6. Testing

MANDATE: the main programmer agent MUST pass the full `TESTING.md` document (repo root) to
EVERY @Mr.Tester subagent it delegates to, together with the group's scope and scenario list.
Testers write/fix ONLY the tests of their group; they report source bugs but do not fix them.
Launch all group delegations IN PARALLEL (single message).

- [x] 6.1 Read `test-plan.md` — Delegation Groups and Coverage Map sections
- [x] 6.2 Delegate group `shell-models-unit` to @Mr.Tester (scope: `tests/utils/test_shell.py`,
      `tests/models/test_results.py`) — pass `TESTING.md` + the group's scenarios
- [x] 6.3 Delegate group `lifecycle-managers-unit` to @Mr.Tester (scope:
      `tests/modules/lifecycle/`) — pass `TESTING.md`; include the deletions from test-plan.md
      "Tests To Delete" (stale `test_blockcommit_timeout`, `_DOMBLKLIST_OUTPUT` + 14 domblklist
      expectation blocks, old `CountingShell`)
- [x] 6.4 Delegate group `state-config-unit` to @Mr.Tester (scope: `tests/state/`,
      `tests/config/`) — pass `TESTING.md` + the group's scenarios
- [x] 6.5 Delegate group `core-orchestration-unit` to @Mr.Tester (scope: `tests/core/`,
      `tests/conftest.py`) — pass `TESTING.md`; include the synthetic-libvirt-output cases from
      test-plan.md "Integration & Synthetic Test Program (a)"
- [x] 6.6 Delegate group `mocks-contracts` to @Mr.Tester (scope: `tests/interfaces/`,
      `tests/mocks/`) — pass `TESTING.md`; mocks must implement the new `IShell` /
      `IStateManager` / `ILifecycleManager` members and pass ABC isinstance checks
- [x] 6.7 Delegate group `integration-stress` to @Mr.Tester (scope: `tests/integration/`,
      `tests/stress/`) — pass `TESTING.md`; include the NEW `test_commit_intent_recovery.py`
      program and the updates to `test_blockcommit_defer.py` / `test_long_chain.py` from
      test-plan.md section (b); these run only where real libvirt is available
      (`@pytest.mark.integration` / `@pytest.mark.stress`)
- [x] 6.8 Review all @Mr.Tester reports; fix any source-level bugs discovered (source fixes are
      owned by the programmer, not the testers)
- [x] 6.9 Re-delegate any groups affected by source fixes (again with `TESTING.md` attached)
- [x] 6.10 Verify all groups pass and coverage matches `test-plan.md`: every Coverage Map row
      has a real, passing test

## 7. Final Verification

- [x] 7.1 Run the full non-hardware suite: `poetry run pytest -m "not integration and not stress and not e2e"` — all green (1931 passed, 0 failed)
- [x] 7.2 Run integration + stress suites where libvirt is available; document any environment skips — 179 passed, 2 skipped (pre-existing: lockfile-concurrency + libvirt-version skip) on libvirt 12.6.0 / qemu-img 11.0.3
- [x] 7.3 Run `poetry run ruff check qsnap tests` and `poetry run ruff format --check qsnap tests`; fix violations — 0 new violations (remaining 2 lint errors + 3 format files are pre-existing in untouched files: bitmap.py, mock_nbd.py, test_bitmap_recovery.py)
- [x] 7.4 Run `poetry run pyright qsnap` (strict) — no new errors (11 pre-existing, unchanged from baseline)
- [x] 7.5 Run `openspec validate harden-blockcommit-races` — change remains valid
- [x] 7.6 Manual smoke on a real host: `qsnap run --dry-run` shows the new predictions; a real
      run logs the `[blockcommit] ... committing ...` intent line and (if a commit runs) the
      heartbeat/merged lines; state file shows `commit_in_progress` only transiently —
      covered by real-libvirt integration tests (`test_real_blockcommit_produces_success_outcome`,
      `test_intent_journal_survives_real_run`, `test_stale_intent_real_recovery_converges_state`,
      `test_active_foreign_blockjob_defers` + dry-run parity tests)

## 8. Post-verification hardening (spec-vs-code audit fixes)

- [x] 8.1 C1: empty-list no-op returns `outcome="success"` in both managers
      (`blockcommit_manager.py`, `qemu_img_commit.py`); enforce the
      `success=True ⇒ outcome="success"` invariant in `CommitResult.__post_init__`;
      tests: outcome assertion in `test_blockcommit_empty_list_no_op`, new
      `test_qemu_img_commit_empty_list_no_op`, new invariant test in `test_results.py`
- [x] 8.2 W1: `_reconcile_commit_outcome` gains `chain_length_before` — quantitative
      file/chain-length agreement on the dispatch path (late_success requires the chain
      to shrink by exactly the merge-set size, failure requires an unchanged length,
      mismatch → inconclusive); crash recovery keeps baseline-less classification.
      Specs/design amended accordingly; tests updated + 2 new disagreement tests
- [x] 8.3 W2: amend `blockjob-protocol` / `core-orchestrator` specs + design D6 — the
      pre-commit probe is gated on the live (`virsh`) executor path (it errors on
      inactive domains); the offline path relies on the D7 domstate re-check
- [x] 8.4 W3: fail-closed domstate re-check added to the deferred-queue drain path
      (before the intent write), mirroring the main path; spec requirement amended to
      name both call sites; new test `test_drain_qemu_img_domstate_recheck_failure_keeps_queued`
- [x] 8.5 W4: "add/refresh" semantics — `_queue_deferred_once` skips duplicate deferred
      entries during intent recovery; new test
      `test_stale_intent_live_job_does_not_duplicate_deferred`
- [x] 8.6 W5 coverage gaps closed: drain probe-active re-queue
      (`test_drain_probe_active_requeues_without_commit`), drain unknown outcome
      (`test_drain_unknown_outcome_keeps_intent_and_requeues`), recovery probe-error
      (`test_stale_intent_probe_error_defers_vm_state_unknown`), recovery inconclusive
      (`test_stale_intent_inconclusive_defers_vm_state_unknown`), dry-run recovery
      (`test_stale_intent_recovery_dry_run_writes_nothing`), JsonStateManager reset
      intent clearing (`test_reset_vm_state_clears_commit_intents`,
      `test_reset_vm_disk_state_clears_only_that_disks_intent`)
- [x] 8.7 S1: real `run_with_heartbeat` reports elapsed at callback time (~60s, ~120s)
      instead of lagging one slice behind (parity with MockShell + observability spec)
- [x] 8.8 S2: `deep_verify_base_image` receives the injected timeout (no hard-coded
      3600 left in the commit path); managers pass their `timeout` through; new test
      `test_blockcommit_deep_verify_injected_timeout_honored`
- [x] 8.9 S3: documented why the already-converged recovery branch does not rewrite
      `last_commit_ts` (marker already persisted by the success ordering; rewriting
      could falsify the G1 gate)
- [x] 8.10 S4: `run_with_heartbeat(check=True)` logging tested (DEBUG not ERROR;
      check=False logs ERROR)
- [x] 8.11 Re-run full verification: non-hardware suite 1947 passed / 0 failed;
      integration+stress 179 passed / 2 pre-existing skips; ruff/pyright at baseline
      (2 + 11 pre-existing errors, 0 new); `openspec validate` passes
