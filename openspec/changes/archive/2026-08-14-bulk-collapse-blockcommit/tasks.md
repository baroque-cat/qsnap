# Tasks: bulk-collapse-blockcommit

References: `proposal.md` (what/why), `design.md` (decisions D1–D9), `specs/*/spec.md`
(normative requirements), `test-plan.md` (coverage map, delegation groups, delete list,
implementer notes). ALL code, comments, docstrings, and log strings MUST be in English.

## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b bulk-collapse-blockcommit`
- [x] 1.2 Verify all existing tests pass before starting: run the full test suite
      (`poetry run pytest tests/ -m "not integration and not stress and not e2e"`)

## 2. Config layer — remove the cap, reject the legacy key

Specs: `config-model` delta. Design: D6. Test group: `config-model`.

- [x] 2.1 Remove the `max_commits_per_run` field from `GlobalConfig` in `qsnap/models/config.py`
      (including its comment block) — no replacement field.
- [x] 2.2 Remove the key from `_GLOBAL_KEYS` / parsing in `qsnap/config/facade.py` and add an
      EXPLICIT rejection branch: any config containing `max_commits_per_run` (global OR VM
      section) raises `ConfigError` naming the removed option and stating that the collapse is
      now a single uncapped bulk blockcommit. Do NOT reuse the generic "Unknown key" wording
      (see test-plan Notes #3; `tests/config/test_unknown_keys.py` forbids it on fixtures).
- [x] 2.3 Update `qsnap.toml.example`: delete the `max_commits_per_run` stanza and the
      multi-run convergence math; document the single-shot collapse (trigger H, floor L, one
      segment job, lock contention of overlapping hourly runs resolves via exit code 3).

## 3. State layer — remove the collapse phase

Specs: `state-management` delta (REMOVED). Design: D5. Test group: `state-management`.

- [x] 3.1 Delete `set_collapse_in_progress`, `clear_collapse_in_progress`, and the phase reader
      from `qsnap/interfaces/state.py` (`IStateManager`).
- [x] 3.2 Delete their implementations and any `collapse_in_progress` reads/writes from
      `qsnap/state/json_manager.py`. IMPORTANT: `reset_vm_state` / `reset_vm_disk_state` must
      STOP touching the key entirely — a stale persisted key must survive resets untouched
      (test-plan Note #6 pins this).
- [x] 3.3 No migration code: JSON readers already tolerate unknown keys; document this in the
      affected docstrings.

## 4. Lifecycle manager — single-segment bulk commit

Specs: `lifecycle-manager` delta. Design: D1, D2, D3. Test group: `lifecycle-manager`.

- [x] 4.1 Rewrite `BlockCommitManager.blockcommit()` in `qsnap/modules/lifecycle/blockcommit_manager.py`:
      empty list → no-op success; otherwise ONE command
      `virsh blockcommit --domain <vm> --path {disk} --base {base_image} --top {snapshots_to_merge[-1].path} --delete --verbose --wait`
      via `run_with_heartbeat`; assert non-empty input; document the oldest-first ordering
      contract (`top` = newest removable). Delete the per-snapshot loop.
- [x] 4.2 Outcome mapping unchanged: MAC denial → `failure` ("blocked by apparmor|selinux"),
      timeout/killed → `unknown`, other non-zero → `failure`, exit 0 → `success` with
      `committed_snapshot` = newest merged snapshot name. Heartbeat logs batch-level wording
      (group count + elapsed), design D9.
- [x] 4.3 `QemuImgCommitManager` stays per-layer (no segment mode) — verify it simply receives
      the full uncapped list; no behavioral change beyond what Core passes.
- [x] 4.4 `ILifecycleManager` signature and `DefaultFactory.create_lifecycle_manager` remain
      unchanged (design D1) — confirm no factory branch additions.

## 5. Core — single-shot collapse wiring

Specs: `core-orchestrator`, `hysteresis-retention`, `dry-run-prediction`,
`commit-observability` deltas. Design: D4, D5, D7, D9. Test groups: `hysteresis-core`,
`commit-core`, `retention-engine`.

- [x] 5.1 Delete `_apply_commit_cap` and every `max_commits_per_run` reference from
      `qsnap/core/__init__.py`.
- [x] 5.2 Rewrite the hysteresis branch of `_evaluate_disk_retention`: read N; `N ≤ H` → empty
      remove set; `N > H` → engine with keep-count L + oldest-prefix filter + preserve_min trim
      → FULL `N − L` set. No phase reads/writes anywhere. Steady mode untouched.
- [x] 5.3 Remove all `collapse_in_progress` usage: phase persistence before commit, post-commit
      phase-convergence block in `_dispatch_commit_outcome`, defensive clears, startup/recovery
      touches. Crash recovery relies solely on the commit intent journal.
- [x] 5.4 Scaled timeout: pass `blockcommit_timeout × len(committable)` to the manager on the
      live path (offline keeps the unscaled per-layer budget); show the scaled value in the
      intent line.
- [x] 5.5 Baseline dedup: extend the pre-commit flow so `chain_length_before` comes from the
      integrity scan result (additive `chain_length` on the scan/verify result); fall back to
      `_get_chain_length` only when verification is disabled or the scan carries no length.
      Post-commit measurement stays a fresh walk.
- [x] 5.6 Log wording per design D9: `[retention] … collapse triggered (N=…, merging …,
      floor=…)` initiation line; `[blockcommit] … collapsing N snapshot(s) into …` intent line;
      heartbeat `still collapsing N layer(s) into base` (singular for N=1); success line switches `merged` →
      `collapsed`. Per-snapshot `ActionRecord(snapshot_delete)` rows KEPT.
- [x] 5.7 Dry-run predictions: full uncapped oldest `N − L` set named, "single bulk
      blockcommit" phrasing, no phase-key reads; zero-mutation invariant unchanged.
- [x] 5.8 Reconciliation untouched except documentation: the partial-prefix branch remains for
      the OFFLINE path only (test-plan Note #7) — do not delete it.

## 6. Testing

ORCHESTRATION RULES (binding):

- The LEAD PROGRAMMER AGENT MUST NOT write the test suites itself. It MUST delegate every
  group below to a dedicated @Mr.Tester subagent, launching all independent groups IN
  PARALLEL (one message, multiple task calls) after the `shared-fixtures` pre-step completes.
- With EVERY delegation the lead programmer MUST attach the project testing doctrine: the file
  `/home/openuser/vm/qsnap/TESTING.md` (philosophy, directory layout, categories, rules,
  checklist) plus the instruction to follow it strictly, and the relevant slice of
  `test-plan.md` (group scope + its Coverage Map rows + applicable Tests-To-Delete entries).
- Testers write/fix ONLY their group's tests; they report source bugs instead of fixing them.
- Production code and test code stay in English.

Tasks:

- [x] 6.1 Read `test-plan.md`: Delegation Groups, Coverage Map, Tests To Delete, and Notes for
      the Implementer.
- [x] 6.2 Execute pre-step group `shared-fixtures` (delegate to @Mr.Tester with TESTING.md):
      shared mocks/fixtures updates everything else depends on (`tests/mocks/mock_state.py`
      phase-method removal, `make_vm_config`/fixture fallout).
- [x] 6.3 Apply the Tests-To-Delete list from `test-plan.md` (exact function/file inventory) —
      deletions precede group work so parallel testers start from a clean tree.
- [x] 6.4 Delegate group `config-model` to @Mr.Tester (scope per test-plan; attach TESTING.md).
- [x] 6.5 Delegate group `state-management` to @Mr.Tester (attach TESTING.md).
- [x] 6.6 Delegate group `lifecycle-manager` to @Mr.Tester (attach TESTING.md).
- [x] 6.7 Delegate group `hysteresis-core` to @Mr.Tester (attach TESTING.md).
- [x] 6.8 Delegate group `commit-core` to @Mr.Tester (attach TESTING.md).
- [x] 6.9 Delegate group `retention-engine` to @Mr.Tester (attach TESTING.md).
- [x] 6.10 Delegate group `models-contracts` to @Mr.Tester (attach TESTING.md).
- [x] 6.11 Delegate group `integration-hysteresis` to @Mr.Tester (attach TESTING.md) — includes
      UPDATING existing integration tests to the new single-job behavior and the new real-libvirt
      error-classification tests (invalid `--top`, active-layer `--top`, foreign blockjob,
      mid-job crash recovery).
- [x] 6.12 Delegate group `stress` to @Mr.Tester (attach TESTING.md) — includes rewriting the
      drip-semantics long-chain test to the bulk expectation.
- [x] 6.13 Delegate group `e2e` to @Mr.Tester (attach TESTING.md).
- [x] 6.14 Review all @Mr.Tester reports; fix reported source-level bugs in `qsnap/`.
- [x] 6.15 Re-delegate any groups affected by source fixes (again with TESTING.md attached).
- [x] 6.16 Verify: unit suite green (`poetry run pytest tests/ -m "not integration and not
      stress and not e2e"`), then integration/stress/e2e suites green on a libvirt host;
      coverage matches the `test-plan.md` Coverage Map (every scenario row has a passing test).

## 7. Final verification & close-out

- [x] 7.1 Run `ruff check` / `ruff format --check` and `pyright` clean over the changed files.
- [x] 7.2 Manual smoke on a disposable VM: force `N > H` (small H/L in a test config), observe
      ONE `collapsing N snapshot(s)` job, chain at floor afterwards, `Post-commit chain
      verification passed`, summary shows `---` rows for every merged snapshot.
- [x] 7.3 Confirm `qsnap check` reports no state inconsistencies after the smoke run.
- [x] 7.4 Validate the change artifacts: `openspec validate --change bulk-collapse-blockcommit`.
- [x] 7.5 Archive the change per the archive workflow once implementation and tests are merged.
