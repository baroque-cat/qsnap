## Context

### Original bugs (found by manual smoke test, present on `main`)

**Bug 1: `_parse_preserve("all")` — silent data loss.** `Core._parse_preserve()` determines `effective_min` via a cascade; `"all"` fell into the `else: effective_min = "0h"` branch, the bucket regex failed to match, and the result was `RetentionPolicy(0,0,0,0,0, preserve_min="0h")` — keep nothing, delete everything (backups included). The retention engine itself handles `preserve_min="all"` correctly; the bug is purely in the mapping.

**Bug 2: `_blockcommit_snapshots()` — no VM state check.** The method called `manager.blockcommit()` unconditionally. With Bug #1 putting the active layer into the remove set, `virsh blockcommit` on a running VM fails with `commit of 'vda' active layer requires active flag`.

### Empirical findings from this change's investigation (libvirt 12.5.0 / QEMU 11.0.2)

These were verified by live experiments, not code reading:

1. **`virsh blockcommit` requires a running domain, always.** On a shut-off domain every variant fails: `Requested operation is not valid: domain is not running`. libvirt's QEMU driver implements blockcommit via block jobs; there is no offline mode.
2. **`virsh blockcommit` of a *non-active* layer works on a running VM** and is fully transactional from qsnap's point of view: libvirt merges the overlay, rewrites the child's backing pointer to the base image (pivot), and deletes the committed file (`--delete`). This is the standard live chain-shortening path that worked on `main`.
3. **`qemu-img commit -d` is a no-op on QEMU 11.0.2** (Bug 4): `qemu-img commit -b <base> -d <snap>` merges data into the base image but neither deletes the top file nor pivots children. Consequences for `QemuImgCommitManager` as written: chain length never decreases (false-positive CRITICAL from post-commit verification), committed files litter `snapshot_dir` as orphans (auto_cleanup only warns about orphan `.qcow2` files; it deletes only `*.tmp`/`*.partial`), and state entries are never healed because the file still exists.
4. **The XML-tip constraint (Bug 3 corollary):** an inactive domain's XML references its top overlay. Deleting that file offline leaves the domain unbootable. Offline commits must therefore never delete the XML-referenced tip; the tip's backing pointer can be freely re-based (the file path is unchanged, so the XML stays valid).
5. **The pre-existing deferred queue cannot drain in virsh mode:** `_check_deferred_operations()` executes only when the VM is shut off — exactly when virsh blockcommit cannot work (finding 1). `"apparmor"`/`"selinux"` entries retry and fail forever in the default mode.
6. **First-iteration guard regression:** the naive "defer everything when not shut off" guard (already implemented in this change) prevents the Bug #2 crash but disables the working live path (finding 2) and routes all removals into the un-drainable queue (finding 5) for virsh-mode users. This design replaces it.
7. **Domain XML `<backingStore>` goes stale after offline commits (found by G4 integration tests):** an inactive domain's persistent XML records the full `<backingStore>` chain from snapshot-creation time. Once offline commits actually delete overlay files (Bug #4 fix), the XML references deleted files and `virsh start` fails with `Cannot access backing file '<committed>' of storage file '<tip>': No such file or directory`. Confirmed empirically: stripping all `<backingStore>` elements and redefining the domain makes start succeed (libvirt re-probes the chain from qcow2 headers). Addressed by D8.

### Interaction of bugs

Bug #1 → remove set contains everything incl. active layer → Bug #2 (live failure). Independently: virsh mode offline → finding 1 (impossible); qemu-img mode online → unsafe base writes; qemu-img mode offline → finding 4 (silent no-op cleanup). No mode/state combination on `main` provides a fully correct commit path.

## Goals / Non-Goals

