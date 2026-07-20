# Test Plan: fix-preserve-all-and-blockcommit-vm-check

## Change Summary

**Original bugs (first iteration, implemented):**
1. **Bug 1 (D1):** `_parse_preserve("all")` → `preserve_min="0h"` instead of `"all"` — silent data loss. ✅ fixed.
2. **Bug 2 (D2-first-iteration):** `_blockcommit_snapshots()` had no VM-state check. ✅ naive guard implemented (defer-all-when-not-shut-off).

**Findings from empirical investigation (this iteration):**
3. **Bug 3:** `virsh blockcommit` cannot run on a shut-off domain (`domain is not running`) — the deferred queue can never drain in virsh mode, and the naive guard regressed the working live-commit path for non-active layers.
4. **Bug 4:** `qemu-img commit -d` is a no-op on QEMU 11.0.2 — `QemuImgCommitManager` never deletes committed files and never pivots children; chains never shorten; state entries never heal.
5. **XML-tip constraint:** the inactive domain XML references the tip overlay; deleting it offline makes the domain unbootable.

**Fixes under test (this iteration):**
- **D2-revised:** adaptive lifecycle fork in Core (`_plan_blockcommit` helper): domstate + domblklist detection, committable/deferrable split, executor selection (`virsh` live / `qemu-img` offline), race re-check before offline commit, deferral reasons `"vm_running"` / `"active_layer"`.
- **D4:** `QemuImgCommitManager` correct offline algorithm: per snapshot — `qemu-img commit -b base` → child discovery → `qemu-img rebase -u -b base <child>` → `rm -f`; short-circuit on failure; MAC detection via shared `qsnap/utils/` helper.
- **D5:** unconditional `remove_snapshot()` after successful commits (main path + drain path).
- **D6:** state-adaptive drain in `_check_deferred_operations()` with partial draining and original-reason re-queue.

---

## Step 1: Coverage Map

