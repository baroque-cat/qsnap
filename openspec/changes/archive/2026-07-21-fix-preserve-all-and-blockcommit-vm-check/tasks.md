## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fix-preserve-all-and-blockcommit-vm-check`
- [x] 1.2 Verify all existing tests pass before starting: run the full test suite `poetry run pytest tests/ -m "not integration and not stress and not e2e" -q`

## 2. Fix `_parse_preserve("all")` (Design D1)

**Specs:** `retention-engine`, `preserve-min-config` (delta specs)
**Design:** D1 — Add `"all"` to `_parse_preserve()` early-return

- [x] 2.1 In `qsnap/core/__init__.py`, locate the `_parse_preserve()` static method (around line 2755-2813). Add `elif preserve_str == "all": effective_min = "all"` to the `effective_min` cascade, between the `elif preserve_str == "latest"` and the `else` clauses.
- [x] 2.2 In the same method, update the early-return guard at the next line: change `if preserve_str is None or preserve_str == "latest":` to `if preserve_str is None or preserve_str in ("latest", "all"):`.
- [x] 2.3 Run existing preserve tests: `poetry run pytest tests/core/test_preserve.py -v` — all existing tests must still pass (no regressions).

## 3. Add VM State Check to `_blockcommit_snapshots()` (first-iteration guard — superseded by §5)

**Specs:** `core-orchestrator`, `deferred-operations` (delta specs)
**Design:** D2 (first iteration) — Defer blockcommit for running VMs; D3 — Deferral at Core level

- [x] 3.1 In `qsnap/core/__init__.py`, locate `_blockcommit_snapshots()` (around line 2067-2242). After the `--preserve-snapshots` and `--dry-run` guards (around line 2103-2110), add a VM state check: run `virsh domstate --domain <vm_name>` via `self._shell.run(cmd, timeout=30)`; if successful and stdout (stripped, lowercased) does NOT contain `"shut off"`, defer via `self._state.add_deferred_blockcommit(vm_config.name, [s.name for s in to_merge], "vm_running")` + INFO log + return; on domstate failure proceed (non-fatal fallback).
- [x] 3.2 Verify the placement: the VM state check MUST be after `--preserve-snapshots` and `--dry-run` guards but BEFORE the pre-commit chain verification.
- [x] 3.3 Verify that `_check_deferred_operations()` (around line 1708-1782) still works correctly — it uses the same `virsh domstate` check.

## 4. Testing (phase 1 — first-iteration fixes)

**CRITICAL — Test delegation protocol:**
The main programmer agent MUST delegate ALL test writing/modification to @Mr.Tester subagents. The programmer SHALL NOT write tests directly. For EACH delegation group below, the programmer MUST:

1. Launch a @Mr.Tester subagent with the group's scope and scenario list
2. **MANDATORY**: Pass the file `/home/openuser/vm/qsnap/TESTING.md` to each @Mr.Tester as essential context — it defines the testing philosophy, categories, mock patterns, directory structure, and rules
3. **MANDATORY**: Inform each @Mr.Tester that a real libvirt/virsh/qemu environment is available (libvirt 12.5.0, QEMU 11.0.2) for integration tests — they can use the existing `test_vm` fixture from `tests/integration/conftest.py`
4. **MANDATORY**: Instruct each @Mr.Tester to not only write NEW tests but also identify OUTDATED tests that may need modification or deletion due to the source changes, and include those in their report
5. Launch ALL groups IN PARALLEL (single message, multiple @Mr.Tester calls)
6. After all testers return: fix any reported source bugs, re-delegate affected groups
7. Repeat until all groups pass

**Reference:** Read `test-plan.md` Delegation Groups section for full details.

