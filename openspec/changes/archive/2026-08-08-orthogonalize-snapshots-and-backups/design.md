## Context

qsnap orchestrates two concerns that today are entangled: **local disk-only snapshots**
(virsh snapshot-create-as, retention, blockcommit) and **off-site backups to targets**
(libvirt checkpoints + NBD dirty-bitmap pull model). The backup phase consumes snapshot
data end-to-end: the transfer queue is the snapshot list, backup files are named after
snapshots, FULL names embed snapshot timestamps, and a "temporal mismatch" guard compares
checkpoint-name wall-clock seconds against snapshot-state microseconds.

Production incident: after the first successful cycle (FULL + checkpoint created ~1.2 s
after its source snapshot), every subsequent run fails the temporal check on the FULL's own
source snapshot → `BackupAbortError` → exit 10 for all VMs. Investigation (see `plan.md`)
proved this is not a data error — all files, checkpoints, and state are consistent — but a
category error between two time scales, and it exposed a family of related defects
(registry P1–P14 in `plan.md`).

Physics that constrains the design: an incremental NBD export is "blocks dirtied since the
baseline checkpoint, read live at export time". Exactly one consistency (freeze) point and
one delta per disk per run are possible; a delta carries no intrinsic point-in-time and
needs an external label; the checkpoint created at `backup-begin`'s freeze point is the only
bitmap baseline. libvirt's backup+checkpoint subsystem is self-contained and does not
require snapshots at all.

Constraints: Python ≥3.11 stdlib-only; DI paradigm per `AGENTS.md` (modules implement ABCs,
Core is the only coordinator, Result objects, factory-only instantiation); zero runtime
dependencies added; existing on-disk data (backups, checkpoints, JSON state) must remain
valid — **zero data migration**.

## Goals / Non-Goals

**Goals:**
- Unblock production immediately (Phase 1) without touching on-disk data.
- Make the backup (target) world self-contained: own work unit, own labels, own checks, no
  snapshot names/timestamps/state consumed (Phase 2).
- Per-disk failure isolation: one disk's failure never abandons other disks' transfers.
- Honest restore semantics: restore points are freeze points; labels do not lie.
- Close concurrency/crash holes: default locking, orphan-checkpoint invariant, blockjob probe.
- Keep the snapshot world unchanged in behavior (it remains the local point-in-time tool).

**Non-Goals:**
- Multi-disk single-freeze backup (one `backup-begin` for all disks) — deferred to a
  follow-up change after Phase 2 stabilizes.
- Changing checkpoint name format (`qsnap-{hash}-{disk}-{ts}-{hex}`) — retained.
- Automatic cleanup of legacy-format/orphan-hash checkpoints — existing `reconcile` path
  stays the only cleaner (scope discipline).
- Parallelizing disks or targets within a run — explicitly forbidden without a new design.
- Any data migration of existing backup files, checkpoints, or state files.

## Decisions

### D1. Two orthogonal worlds, Core is the only bridge
The snapshot world (trigger `snapshot_create`, retention, blockcommit, `{vm}.json` state)
and the target world (trigger `backup_create`, checkpoints, `_target_state.json` /
`_full_backups.json` / `_dependencies.json`) share no data. Core invokes them sequentially
under one lock. **Why:** every discovered defect traces to snapshot data leaking into the
target world. **Alternative considered:** keep the coupling and "fix" the temporal check
with a tolerance — rejected: the check compares two unrelated time scales; any tolerance is
a heuristic over a category error, and the snapshot-labeled delta remains a lie.

### D2. Work unit: one `run_backup(vm_config, target, disk)` per disk per run
`IBackupProvider` gains `run_backup` and loses `transfer_missing(snapshots)` and
`create_full_backup(source_snapshot)` (**BREAKING**). The provider decides: no checkpoint
for (target, disk) → FULL; checkpoint exists → delta since the newest checkpoint. Successor
checkpoint is created atomically at the freeze point (unchanged mechanics). **Why:** this is
the only unit of work the physics allows; it removes the queue, the backlog, and the entire
class of temporal defects. **Alternative considered:** keep a checkpoint-internal queue of
"pending deltas" — rejected: deltas are not queueable; the bitmap accumulates into one.

### D3. Labels: freeze-timestamp naming
New backups are named `{vm}.{freeze_ts}_{disk}_{hex6}.qcow2` (FULL: `.FULL.` infix), where
`freeze_ts` is the backup-begin freeze point (wall clock, seconds). Format stays compatible
with `parse_timestamp`/`parse_disk_from_snapshot_name`. **Why:** the freeze point is the
only honest point-in-time a backup carries; the checkpoint provides it for free.
**Alternative considered:** checkpoint-name-labeled files — rejected: file names must be
parseable without libvirt access (target-only tooling, restore on another host).

### D4. Zero data migration; chain resolution is physical
Old snapshot-named files and FULLs with snapshot timestamps remain readable: previous-backup
resolution walks target files filtered by disk and chains via qcow2 backing headers, not via
names. `_dependencies.json` accepts both legacy snapshot-name keys and new backup-name keys;
chain-length counting is key-format agnostic; legacy records expire through generation
rotation. The existing production checkpoint becomes the baseline of the first post-change
delta, which absorbs all accumulated changes in one gap-free file. **Why:** on-disk data is
consistent; any migration would add risk without benefit.

