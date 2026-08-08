# Design: recover-lost-checkpoint-bitmaps

## Context

qsnap's incremental backups depend on libvirt checkpoints whose dirty bitmaps live inside
qcow2 files. After an unclean host shutdown, QEMU discards bitmaps that were not cleanly
synced (qcow2 spec: `in_use` flag / autoclear consistency bit), while the libvirt
checkpoint metadata survives. The 2026-08-08 production incident showed the consequences:

- Checkpoint discovery is **name-only** (`bitmap.py:1324-1491`): `virsh checkpoint-list`
  plus name parsing. No bitmap introspection exists anywhere in the codebase.
- The startup orphan invariant (`core/__init__.py:3280-3385`) declares a checkpoint healthy
  when a covering backup file exists — true in the incident, so the dead checkpoint was kept.
- No error branch matches `checkpoint inconsistent: missing or broken bitmap`
  (`_is_collision_error` matches only "already exists"; `is_retryable` matches only network
  patterns) → `BackupAbortError` → exit 10 on every run, forever, while each failed run
  still creates snapshots.
- Dry-run prediction (`core/__init__.py:5142-5187`) uses the same name-only data and skips
  the blockjob probe and startup validation — it predicted "delta since checkpoint ..."
  minutes before the real run failed.

Constraints: zero new runtime dependencies (probes must use existing `virsh`/`qemu-img` via
`IShell`); modules remain stateless workers behind ABC interfaces; Core is the only
coordinator; expected failures return result objects, never exceptions; dry-run must not
mutate anything.

## Goals / Non-Goals

**Goals:**

- Detect dead checkpoint bitmaps before attempting a delta (real run and dry-run).
- Self-heal without operator intervention: recovered delta when provably safe, FULL
  otherwise; successful recovery exits 0 with a WARNING.
- Make dry-run honest: it performs every read-only check of the real path and predicts the
  exact recovery outcome.
- Eliminate the infinite failure loop under all interleavings (probe miss included).
- Persist the minimal evidence needed for accurate log messages and safe gating
  (`boot_id`, `last_commit_ts`) with an additive, migration-free state schema.

**Non-Goals:**

- Preventing bitmap loss itself (QEMU behavior by design; unclean host death always kills
  unsynced bitmaps).
- An active blockcommit guard that defers commits whose range contains checkpoint bitmaps
  (follow-up change; this change lays the `last_commit_ts` foundation it needs).
- Guest-agent quiescing for recovery transfers (parity with today's crash-consistent model).
- Reporting dead-bitmap checkpoints in `qsnap check --state` (follow-up; `check` remains
  target-hash-orphan-only).
- Multi-disk single-freeze-point backups.

## Decisions

### D1 — Probe mechanism and placement

The health probe lives in `BitmapBackupProvider` (checkpoint/bitmap domain) and runs via
`IShell`:

- Running VM: `virsh qemu-monitor-command --domain <vm> '{"execute":"query-named-block-nodes"}'`;
  healthy ⟺ some node of the disk's chain advertises a dirty bitmap named exactly as the
  checkpoint with `inconsistent` false.
- Stopped VM: `qemu-img info -U --backing-chain --output=json` from the top layer; scan all
  chain nodes' `dirty-bitmaps` sections (the bitmap stays in the layer that was active when
  the checkpoint was created).

Result is a tri-state `HEALTHY | DEAD | UNKNOWN`.

*Alternatives considered:* `virsh checkpoint-dumpxml --size` (requires a running domain,
error semantics for dead bitmaps are hypervisor-dependent — rejected as primary probe);
reactive-only handling via `backup-begin` failure (rejected as the sole mechanism: dry-run
needs the probe, and a wasted failing `backup-begin` per run is exactly the incident
behavior). QMP was chosen because it is read-only, cheap, and reports the `inconsistent`
flag directly.

### D2 — UNKNOWN never blocks

If the probe cannot conclude (QMP unavailable, timeout, unparseable output), qsnap behaves
exactly as today: attempt the delta. The reactive backstop (D9) catches the failure. The
probe is an optimization and a dry-run enabler, never a new failure mode.

