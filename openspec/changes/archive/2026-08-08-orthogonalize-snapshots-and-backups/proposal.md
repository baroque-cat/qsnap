## Why

Production is blocked: after the first successful backup cycle (FULL + checkpoint), every
subsequent run aborts with `temporal mismatch` → `BackupAbortError` → exit 10 for all VMs,
so no incremental backups reach the targets. Root cause: the backup (target) world parasitizes
on the snapshot world — the transfer queue, backup file names, timestamps, and a protective
check are all derived from snapshots, although the physics of NBD backups (libvirt checkpoints
+ dirty bitmaps) is self-contained. The temporal check compares two incompatible time scales
(checkpoint-name wall-clock seconds vs snapshot-state microseconds) and fires on legitimate
data by construction. Investigation also exposed a family of related defects: batches of ≥2
transfers always break, one disk's failure kills other disks' transfers, stopped VMs abort
instead of deferring, `lockfile=None` silently disables locking, crash-orphaned checkpoints
open coverage gaps, and restore-point labels misrepresent the actual point in time.

## What Changes

**Phase 1 — urgent unblock (current model, minimal diff):**
- Replace the snapshot-based transfer queue with a single per-disk rule: transfer only
  snapshots newer than the newest FULL anchor timestamp for that disk; removes the
  same-run-only `full_source_names` mechanism.
- Remove the temporal mismatch check entirely (it contradicts design D3 chaining; the
  FULL←delta chain is gap-free by construction).
- Convert all definitive-failure `break` points in the transfer loop to `continue` so one
  disk's failure no longer abandons other disks' transfers; Core aggregates failures and
  aborts the VM only after all disks were attempted.
- Stopped VMs: incremental transfer defers (new `deferred` flag on `BackupResult`) instead
  of failing; the onchange baseline is NOT updated so the first run after boot transfers the
  full delta with no coverage gap.
- Startup invariant: a newest checkpoint with no backup file of `mtime >= checkpoint ts` is
  an orphan of a crashed export and is deleted best-effort (closes the coverage-gap window).
- Blockjob probe (`virsh blockjob`) before `backup-begin`/FULL convert; active block job →
  defer that disk's backup for this run (INFO, not an error).
- Locking hardened: default lockfile `/var/lib/qsnap/qsnap.lock`, explicit `lockfile = "off"`
  to disable, exclusive lock only for mutating commands (read-only commands run unlocked).
- Error attribution: `BackupAbortError` and the transfer-failure WARNING name the target and
  disk, not "snapshot(s) failed".

**Phase 2 — decoupling (orthogonality paradigm):**
- **BREAKING** `IBackupProvider` redesign: `run_backup(vm_config, target, disk)` replaces
  `transfer_missing(snapshots)` and `create_full_backup(source_snapshot)`; `list()` returns
  new `BackupInfo` model instead of `SnapshotInfo`; the provider never receives snapshot data.
- Core backup phase stops consuming snapshot state: no transfer queue, no snapshot-name file
  naming, no snapshot-timestamp FULL names; backup files are labeled by their own freeze
  point `{vm}.{freeze_ts}_{disk}_{hex6}.qcow2`; stale-state healing returns to the snapshot
  world.
- Zero data migration: old files remain readable (chain resolution walks backing chains, not
  names); `_dependencies.json` accepts both old (snapshot-keyed) and new (backup-keyed)
  records; existing checkpoints remain valid baselines.
- Restore semantics become honest: `restore --at <ts>` selects the first restore point ≥ ts
  (superset policy, actual point logged); legacy name resolution kept as a shim; new command
  `qsnap list restore-points <vm>` shows real freeze points per target.
- Dry-run predicts "delta/FULL will be created" from target-internal data (checkpoint
  presence, gate state) instead of phantom snapshot file names.
- Structural error attribution: `VMRunResult`/error `ActionRecord` carry disk and target.

**Phase 3 — multi-disk single freeze (optional, after Phase 2 stabilizes):**
- One `backup-begin` for all disks of a VM (single freeze point) with a domain-level
  checkpoint naming scheme and all-or-nothing checkpoint rollback on per-disk export failure.