### D5. Phased delivery inside one change
Phase 1 (urgent, current model): anchor-based queue rule (transfer only snapshots newer than
the newest FULL anchor ts for that disk), temporal check removal, break→continue, stopped-VM
defer, orphan-checkpoint startup invariant, blockjob probe, locking default, error-message
attribution. Phase 2 (decoupling): everything spec-level (run_backup, BackupInfo, freeze-ts
naming, restore --at, dry-run rework). The Phase-1 anchor queue is a **transitional**
mechanism — it exists only between phases and is deleted by Phase 2; therefore it is
documented here, not in specs. **Why:** production unblocks with a small diff first; the
spec deltas describe the final state.

### D6. Stopped VMs: defer, do not fail (variant A — owner-approved)
VM stopped + checkpoint exists → `BackupResult(deferred=True)` (not a failure); the onchange
baseline is NOT updated, so the gate stays open and the first run after boot transfers the
full delta since the last checkpoint — no coverage gap. VM stopped + no checkpoint →
offline FULL via the existing `qemu-img convert` path. **Alternative considered (B):**
offline FULL on every open gate — rejected: full-disk copies per run on sleeping VMs,
pointless generation churn.

### D7. Restore: point-in-time with honest labels (owner-approved)
`restore --at <ts>` selects the **first restore point ≥ ts** (superset policy); the actually
used point is always logged. Legacy name resolution (snapshot/old backup names) is retained
as a compatibility shim mapping name → ts → `--at`. Local snapshots restore via the snapshot
chain (exact points while not blockcommitted) — untouched. New command
`qsnap list restore-points <vm>` enumerates real freeze points per target. **Why:** a delta
physically contains data up to its export moment; "restore to snapshot S" from a target was
never exact. **Alternative considered:** nearest point below ts — rejected: undershoots the
requested moment; superset is the safer default for recovery.

### D8. Failure isolation: continue + aggregate, abort after all disks
Definitive per-disk failures in the backup loop `continue` (partial file still deleted
immediately); Core audits and records successes, then raises `BackupAbortError` after all
disks were attempted. VM-level isolation between VMs is unchanged; ENOSPC still suspends
only the affected target. **Why:** the old `break` let one disk's failure silently abandon
every other disk's transfer in the batch.

### D9. Concurrency hardening
Default lockfile `/var/lib/qsnap/qsnap.lock` (parent dir auto-created); explicit
`lockfile = "off"` to disable; exclusive lock for mutating commands only — read-only
commands run unlocked (JSON state is written atomically, readers always see a complete
file). Startup invariant: the newest checkpoint for (target, disk) must have a backup file
with `mtime >= checkpoint ts`, else it is a crash orphan and is deleted best-effort
(mtime-based while file names are snapshot-derived; simplifies to freeze-ts equality after
Phase 2 naming). Blockjob probe before `backup-begin`/FULL convert defers the disk's backup
when a block job is still active (covers the timeout-orphaned `virsh blockcommit --wait`
case). **Alternatives considered:** LOCK_SH/LOCK_EX reader-writer split — rejected as
needless mechanics; checkpoint-by-name pairing — impossible before Phase 2 naming.

### D10. Error attribution
Backup failures are attributed to target and disk, never to snapshots: WARNING and
`BackupAbortError` texts name `target` + `disk` + reason; `VMRunResult`/error `ActionRecord`
gain disk/target fields. **Why:** snapshots are created successfully before the backup phase;
"snapshot(s) failed" misdirects operators.

## Risks / Trade-offs

- [Restore points are no longer snapshot-shaped; operator muscle memory expects hourly
  points] → `list restore-points` shows reality; docs state RPO = backup run frequency;
  exact points remain available from the local snapshot chain.
- [`run_backup` is a BREAKING ABC change — all implementations and mocks must update] →
  single provider + mock set in one repo; contract tests parametrized over both; tasks order
  interface → provider → Core → mocks → tests.
- [Phase 1→Phase 2 window ships a transitional anchor queue] → documented here (D5), removed
  by Phase 2 tasks; both phases land in this single change before archive.
- [Clock rollback (NTP) breaks newest-wins ordering of wall-clock names] → accepted
  limitation (pre-existing), documented; monotonic sequence in names is a possible future
  improvement.
- [mtime-based orphan-checkpoint invariant can false-positive if target files are copied
  without mtimes] → invariant deletes only when NO file qualifies; operators copying targets
  must preserve mtimes (documented); Phase 2 switches to exact freeze-ts equality.
- [Deferred backups on long-stopped VMs produce one large delta at boot] → expected and
  correct; size bounded by actual writes; stall detection covers long transfers.
- [Dry-run predictions become coarser ("a delta will be created") than per-snapshot lines] →
  accuracy over false precision: old lines predicted files that could never exist.

## Migration Plan

1. **Phase 1** (unblock): implement queue rule, check removal, continue-on-failure, defer,
   startup invariant, blockjob probe, locking, messages. Deploy → first run transfers the
   pending delta against the existing checkpoint; verify via `qsnap check --state` and
   `qsnap -n run`. No data touched. Rollback: revert the release (state/data unaffected).
2. **Phase 2** (decouple): interface → provider → Core → CLI (`--at`, `list
   restore-points`) → mocks/state/dry-run → AGENTS.md refresh. Old and new generations
   coexist on targets throughout. Rollback: revert; new-format files remain readable by the
   old code's chain walker (names parse, backing chains resolve) — old code re-queues nothing
   harmful since checkpoints, not state, drive baselines.
3. **Phase 3** (optional follow-up change): single-freeze multi-disk backup.

## Open Questions

None — all decisions approved by the owner: `--at` superset policy, stopped-VM variant A,
locking default + `"off"`, mitigations (`list restore-points`, blockjob probe, startup
invariant) included.