### D3 — Crash evidence is for log accuracy, not gating

`boot_id` (`/proc/sys/kernel/random/boot_id`) is recorded in per-VM state after each
successful run. At recovery time, a changed boot_id plus a covering backup file yields the
log line "unclean host shutdown detected". Evidence does **not** gate recovery: the
operational fact is the dead bitmap, whatever the cause. *Alternative considered:* gating
recovery on boot_id change — rejected (bitmaps can die from other causes; healing must not
depend on diagnosing the cause).

### D4 — Gates G1–G3 decide recovered delta vs FULL

A recovered delta is built only when all gates pass; any failure → FULL:

- **G1** no blockcommit/`qemu-img commit` touched this disk's chain since the checkpoint
  freeze. Source: new persistent per-disk marker `last_commit_ts`, written by Core after
  every successful commit. **Marker absent (pre-feature state) → gate fails** (conservative).
  Rationale: commits move allocations into lower layers; the copy-set computation (D5)
  would then miss blocks → corrupt backup.
- **G2** live backing chain matches snapshot state: every post-freeze snapshot present, in
  order (guards against external interference).
- **G3** every post-freeze overlay is readable (`qemu-img info` succeeds).

### D5 — Copy set from snapshot-state timestamps (scoped orthogonality exception)

Copy set `S` = the layer active at the checkpoint freeze (newest snapshot with
timestamp ≤ freeze-ts) plus all layers created after. Timestamps come from per-VM snapshot
state. This is a **scoped exception to backup-target orthogonality**: only the recovery
path may consult snapshot state, only timestamps, only to compute `S`; the normal backup
path remains fully orthogonal (codified in the spec delta). If snapshot state is
incomplete, `S` falls back to all overlays above `base_image` — a larger but still correct
superset.

*Alternative considered:* always copy all overlays (no state lookup) — rejected: with chains
up to 72 snapshots the cost approaches a FULL, defeating the purpose.

**Correctness argument:** by qcow2 COW semantics a guest write can only land in the
topmost layer; therefore every write after the freeze is allocated in `S`. Blocks are
copied oldest→newest so newer content shadows older. Zero extents are copied explicitly
(guest discard creates zero clusters; skipping them would expose stale backing data).
Holes are the only extents skipped.

### D6 — Freeze point via `virsh checkpoint-create`

