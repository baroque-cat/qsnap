## Context

Hysteresis retention grows each disk's backing chain from the floor L (`snapshot_preserve_min`,
default 24) to the threshold H (`snapshot_chain_length`, default 72) with zero commits, then
collapses the oldest `N − L` snapshots into the base image. Today the collapse is portioned:
`Core._apply_commit_cap` truncates the remove set to `max_commits_per_run = 12`, and
`BlockCommitManager` merges one snapshot per `virsh blockcommit … --wait` invocation
(short-circuit on first failure). A 73→24 collapse therefore spans ~5 hourly runs; production
observation shows up to ~16 minutes per merged layer on busy VMs, plus ~140 housekeeping shell
calls per batch (three full `qemu-img info --backing-chain` walks, ~2N `test -f` probes,
domstate/domblklist/blockjob probes).

Two independent facts make a faster design possible:

1. `virsh blockcommit --base <B> --top <T>` merges the ENTIRE segment `(B, T]` in one QEMU
   block job, copying each cluster at most once (the topmost allocation within the segment
   wins). Sequential per-layer commits re-copy every cluster that later layers overwrite.
2. qsnap's recovery machinery (commit intent journal, `virsh blockjob` probe, chain-length
   reconciliation, deferred-operation queue) already operates on merge SETS, not single
   snapshots — it survives the switch to all-or-nothing batches essentially unchanged.

Constraints: pure-stdlib Python, DI/ABC paradigm (AGENTS.md), no backward-compatibility
requirement (personal project), offline path must keep working (`qemu-img commit` has no
segment mode).

## Goals / Non-Goals

**Goals:**

- Collapse completes within ONE run: when `N > H`, all oldest `N − L` snapshots of the disk
  are merged by a single `virsh blockcommit` segment job (live path), leaving exactly the
  base image + the newest `L` snapshots + the active layer.
- Remove the portioning machinery entirely: no `max_commits_per_run`, no
  `collapse_in_progress` phase, no started/active/complete phase logging.
- Eliminate duplicated pre-commit chain scanning (baseline length derived from the integrity
  scan).
- Preserve every existing safety mechanism: floor invariant, active-layer exclusion, pre/post
  chain verification, intent journal, three-valued outcomes, reconciliation, deferral
  (MAC/ENOSPC/blockjob), VM-level isolation on definitive failure.

**Non-Goals:**

- No new config flag or parallel strategy mode — the old drip behavior is replaced, not
  selectable.
- No chunking / partial batching of the collapse (explicit owner decision: one chunk).
- No progress-watchdog timeout redesign (fixed scaled timeout for now; a
  `virsh blockjob --info` progress watchdog is future work).
- No changes to the backup-target world, snapshot creation, or steady-mode keep semantics
  (steady mode only loses the irrelevant cap).
- No offline segment commit (impossible with `qemu-img`); offline stays per-layer but
  uncapped.

## Decisions

### D1 — Replace in place: no new class, no factory branch

`BlockCommitManager.blockcommit()` keeps its signature (it already receives the full merge-set
list) and switches from a per-snapshot loop to ONE command:
`virsh blockcommit --domain <vm> --path <disk> --base <base_image> --top <snapshots_to_merge[-1].path>
--delete --verbose --wait`.

Alternatives considered: (a) a new `BulkBlockCommitManager` behind a config flag — rejected:
owner wants the old mode fixed, not paralleled; doubles test matrix for a personal project.
(b) Keep the loop, raise the cap — rejected: does not remove redundant cluster re-copying,
the dominant cost.

### D2 — Merge-set ordering contract: top = LAST element

`TimeBasedRetention.evaluate` sorts ascending and returns `remove` oldest-first
(`qsnap/retention/time_based.py`); `_apply_commit_cap`'s removal does not reorder. The bulk
command relies on `snapshots_to_merge[-1]` being the NEWEST removable snapshot. The manager
asserts non-empty input and documents the ordering invariant; Core never reverses the list.
The active layer and everything above the floor is excluded upstream by retention + 
`_plan_blockcommit`, so `--top` is always strictly below the active layer (legal non-active
commit; no `--pivot`, no `--active`).

### D3 — All-or-nothing live outcome; partial-prefix reconciliation kept for offline only

A segment job either completes (QEMU rewires the child of top to base atomically; libvirt
`--delete` removes all intermediates) or has no visible effect. `_reconcile_commit_outcome`
rules are unchanged: `k == n` + chain shrank by n → `late_success`; `k == 0` + unchanged →
`failure`; contradictions → `inconclusive`. The `0 < k < n` prefix branch can no longer occur
on the live path but MUST remain: the offline `QemuImgCommitManager` still commits per layer
and can die between two `qemu-img commit` invocations.

### D4 — Timeout budget scales with the merge set

`blockcommit_timeout` (default 1800 s) keeps its documented "per merged layer" meaning. Core
passes `timeout × len(committable)` to the manager for the bulk job. If the scaled budget is
exceeded the process is killed, outcome is `"unknown"`, and reconciliation typically finds the
QEMU job still running (`job_active`) → deferral with intent kept; the deferred drain finishes
it on a later run. This reuses proven machinery instead of inventing a watchdog (non-goal).
Overflow at pathological depths (hundreds of layers) is accepted — such chains indicate an
operator problem anyway.

