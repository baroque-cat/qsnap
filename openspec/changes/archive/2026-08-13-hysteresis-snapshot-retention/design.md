# Design: hysteresis-snapshot-retention

## Context

qsnap's snapshot retention is steady-state count-based: every run keeps the newest
`snapshot_chain_length` snapshots and commits the excess. With hourly runs the chain
oscillates at `L..L+1` overlays forever and a `virsh blockcommit` runs every hour for every
VM. The 2026-08-12 incident (a `virsh blockcommit --wait` client hang resolved only by the
then-3600 s timeout) showed that each commit is an exposure point to libvirt's fragile
deep-commit path; operators also expect "hourly snapshots of the last 24 hours, older layers
periodically collapsed into the base", not a permanently maximal chain.

In the same incident family, the backup-side block-job probe was observed failing on every
run in production: `BitmapBackupProvider` passes the **base image path** to
`virsh blockjob --path`, but with external snapshots the domain XML resolves only the active
overlay as the disk source — libvirt answers `invalid argument: disk '...img.qcow2' not found
in domain`. The safety gate ("defer backup while a block job is active") is therefore dead,
and libvirtd journals accumulate errors hourly.

Constraints: DI paradigm (modules stateless, Core mediates, config as frozen-dataclass method
parameters); result objects, never exceptions, for expected failures; pure retention engine;
additive state schema (old code must tolerate new keys; new code must tolerate missing keys);
zero runtime PyPI dependencies; existing hosts carry 70+ snapshots per VM and must migrate
gradually, not via one giant batch.

## Goals / Non-Goals

**Goals:**

- A retention mode (`hysteresis`, the default) implementing grow-to-threshold /
  collapse-to-floor: no commits while `N ≤ H`; when `N > H`, merge the oldest `N − L`
  snapshots down to the floor.
- Collapse survives across runs (persisted phase) and is bounded per run
  (`max_commits_per_run`), so first-time migration from deep chains is gradual and safe.
- Hysteresis is the out-of-the-box mode; `snapshot_retention_mode = "steady"` restores the
  pre-existing count-based behavior.
- The backup-side block-job probe works on domains with external snapshots (target-name
  addressing) and has defined defer/proceed semantics.
- Reconciliation converges partially completed multi-snapshot merge sets instead of deferring
  them indefinitely.
- Dry-run predicts hysteresis outcomes (silence below threshold, batch collapses, cap) with
  zero mutations.

**Non-Goals:**

- Single range-commit block job (`--top` = newest removed snapshot, one job for the whole
  segment). Attractive for speed but version-sensitive in libvirt/QEMU; requires a dedicated
  spike and validation on production versions — follow-up change.
- Per-VM pipeline deadline / hang isolation (separate change).
- Target-world retention changes (`keep_generations`, FULL cadence untouched).
- Automatic `virsh blockjob --abort` (still operator-only).
- Calendar/time-bucket retention.

## Decisions

### D1. Explicit mode flag; no silent re-semantization

New option `snapshot_retention_mode: "steady" | "hysteresis"` (global, VM-overridable via the
standard inheritance chain; default `"hysteresis"`). In `hysteresis` mode the EXISTING knobs are
reinterpreted: `snapshot_chain_length` = trigger threshold **H**, `snapshot_preserve_min` =
collapse floor **L**. Validation (ConfigFacade): in `hysteresis` mode `H > L` and `L ≥ 1`,
otherwise `ConfigError` naming both values.

- Factory defaults form a valid hysteresis band: `chain_length=72` (H) and
  `preserve_min=24` (L) — grow to ~3 days of hourly snapshots, collapse to the newest 24.
- **Alternative considered:** change `chain_length` semantics globally. Rejected: silently
  breaks every existing deployment.

### D2. Persisted collapse phase (state key `collapse_in_progress`)

Per-VM state gains `collapse_in_progress`: a list of disk names currently collapsing
(missing key = empty; additive, no migration). Lifecycle:

- **Set** during retention evaluation when the trigger fires (`N > H`), persisted BEFORE the
  blockcommit step starts (crash-safe: a killed run resumes collapsing next run).