The recovered delta gets its own atomic freeze point `T'` and successor bitmap via
`virsh checkpoint-create` with the existing `write_checkpoint_xml` output (no `--quiesce` —
crash-consistent parity with today's `backup-begin`). No backup job is started, so no
`domjobabort` cleanup is needed. *Fallback:* if integration testing shows
`checkpoint-create` unsuitable on a supported libvirt version, use the proven
`backup-begin` FULL-XML + checkpoint-XML form and abort the unused job afterwards.

**Live-guest consistency:** writes after `T'` land in the successor bitmap; any block read
stale or torn during the copy is re-copied by the next regular delta. This is the same
consistency model as the existing NBD transfer. All layers of `S` except the topmost are
frozen by chain structure.

### D7 — Transfer mechanics reuse existing infrastructure

`qemu-img create -f qcow2 -b <newest target backup> -F qcow2 <tmp>`; existing
`_start_write_server` (qemu-nbd write server) for the target; each source layer served
read-only; the libnbd copy loop iterates `base:allocation` and copies data+zero extents.
Publish via `mv`, then verify: chain resolves to the FULL anchor + `qemu-img check`.
On failure: delete successor checkpoint, remove `<tmp>`, fall back to FULL **in the same
run**.

### D8 — FULL fallback: delete-after-verification, immediate retirement

In the FULL branch the user-mandated ordering is enforced: create FULL → M1/M2 verification
inside `run_backup` → only then delete the dead checkpoint and retire the superseded
generation. The retirement bypasses `keep_generations` (recovery path only); the deleted
FULL still passes the existing M1+M2 verify-before-delete gates. The ordering is also
structurally guaranteed: a failed `run_backup` raises `BackupAbortError` before
`_cleanup_backups` is reachable. In the recovered-delta branch nothing is retired — the old
chain is healthy and remains the foundation; only the dead checkpoint metadata is removed.

### D9 — Reactive backstop

New `_is_inconsistent_checkpoint_error` beside `_is_collision_error`: on
`checkpoint inconsistent` from `backup-begin`, delete exactly the named checkpoint and
retry once — recovered delta if gates pass, else FULL. Covers UNKNOWN probe results and
TOCTOU races. The infinite failure loop is eliminated under all interleavings.

### D10 — Dry-run parity

Rule: **dry-run = real run minus mutations.** Dry-run therefore executes the bitmap probe,
the blockjob probe (currently skipped at `core:5066`), and the read-only part of startup
validation (currently skipped at `core:3171-3174`), and predicts: recovered-delta with size
estimate and gate status, or FULL with the failed gate named. To support this without
breaking Core's ignorance of provider internals, `IBackupProvider` gains one read-only
assessment method (status + reason + size estimate) — **BREAKING** for implementations and
mocks. The latent mutation bug in `_check_orphan_checkpoint` (checkpoint-delete without
dry-run guard) is fixed: dry-run logs "Would remove orphaned checkpoint ..." only.

### D11 — Exit-code and audit semantics

Successful recovery (either branch) → WARNING + exit 0. Recovery is designed behavior, not
an abort. Only an exhausted recovery (both delta attempt and FULL fallback fail) follows
the existing `BackupAbortError` → exit 10 path. `BackupResult` gains `kind`
(`full | delta | recovered_delta`) for the action audit trail and summary rendering.

### D12 — Startup invariant extension

A checkpoint whose bitmap is DEAD is an orphan **even when a covering backup file exists**.
Real runs remove it (its bitmap is already gone — deletion destroys nothing); dry-run warns.
The existing crash-orphan check (no covering file) is unchanged.

## Risks / Trade-offs

- [QMP availability/permissions differ across hosts] → UNKNOWN keeps today's behavior;
  reactive backstop guarantees healing anyway.
- [`checkpoint-create` rejected on some libvirt versions] → D6 fallback via backup-begin;
  integration tests must cover the actually deployed libvirt.
- [Torn/stale reads while copying the live topmost layer] → successor bitmap marks every
  post-T' write; next delta re-copies. Same model as today's transfer.
- [Copy set over-includes pre-freeze writes] → harmless shadowing with identical content;
  size estimate is an upper bound, marked `~` in summaries.
- [`last_commit_ts` absent on upgraded installs] → one conservative FULL on first bitmap
  loss; acceptable and self-clearing.
- [Immediate retirement reduces restore-point redundancy] → the verified new FULL is a
  complete restore point before anything is deleted; explicitly mandated recovery policy.
- [Orthogonality exception invites scope creep] → exception is recovery-only, timestamp-only,
  and codified in the spec delta; normal backup path contract tests must keep failing on
  snapshot-data consumption.
- [Extra QMP call per delta attempt] → one read-only call; negligible against a multi-GiB
  transfer.

## Migration Plan

1. Deploy. State files gain optional fields lazily on first write; old files remain fully
   readable; no data migration.
2. First run on an incident-style system: probe detects DEAD bitmap → WARNING → recovered
   delta (gates pass) or FULL → dead checkpoint deleted → exit 0. Second run: clean delta,
   no warnings (acceptance criterion).
3. Rollback: old code ignores the new state fields. Note: rolling back before healing
   re-exposes the infinite failure loop on systems with dead checkpoints — heal first.

## Open Questions

- Should `qsnap check --state` also surface dead-bitmap checkpoints? (Proposed follow-up;
  keeps this change scoped.)
- Active blockcommit guard (defer commits whose range contains a checkpoint bitmap):
  scheduled as a follow-up change building on `last_commit_ts`.