- [x] 4.1 Read `test-plan.md` Delegation Groups section
- [x] 4.2 Delegate group `G5-CONFTEST` to @Mr.Tester (scope: `tests/conftest.py`). Added `virsh domstate → "shut off"` expectation to `_setup_validation_expectations()`.
- [x] 4.3 Delegate group `G1-PARSE` to @Mr.Tester — 9 new tests, 24/24 pass.
- [x] 4.4 Delegate group `G2-PIPELINE` to @Mr.Tester — 6 new tests + `_set_vm_state` → `expect_first` fix + 1 outdated test corrected; 80/80 pass.
- [x] 4.5 Delegate group `G3-STATE` to @Mr.Tester — 1 new test (`vm_running` reason), 38/38 pass.
- [x] 4.6 Delegate group `G4-INTEGRATION` to @Mr.Tester — 4 new integration tests, all pass on real libvirt.
- [x] 4.7 Review all @Mr.Tester reports — outcome: G4's report + empirical verification uncovered Bugs #3/#4 and the first-iteration regression → design revised (see design.md "Empirical findings"), scope expanded into sections 5–9.
- [x] 4.8 Re-delegation of affected groups — superseded by the phase-2 delegation in §7 (the revised design changes test semantics).
- [x] 4.9 Verify all unit tests pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -q` — 1119 passed.
- [x] 4.10 Verify integration tests pass: `poetry run pytest tests/integration/ -m integration -q` — 4 passed.
- [x] 4.11 Verify coverage matches `test-plan.md` — deferred to §9.7 (test-plan revised). — superseded by 9.7, done.

## 5. Adaptive Lifecycle Fork in Core (Design D2-revised, D5, D6)

**Specs:** `core-orchestrator`, `deferred-operations` (delta specs)
**Design:** D2 (fork matrix, split rule, race guard), D5 (unconditional state cleanup), D6 (adaptive drain)

- [x] 5.1 Add the shared MAC-denial helper: move `_detect_mac_denial()` from `qsnap/modules/lifecycle/blockcommit_manager.py` to a new `qsnap/utils/mac.py` and update `BlockCommitManager` to import it. Behavior of `BlockCommitManager` MUST NOT change (its 13 existing tests must pass unmodified).
- [x] 5.2 In `qsnap/core/__init__.py`, add the frozen `_CommitPlan` dataclass (module-private): fields `committable: list[SnapshotInfo]`, `deferrable: list[SnapshotInfo]`, `effective_mode: str | None`, `defer_reason: str | None`.
- [x] 5.3 Implement `Core._plan_blockcommit(vm_config, candidates) -> _CommitPlan | None` per design D2: `virsh domstate` (timeout 30; failure → `None` legacy fallback); active-layer path via `virsh domblklist` + `parse_domblklist_path()` (failure → newest `IStateManager` snapshot + WARNING); fork matrix (running+virsh → split, running+qemu-img → defer all, shut off → split excluding XML-tip with reason `"active_layer"`, paused/other → defer all).
- [x] 5.4 Rewrite the first-iteration guard in `_blockcommit_snapshots()` (added in §3.1) into the full fork: call `_plan_blockcommit()`; `None` → legacy path (configured mode, full set); defer `plan.deferrable` with `plan.defer_reason` + INFO log; if `plan.committable` empty → return; when `effective_mode == "qemu-img"` re-check `virsh domstate` immediately before invoking the manager (race guard → defer with `"vm_running"` if no longer shut off); use `factory.create_lifecycle_manager(mode=plan.effective_mode)`.
- [x] 5.5 Implement D5: after a successful commit in `_blockcommit_snapshots()`, call `self._state.remove_snapshot(vm_config.name, sn.name)` for every committed snapshot UNCONDITIONALLY (move it out of the `chain_verify_after_commit` branch). Keep `ActionRecord("snapshot_delete")` per committed snapshot.
- [x] 5.6 Rewrite `_check_deferred_operations()` per D6: per queue entry, call `_plan_blockcommit()` on the entry's snapshots (resolved via `IStateManager`); `None` or nothing committable → keep entry; commit the committable subset with `deep_verify=vm_config.blockcommit_deep_verify`; on success `remove_snapshot()` for committed names; re-queue the remainder (tip/active) with the entry's ORIGINAL reason; drop stale entries (unchanged). qemu-img mode + running VM and paused VM → skip all entries (unchanged conservative behavior).
- [x] 5.7 Verify placement invariants: `--preserve-snapshots` and `--dry-run` guards still run before any `virsh` calls; pre-commit chain verification (`chain_verify_before_commit`) still runs before every commit; post-commit verification (`chain_verify_after_commit`) still runs after every successful commit in both branches.
- [x] 5.8 Run unit tests: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -q` — expect failures ONLY in tests listed for revision in test-plan Step 2 (they are updated in §7). — 17 failed / 1102 passed; all failures in Step 2 categories (drain tests needing conftest domblklist default, 3 G2 revisions, stale-guard/chain-verify tests).

