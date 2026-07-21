# Proposal: atomic-backup-checkpoints

## Intent

Replace post-hoc checkpoint creation in bitmap backup mode with **atomic checkpoint creation inside `virsh backup-begin`** (third positional argument: a checkpoint XML). The dirty-bitmap baseline must always coincide with the backup job's point-in-time freeze, so that `FULL + incrementals` is a faithful, gap-free representation of the disk. This eliminates three correctness defects in the current design (D4) and removes the state-timing-dependent `checkpoint-only` code path entirely.

## Scope

**In scope:**
- `qsnap/utils/nbd.py` — extend `write_backup_xml()` / add `write_checkpoint_xml()`; extend `nbd_full_export()` to optionally pass a checkpoint XML to `virsh backup-begin`.
- `qsnap/modules/backup/bitmap.py` — `create_full_backup()` and `transfer_missing()` create checkpoints atomically with every `backup-begin`; checkpoint rotation becomes *create-new-atomically → delete-superseded-after-success*; removal of the D4 `checkpoint-only` guard (`bitmap.py:148-157`) and `_create_checkpoint_only()` as a pipeline step.
- Checkpoint naming and prior-checkpoint discovery (uniqueness per creation, newest-wins lookup, self-healing after crashes).
- Spec deltas: `nbd-bitmap-backup` (major rework), `backup-provider` (signature/invocation delta).
- Tests: new unit + integration coverage for atomic checkpoints; removal of tests that encode the obsolete D4 behavior.
- Secondary (same code path): raise the libvirt capability gate in `is_libvirt_new_enough()` from 6.0 to 7.2, matching the libvirt knowledge base requirement for the incremental backup API.

**Out of scope:**
- `FileCopyBackupProvider` transfer semantics (rsync path unchanged; it passes no checkpoint XML).
- Snapshot creation, retention engines, blockcommit lifecycle.
- Core pipeline step order (FULL-before-transfer is unchanged).
- Parallel VM processing.

## Approach

`virsh backup-begin --domain VM backup.xml checkpoint.xml` creates the checkpoint **atomically at backup-job start**. QEMU activates the new dirty bitmap at the freeze point, so writes during the export are tracked for the *next* incremental. The current code instead creates checkpoints in separate `virsh checkpoint-create-as` calls *after* exports finish, which opens three windows:

1. **First-run gap (R1):** FULL export is frozen at `backup-begin` start (t0), but the baseline checkpoint is created only after the FULL completes (t1). Blocks written in `[t0, t1]` and never rewritten are in neither the FULL nor any incremental — silent data loss on restore. Observed window in production: ~50 minutes / ~17 GB of guest writes.
2. **Per-incremental gap (R2):** same shape for every incremental export — new baseline is created after the export ends, losing `[export_start, export_end]` writes to previously-clean blocks.
3. **Crash window (R3):** rotation deletes the prior checkpoint *before* creating the new one (`bitmap.py:275-295`). A crash in between leaves zero checkpoints; the next run hits the D4 checkpoint-only path and silently re-baselines, skipping the entire delta since the last successful incremental.

Design D4 ("checkpoint-only when FULL exists in state") traded correctness for saving one full export, and made correctness depend on state-recording timing — the mechanism that failed to fire in production on 2026-07-21 (duplicate 17 GB transfer). Atomic checkpoints remove the temptation and the failure mode: the FULL run leaves a valid baseline by construction, so `transfer_missing()` always performs a real incremental against it.

**Consequence (intentional):** the first incremental after a FULL contains all blocks written since the FULL *started*. This is not a duplicate — it is the true delta. FULLs should be scheduled during low write activity.

Checkpoint naming gains a creation timestamp (`qsnap-{target_hash}-{yyyymmddTHHMMSS}`) so every atomic checkpoint is unique; prior discovery selects the newest `qsnap-{target_hash}-*` checkpoint via `virsh checkpoint-list`. After a successful, verified export, all superseded qsnap checkpoints for that target are deleted (metadata-only). A crash before deletion leaves a stale older checkpoint — harmless, since newest-wins lookup still yields the correct baseline; cleanup is retried on the next successful run. Degradation is always towards *redundant work*, never towards silent data loss.