- **Kept** while post-commit state count `N > L` (commits deferred or capped).
- **Cleared** after successful commit convergence when the disk's state count reaches `≤ L`
  (same run), or defensively when evaluation observes `N ≤ L` with the phase active
  (operator intervention / restore / healing shrank the chain).

Phase writes honor dry-run (predicted, not written). `reset_vm_state` /
`reset_vm_disk_state` clear the marker alongside other per-disk state.

- **Why persisted:** with a per-run cap, collapse spans multiple runs; without a marker, a
  run seeing `L < N ≤ H` would stop collapsing and the chain would oscillate mid-band
  forever (trace: 73→61 capped, regrow to 73, cap again — floor never reached).
- **Alternative considered:** derive phase from `N > L`. Rejected: would commit during the
  growth phase too (defeats hysteresis).

### D3. Retention branch lives in Core; the engine stays pure

`Core._evaluate_disk_retention` gains the mode branch:

- `steady`: unchanged (engine keep-count = `chain_length`, oldest-prefix filter,
  preserve_min floor).
- `hysteresis`: if phase active OR `N > H` → invoke the SAME pure engine with effective
  keep-count `L` (remove = oldest `N − L`), then oldest-prefix filter, then cap (D4).
  Otherwise remove = [] — NO commits at all below the threshold.

`TimeBasedRetention` is untouched (pure count-keeper; it already ignores `preserve_min` per
spec). Trigger/phase/cap are orchestration — Core's job per the paradigm.

- **Alternative considered:** hysteresis logic inside the engine. Rejected: the engine must
  stay a deterministic pure function of (items, policy); phase is cross-run state and
  belongs to Core + IStateManager.

### D4. `max_commits_per_run` — bounded catch-up

New global option `max_commits_per_run: int` (default **12**, `0` = unlimited, must be ≥ 0).
Applied per disk per run: the final remove list (already floor-trimmed, oldest-first) is
truncated to the cap, keeping the OLDEST entries. Snapshot world only.

- First-time migration arithmetic (production case): N≈74, H=72, L=24 → 50 to merge →
  ceil(50/12) = 5 hourly runs ≈ 5 hours, each run bounded (~12 commits × seconds on a
  healthy host; worst case bounded by per-commit timeout + reconciliation).
- Operators wanting one-shot collapse under supervision set `max_commits_per_run = 0`.
- Cap truncation happens AFTER the preserve_min floor trim and removes from the oldest end,
  so the floor invariant (≥ L newest kept) holds regardless of the cap.

### D5. Backup-side probe fix — target-name addressing, shared classifier

`virsh blockjob --path` SHALL receive the disk TARGET name (e.g. `vda`), never the base
image path: libvirt resolves targets regardless of the active layer, while the base path is
unresolvable once external snapshots exist.

- New tiny pure helper `classify_blockjob_output(stdout: str, *, stderr: str = "",
  success: bool = True) -> Literal["none", "active", "error"]` in `qsnap/utils/blockjob.py`.
  It inspects BOTH stdout and stderr (a throttled `virsh blockcommit` reports its progress on
  stderr while stdout may carry only a bandwidth line) and returns `"none"` (no job),
  `"active"` (job-describing output), or `"error"` (failed command or unclassifiable output).
  Both `Core._probe_blockjob` and `BitmapBackupProvider` consume it — one classification, no drift.
- `BitmapBackupProvider.run_backup` step 5: probe with `disk_target`; `active` → defer
  backup (existing deferred result); `error` → log WARNING once and PROCEED (fail-open).
- **Fail-open rationale for backups:** a fail-closed probe error would permanently block all
  backups whenever virsh is flaky; the dangerous direction (starting a commit over an active
  job) is guarded fail-closed on the commit side. The WARNING makes repeated probe failures
  visible instead of today's silence.
- Module boundary respected: the provider owns its probe (it already receives `IShell`);
  Core does not duplicate it.

### D6. Partial merge-set reconciliation (prefix convergence)

With capped batches, a timeout can strike mid-batch: the oldest `k` of `n` merge-set files
gone, the rest present. Today this yields `inconclusive` → deferred forever (the stale-entry
heal eventually papers over it, but leaves stale deferred entries and misleading messages).