**Goals:**
- Fix `_parse_preserve("all")` so `preserve = "all"` keeps everything (D1, already implemented).
- Make snapshot blockcommit work correctly and safely in every VM power state and both lifecycle modes, choosing the mechanism that is valid for the current state (the "adaptive fork").
- Restore the live-commit path for non-active snapshots on running VMs (regression from the first-iteration guard).
- Make offline commits actually shorten the chain: correct pivot + explicit file deletion (Bug #4).
- Never delete the XML-referenced tip overlay of an inactive domain (Bug 3 corollary).
- Make the deferred-operations queue drainable in all modes, including pre-existing `"apparmor"`/`"selinux"` entries.
- Clean `IStateManager` snapshot entries unconditionally after successful commits.
- Maintain backward compatibility — no ABC interface changes.

**Non-Goals:**
- Adding `--active` to `BlockCommitManager` (risky live operation; unnecessary — the active layer simply waits).
- Editing inactive domain XML to re-point the tip disk offline (v2 candidate; v1 defers the tip instead — see D2/D7).
- Changing `ILifecycleManager` or any other ABC signature.
- Changing `_parse_preserve` call sites, `RetentionPolicy`, or the retention engine.
- Changing backup providers, incremental dependency tracking, ghost retention, or `_cleanup_backups()` (target-side logic is unaffected by source-side commit mechanics).
- Adding a `qsnap commit` CLI command (future change).

## Decisions

### D1: Add `"all"` to `_parse_preserve()` early-return — UNCHANGED (implemented)

Add `elif preserve_str == "all": effective_min = "all"` to the cascade and include `"all"` in the early-return guard. Minimal fix; only the (`"all"`, `None`) row changes behavior.

| `preserve_str` | `preserve_min_str` | Before fix | After fix |
|---|---|---|---|
| `"all"` | `None` | **Delete all** (BUG) | **Keep all** |
| `"all"` | `"all"` | Keep all | Keep all (no change) |
| `"all"` | `"6h"` | Keep <6h | Keep <6h (no change) |
| `"all"` | `"0h"` | Delete all | Delete all (no change, override wins) |
| `None` | `None` | Keep all | Keep all (no change) |
| `"24h 7d"` | `None` | Keep 24h+7d | Keep 24h+7d (no change) |

### D2 (revised): Adaptive lifecycle fork in Core

**Decision:** Introduce a pure decision helper in Core that maps (VM power state, `lifecycle_mode`, active-layer path, candidate snapshots) to a commit plan. Both `_blockcommit_snapshots()` and `_check_deferred_operations()` use it.

```python
@dataclass(frozen=True)
class _CommitPlan:
    committable: list[SnapshotInfo]   # oldest-first; safe to commit NOW
    deferrable: list[SnapshotInfo]    # active layer / whole set when unsafe
    effective_mode: str | None        # "virsh" | "qemu-img" | None (nothing committable)
    defer_reason: str | None          # "vm_running" | "active_layer" | None
```

**State detection:** `virsh domstate --domain <vm>` (timeout 30s), consistent with existing code. On failure → return `None` (legacy fallback: proceed with configured mode and full candidate set, no deferral — the pre-change `main` behavior; non-fatal by design).

**Active-layer detection:** `virsh domblklist --domain <vm>` parsed with the existing `parse_domblklist_path()` utility — returns the current source file, which is the active overlay on a running VM and the XML-referenced tip on an inactive one. On failure → fall back to the newest snapshot in `IStateManager` (by timestamp) with a WARNING log; this heuristic is correct in the normal case because qsnap-created snapshots are appended in order.

**Fork matrix:**

| VM state | `lifecycle_mode` | committable | deferrable | effective_mode | defer_reason |
|---|---|---|---|---|---|
| running | `virsh` (adaptive) | candidates minus active | active layer (if in candidates) | `virsh` | `vm_running` |
| running | `qemu-img` | — | all candidates | — | `vm_running` |
| shut off | any | candidates minus XML-tip | XML-tip (if in candidates) | `qemu-img` | `active_layer` |
| paused / other | any | — | all candidates | — | `vm_running` |
| domstate failed | any | all candidates (legacy path) | — | configured mode | — |

**Mode semantics (documentation change, no config schema change):**
- `lifecycle_mode = "virsh"` (default) becomes **adaptive**: live `virsh blockcommit` for non-active layers while the VM runs; `qemu-img commit` offline when the VM is shut off. This restores `main`'s live behavior and fixes the previously impossible offline path.
- `lifecycle_mode = "qemu-img"` remains **offline-only**: never commits while the VM runs (qemu-img writing into the base image of a live chain risks guest-visible corruption); defers with `"vm_running"`.

**Split rule (user-approved D-b):** the active layer is always the chain tip, so it can appear in candidates only as the newest element. Committing the non-active prefix is safe and shortens the chain immediately; only the tip is deferred.

**`_blockcommit_snapshots()` flow after the fork:**
1. stale-state guard → `--preserve-snapshots` → `--dry-run` (unchanged, run before any virsh calls)
2. `plan = _plan_blockcommit(...)`; `None` → legacy path (chain verification → manager(configured mode) → existing MAC handling → continue at step 6)
3. defer `plan.deferrable` via `add_deferred_blockcommit(reason=plan.defer_reason)` + INFO log; if `committable` empty → return
4. pre-commit chain verification (`chain_verify_before_commit`), `chain_length_before` — unchanged
5. if `effective_mode == "qemu-img"`: re-check `virsh domstate` immediately before invoking the manager (narrows the start-VM-during-commit race); if no longer shut off → defer `committable` with `"vm_running"` and return
6. `manager = factory.create_lifecycle_manager(mode=plan.effective_mode)` → `manager.blockcommit(vm_config, committable)` → MAC denial / failure handling (unchanged)
7. on success: `ActionRecord("snapshot_delete")` per committed snapshot; **`remove_snapshot()` for committed snapshots unconditionally** (D5); post-commit chain verification (`chain_verify_after_commit`) — unchanged

**Rationale:** the fork places all mechanism selection in the orchestrator (consistent with D3 and AGENTS.md: modules are stateless workers; Core owns sequencing). No ABC changes; the factory interface is reused — Core only computes the mode string. Live and offline paths each use the one mechanism that is valid in that state (findings 1–3).

**Alternatives considered:**
- *Defer-everything-when-running guard (first iteration).* Rejected: regresses the working live path and feeds an un-drainable queue in virsh mode (findings 2, 5).
- *`--active` live commits.* Rejected: riskier, virsh-only, requires active-layer identification anyway.
- *New `lifecycle_mode = "auto"` value.* Rejected: adds config surface; making `"virsh"` adaptive is strictly better than its current behavior (live works as before; offline now works instead of failing) and requires no migration. `"qemu-img"` stays the conservative explicit choice.
- *Composite `AdaptiveCommitManager` module wrapping both managers.* Rejected: violates D3 (module would need VM-state knowledge) and adds a third lifecycle class without need — Core is the rightful owner of the decision.

### D3: Orchestration stays in Core — UNCHANGED

VM-state detection, active-layer detection, splitting, deferral, and executor selection all live in Core. Managers receive only a concrete, safe work list. No `check_vm_state` parameters on the ABC.

### D4: Correct offline algorithm in `QemuImgCommitManager` (Bug #4 fix)

**Decision:** Replace the reliance on `qemu-img commit -d` with an explicit per-snapshot sequence, oldest first, for each snapshot `si` in `snapshots_to_merge`:

1. `qemu-img commit -b <base_image> <si.path>` — merge `si` (and anything below it not yet merged) into the base image. Never commits *into* a kept overlay: the target is always the base.
2. **Child discovery:** scan `vm_config.snapshot_dir` for `*.qcow2` files; run `qemu-img info --output=json` on each; the child is the file whose resolved `backing-filename` equals `si.path` (linear chain ⇒ at most one).
3. If a child exists: `qemu-img rebase -u -F qcow2 -b <base_image> <child>` — metadata-only pivot. Safe because `si`'s data is now contained in the base image, so the child's view through the base is identical to its previous view through `si`.
4. `rm -f <si.path>` — delete only after the pivot succeeded (or when no child exists).

**Invariants:**
- Never delete a file that still has a child pointing at it (step 4 is ordered after step 3).
- On any failure: short-circuit — no `rm`, no further iterations; return `CommitResult(success=False, committed_snapshot=si.name, error=...)`. The chain is left consistent (committed data is idempotent; un-pivoted children still point at existing files), so the next run can safely retry.
- The XML-tip is never in `snapshots_to_merge` (Core excludes it — D2), so step 4 never deletes the domain-referenced file. The tip may appear as a *child* in step 3 — pivoting it is fine (path unchanged, XML stays valid).
- Non-contiguous remove sets are handled naturally: kept overlays are never written to; each kept overlay above a committed file is pivoted to the base.

**MAC detection:** add the same AppArmor/SELinux stderr detection as `BlockCommitManager` (offline qemu-img file access can equally be denied). Move `_detect_mac_denial()` to a shared helper in `qsnap/utils/` (e.g. `qsnap/utils/mac.py`); both managers use it. Modules importing from `utils/` is established practice (`utils/parsing.py`, `utils/nbd.py`), not a module-to-module import.

**`deep_verify`:** unchanged (qemu-img check on the base image after success).

**Alternative considered:** single range-commit `qemu-img commit -b <base> <newest-of-set>` + one pivot + batch `rm`. Rejected for v1: per-snapshot iteration matches `BlockCommitManager`'s loop structure, gives precise per-snapshot failure attribution (`committed_snapshot`), and keeps rollback trivially safe at every step.

### D5: Unconditional state cleanup after successful commits

**Decision:** Move `self._state.remove_snapshot(vm, name)` for committed snapshots out of the `chain_verify_after_commit` conditional in `_blockcommit_snapshots()` — it runs after every successful commit regardless of verification settings. Add the same cleanup to `_check_deferred_operations()` for successfully drained snapshots (currently missing entirely).

**Rationale:** state must reflect disk reality before backup steps run (`_execute_backup_steps` fetches the survivor list from state). Today, with verification disabled, stale entries flow into `FileCopyBackupProvider.transfer_missing()`, causing rsync "file not found" warnings and stale-heal churn; with Bug #4 (files not deleted) they caused infinite re-commits. Chain-length verification only *measures*; it must not gate bookkeeping.

### D6: Adaptive drain in `_check_deferred_operations()`

**Decision:** Replace the single "shut off + configured mode" execution rule with the same fork (D2), applied per queue entry:

| VM state | `lifecycle_mode` | Drain behavior |
|---|---|---|
| shut off | any | executor = `qemu-img`; per entry: commit snapshots except the XML-tip; on success remove committed from state; re-queue remainder (tip) with **original reason** |
| running | `virsh` | executor = `virsh`; per entry: commit non-active snapshots; re-queue remainder with original reason |
| running | `qemu-img` | skip all entries (unchanged) |
| paused / other | any | skip all entries (unchanged) |
| domstate failed | any | skip all entries (unchanged, conservative) |

- Partial drain is explicit: an entry is removed from the queue only when all its snapshots are committed; otherwise it is re-queued with the remaining names and its **original** reason (reason describes why it entered the queue, not why it is still there).
- `deep_verify=vm_config.blockcommit_deep_verify` continues to be passed on the drain path (unchanged); the main path keeps its current behavior (no `deep_verify`) — unifying is an open question, not part of this change.
- Entries whose snapshots are gone from state are dropped as "stale deferred" (behavior change vs. the pre-change code, which re-queued them forever — dropping is the intended semantics; flagged here because earlier drafts of this design called it "unchanged").
- This makes pre-existing `"apparmor"`/`"selinux"` entries drainable in virsh mode for the first time (finding 5) — offline execution via qemu-img does not depend on the MAC policy that blocked the original live attempt; if qemu-img is *also* denied, D4's MAC detection re-defers with the new reason.

### D8: Domain XML `<backingStore>` refresh after offline commits

**Decision:** After any successful OFFLINE commit (executor `qemu-img`, main path or deferred drain), Core refreshes the inactive domain's persistent XML via a best-effort helper `_refresh_domain_backing_store(vm_config)`:

1. `virsh dumpxml --domain <vm>` → parse XML.
2. Remove every `<backingStore>` element from every `<disk>` element (nested stores disappear with their parent).
3. Write to a temp file and `virsh define <file>`; delete the temp file.

With no `<backingStore>` recorded, libvirt re-probes the shortened chain from the qcow2 headers on next start, so the domain stays bootable after committed files are deleted (finding 7). All failures (dumpxml, parse, temp file, define) are WARNING + non-fatal — the commit itself already succeeded; the log tells the operator how to recover manually.

**Placement (D3):** the refresh is Core orchestration, not manager behavior — `QemuImgCommitManager` stays libvirt-free (it is the "libvirt-unavailable" executor). Core calls it once per `_blockcommit_snapshots()` invocation when `effective_mode == "qemu-img"`, and once per `_check_deferred_operations()` invocation when at least one entry drained via qemu-img. Live commits (virsh mode) need no refresh: libvirt maintains the chain itself.

**Alternative considered:** surgical removal of only the deleted files' `<backingStore>` nodes with re-pointing of children inside the XML. Rejected for v1: stripping all stores is idempotent, trivially correct (libvirt re-probes everything), and matches the empirical workaround verified by G4. Fine-grained XML editing is the v2 candidate already noted for tip re-pointing.

### D7: Safety invariants (normative summary)
1. `QemuImgCommitManager` is never invoked while the VM is running (Core guarantees; D2 matrix + re-check in step 5).
2. `BlockCommitManager` is never invoked while the VM is shut off, and never receives the active layer in `snapshots_to_merge`.
3. The XML-referenced tip overlay of an inactive domain is never committed or deleted offline; it is deferred with reason `"active_layer"` and becomes committable when it is no longer the tip (a newer snapshot exists and the VM runs — live drain in virsh mode).
4. Kept overlays are never written to or deleted by either manager.
5. A committed file is deleted only after its child has been pivoted (offline path; D4).
6. Chain verification (pre/post-commit), MAC-denial deferral, `ActionRecord` audit, and transaction-log behavior apply equally in both branches.
7. Expected failures (deferral, commit errors, missing files) are result objects and log lines — never exceptions.
8. After every successful offline commit (main path or drain), the domain's persistent XML is refreshed so it no longer references deleted overlay files (D8); refresh failures are non-fatal WARNINGs.

**Residual accepted risk:** a VM started *during* an offline commit sequence (after the D2 step-5 re-check) can race `qemu-img` writes to the base image. The window is seconds and requires an out-of-band `virsh start`; qsnap has no cross-process power-state lock. Documented; a libvirt hook/lock integration is future work.

## Risks / Trade-offs

- **[Risk] Already-lost backups from Bug #1** → Not recoverable; the fix prevents future loss. Document in README that `preserve = "all"` was broken before this change.
- **[Risk] Active layer deferred on always-running VMs** → Mitigation: with D-b only the tip waits; the prefix is committed live every run, so chains stay short. The tip drains automatically once a newer snapshot exists above it (live drain, virsh mode) — monitored by existing deferred thresholds.
- **[Risk] XML-tip deferred on mostly-stopped VMs** → A VM that never runs while having pending tip-only entries keeps them queued; thresholds (`deferred_warn_count`/`deferred_crit_count`/age) alert the operator. v2 may re-point domain XML offline.
- **[Risk] domblklist fallback heuristic (newest state snapshot)** → If wrong, Core may defer one snapshot too many (safe direction) or attempt a live commit of the actual tip (virsh then fails with `active layer requires active flag` — a logged error, no corruption). Acceptable.
- **[Trade-off] Two extra virsh calls per VM per run** (`domstate` + `domblklist`, plus one `domstate` re-check on offline commits) → 30s timeouts, non-fatal; negligible against pipeline cost.
- **[Risk] Paused VMs** → Deferred (neither mechanism is safe on a frozen guest). Unchanged from first iteration.
- **[Trade-off] `lifecycle_mode = "virsh"` semantics become adaptive** → Behavior change only where virsh previously *failed* (offline) or *crashed* (active layer). Documented in `qsnap.toml.example` and README.
- **[Risk] qemu-img child discovery scans `snapshot_dir`** → O(files) `qemu-img info` calls per committed snapshot; directories are small (tens of files). Encapsulated in the manager; mockable via `IShell`.

## Migration Plan

1. **Code** (this change): D1 (done), D2 fork + D5 + D6 in Core, D4 in `QemuImgCommitManager`, shared MAC helper in `qsnap/utils/`.
2. **No user action required.** `lifecycle_mode = "virsh"` gains working offline commits automatically; `"qemu-img"` users gain correct cleanup.
3. **Docs:** update `qsnap.toml.example` (`lifecycle_mode` descriptions) and README (adaptive semantics; `"active_layer"` deferral; historical Bug #1 note).
4. **Deferred queue:** existing `"apparmor"`/`"selinux"`/`"vm_running"` entries are drained by the new adaptive logic with no conversion.

## Open Questions

- Should the main blockcommit path honor `blockcommit_deep_verify` like the drain path does? (Currently divergent; proposed for a follow-up.)
- Should `DeferredBlockcommit.reason` become an enum (`"apparmor" | "selinux" | "vm_running" | "active_layer"`)? (Free-form string kept for backward compatibility.)
- v2: offline re-pointing of the domain XML tip (would let tip-only queues drain without a VM start) — worth a separate change?
- v2: power-state locking to eliminate the residual start-during-offline-commit race (D7)?
