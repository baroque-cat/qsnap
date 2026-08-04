## Why

The multi-disk refactor made config, state, and pipeline modules per-disk aware, but five defects still violate the core principle that **each disk is an isolated entity with its own chain, state, checkpoints, and verification**:

1. `Core._verify_active_layer_match` picks ONE newest snapshot across all disks and compares every `domblklist` disk against it — guaranteed false positives on multi-disk VMs (violates the existing `triple-source-check` spec, which already requires per-disk matching).
2. `ActionRecord` and `BackupResult` carry no `disk` field — the audit trail, summary table, and any downstream consumer cannot tell which disk an action belongs to.
3. `qsnap fork` silently ignores `--dry-run` (global flag is set on Core but never consulted) — a destructive multi-GB convert runs when the user asked for a preview. A UX trap.
4. `qsnap restore` of ONE disk calls `reset_vm_state()` + `reset_target_state()`, wiping state for ALL disks of the VM and FULL-backup records of ALL VMs sharing a target; checkpoint cleanup deletes checkpoints of all disks, breaking other disks' incremental bitmap lines.
5. `fork`/`restore` run raw `qemu-img convert` with no post-convert verification, no retry, and leave partial output files behind on failure — below the reliability bar the pipeline enforces elsewhere (M1/M2 verification, retry with backoff).

## What Changes

- **F1**: `_verify_active_layer_match` groups snapshots by `SnapshotInfo.disk` and compares each `domblklist` disk against its own newest snapshot; disks without snapshots are skipped; mismatch messages name the disk.
- **F2**: `ActionRecord` and `BackupResult` gain `disk: str | None = None`. All 7 `ActionRecord` creation sites and `BitmapBackupProvider.transfer_missing` populate it. Summary lines render a `[disk]` prefix when present. **BREAKING** for `IBackupProvider` implementations and mocks (result contract widens).
- **F3**: `fork` gains a local `--dry-run` flag (mirroring `reconcile`); `Core.fork()` consults `self._dry_run` after the read-only chain-size estimate, logs the plan, and returns without creating any file.
- **F4**: **BREAKING** — `IStateManager` gains `reset_vm_disk_state(vm_name, disk)` and `reset_target_disk_state(target_path, vm_name, disk)`. Restore step 8 switches from full resets to these per-disk resets; step 9 deletes only checkpoints whose name encodes the restored disk. Existing full-reset methods remain for their current spec'd uses. No JSON schema migration needed (methods operate on the existing schema).
- **F5**: New stateless utility `qsnap/utils/convert.py` — `convert_to_standalone()` (best-effort partial-file cleanup on failure), `verify_standalone_image()` (M1 virtual-size + M2 `qemu-img check`), `convert_with_retry()` (reuses `backup_retry_max`/`backup_retry_base` via existing pure retry utils). `fork` and `restore` route through it; restore verifies the tmp image BEFORE `os.replace`.
- Transaction log line format is explicitly UNCHANGED (btrbk-compatible 6 fields; disk already encoded in file paths).

## Capabilities

### New Capabilities

- `standalone-image-conversion`: stateless convert/verify/retry helpers for flattening a backing chain into a standalone qcow2, shared by `fork` and `restore` (conversion command contract, M1/M2 output verification, retry policy, partial-file cleanup).

### Modified Capabilities

- `triple-source-check`: active-layer match requirement made explicit per disk (code currently violates the existing per-disk wording; delta pins the grouping algorithm and skip-when-no-snapshots behavior).
- `result-types`: `ActionRecord` and `BackupResult` gain optional `disk` field. **BREAKING** (result contract consumed by all `IBackupProvider` implementations).
- `action-audit-trail`: every disk-scoped action record MUST carry its disk; VM-level records carry `None`.
- `backup-summary`: summary lines render a `[disk]` prefix for disk-scoped actions; VM-level lines unchanged.
- `transaction-log`: line format explicitly frozen (6 btrbk-compatible fields); disk is carried in file paths, not as a new column.
- `backup-provider`: `transfer_missing` and `create_full_backup` MUST return `BackupResult` with `disk` populated. **BREAKING** (ABC return contract).
- `fork-mode`: dry-run behavior added; convert routed through the shared helper with verification, retry, and partial-file cleanup.
- `cli-interface`: `fork` subcommand accepts local `--dry-run` in addition to the global `-n`.
- `state-management`: two new per-disk reset methods added to `IStateManager` with exact clearing semantics. **BREAKING** (ABC gains abstract methods; all implementations and mocks must implement).
- `restore-command`: step 8 switches to per-disk state resets (other disks and other VMs on shared targets untouched); step 9 deletes only the restored disk's checkpoints; tmp image verified before atomic replace.
- `core-orchestrator`: restore/fork orchestration steps updated to match the above.

## Impact

- **Code**: `qsnap/models/results.py`, `qsnap/core/__init__.py` (`_verify_active_layer_match`, `fork`, `restore`, `_cleanup_checkpoints_after_restore`, 7 `ActionRecord` sites), `qsnap/interfaces/state.py`, `qsnap/interfaces/backup.py` (docstring contract), `qsnap/state/json_manager.py`, `qsnap/modules/backup/bitmap.py`, `qsnap/cli/app.py`, `qsnap/cli/commands.py`, `qsnap/cli/summary.py`, new `qsnap/utils/convert.py`.
- **ABCs (BREAKING)**: `IStateManager` (+2 methods), `IBackupProvider` (result contract). All implementations (`JsonStateManager`, `BitmapBackupProvider`) and all mocks (`InMemoryStateManager`, `MockBackupProvider`/`MockBitmapBackupProvider`, contract tests) must update in the same change.
- **Factory**: unchanged — no new `create_*` branches (the convert helper is a stateless utility, not a module; precedent: `scan_backing_chain`).
- **State files**: no schema change; new reset methods read/write existing keys. Legacy `last_allocation` integers are treated as absent by per-disk clearing.
- **Config**: no new user-facing options; retry reuses `backup_retry_max` / `backup_retry_base`.
- **Tests**: contract, unit, mock-parity, integration, and e2e suites affected (detailed in test-plan.md).
