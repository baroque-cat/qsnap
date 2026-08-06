## Why

Three related fault-tolerance weaknesses survive in qsnap despite its conservative
preserve-over-delete philosophy:

1. **Disk-full (ENOSPC) is handled only passively.** A full target aborts the whole VM
   pipeline (other targets on different storage are never backed up), a full *state*
   directory crashes the entire process mid-run (`JsonStateManager._save` does not catch
   `OSError`), blockcommit ENOSPC raises instead of deferring, and nothing checks free
   space before starting a transfer. Recovery depends on the operator noticing the error;
   there is no first-class classification and no defined auto-resume contract.
2. **Quiesce (guest fs-freeze) covers only the first disk of a multi-disk VM.** Each disk
   is snapshotted by a separate `virsh snapshot-create-as` call, so disks 2..N are
   snapshotted *after* the guest agent has already thawed — multi-disk VMs get
   cross-disk-inconsistent snapshots even with `snapshot_quiesce = true`, and there is no
   all-or-nothing atomicity across disks at all.
3. **`snapshot_preserve_min` defaults to 0 (inactive).** With the default
   `snapshot_chain_length = 24` and hourly runs, a misconfiguration or a burst of runs can
   commit the entire local history down to the active layer, leaving zero local restore
   points. A safe default floor is missing.

All three are non-destructive-hardening changes: they strengthen failure isolation,
consistency, and recovery without weakening any existing protection.

## What Changes

**Task 1 — ENOSPC fault handling and auto-resume:**
- New pure helper `is_space_error(error)` classifies "no space left on device" / EDQUOT
  failures at a single point (`qsnap/utils/retry.py` or a new `qsnap/utils/space.py`).
- **Per-target isolation:** a space error during backup suspends *only the affected
  target* (its remaining transfers are skipped); other targets of the same VM continue;
  retention and cleanup still run for every target (deletion frees space — self-heal).
  Non-space failures keep the current `BackupAbortError` VM-abort behavior.
- **State-write protection:** `JsonStateManager._save` catches `OSError`, logs CRITICAL,
  and degrades to a controlled per-VM abort instead of crashing the whole process.
- **Blockcommit ENOSPC → deferral:** commit failures classified as space errors are queued
  in the deferred-operations queue with reason `enospc` instead of raising `RuntimeError`.
- **Proactive free-space gate:** before every FULL or incremental transfer, Core estimates
  the required space and checks `shutil.disk_usage` on the target; new global options
  `free_space_check` (`strict` | `warn` | `off`, default `strict`),
  `free_space_reserve` (bytes, default 0), `free_space_factor` (default 1.0).
- **New exit code 4** (`EXIT_DISKFULL`) reported when any VM/target run was limited by a
  space error.
- Behavioral contract: qsnap NEVER deletes data in reaction to ENOSPC; the only artifacts
  an interrupted transfer may leave are `.tmp` files (cleaned by existing pre-flight
  cleanup); the next scheduled run auto-resumes from the last good checkpoint because
  checkpoints/baselines/state are only advanced after success.

**Task 2 — atomic multi-disk snapshots with quiesce on all disks:**
- **BREAKING** `ISnapshotProvider` gains `create_multi(vm_config, specs, quiesce) ->
  list[SnapshotResult]`; the single-disk `create()` stays for compatibility and tests.
- `ExternalSnapshotProvider.create_multi` issues ONE
  `virsh snapshot-create-as --diskspec <disk>,file=... --diskspec ... --disk-only
  --atomic [--quiesce]` call: a single guest-agent freeze covers all disks, `--atomic`
  gives all-or-nothing creation. Lock-retry wraps the whole call; post-creation validation
  runs per file; one domblklist pivot check covers all disks.
- `Core._create_snapshot` generates all names/paths first, calls the provider once, and
  records state only on full batch success. The `index == 0` quiesce hack is removed
  (`quiesce = vm_config.snapshot_quiesce`). Any failure rejects the whole batch: nothing
  is recorded, leftover files are removed best-effort and caught by pre-flight orphan
  detection otherwise.
- Single-disk VMs are the degenerate case of the same code path.