| Spec Capability | Scenario | Test File | Test Name | Group | Status |
|---|---|---|---|---|---|
| retention-engine / preserve-min-config | All 9 `_parse_preserve("all")` scenarios | tests/core/test_preserve.py | `test_parse_preserve_all_*` (9 tests) | G1-PARSE | ✅ done |
| core-orchestrator | Non-active snapshots committed live when VM is running | tests/core/test_pipeline.py | `test_blockcommit_live_commit_when_vm_running` | G2-PIPELINE | ⬜ revise (was `test_blockcommit_deferred_when_vm_running`) |
| core-orchestrator | Active layer deferred when VM is running (virsh mode) | tests/core/test_pipeline.py | `test_blockcommit_active_layer_deferred_when_running` | G6-FORK | ⬜ new |
| core-orchestrator | qemu-img mode defers everything when VM is running | tests/core/test_pipeline.py | `test_blockcommit_qemu_img_mode_defers_when_running` | G6-FORK | ⬜ new |
| core-orchestrator | Blockcommit deferred when VM is paused | tests/core/test_pipeline.py | `test_blockcommit_deferred_when_vm_paused` | G2-PIPELINE | ✅ done (semantics unchanged) |
| core-orchestrator | Offline commit via qemu-img when VM is shut off | tests/core/test_pipeline.py | `test_blockcommit_executes_when_vm_shut_off` | G2-PIPELINE | ⬜ revise (assert mode="qemu-img") |
| core-orchestrator | XML-referenced tip excluded from offline commit | tests/core/test_pipeline.py | `test_blockcommit_xml_tip_deferred_active_layer` | G6-FORK | ⬜ new |
| core-orchestrator | VM state check failure is non-fatal (legacy fallback) | tests/core/test_pipeline.py | `test_blockcommit_vm_state_check_failure_non_fatal` | G2-PIPELINE | ✅ done (semantics unchanged) |
| core-orchestrator | Race guard before offline commit | tests/core/test_pipeline.py | `test_blockcommit_race_guard_defers_when_vm_started` | G6-FORK | ⬜ new |
| core-orchestrator | State entries removed unconditionally after commit | tests/core/test_pipeline.py | `test_blockcommit_state_removed_without_post_verify` | G6-FORK | ⬜ new |
| core-orchestrator | domblklist failure falls back to newest state snapshot | tests/core/test_pipeline.py | `test_blockcommit_active_detection_fallback` | G6-FORK | ⬜ new |
| core-orchestrator | preserve="all" with VM running — no blockcommit | tests/core/test_pipeline.py | `test_preserve_all_vm_running_no_blockcommit` | G2-PIPELINE | ✅ done (semantics unchanged) |
| core-orchestrator | Chain verification applies in both branches | tests/core/test_pipeline.py | 17 existing chain-verify/stale-guard tests | (existing) | ⬜ needs domblklist mock (conftest) |
| lifecycle-manager | Live blockcommit success/virsh error/MAC/empty list | tests/modules/lifecycle/test_blockcommit.py | 13 existing tests | (existing) | ✅ no change |
| lifecycle-manager | Offline commit pivots child and deletes file | tests/modules/lifecycle/test_qemu_img_commit.py | `test_qemu_img_commit_pivots_child_and_deletes` | G7-QEMUIMG | ⬜ new |
| lifecycle-manager | Offline commit without child skips rebase | tests/modules/lifecycle/test_qemu_img_commit.py | `test_qemu_img_commit_no_child_skips_rebase` | G7-QEMUIMG | ⬜ new |
| lifecycle-manager | Commit failure short-circuits (no rm, no continue) | tests/modules/lifecycle/test_qemu_img_commit.py | `test_qemu_img_commit_failure_no_delete_short_circuit` | G7-QEMUIMG | ⬜ new |
| lifecycle-manager | Rebase failure prevents deletion | tests/modules/lifecycle/test_qemu_img_commit.py | `test_qemu_img_rebase_failure_keeps_file` | G7-QEMUIMG | ⬜ new |
| lifecycle-manager | MAC denial detection in qemu-img manager | tests/modules/lifecycle/test_qemu_img_commit.py | `test_qemu_img_commit_blocked_by_apparmor`, `test_qemu_img_commit_blocked_by_selinux` | G7-QEMUIMG | ⬜ new |
| lifecycle-manager | deep_verify runs qemu-img check | tests/modules/lifecycle/test_qemu_img_commit.py | `test_qemu_img_commit_deep_verify` | G7-QEMUIMG | ⬜ new |
| deferred-operations | Queue CRUD incl. vm_running reason | tests/state/test_manager.py | existing + `test_add_deferred_blockcommit_vm_running_reason` | G3-STATE | ✅ done |
| deferred-operations | Add deferred blockcommit with active_layer reason | tests/state/test_manager.py | `test_add_deferred_blockcommit_active_layer_reason` | G3-STATE | ⬜ new |
| deferred-operations | Drain on shut-off VM uses qemu-img executor | tests/core/test_deferred.py | `test_drain_shutoff_uses_qemu_img_executor` | G8-DRAIN | ⬜ new |
| deferred-operations | Tip-only remainder stays queued with original reason | tests/core/test_deferred.py | `test_drain_shutoff_tip_remainder_requeued` | G8-DRAIN | ⬜ new |
| deferred-operations | Drain on running VM in virsh mode commits non-active | tests/core/test_deferred.py | `test_drain_running_virsh_mode_commits_non_active` | G8-DRAIN | ⬜ new |
| deferred-operations | No drain on running VM in qemu-img mode | tests/core/test_deferred.py | `test_drain_running_qemu_img_mode_skips` | G8-DRAIN | ⬜ new |
| deferred-operations | Drain paused VM skips | tests/core/test_deferred.py | `test_drain_paused_skips` | G8-DRAIN | ⬜ new |
| deferred-operations | State removal after successful drain | tests/core/test_deferred.py | `test_drain_removes_committed_from_state` | G8-DRAIN | ⬜ new |
| deferred-operations | Deferred executed after VM shutdown (e2e path) | tests/core/test_pipeline.py | `test_deferred_blockcommit_executed_after_vm_shutdown` | G2-PIPELINE | ⬜ revise (qemu-img executor + state removal) |
| (integration) | `_parse_preserve("all")` produces correct policy | tests/integration/test_preserve_all.py | `test_parse_preserve_all_produces_correct_policy` | G4-INTEGRATION | ✅ done |
| (integration) | preserve="all" keeps all backups on real run | tests/integration/test_preserve_all.py | `test_preserve_all_keeps_all_backups_integration` | G4-INTEGRATION | ✅ done |
| (integration) | Live commit of old snapshot while VM runs | tests/integration/test_blockcommit_defer.py | `test_live_commit_non_active_while_running_integration` | G4-INTEGRATION | ⬜ revise (was deferred-assertion) |
| (integration) | Active layer deferred on running VM | tests/integration/test_blockcommit_defer.py | `test_active_layer_deferred_running_integration` | G4-INTEGRATION | ⬜ revise (was blanket deferral) |
| (integration) | Deferred executes after shutdown — files deleted, chain shortened, VM boots | tests/integration/test_blockcommit_defer.py | `test_deferred_blockcommit_executes_after_shutdown_integration` | G4-INTEGRATION | ⬜ strengthen |
| (integration) | XML-tip exclusion keeps domain bootable | tests/integration/test_blockcommit_defer.py | `test_xml_tip_excluded_offline_vm_boots_integration` | G4-INTEGRATION | ⬜ new |

