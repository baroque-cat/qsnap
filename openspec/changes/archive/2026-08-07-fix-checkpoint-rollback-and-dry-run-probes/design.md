## Context

Two defects were confirmed by code-level analysis (see proposal):

1. `Core._cleanup_failed_checkpoint()` (`qsnap/core/__init__.py:5288-5327`) deletes every
   checkpoint matching `qsnap-{target_hash}-*` after a failed FULL post-create verification.
   Checkpoint names are disk-scoped (`qsnap-{hash}-{disk}-{ts}-{hex}`,
   `qsnap/modules/backup/bitmap.py:1742-1775`), so the bulk filter destroys: checkpoints of
   every other disk on the target, and the previous dirty-bitmap baseline of the failed disk
   itself. For stopped VMs, `create_full_backup` creates NO checkpoint
   (`bitmap.py:1508-1510`), yet the rollback still wipes all baselines. The rollback runs
   inside `_execute_with_retry` (`core:5039`), so up to `backup_retry_max` destructive
   cleanups can occur per run. The provider already knows the exact checkpoint name
   (`bitmap.py:1426`) and already deletes precisely its own checkpoint on transfer failure
   (`bitmap.py:1498`) — the information simply never reaches Core because `BackupResult`
   does not carry it.
2. In dry-run, FULL size estimation probes the simulated snapshot path — which by
   construction does not exist (`core:3459-3494`, `core:4880-4887`). `estimate_full_size()`
   (`qsnap/utils/space.py:49-59`) omits `check=True`, so `SubprocessShell` logs an ERROR for
   an expected failure, violating the shell-abstraction probe rule. Because simulated
   snapshots always have `timestamp = now`, `most_recent` is always the simulated snapshot,
   so every dry-run that predicts a FULL reproduces the noise. Two sibling probes already
   implement the correct patterns: `_estimate_chain_size` (`core:1543-1580`, `check=True`)
   and `_estimate_file_actual_size` (`core:1582-1605`, existence guard with fallback).

Constraints: zero ABC interface changes; additive result-object changes only; the codebase
principle "never delete what is not proven orphaned" (pre-flight cleanup philosophy); dry-run
zero-mutation invariant.

## Goals / Non-Goals

**Goals:**

- Rollback deletes exactly the checkpoint created by the failed FULL attempt — never another
  disk's checkpoint, never the previous baseline, never anything when no checkpoint was
  created.
- Dry-run on a fresh system emits no ERROR/WARNING from size estimation and produces a
  numeric FULL size estimate derived from existing files.
- All size-estimation probes comply with the shell-abstraction `check=True` probe rule.

**Non-Goals:**

- Changing checkpoint naming, rotation, or orphan-checkpoint reconciliation (existing
  mechanisms remain responsible for crash-leftover checkpoints).
- Changing the retry/backoff behavior of `_execute_with_retry`.
- Improving first-run `~0 B` allocation predictions (change-detection baseline absence is
  spec-mandated behavior).
- Renaming the `chain_length=%d` log field (cosmetic; separate change if desired).

## Decisions

### D1: Exact-name rollback via `BackupResult.checkpoint` (chosen)

Add `checkpoint: str | None = None` to `BackupResult`. `create_full_backup` sets it to the
successor checkpoint name on the running-VM path (after `backup-begin` succeeds) and leaves
it `None` on the stopped-VM path. `_cleanup_failed_checkpoint` deletes exactly that name via
`virsh checkpoint-delete --metadata` and treats failures as non-fatal (WARNING).

**Alternatives considered:**

- *Disk-scoped prefix filter* (`qsnap-{hash}-{disk}-*`): rejected — still deletes the
  previous baseline checkpoint of the failed disk.
- *Delete only the newest disk checkpoint* (timestamp sort): rejected — correct for the
  running path but indistinguishable from the stopped path, where nothing was created; would
  delete a healthy baseline.
- *Diff inventory* (list checkpoints before the attempt, delete the set difference): viable
  and Core-local, but adds an extra `virsh checkpoint-list` per FULL attempt and duplicates
  knowledge the provider already has. Rejected in favor of the explicit result field, which
  follows the result-object paradigm (results carry everything callers need).

### D2: `checkpoint is None` → delete nothing

When the name is unknown, Core SHALL NOT guess. This matches the codebase's
never-delete-on-suspicion principle. Crash-window leftovers (checkpoint created but process
died before returning) remain covered by orphan-checkpoint detection (`qsnap check --state`)
and `qsnap reconcile`, unchanged.

### D3: Dry-run FULL estimate falls back to the disk's `base_image` chain

When the source snapshot file does not exist (simulated snapshot), estimate from
`disk.base_image`'s backing chain instead. Rationale: `base_image` exists by pre-flight
validation, and a real FULL exports exactly the base chain plus a near-zero fresh overlay, so
the base-chain sum is a sound approximation. The fallback applies to BOTH dry-run consumers:
the prediction (`core:4925`) and the free-space gate estimate (`core:4887`), via one shared
helper so the two never disagree. When the source file exists (non-dry-run or mature-state
edge cases), behavior is unchanged.

**Alternative considered:** estimating from the current active layer via `virsh domblklist` —
rejected: more shell calls, and on a shut-off VM with pending overlays it equals the same
chain the base image anchors; `base_image` is simpler and already resolved per disk.

### D4: `check=True` on estimation probes

`estimate_full_size()` and `estimate_incremental_size()` pass `check=True` so expected
failures log at DEBUG. This is compliance with the existing shell-abstraction requirement;
the spec's audit call-site list gains `utils/space.py` so the obligation is explicit.

## Risks / Trade-offs

- [A future second `IBackupProvider` implementation forgets to populate `checkpoint`] →
  Mitigation: contract test in `tests/interfaces/test_backup_provider.py` asserting the
  field contract; Core's None-means-no-op makes omission safe (worst case: an orphan
  checkpoint handled by reconcile).
- [Crash between `backup-begin` and result return leaves a checkpoint Core cannot name] →
  Mitigation: unchanged orphan-checkpoint detection + reconcile path (documented in spec
  scenario).
- [`base_image` estimate slightly understates a real snapshot-chain size (missing overlay
  contribution)] → Acceptable: spec presents estimates as approximate; overlay contribution
  at FULL-decision time is bounded by the active layer's delta.
- [Fallback masks a genuinely broken base chain] → Mitigation: if `qemu-img info` fails even
  for `base_image`, the prediction degrades to "size unknown" (existing contract) at DEBUG;
  pre-flight validation and `qsnap check --deep` remain the authoritative chain-health
  signals.
- [Existing tests assert the bulk-filter behavior] → Mitigation: the test-plan task includes
  an explicit inventory-and-delete step for obsolete assertions.

## Migration Plan

No state-file, config, or CLI changes; `BackupResult.checkpoint` defaults to `None` so all
existing constructions remain valid. Deploy as a normal update. Rollback = revert the change;
no data migration in either direction.

## Open Questions

None — all decisions resolved above.