## 6. QemuImgCommitManager Offline Algorithm (Design D4)

**Specs:** `lifecycle-manager` (delta spec)
**Design:** D4 — commit → pivot → explicit delete; invariants; MAC parity

- [x] 6.1 In `qsnap/modules/lifecycle/qemu_img_commit.py`, reimplement the per-snapshot loop (oldest first): `qemu-img commit -b <base_image> <si.path>` → child discovery (scan `vm_config.snapshot_dir` for `*.qcow2`, `qemu-img info --output=json` each, match resolved `backing-filename` to `si.path`) → if child: `qemu-img rebase -u -F qcow2 -b <base_image> <child>` → `rm -f <si.path>` (only after successful rebase or when no child exists).
- [x] 6.2 Implement short-circuit semantics: any step failure → no deletion, no further iterations, return `CommitResult(success=False, committed_snapshot=<failing name>, error=<stderr>)`.
- [x] 6.3 Add MAC-denial detection using the shared `qsnap/utils/mac.py` helper; return `error="blocked by apparmor|selinux"` with empty `committed_snapshot` on MAC denial (parity with `BlockCommitManager`).
- [x] 6.4 Keep `deep_verify` behavior (`qemu-img check --output=json <base_image>` after success) and the empty-list no-op behavior unchanged.
- [x] 6.5 Do NOT change `ILifecycleManager` or `BlockCommitManager` beyond the import in 5.1.

## 7. Testing (phase 2 — adaptive fork)

Same delegation protocol as §4 (all work via @Mr.Tester, TESTING.md mandatory, real libvirt available, parallel launch, outdated-test reporting).

- [x] 7.1 Delegate `G5-CONFTEST` revision: add default `virsh domblklist` expectation to `_setup_validation_expectations()` (table format parseable by `parse_domblklist_path()`; source = newest fixture snapshot path). Prerequisite for 7.2–7.4. — done: source = `/var/lib/libvirt/snapshots/testvm/snap4.qcow2` (newest across chain fixtures).
- [x] 7.2 Delegate `G2-PIPELINE` revisions: revise `test_blockcommit_deferred_when_vm_running` → `test_blockcommit_live_commit_when_vm_running`; revise `test_blockcommit_executes_when_vm_shut_off` (assert `mode="qemu-img"` executor + state removal); revise `test_deferred_blockcommit_executed_after_vm_shutdown` (qemu-img drain). Re-verify the 3 unchanged tests. — done, all pass; follow-up round fixed 4 more (`expect`→`expect_first` ×2, D5 assertion updates ×2) + 2 new D8 XML-refresh tests.
- [x] 7.3 Delegate `G6-FORK` (6 NEW): active-layer deferral on running VM, qemu-img-mode blanket deferral, XML-tip exclusion with `"active_layer"` reason, race guard, unconditional state removal without post-verify, domblklist fallback heuristic. — done, 6/6 pass in new `tests/core/test_lifecycle_fork.py`.
- [x] 7.4 Delegate `G8-DRAIN` (6 NEW + review of existing drain tests per test-plan Step 2.2): qemu-img executor on shut-off, tip remainder re-queued with original reason, running+virsh drain of non-active entries, running+qemu-img skip, paused skip, state removal after drain. — done, 23/23 pass (6 new + 5 existing rewired with domblklist expectations).
- [x] 7.5 Delegate `G7-QEMUIMG` (6 NEW): pivot+delete command order, no-child skip, commit-failure short-circuit (no rm), rebase-failure keeps file, MAC apparmor/selinux, deep_verify. — done, 29/29 pass (incl. rewired `test_qemu_img_commit_success`).
- [x] 7.6 Delegate `G3-STATE` addition (1 NEW): `test_add_deferred_blockcommit_active_layer_reason`. — done, 39/39 pass.
- [x] 7.7 Delegate `G4-INTEGRATION` revisions (3 MODIFY + 1 NEW): live commit while running (chain shortens, VM healthy), active-layer deferral without errors, strengthened post-shutdown drain (files deleted, chain shortened, `virsh start` succeeds), XML-tip exclusion keeps domain bootable. — done, 4/4 pass on real libvirt; G4 uncovered the stale-XML-`<backingStore>` bootability bug → design D8 added; workaround removed after source fix.
- [x] 7.8 Review all phase-2 @Mr.Tester reports; fix any source-level bugs discovered; re-delegate affected groups until green. — D8 `_refresh_domain_backing_store()` implemented in Core (main path + drain), design/specs updated; G2 follow-up (4 fixes + 2 D8 tests, 65/65 pass) and G4 follow-up (workaround removed, `virsh start` asserted directly) both green.
- [x] 7.9 Verify full unit suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -q` — 1142 passed.
- [x] 7.10 Verify integration suite: `poetry run pytest tests/integration/ -m integration -q` — 34 passed, 1 skipped.

## 8. Spec Sync & Documentation

- [x] 8.1 Update `qsnap.toml.example`: document adaptive `lifecycle_mode` semantics — `"virsh"` (default; live commits for non-active layers while running, offline commits via qemu-img when shut off) and `"qemu-img"` (offline-only; defers while running).
- [x] 8.2 Update `README.md`: adaptive lifecycle behavior, `"vm_running"`/`"active_layer"` deferral reasons, XML-tip exclusion rule, and a note that `preserve = "all"` was broken before this change (already-lost backups are not recoverable). — new "Snapshot Lifecycle (Blockcommit)" section.
- [x] 8.3 Sync delta specs to main specs: run `openspec sync` or manually update `openspec/specs/` with the delta changes. — synced all 5 capabilities (core-orchestrator, deferred-operations, lifecycle-manager, preserve-min-config, retention-engine) incl. D8 XML-refresh content.
- [x] 8.4 Verify the change is complete: `openspec status --change fix-preserve-all-and-blockcommit-vm-check` — 5/5 artifacts complete.
- [x] 8.5 Run `openspec validate` to ensure spec consistency. — all 5 change-touched specs valid (added missing `## Purpose` sections + scenarios for pre-existing gaps in core-orchestrator/retention-engine); 19 remaining failures are pre-existing in specs untouched by this change.
- [x] 8.6 Update `AGENTS.md` if any paradigm-level changes were made (expected: none — no ABC changes; the fork is Core-internal orchestration). — confirmed none; AGENTS.md unchanged.