---

## Step 2: Outdated Tests

### 2.1 Tests from the first iteration requiring revision (G2-PIPELINE)

| File | Test Name | Change Needed | Why |
|---|---|---|---|
| `tests/core/test_pipeline.py` | `test_blockcommit_deferred_when_vm_running` | **Revise + rename** to `test_blockcommit_live_commit_when_vm_running`: running VM, `lifecycle_mode="virsh"`, remove set = non-active snapshot → assert factory got `mode="virsh"`, `blockcommit` called, no deferral. | Fork restores live commits for non-active layers; blanket deferral no longer correct. |
| `tests/core/test_pipeline.py` | `test_blockcommit_executes_when_vm_shut_off` | **Revise**: assert factory got `mode="qemu-img"` (offline executor) even though `lifecycle_mode="virsh"`; assert `remove_snapshot` called. | Offline commits always use qemu-img now. |
| `tests/core/test_pipeline.py` | `test_deferred_blockcommit_executed_after_vm_shutdown` | **Revise**: drain uses qemu-img executor; assert committed names removed from state. | D6 adaptive drain. |
| `tests/core/test_pipeline.py` | `test_blockcommit_deferred_when_vm_paused` | No change (semantics unchanged: defer all, reason `"vm_running"`). | Fork keeps paused → defer. |
| `tests/core/test_pipeline.py` | `test_blockcommit_vm_state_check_failure_non_fatal` | No change (legacy fallback: configured mode, full set, no deferral). | Unchanged in fork. |
| `tests/core/test_pipeline.py` | `test_preserve_all_vm_running_no_blockcommit` | No change. | Unchanged. |

### 2.2 Existing tests needing new mock expectations

| File | Tests | Change Needed | Why |
|---|---|---|---|
| `tests/conftest.py` | `_setup_validation_expectations()` | Add default `virsh domblklist` expectation returning a table whose source is the newest snapshot path used by test fixtures (active-layer detection). Keep the `domstate → "shut off"` default added in the first iteration. | Fork calls `domblklist` whenever the remove set is non-empty; tests going through `core.run()` need a sane default. Tests overriding the active layer use `expect_first("domblklist")`. |
| `tests/core/test_pipeline.py` | 17 chain-verify/stale-guard tests | No per-test change if conftest default covers domblklist; otherwise add `expect("domblklist")`. | Same as above. |
| `tests/core/test_deferred.py` | `test_deferred_blockcommit_passes_deep_verify_true` and other drain tests with `lifecycle_mode="virsh"` + shut off | Verify against new drain semantics: executor is qemu-img, deep_verify still forwarded; state removal asserted. May need factory-mock expectation updates. | D6 changes executor selection on the drain path. |

### 2.3 Tests confirmed unaffected

