# Startup State Validation

## Purpose

Lightweight state-vs-disk validation at pipeline startup that detects and cleans phantom FULLs and stale per-disk `last_backup_allocation` baselines before the onchange gate runs. Detected broken backup chains are preserved for operator review rather than auto-deleted.

## Requirements

### Requirement: Startup state validation before onchange gate
The system SHALL perform a lightweight state-vs-disk validation at the start of the pipeline (before snapshot and backup steps) that detects and cleans phantom FULLs and stale per-disk `last_backup_allocation` baselines. It SHALL also detect broken backup chains and log a CRITICAL message preserving the file for operator review.

#### Scenario: Startup validation cleans phantom FULLs
- **WHEN** the pipeline starts for a VM and a FULL backup record exists in state whose file does not exist on disk
- **THEN** the system SHALL remove the phantom FULL record from `_full_backups.json`
- **AND** SHALL remove all linked incremental dependencies from `_dependencies.json` via `remove_all_incremental_dependencies()`
- **AND** SHALL log a WARNING

#### Scenario: Startup validation clears stale per-disk baselines when no FULLs remain after phantom cleanup
- **WHEN** the pipeline starts for a VM and after phantom cleanup no FULL records remain for a target, but per-disk `last_backup_allocation` baselines still exist in `_target_state.json`
- **THEN** the system SHALL call `clear_last_backup_allocation(target_path, disk.target)` for each configured disk
- **AND** SHALL log an INFO message

#### Scenario: Startup validation clears stale per-disk baselines when no FULLs in state
- **WHEN** the pipeline starts for a VM and no FULL backup records exist in state for a target, but per-disk `last_backup_allocation` baselines exist in `_target_state.json`
- **THEN** the system SHALL call `clear_last_backup_allocation(target_path, disk.target)` for each configured disk
- **AND** SHALL log an INFO message

#### Scenario: Startup validation is non-fatal
- **WHEN** the pipeline starts and state validation encounters an error (e.g., corrupt JSON file)
- **THEN** the system SHALL log a WARNING and continue the pipeline without raising an exception

#### Scenario: Startup validation runs for standalone backup
- **WHEN** `qsnap backup` is invoked (standalone backup without snapshot steps)
- **THEN** the system SHALL run state validation at the start of `_execute_backup_steps` before processing targets

### Requirement: Broken backup chain detection at startup
After phantom FULL cleanup, the system SHALL detect broken backup chains on each target. For each non-FULL backup on the target, the system SHALL run `qemu-img info --force-share --backing-chain --output=json` via `IShell`. If the command fails (broken chain), the system SHALL log a CRITICAL message and PRESERVE the file for operator review — it SHALL NOT auto-delete. The operator must run `qsnap reconcile` to clean up or restore from snapshots. This detection SHALL run BEFORE retention evaluation.

#### Scenario: Broken-chain backups detected and preserved
- **WHEN** a target has FULL + inc1 + inc2 + inc3, and inc2's backing file has been deleted
- **THEN** the system runs `qemu-img info --backing-chain` on each non-FULL backup
- **AND** inc2 and inc3 are detected as broken (verification fails)
- **AND** a CRITICAL log is emitted preserving the files for operator review
- **AND** the broken files are NOT deleted from the target

#### Scenario: No broken chains — no recovery needed
- **WHEN** all backups on a target have intact backing chains
- **THEN** no broken-chain detection log is emitted
- **AND** the pipeline continues normally

#### Scenario: Auto-recovery forces FULL when no valid FULL remains
- **WHEN** broken-chain detection finds no valid FULL remains on a target (all FULL files missing from disk)
- **THEN** the target path is added to `_force_full_targets`
- **AND** on the next `_backup_target()` call, a new FULL is created unconditionally for every disk
- **AND** the force-full flag is cleared after FULL creation

#### Scenario: Auto-recovery verification error does not abort pipeline
- **WHEN** `qemu-img info --backing-chain` times out for one backup
- **THEN** a WARNING is logged for that backup
- **AND** broken-chain detection continues with the next backup
- **AND** the pipeline continues normally

#### Scenario: Auto-recovery skipped in preserve or dry-run mode
- **WHEN** `--preserve-backups` or `--dry-run` is active
- **THEN** the broken-chain detection loop is skipped entirely
- **AND** no `qemu-img info --backing-chain` commands are executed

### Requirement: Orphan checkpoint invariant at startup

At pipeline startup, for each configured (target, disk) of each VM, the system SHALL verify that the newest `qsnap-{target_hash}-{disk}-*` checkpoint has a corresponding backup file on the target whose `mtime` is greater than or equal to the checkpoint's name timestamp. If no such file exists, the checkpoint is an orphan of a crashed or killed export (created at `backup-begin` but never completed to a final file) and the system SHALL delete it best-effort via `virsh checkpoint-delete` with `--metadata` fallback and log a WARNING naming the checkpoint and target. Deleting the orphan makes the previous checkpoint newest again, so the next export re-covers the interval with no coverage gap. The invariant SHALL be non-fatal: deletion failures log a WARNING and the pipeline continues. When freeze-timestamp backup naming is in effect (capability `backup-target-orthogonality`), the check MAY match by exact freeze-ts equality between checkpoint name and backup file name instead of `mtime`.

#### Scenario: Orphan checkpoint deleted at startup

- **WHEN** the newest checkpoint for (target, vda) is `qsnap-abc12345-vda-20260808T030000-aa11bb`
- **AND** no backup file on the target has `mtime >= 2026-08-08T03:00:00`
- **THEN** the checkpoint is deleted best-effort
- **AND** a WARNING log names the checkpoint and the target
- **AND** the next backup for that disk uses the previous checkpoint as baseline

#### Scenario: Healthy checkpoint kept

- **WHEN** the newest checkpoint has a matching backup file with `mtime >= checkpoint ts`
- **THEN** the checkpoint is kept and no warning is logged

#### Scenario: Invariant failure is non-fatal

- **WHEN** checkpoint deletion fails (e.g., libvirt error)
- **THEN** a WARNING is logged
- **AND** the pipeline continues without raising