**Task 3 — safe default for snapshot_preserve_min:**
- `GlobalConfig.snapshot_preserve_min` default changes **0 → 48**. With the default
  `snapshot_chain_length = 24`, the floor dominates: effective default retention becomes
  keep-newest-48 per disk (~2 days of hourly snapshots are never committed). Explicit
  `snapshot_preserve_min = 0` still disables the floor.
- `qsnap.toml.example` and the affected specs are updated to document the new default and
  its interaction with `snapshot_chain_length`.

## Capabilities

### New Capabilities

- `enospc-fault-handling`: first-class disk-full behavior — space-error classification,
  per-target suspension with continued retention/cleanup, state-write resilience,
  blockcommit deferral with reason `enospc`, proactive free-space gate before transfers,
  the never-delete-on-ENOSPC invariant, `.tmp`-only leftovers, and auto-resume semantics.

### Modified Capabilities

- `snapshot-provider`: new `create_multi` batch method (**BREAKING** ABC addition);
  multi-disk creation happens in a single `virsh snapshot-create-as` call with one
  `--diskspec` per disk.
- `quiesce-snapshot`: quiesce SHALL cover ALL disks of the VM in one guest-agent
  freeze/thaw cycle (replaces the first-disk-only requirement).
- `post-creation-validation`: validation gains batch semantics — if any disk's file fails
  validation the entire batch is rejected and nothing is recorded in state.
- `core-orchestrator`: the snapshot-creation step becomes one batch call per VM with
  all-or-nothing state recording; backup steps gain per-target space-error isolation
  (suspend target, continue others, still run retention/cleanup).
- `state-recovery`: state *writes* SHALL survive ENOSPC — `_save` catches `OSError` and
  degrades to a controlled per-VM abort instead of crashing the process.
- `deferred-operations`: blockcommit failures classified as space errors are deferred with
  reason `enospc` and drained by the next run like other deferred operations.
- `cli-interface`: new exit code `4` (`EXIT_DISKFULL`) for runs limited by disk-full
  conditions.
- `config-model`: `snapshot_preserve_min` default 0 → 48; new global fields
  `free_space_check` (default `strict`), `free_space_reserve` (default 0),
  `free_space_factor` (default 1.0).
- `config-parsing`: parsing/validation requirements for the three new free-space options
  (enum validation, non-negative numeric validation, global→VM inheritance).
- `snapshot-preserve-min`: the floor is active by default (48); documented interaction:
  when `preserve_min > snapshot_chain_length`, the floor dominates effective retention.

## Impact

- **Affected code:** `qsnap/interfaces/snapshot.py` (BREAKING: new ABC method),
  `qsnap/modules/snapshot/external.py`, `qsnap/core/__init__.py` (snapshot step, backup
  step isolation, pre-transfer space gate), `qsnap/state/json_manager.py` (`_save`
  resilience), `qsnap/models/config.py` (defaults + new fields), `qsnap/config/facade.py`
  (parsing/validation of new fields), `qsnap/utils/` (space-error classification,
  free-space estimation), `qsnap/cli/app.py` + `qsnap/errors.py` (exit code 4),
  `qsnap/modules/lifecycle/*` error paths via Core.
- **Mocks/tests:** `MockSnapshotProvider` must implement `create_multi`; contract tests
  parametrized over all `ISnapshotProvider` implementations; fixtures constructing
  `GlobalConfig()` without explicit `snapshot_preserve_min` must be audited (old default 0
  no longer holds).
- **Factory:** no new `IVMModuleFactory.create_*` branches — existing providers gain
  methods/behavior.
- **State schema:** no JSON schema changes (deferred queue reuses the existing `reason`
  string field; value `enospc` added). No migration needed.
- **Dependencies:** none added (stdlib `shutil.disk_usage` only).
- **Operational:** default installations keep ~2 days of local snapshots uncommitted after
  this change (disk-usage increase for snapshot dirs); operators who want the old behavior
  set `snapshot_preserve_min = 0` explicitly.
- **Builds on:** archived changes `2026-07-15-fault-tolerance-and-safety`,
  `2026-07-30-preserve-min-independent-onchange`, `2026-08-05-vm-level-isolation`,
  `2026-08-05-fix-per-disk-isolation`.