- `tests/core/test_preserve.py` — all (D1 unchanged).
- `tests/modules/lifecycle/test_blockcommit.py` — all 13 (BlockCommitManager behavior unchanged).
- `tests/state/test_manager.py` — existing deferred CRUD tests.
- `test_deferred_blockcommits_skipped_on_running_vm` (`test_deferred.py`) — semantics preserved only for `lifecycle_mode="qemu-img"`; in virsh mode running VMs now drain non-active entries — G8 must verify which config this test uses and adjust if needed.

---

## Step 3: Delegation Groups

### G1-PARSE ✅ COMPLETE (9 tests, no further work)

### G2-PIPELINE: Revise first-iteration tests

| Property | Value |
|---|---|
| Scope | `tests/core/test_pipeline.py` |
| Scenarios | 3 revisions from Step 2.1 + re-verify 3 unchanged |
| Action type | **MODIFY** (3 tests) |
| Special instructions | MockShell: `expect_first("domstate")` for state overrides; `expect_first("domblklist")` for active-layer overrides. Assert factory mode argument and `remove_snapshot` calls. |

### G3-STATE: active_layer reason

| Property | Value |
|---|---|
| Scope | `tests/state/test_manager.py` |
| Scenarios | 1 scenario (active_layer reason round-trip) |
| Action type | **NEW** (1 test) |
| New test names | `test_add_deferred_blockcommit_active_layer_reason` |

### G4-INTEGRATION: Revised + new real-libvirt tests

| Property | Value |
|---|---|
| Scope | `tests/integration/test_blockcommit_defer.py` (revise 2, strengthen 1, add 1), `test_preserve_all.py` unchanged |
| Action type | **MODIFY** (3) + **NEW** (1) |
| Special instructions | Real libvirt 12.5.0 / QEMU 11.0.2 available; `test_vm` fixture. Every test must clean up (destroy VM, delete snapshot metadata, undefine, remove temp dirs). Strengthened assertions: file deletion on disk, `qemu-img info --backing-chain` length, `virsh start` succeeds after offline operations. |

### G5-CONFTEST: domblklist default expectation

| Property | Value |
|---|---|
| Scope | `tests/conftest.py` |
| Action type | **MODIFY** — add `virsh domblklist` default to `_setup_validation_expectations()` |
| Special instructions | Prerequisite for G2/G6/G8. Output must mimic `virsh domblklist` table format parseable by `parse_domblklist_path()`. |

### G6-FORK: Adaptive fork unit tests (Core)

| Property | Value |
|---|---|
| Scope | `tests/core/test_pipeline.py` (or new `tests/core/test_lifecycle_fork.py`) |
| Scenarios | 6 new scenarios from `core-orchestrator/spec.md` (active deferred, qemu-img-mode defer, XML-tip exclusion, race guard, unconditional state removal, domblklist fallback) |
| Action type | **NEW** (6 tests) |
| New test names | `test_blockcommit_active_layer_deferred_when_running`, `test_blockcommit_qemu_img_mode_defers_when_running`, `test_blockcommit_xml_tip_deferred_active_layer`, `test_blockcommit_race_guard_defers_when_vm_started`, `test_blockcommit_state_removed_without_post_verify`, `test_blockcommit_active_detection_fallback` |

### G7-QEMUIMG: QemuImgCommitManager algorithm tests

| Property | Value |
|---|---|
| Scope | `tests/modules/lifecycle/test_qemu_img_commit.py` |
| Scenarios | 6 scenarios from `lifecycle-manager/spec.md` (pivot+delete order, no-child, commit-failure short-circuit, rebase-failure keeps file, MAC ×2, deep_verify) |
| Action type | **NEW** (6 tests) |
| Special instructions | Assert exact shell command order (commit → info-scan → rebase → rm) via MockShell call log. Child discovery responses are `qemu-img info --output=json` payloads with `backing-filename` fields. |

### G8-DRAIN: Adaptive deferred-queue drain tests

