# Design: atomic-backup-checkpoints

## Context

In bitmap backup mode, qsnap chains backups as `FULL + incrementals`, where incrementality is driven by libvirt checkpoints (dirty bitmaps). Today checkpoints are created **post-hoc**, in separate `virsh checkpoint-create-as` calls that run *after* the data export they are meant to anchor:

- First run: `create_full_backup()` runs `virsh backup-begin` (no checkpoint XML), and only after the FULL finishes does `transfer_missing()` create a checkpoint (design D4, `bitmap.py:148-157`). The FULL export is frozen at `backup-begin` start (t0); the checkpoint bitmap starts at FULL completion (t1). Guest writes inside `[t0, t1]` that are never rewritten afterwards are in neither the FULL nor any later incremental — a silent restore hole. Observed in production on 2026-07-21: window ≈ 50 minutes, ≈ 17 GB of guest writes.
- Every incremental: the next baseline checkpoint is created after the export ends (`bitmap.py:294-295`), leaving the same shaped hole of `[export_start, export_end]`.
- Rotation order is *delete-prior-then-create-new* (`bitmap.py:275-295`). A crash in between leaves zero checkpoints; the next run re-baselines via the D4 checkpoint-only path and silently skips the whole delta since the last successful incremental.
- The D4 guard itself depends on state-recording timing (`get_full_backups()` must already contain the FULL). It failed to fire in production on 2026-07-21 (qsnap 0.2.0), producing a duplicate 17 GB transfer. Root cause unresolved — the correct move is to delete the fragile path, not to debug its timing.

`virsh backup-begin DOMAIN backup.xml checkpoint.xml` accepts a checkpoint XML as third positional argument and creates the checkpoint **atomically at backup-job start**: QEMU activates the new dirty bitmap at the export's freeze point, so writes during the export are tracked for the *next* incremental. This is the mechanism virtnbdbackup uses, and the reason `virDomainBackupBegin` takes a second XML document at the API level.

Constraints: zero runtime PyPI deps; all external calls via `IShell`; modules are stateless workers created by `DefaultFactory`; config immutable; Result objects instead of exceptions; libvirt backup API requires ≥ 7.2 per the libvirt knowledge base (current gate checks ≥ 6.0 — latent bug on the same code path).

## Goals / Non-Goals

**Goals:**
- Eliminate the first-run coverage gap (R1): the checkpoint baseline of a FULL run must be ≤ the FULL's freeze point.
- Eliminate the per-incremental coverage gap (R2): each incremental's successor baseline must be created atomically at that incremental's freeze point.
- Eliminate the zero-checkpoint crash window (R3): rotation must never delete the current baseline before its successor exists; degradation after crashes must be towards *redundant work*, never *silent data loss*.
- Remove the D4 checkpoint-only path and its state-timing dependency.
- Keep `FileCopyBackupProvider` behavior unchanged (it passes no checkpoint XML).
- Raise the libvirt capability gate to ≥ 7.2 (backup API baseline per libvirt docs).

**Non-Goals:**
- Changing Core's pipeline order (FULL-before-transfer stays) or bucket strategy.
- Changing rsync/file-copy transfer semantics.
- Parallel VM processing, retention changes, restore tooling.
- Push-model backups, TLS/networked NBD.

## Decisions

### D1: Atomic checkpoint XML on every bitmap-mode `backup-begin`

Every `virsh backup-begin` issued by `BitmapBackupProvider` (FULL via `create_full_backup()`, incremental via `transfer_missing()`) SHALL pass a generated checkpoint XML as the third positional argument:

```xml
<domaincheckpoint>
  <name>qsnap-{target_hash}-{yyyymmddTHHMMSS}</name>
</domaincheckpoint>
```

**Why atomic over post-hoc:** the bitmap baseline then coincides with the export freeze point by construction — no gap, no dependence on state-recording order, no separate `checkpoint-create-as` call.

**Why not "checkpoint before FULL" (alternative):** creating the checkpoint in a separate call *before* `backup-begin` also closes the gap (baseline ≤ t0), but adds a call, an ordering constraint, and a small stale-checkpoint window if the FULL then fails. Atomic is one call and one failure domain. Rejected.

**Why not "keep D4 and document the gap" (alternative):** a backup chain with a silent data-loss window is not a backup. Rejected.

### D2: Checkpoint names carry the creation timestamp

New format: `qsnap-{target_hash}-{yyyymmddTHHMMSS}` (local time, seconds resolution, same clock as snapshot naming). Uniqueness per creation is required because the checkpoint created with the FULL and the checkpoint created with the first incremental may otherwise derive from the same snapshot name. Legacy names (`qsnap-{target_hash}-{snapshot_name}`) remain parseable (see D3).

### D3: Prior discovery = newest-wins; rotation = create-atomically-then-delete-superseded

- Prior checkpoint for a run = the newest `qsnap-{target_hash}-*` checkpoint, ordered by embedded creation timestamp; legacy-format names are ordered by the timestamp embedded in the snapshot-name segment. Unparseable names sort oldest (conservative).
- After a successful **and verified** export, the provider deletes **all older** qsnap checkpoints for this VM+target (metadata-only `--metadata`), keeping exactly the one created in D1. Delete failures → WARNING, never a failed `BackupResult`.
- A crash between export and cleanup leaves a stale older checkpoint: harmless — newest-wins still picks the correct baseline; cleanup retries on the next successful run.
- The provider SHALL NOT delete the current newest baseline before its successor checkpoint exists (guaranteed by construction: deletion happens after the new checkpoint was created atomically with the export).

