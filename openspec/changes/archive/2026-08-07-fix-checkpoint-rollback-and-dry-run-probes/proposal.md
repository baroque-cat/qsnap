## Why

Two defects undermine rollback safety and dry-run trustworthiness:

1. **Over-broad checkpoint rollback.** `Core._cleanup_failed_checkpoint()` deletes every
   `qsnap-{target_hash}-*` checkpoint of the VM after a failed FULL verification — across
   ALL disks of the target and including the previous dirty-bitmap baseline of the failed
   disk itself. For a stopped VM the failed FULL creates no checkpoint at all, yet the
   rollback still wipes every existing baseline. Consequence: silent degradation of every
   affected disk's next incremental to an unplanned FULL pull. The requirement predates the
   multi-disk refactor (checkpoint names became `qsnap-{hash}-{disk}-{ts}-{hex}`) and the
   spec itself mandates the stale filter.
2. **Dry-run false alarms on fresh systems.** In dry-run, FULL size estimation probes the
   simulated snapshot path — a file the code knows does not exist — via `estimate_full_size()`
   which omits `check=True`, so `SubprocessShell` logs an ERROR and the prediction degrades
   to "size unknown" on every dry-run that predicts a FULL (always, on a fresh install).
   Operators reasonably read dry-run ERRORs as "do not run", so the false alarm erodes trust
   in the pre-flight check. Two sibling probes (`_estimate_chain_size` with `check=True`,
   `_estimate_file_actual_size` with an existence guard) already implement the correct
   pattern; `estimate_full_size()` does not follow it.

## What Changes

- `BackupResult` gains an optional `checkpoint: str | None = None` field carrying the exact
  libvirt checkpoint name created during the operation (backward compatible — additive field
  with default).
- `BitmapBackupProvider.create_full_backup()` populates `checkpoint` with the successor
  checkpoint name on the running-VM path (`backup-begin` succeeded) and leaves it `None` on
  the stopped-VM path (no checkpoint is created there).
- `Core._cleanup_failed_checkpoint()` deletes **exactly** the checkpoint named in
  `full_result.checkpoint`; when the name is `None` it deletes nothing (never delete on
  suspicion). The `qsnap-{target_hash}-*` bulk filter is removed.
- Dry-run FULL size estimation falls back to the disk's existing `base_image` backing chain
  when the source snapshot file does not exist (simulated snapshot), yielding a meaningful
  estimate on first-ever dry-runs instead of "size unknown".
- `estimate_full_size()` and `estimate_incremental_size()` probe calls gain `check=True`
  (expected failure → DEBUG, not ERROR), complying with the shell-abstraction probe rule.
- No ABC (`I`-prefix) interface changes. No `IStateManager` schema changes. No new
  `IVMModuleFactory.create_*` branches.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `core-orchestrator`: the `_cleanup_failed_checkpoint` rollback method requirement changes
  from bulk `qsnap-{target_hash}-*` deletion to exact-name deletion of the single checkpoint
  created by the failed attempt, with a no-op guarantee when no checkpoint was created.
- `result-types`: the `BackupResult` dataclass requirement gains the optional `checkpoint`
  field.
- `backup-provider`: the FULL backup creation requirement gains an obligation to report the
  created checkpoint name in `BackupResult.checkpoint` (running VM) and to report `None`
  when no checkpoint is created (stopped VM).
- `dry-run-prediction`: the FULL backup prediction requirement gains a base-image fallback
  for size estimation when the source snapshot is simulated (file absent), and a guarantee
  that estimation probe failures do not log above DEBUG.
- `shell-abstraction`: the probe-audit call-site list gains `utils/space.py`
  (`estimate_full_size()`, `estimate_incremental_size()`).

## Impact

- **Code:** `qsnap/models/results.py` (BackupResult field), `qsnap/modules/backup/bitmap.py`
  (`create_full_backup` result construction), `qsnap/core/__init__.py`
  (`_cleanup_failed_checkpoint`, dry-run FULL prediction block ~4886-4957),
  `qsnap/utils/space.py` (probe `check=True`).
- **Tests:** `tests/core/test_full_verification_pipeline.py` (rollback spy tests must assert
  exact-name deletion), `tests/integration/test_dry_run.py` (first-run prediction),
  `tests/models/test_results.py`, provider unit tests; obsolete bulk-filter assertions must
  be identified and removed.
- **Specs:** five delta spec files under this change; `core-orchestrator` rollback scenarios
  rewritten for multi-disk safety.
- **Runtime behavior:** rollback deletes fewer checkpoints (only the one created); dry-run
  output loses the spurious ERROR/WARNING and gains numeric FULL size estimates on fresh
  systems. No config, state-file, or CLI changes.