## Capabilities

### New Capabilities
- `backup-target-orthogonality`: the governing paradigm — the backup (target) world is
  self-contained: its work unit is one `backup-begin` per disk per run, its baseline is the
  newest checkpoint, its files are labeled by their own freeze timestamps, and it SHALL NOT
  consume snapshot names, timestamps, or state.
- `restore-points-listing`: `qsnap list restore-points <vm>` — enumerates real restore points
  (freeze timestamps of FULL/delta chains) per target so operators see actual coverage.

### Modified Capabilities
- `nbd-bitmap-backup`: REMOVED "Temporal mismatch detection" requirement; checkpoint baseline
  semantics restated (newest-wins, gap-free chaining); stopped-VM defer behavior.
- `backup-provider`: **BREAKING** — new `run_backup`/`BackupInfo` contract replacing
  `transfer_missing`/`create_full_backup`; definitive failures `continue` instead of `break`;
  `deferred` result classification.
- `core-orchestrator`: anchor-based transfer queue (Phase 1); snapshot-free backup phase
  (Phase 2); per-disk failure aggregation; WARNING/abort message attribution to target+disk;
  blockjob probe before backup.
- `backup-summary`: error lines attributed to target and disk (not framed as snapshot
  failures).
- `action-audit-trail`: error `ActionRecord` carries `disk` and target path.
- `locking`: default lockfile path, `"off"` sentinel for explicit disable, exclusive lock
  restricted to mutating commands.
- `startup-state-validation`: orphan-checkpoint invariant (newest checkpoint must have a
  backup file with `mtime >= checkpoint ts`, else best-effort delete).
- `state-management`: incremental dependency records accept backup-name keys alongside legacy
  snapshot-name keys; chain-length counting is key-format agnostic.
- `restore-command`: `--at <timestamp>` point-in-time restore with first-point-≥-ts policy and
  actual-point logging; legacy name resolution retained as compatibility shim.
- `dry-run-prediction`: incremental/FULL predictions derived from target-internal data
  (checkpoint presence, onchange gate), not snapshot names.

## Impact

- **Affected modules:** `interfaces/backup.py` (**BREAKING** ABC change), `models/results.py`
  (`BackupInfo`, `BackupResult.deferred`, `VMRunResult` fields), `core/__init__.py` (queue
  rule, backup phase, startup invariant, blockjob probe, error messages),
  `modules/backup/bitmap.py` (temporal check removal, break→continue, freeze-ts naming,
  stopped-VM defer), `cli/app.py` + `cli/commands.py` (locking scope, `list restore-points`,
  `restore --at`), `config/facade.py` + `models/config.py` (lockfile default/`"off"`),
  `state/json_manager.py` (dependency key compatibility), `factory/default.py`
  (`create_backup_provider` signature unchanged — no new factory branches),
  `utils/` (no new external deps).
- **IVMModuleFactory:** no new `create_*` branches; existing `create_backup_provider`
  returns the redesigned provider.
- **State migration path:** none required. JSON state files under `/var/lib/qsnap/state/`
  stay valid; `_dependencies.json` readers accept both key formats; old-format records expire
  naturally through generation rotation. Backup files and checkpoints on targets are untouched
  and remain readable.
- **Config compatibility:** existing TOML configs keep working; `lockfile` gains a default
  value and the `"off"` sentinel; `qsnap.toml.example` updated.
- **Docs:** `AGENTS.md` refreshed (remove misleading `*Module(Core)` naming row in Phase 1;
  add orthogonality paradigm, locking contract, new pipeline in Phase 2).
- **Builds on archived changes:** `2026-07-28-chain-aware-retention-recovery` (introduced the
  temporal check being removed), `2026-07-30-preserve-min-independent-onchange` (target
  onchange already decoupled — untouched), `2026-07-21-atomic-backup-checkpoints` (checkpoint
  naming/freeze-point semantics preserved), `2026-08-05-vm-level-isolation` (VM-level
  isolation retained, extended with per-disk progress before abort).