### D5 — Phase state removed; natural re-trigger replaces it

`collapse_in_progress` existed only because the cap spread one logical collapse across many
runs. Without the cap, a collapse either finishes in its run or was deferred/failed — in both
cases `N > H` still holds on the next run and the trigger fires again identically. The commit
intent journal (`commit_in_progress`) remains the ONLY crash-recovery record. The three
`IStateManager` phase methods are deleted; `JsonStateManager` readers already tolerate unknown
keys, so stale persisted keys are simply never read again (no migration).

### D6 — `max_commits_per_run` removed loudly

The option is deleted from `GlobalConfig`, facade parsing, docs, and example config. If the
key appears in a user config, `ConfigFacade` SHALL raise `ConfigError` naming the removed
option and explaining the single-shot collapse (loud failure beats silent behavior change).
Validation of `H > L >= 1` for hysteresis mode is unchanged.

### D7 — Chain-length baseline piggybacks on the integrity scan

`scan_backing_chain` already returns the full parsed chain array. `ChainVerifyResult` gains an
additive `chain_length: int | None` field (populated from the scan; `None` on scan failure or
when the pre-commit check is disabled). `_blockcommit_one_disk` uses it as
`chain_length_before` and skips the separate `_get_chain_length` call when available;
`_get_chain_length` itself stays for reconciliation/post-commit use. One full chain walk per
batch instead of two. Post-commit measurement is unchanged (still a fresh walk — it must
observe post-mutation reality).

### D8 — Offline path: uncapped, otherwise untouched

`QemuImgCommitManager` keeps its per-layer loop (`qemu-img commit` → `rebase -u` pivot →
`rm -f`); it simply receives the full remove set now that the cap is gone. An offline collapse
thus also converges in one run (bounded by per-layer `blockcommit_timeout` as today). The
qemu-img race-guard re-check, XML `<backingStore>` refresh, and tip exclusion are unchanged.

### D9 — Observability wording

Two distinct lines open a collapse. Retention-level initiation (carries the full arithmetic,
satisfies `hysteresis-retention` observability): `[retention] {vm}/{disk}: collapse triggered
(N={total}, merging {n}, floor={L}) — single bulk blockcommit`. Manager-intent line
immediately before the command: `[blockcommit] {vm}/{disk}: collapsing {n} snapshot(s) into
{base} (mode=virsh, timeout={scaled}s)`. Heartbeat: `[blockcommit] {vm}/{disk}: still
collapsing {n} layer(s) into base ({elapsed}s elapsed)` — the noun is singular (`layer`)
when n = 1 and plural (`layers`) otherwise. Success: `[blockcommit] {vm}/{disk}:
collapsed {n} snapshot(s) — {names}` (replaces the old "merged" wording). Dry-run prediction
names the full oldest `N − L` set ("would collapse N snapshot(s) in one blockcommit").
Per-snapshot `--- [vda] <name>` summary rows are KEPT — one `ActionRecord(snapshot_delete)`
per merged snapshot is still appended after success (audit trail granularity is per snapshot
even though the job is bulk).

## Risks / Trade-offs

- [Long bulk job holds the lockfile; hourly timer runs exit 3 until it finishes] → Accepted
  and self-regulating: collapse happens once per ~49 hours of snapshots; subsequent runs skip
  cleanly. Documented in qsnap.toml.example.
- [All-or-nothing: a failure at 95% retries the whole segment] → Failure surface is one
  standard virsh/QEMU verb already used today; ENOSPC/MAC/timeout all route into existing
  deferral/reconciliation; net I/O is still lower than drip because clusters copy once.
- [libvirt `--delete` correctness on deep segments] → Requires libvirt ≥ 6.x (documented);
  post-commit chain-length verification + reconciliation catch anomalies; CRITICAL +
  RuntimeError abort on unchanged chain (VM-level isolation) as today.
- [Base image grows during the job; target FS fills] → Same or fewer bytes written than drip
  (each cluster ≤ once); ENOSPC classification → `enospc` deferral already exists.
- [Merge-set ordering violated by a future caller] → Manager asserts non-empty list and
  documents the oldest-first contract; retention engine output order is unit-tested.
- [Stale `collapse_in_progress` keys after downgrade/upgrade] → Ignored by readers in both
  directions (old code sees nothing to resume; new code never reads it).

## Migration Plan

1. Upgrade qsnap; if `/etc/qsnap/qsnap.toml` sets `max_commits_per_run`, delete the line
   (startup fails loudly until then, by design).
2. Run `qsnap check` (read-only) to confirm state consistency, then let the hourly timer run.
3. First trigger observation: expect ONE `[blockcommit] … collapsing N snapshot(s)` job and a
   single post-commit verification pass bringing the disk to the floor.
4. Rollback (if ever needed): reinstall the previous version — stale state keys are tolerated
   both ways. No state file migration exists or is needed.

## Open Questions

None blocking. Future candidates (out of scope here): progress-aware watchdog replacing the
scaled fixed timeout; optional chunk size for extremely deep first-time migrations.