**Why not delete-then-create (current order):** it opens the zero-checkpoint crash window that degrades into silent re-baselining. Reversed order degrades into a harmless duplicate checkpoint.

### D4: Remove the checkpoint-only guard and `_create_checkpoint_only()` pipeline step

The `prior is None and FULLs in state → checkpoint-only, skip transfer` branch (`bitmap.py:148-157`) and its spec requirement are removed. After D1, a FULL run always leaves a checkpoint, so `prior is None` on a target that already has FULLs can only mean: crashed/migrated state, manual checkpoint deletion, or pre-change installation. In all these cases the correct behavior is a **full NBD export** (the pre-D4 fallback), which is safe: it costs transfer time but never loses data. This also removes the state-timing dependency that failed in production.

**Trade-off accepted:** the first incremental after a FULL transfers all blocks dirtied since the FULL *started* (e.g. 17 GB observed). This is the honest delta, not a duplicate. Documented in the spec; README gains a scheduling note (run FULLs during low write activity).

### D5: `nbd_full_export()` and `write_backup_xml()` gain optional checkpoint support

- New `write_checkpoint_xml(checkpoint_name) -> Path` in `qsnap/utils/nbd.py` (tempfile, same pattern as `write_backup_xml()`).
- `nbd_full_export(..., checkpoint_name: str | None = None)`: when non-None, appends the checkpoint XML path as third positional arg to `virsh backup-begin`. Default `None` preserves current behavior for `FileCopyBackupProvider` (running-VM FULL path) — file-copy mode creates no checkpoints.
- `BitmapBackupProvider.create_full_backup()` always passes a checkpoint name; `transfer_missing()` passes a fresh one per exported snapshot.
- Both XML temp files are removed in the same `finally` discipline as today (socket + domjobabort cleanup unchanged).

### D6: Libvirt gate raised to 7.2

`is_libvirt_new_enough()` threshold changes from 6.0 to 7.2 (libvirt KB: incremental backup API complete since 7.2; `<incremental>` XML element and checkpoint XML for `backup-begin` are exercised by this change). Factory fallback to `FileCopyBackupProvider` with WARNING is unchanged. Same code path, same spec sections — bundled here deliberately.

## Risks / Trade-offs

- [libvirt < 7.2 deployments silently flip to file-copy mode after upgrade] → WARNING log already exists; additionally call out in README/changelog. Acceptable: correctness over mode purity, and file-copy remains fully functional.
- [First incremental after a FULL is large (all writes during the FULL)] → documented as intended; README scheduling note; size is bounded by write rate × FULL duration, which the user controls via scheduling.
- [Checkpoint XML third arg unsupported on exotic/older virsh builds] → gated by D6; on failure of `backup-begin` the export fails cleanly (Result object), prior checkpoint untouched, retry-safe.
- [Legacy checkpoint names mixed with new names during ordering] → conservative sort (unparseable = oldest); worst case one redundant full export, never data loss.
- [Stale checkpoints accumulate if cleanup keeps failing] → cleanup retried every successful run; `qsnap check`/manual `virsh checkpoint-list` visibility; WARNING logs surface the failure.
- [libvirt creates checkpoint even when `qemu-img convert` later fails] → the new checkpoint exists but its baseline equals the failed export's freeze point; the *prior* checkpoint was NOT deleted (failure path), so newest-wins would pick the newer one whose export failed. Mitigation: on export/verify failure, the provider deletes the just-created checkpoint (best-effort, WARNING on failure), restoring prior as newest baseline.

## Migration Plan

1. Existing checkpoints in legacy naming remain valid baselines (D3 parses them); no user action required.
2. State JSON untouched — no schema migration (`IStateManager` API unchanged; bitmap provider stops *reading* `get_full_backups()` for transfer decisions).
3. First run after upgrade: newest-wins discovery picks the legacy checkpoint; first incremental is exported against it; a new-format checkpoint is created atomically; legacy one is deleted post-success. Seamless.
4. Rollback: reverting the code restores post-hoc behavior; checkpoints created by the new code are still valid `qsnap-` prefixed checkpoints that the old code discovers by prefix (old code takes `prior_checkpoints[-1]` from `virsh checkpoint-list` — ordering note: verify during implementation that old-code ordering tolerates new names; if not, document that rollback requires deleting the newest checkpoint manually).
5. Deployments with libvirt < 7.2 flip to file-copy on upgrade (D6) — announce in changelog.

## Open Questions

- Does `virsh checkpoint-list` output order need `--name` parsing plus timestamp sort, or is there a machine-readable creation-time field worth using (`virsh checkpoint-info`)? Implementation may use per-checkpoint `checkpoint-info` if sorting by name proves fragile — decision left to the implementer, spec only mandates newest-wins semantics.
- Should `qsnap check` report stale/legacy checkpoints? Nice-to-have; not in this change's critical path.