## 9. Final Verification

- [x] 9.1 Run the complete test suite: `poetry run pytest tests/ -m "" -q` — 1176 passed, 5 skipped. (Initial run failed 147× with `OSError: Disk quota exceeded` — root cause: `/tmp` tmpfs full from stale `pytest-of-openuser` dirs (~3.0G qcow2 artifacts); cleaned, re-ran green.)
- [x] 9.2 Run ruff linter: `poetry run ruff check qsnap/ tests/` — clean (SIM105 fixed in core; 7 autofixes in test files).
- [x] 9.3 Run ruff formatter: `poetry run ruff format --check qsnap/ tests/` — clean (9 files reformatted; suite re-verified afterwards: 1176 passed).
- [x] 9.4 Run pyright type checker: `poetry run pyright qsnap/` — 0 errors, 0 warnings.
- [x] 9.5 Verify no Cyrillic characters in source files: `grep -rnP '[\x{0400}-\x{04FF}]' qsnap/ tests/` — 0 matches.
- [x] 9.6 Manual smoke test on a real VM with `preserve = "all"`: (a) backups NOT deleted; (b) with VM running — old snapshots committed live, active layer deferred without errors; (c) after `virsh shutdown` — deferred entries drain via qemu-img, files deleted, chain shortened, `virsh start` succeeds; (d) deferred queue empty at the end. — verified on real libvirt 12.5.0 / QEMU 11.0.2 via the integration suite: (a) `test_preserve_all_keeps_all_backups_integration`, (b) `test_live_commit_non_active_while_running_integration` + `test_active_layer_deferred_running_integration`, (c) `test_deferred_blockcommit_executes_after_shutdown_integration` (files deleted, chain shortened, `virsh start` succeeds after D8 XML refresh), (d) queue asserted empty in drain tests; full suite green (§9.1).
- [x] 9.7 Verify coverage matches `test-plan.md` — every spec scenario has at least one test (supersedes 4.11). — all 27 coverage-map test names verified present, plus 2 D8 XML-refresh tests added during implementation.