| Property | Value |
|---|---|
| Scope | `tests/core/test_deferred.py` |
| Scenarios | 6 scenarios from `deferred-operations/spec.md` drain matrix + review of existing drain tests per Step 2.2 |
| Action type | **NEW** (6 tests) + **MODIFY** (existing drain tests as needed) |
| New test names | `test_drain_shutoff_uses_qemu_img_executor`, `test_drain_shutoff_tip_remainder_requeued`, `test_drain_running_virsh_mode_commits_non_active`, `test_drain_running_qemu_img_mode_skips`, `test_drain_paused_skips`, `test_drain_removes_committed_from_state` |

---

## Step 4: Integration Test Plan

### 4.1 `test_parse_preserve_all_produces_correct_policy` — ✅ done, unchanged

### 4.2 `test_preserve_all_keeps_all_backups_integration` — ✅ done, unchanged

### 4.3 REVISED: Live commit of a non-active snapshot while the VM runs

**File:** `tests/integration/test_blockcommit_defer.py`
**Test:** `test_live_commit_non_active_while_running_integration`

Setup:
1. `test_vm` fixture; `VMConfig` with `lifecycle_mode="virsh"`, `snapshot_preserve` configured so the oldest snapshot lands in the remove set
2. Start VM; create snap1, snap2, snap3 (snap3 = active)
3. Call `core._blockcommit_snapshots(vm, retention)` with remove = {snap1}

Assertions:
- `virsh domstate` = "running"; snap1 committed live (its file **deleted** from disk)
- snap2's backing now points to the base image (`qemu-img info`)
- VM still running; snap3 still the active layer (`domblklist`)
- No deferred entries created

### 4.4 STRENGTHENED: Deferred blockcommit executes after VM shutdown

**File:** `tests/integration/test_blockcommit_defer.py`
**Test:** `test_deferred_blockcommit_executes_after_shutdown_integration`

Setup: as before — defer an entry on the running VM (e.g. via `lifecycle_mode="qemu-img"`), then `virsh destroy`.

Assertions (strengthened vs. first iteration):
- Queue drained via the qemu-img executor
- Committed snapshot files are **deleted from disk** (Bug #4 regression guard)
- `qemu-img info --backing-chain` on the tip shows the chain **shortened** and intact
- Committed names removed from `IStateManager`
- **`virsh start` succeeds** afterwards (domain XML still valid)

### 4.5 REVISED: Active layer deferred on a running VM

**File:** `tests/integration/test_blockcommit_defer.py`
**Test:** `test_active_layer_deferred_running_integration`

Setup: VM running; remove set contains the **active** snapshot (snap3) plus an old one (snap1).

Assertions:
- snap1 committed live; snap3 deferred with reason `"vm_running"`
- No `requires active flag` error anywhere in logs
- Chain intact; VM healthy

### 4.6 NEW: XML-tip exclusion keeps the domain bootable

**File:** `tests/integration/test_blockcommit_defer.py`
**Test:** `test_xml_tip_excluded_offline_vm_boots_integration`

Setup:
1. VM running → create snap1, snap2 (snap2 = tip); `virsh destroy`
2. Run blockcommit with remove = {snap1, snap2}

Assertions:
- snap1 committed offline via qemu-img (file deleted, chain shortened)
- snap2 (XML-tip) deferred with reason `"active_layer"`; its file still exists
- `virsh start` succeeds — domain boots off snap2

---

## Appendix: Test Data Summary

| Category | Existing | New | Modified | Status |
|---|---|---|---|---|
| `_parse_preserve` (G1) | 15 | 9 | 0 | ✅ complete |
| Pipeline fork (G2 revise) | 6 (first iteration) | 0 | 3 | ⬜ |
| Fork matrix (G6) | 0 | 6 | 0 | ⬜ |
| QemuImgCommitManager (G7) | 6 | 6 | 0 | ⬜ |
| Drain adaptation (G8) | ~38 in test_deferred.py | 6 | as needed (2.2) | ⬜ |
| State (G3) | 38 | 1 | 0 | ⬜ |
| Integration (G4) | 4 | 1 | 3 | ⬜ |
| conftest (G5) | — | 0 | 1 | ⬜ |