Extended protocol for merge sets with `n > 1` (both dispatch and step-0 recovery paths):

1. Probe first: active job → `job_active` (unchanged).
2. Compute the largest oldest-prefix `P` (length `k`, `0 ≤ k ≤ n`) whose files are ALL gone.
3. `k = n` → existing rules (quantitative chain check when baseline available).
4. `0 < k < n` AND (no baseline OR chain shrank by exactly `k`) → **partial late success**:
   converge state for the vanished prefix (`set_last_commit_ts` ONCE, `remove_snapshot` for
   the prefix, intent rewritten to the remaining suffix), outcome reported as `late_success`
   with the converged subset; the suffix stays removable and is retried via the normal
   retention/phase mechanism next run.
5. `k = 0` → existing rules (`failure` if chain unchanged, else `inconclusive`).
6. Any chain-length contradiction → `inconclusive` (fail closed), intent kept.

For `n = 1` (steady mode) behavior is byte-identical to today.

### D7. Observability

- INFO at trigger: `[retention] {vm}/{disk}: collapse phase started — merging oldest {K} of
  {N} snapshots down to floor {L}`.
- INFO per capped run: `[retention] {vm}/{disk}: collapse phase active — merging {k} this
  run ({remaining} remain above floor {L})`.
- INFO at completion: `[retention] {vm}/{disk}: collapse complete — chain at floor {L}`.
- WARNING on probe error in the backup path (once per occurrence) naming VM/disk/error.
- Dry-run emits the equivalent predictions (including predicted phase transitions) without
  state writes.

### D8. Config surface and documentation

```toml
# global section (inheritable per VM where noted)
snapshot_retention_mode = "hysteresis"   # "hysteresis" (default) | "steady"
max_commits_per_run     = 12             # 0 = unlimited
```

`qsnap.toml.example` documents both modes with the production migration recipe; README
retention section updated.

## Risks / Trade-offs

- [Longer runs during catch-up; hourly timer hits the lock (exit 3)] → bounded by the cap;
  skipped triggers retry next hour (`Persistent=true`); acceptable and visible in logs.
- [Phase marker orphaned by persistent commit failures] → the marker alone is inert; the
  failures themselves surface via existing ERROR/defer/WARNING paths and deferred-threshold
  monitoring; clearing happens automatically once reality reaches the floor by any means.
- [Stale-entry healing shrinks N mid-phase] → defensive clear path (evaluation observes
  `N ≤ L` with phase active → clears phase, no commits).
- [Probe fail-open could let a backup race an operator-started job] → accepted and
  documented; commit-side guards remain fail-closed; WARNING exposes probe breakage.
- [Default cap slows migration on healthy hosts] → documented escape hatch
  (`max_commits_per_run = 0` for supervised one-shot collapse).
- [Partial-prefix convergence misattributes externally deleted files] → requires the
  oldest-prefix pattern PLUS chain-length agreement when a baseline exists; contradictions
  stay `inconclusive` (fail closed).
- [Two retention modes increase test matrix] → matrix is orthogonal (mode × phase × cap) and
  fully enumerable; obsolete steady-only assertions are refactored, not duplicated.

## Migration Plan

1. Release with `snapshot_retention_mode = "hysteresis"` default (band H=72, L=24). Fresh
   installs grow to 72 snapshots then collapse to 24 — no steady-state blockcommit-per-hour.
   The probe fix activates immediately (noise stops, gate lives).
2. On hosts with deep chains (e.g. production: 73 snapshots/VM): the first trigger cycle runs
   a capped catch-up (~5 capped runs to the floor) — watch it via the new log lines, verify
   `qsnap check` clean. Optionally raise `max_commits_per_run` temporarily for faster
   catch-up under supervision.
3. Rollback: set the mode to `steady` (chain resumes steady-state from its current depth;
   no data movement). Package downgrade: old code ignores unknown state keys and
   never reads the new options — equivalent to steady.

## Open Questions

None blocking. Range-commit (one block job per collapse segment) is parked as a follow-up
spike gated on production libvirt/QEMU versions.
