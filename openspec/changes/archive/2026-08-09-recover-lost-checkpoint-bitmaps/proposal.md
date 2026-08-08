# Proposal: recover-lost-checkpoint-bitmaps

## Why

After an unclean host shutdown (power loss, hard reboot), QEMU discards persistent dirty
bitmaps whose `in_use` flag was never cleared (qcow2 spec: unsynced bitmaps "must be
considered inconsistent"). The libvirt checkpoint metadata that references those bitmaps
survives, leaving **orphaned checkpoints**: `virsh checkpoint-list` shows them, but
`virsh backup-begin` fails with `checkpoint inconsistent: missing or broken bitmap`.

Production incident 2026-08-08: a power outage between two runs left all 3 VMs with dead
checkpoint bitmaps. Every subsequent `qsnap run` failed with exit 10 (`BackupAbortError`),
forever: qsnap discovers checkpoints **by name only** (no bitmap health check anywhere in
the codebase), has no recovery branch matching the error, never deletes the broken
checkpoint, and keeps creating snapshots on each failed run — widening the protection gap.
The dry-run predicted "Would create delta backup since checkpoint ..." with no warnings,
because it performs no integrity probes at all.

The archived change `2026-08-08-orthogonalize-snapshots-and-backups` assumed "existing
checkpoints remain valid baselines" — an assumption that does not survive unclean shutdowns.
qsnap must detect this state, heal itself without operator intervention, and the dry-run
must stop lying about it.

## What Changes

- **Bitmap health probe (read-only)**: before attempting a delta, `BitmapBackupProvider`
  probes whether the newest checkpoint's dirty bitmap exists and is consistent —
  `virsh qemu-monitor-command` (`query-named-block-nodes`) for running VMs,
  `qemu-img info -U --backing-chain` for stopped VMs. Result: `HEALTHY | DEAD | UNKNOWN`.
- **Crash-evidence collection**: qsnap persists the host `boot_id` in state after each
  successful run; a changed boot_id plus a dead bitmap plus a covering backup file is
  reported as "unclean host shutdown detected" via WARNING — **exit 0 on successful
  recovery, never exit 10**.
- **Recovered delta (primary recovery path)**: when the bitmap is DEAD and gates G1–G3
  pass (no commit since the checkpoint freeze — `last_commit_ts` state marker; live chain
  matches snapshot state; post-freeze overlays readable), qsnap builds an
  allocation-superset delta: it copies all data and zero extents from the overlays that
  were live since the checkpoint freeze point into a new backup chained onto the newest
  existing target backup. No FULL transfer needed (~21 GiB vs ~46 GiB in the incident).
- **FULL fallback with immediate retirement**: when any gate fails (or the recovered delta
  itself fails), qsnap falls back to FULL in the same run. Only after the new FULL passes
  M1/M2 verification are the dead checkpoint deleted and the superseded generation retired
  immediately — bypassing `keep_generations` in the recovery path only.
- **Reactive backstop**: if the probe missed it and `backup-begin` returns
  `checkpoint inconsistent: missing or broken bitmap`, the provider deletes exactly that
  checkpoint and retries once (recovered delta if gates pass, else FULL). The infinite
  failure loop is eliminated under all interleavings.
- **Dry-run parity**: dry-run executes every read-only check the real run performs —
  bitmap probe, blockjob probe, read-only startup-validation part — and predicts the exact
  recovery outcome ("recovered-delta ~21 GiB, gates OK" or "FULL, gate failed: ...").
  Principle: **dry-run = real run minus mutations**. The latent mutation bug in
  `_check_orphan_checkpoint` (checkpoint-delete without dry-run guard) is fixed.
- **Startup invariant extension**: a checkpoint whose bitmap is dead is an orphan even when
  a covering backup file exists; it is detected at startup and (in real runs) removed.
- **State schema additions (additive, optional)**: `boot_id` and per-disk `last_commit_ts`.
  Absent fields (pre-feature state) force the conservative FULL path — no data migration.
- **BREAKING**: `IBackupProvider` gains one read-only assessment method used by Core's
  dry-run prediction. All implementations and mocks must implement it.

## Capabilities

### New Capabilities

- `checkpoint-bitmap-health-probe`: read-only assessment of a checkpoint's dirty bitmap
  (present and consistent vs missing/inconsistent vs unknown), for running and stopped VMs,
  exposed through the backup provider for use by both real runs and dry-run prediction.
- `bitmap-loss-recovery`: end-to-end self-healing when a checkpoint's bitmap is lost —
  crash evidence and WARNING without error exit, gates G1–G3, the recovered
  allocation-superset delta lifecycle, FULL fallback with post-verification retirement of
  the superseded generation, reactive backstop on `checkpoint inconsistent` errors, and a
  clean, warning-free next run.

### Modified Capabilities

- `nbd-bitmap-backup`: `run_backup` performs the bitmap health probe before choosing the
  delta path and routes DEAD baselines into recovery instead of failing; the
  `checkpoint inconsistent` error class is handled (today only `bitmap already exists`
  collisions are); `BackupResult` reports the backup kind (full / delta / recovered_delta).
- `backup-provider`: **BREAKING** — `IBackupProvider` gains a read-only baseline assessment
  method (health status + reason + size estimate) consumed by Core dry-run prediction.
- `startup-state-validation`: the crash-orphan checkpoint invariant additionally treats a
  dead-bitmap checkpoint as an orphan even when a covering backup file exists; checkpoint
  deletion gains the missing dry-run guard.
- `dry-run-prediction`: backup prediction runs all read-only probes of the real path
  (bitmap health, blockjob, startup-validation read-only part) and predicts recovery
  outcomes; zero-mutation invariant enforced for checkpoint deletion.
- `state-management`: new optional state fields — host `boot_id` (recorded on successful
  run completion) and per-disk `last_commit_ts` (recorded after successful blockcommit /
  qemu-img commit). Absence is well-defined (conservative behavior), no migration.
- `backup-target-orthogonality`: baseline validity is refined (a checkpoint is a valid
  delta baseline only while its bitmap is healthy) and a scoped exception is codified: the
  recovery path may consult snapshot-state timestamps to compute the copy set; the normal
  backup path remains fully orthogonal.
- `per-chain-retention`: recovery exception — a generation superseded by a recovery FULL is
  retired immediately after the new FULL passes verification, regardless of
  `keep_generations` (verify-before-delete gates still apply to the deleted FULL).
- `size-estimation`: adds recovered-delta size estimation (sum of `actual-size` of the
  post-freeze overlay set) used by dry-run prediction and the free-space gate.

## Impact

- **Affected modules**: `interfaces/backup.py` (BREAKING ABC change),
  `interfaces/state.py`, `modules/backup/bitmap.py` (probe, gates, recovered-delta
  lifecycle, reactive backstop), `core/__init__.py` (boot_id/last_commit_ts recording,
  dry-run prediction, startup invariant, immediate retirement), `state/json_manager.py`
  (new optional fields), `models/results.py` (`BackupResult.kind`), `utils/space.py`
  (estimator), `cli/summary.py` (prediction rendering for recovered-delta).
- **Mocks/tests**: `MockBackupProvider` and `InMemoryStateManager` must implement the new
  members; contract tests parametrized over all implementations.
- **Factory**: no new `create_*` branch — recovery lives inside `BitmapBackupProvider`;
  `DefaultFactory` unchanged.
- **Dependencies**: none added — probes use `virsh` / `qemu-img` through the existing
  `IShell`.
- **State files**: additive optional fields in per-VM JSON (`boot_id`) and a new per-disk
  marker (`last_commit_ts`); old state files remain fully readable.
- **External behavior**: successful recovery exits 0 with WARNING (previously exit 10
  forever); dry-run output gains recovery predictions; one extra read-only QMP call per
  delta attempt.
- **Builds on**: archived change `2026-08-08-orthogonalize-snapshots-and-backups`
  (invalidates its "existing checkpoints remain valid baselines" assumption).
