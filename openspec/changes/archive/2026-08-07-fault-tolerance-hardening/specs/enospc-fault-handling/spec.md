# ENOSPC Fault Handling

## Purpose

First-class disk-full (ENOSPC) behavior for qsnap: a single pure classifier for space
errors, per-target failure isolation during backup steps, state-write resilience,
blockcommit deferral on space errors, a proactive free-space gate before transfers, a
never-delete-on-ENOSPC invariant, and a defined auto-resume contract so the next
scheduled run completes interrupted work without operator intervention.

## ADDED Requirements

### Requirement: Space-error classification helper

`qsnap.utils` SHALL provide a pure function `is_space_error(error: str | None) -> bool`
that returns `True` when the error text indicates an out-of-space condition. The match
SHALL be case-insensitive and SHALL cover at least `no space left on device` and
`disk quota exceeded`. `None` or empty input SHALL return `False`. The helper SHALL NOT
perform I/O and SHALL NOT raise. All ENOSPC decisions in Core, state, and lifecycle paths
SHALL use this single helper.

#### Scenario: ENOSPC message classified

- **WHEN** `is_space_error("qemu-img: error writing: No space left on device")` is called
- **THEN** it returns `True`

#### Scenario: Disk quota classified

- **WHEN** `is_space_error("write error: Disk quota exceeded")` is called
- **THEN** it returns `True`

#### Scenario: Unrelated error not classified

- **WHEN** `is_space_error("connection refused")` or `is_space_error(None)` is called
- **THEN** it returns `False`

### Requirement: Per-target suspension on space errors in backup steps

When a FULL creation or incremental transfer fails with a space-classified error
(`is_space_error` returns `True`), Core SHALL suspend ONLY the affected target: the
remaining disks/transfers of that target SHALL be skipped, a CRITICAL log SHALL name the
target and the space condition, and `_execute_backup_steps` SHALL continue with the next
target of the VM. Retention evaluation and cleanup SHALL still run for the suspended
target. Non-space failures SHALL keep the existing `BackupAbortError` VM-abort behavior
unchanged. Verification failures (M1/M2/M3) SHALL NOT be treated as space errors and
SHALL still abort the VM before cleanup (verify-before-delete gate preserved).

#### Scenario: Full target suspends only itself

- **WHEN** target A's FULL transfer fails with "No space left on device"
- **AND** the VM also has target B on different storage
- **THEN** target A's remaining transfers are skipped
- **AND** target B is backed up normally
- **AND** retention and cleanup run for both targets
- **AND** no `BackupAbortError` is raised

#### Scenario: Retention still runs for the suspended target

- **WHEN** target A is suspended after a space error
- **THEN** `_evaluate_backup_retention()` and `_cleanup_backups()` still execute for target A
- **AND** deletions performed by cleanup free space on the target (self-heal)

#### Scenario: Non-space failure still aborts the VM

- **WHEN** a transfer fails with "permission denied" (not space-classified)
- **THEN** `BackupAbortError` is raised after retries as before
- **AND** retention/cleanup do not run for this VM

#### Scenario: Verification failure is never a space error

- **WHEN** a newly created FULL fails M2 verification with a `qemu-img check` error
- **THEN** the failure is handled by the existing rollback + `BackupAbortError` path
- **AND** the per-target suspension path is NOT taken even if the message is unusual

### Requirement: Never-delete-on-ENOSPC invariant

qsnap SHALL NEVER delete snapshots, backups, checkpoints, or state records in reaction to
a space-classified error. The only artifacts an interrupted transfer MAY leave behind are
`.tmp`/`.partial` files, which the existing pre-flight cleanup removes on the next run.
Retention-driven deletions remain governed solely by retention policy, never by space
pressure.

#### Scenario: Interrupted FULL leaves only a .tmp file

- **WHEN** a FULL `qemu-img convert` dies with ENOSPC mid-transfer
- **THEN** only `<target>.qcow2.tmp` may remain
- **AND** no backup, snapshot, checkpoint, or state record is deleted because of the error
- **AND** the next run's pre-flight cleanup removes the `.tmp` file

#### Scenario: Space pressure never triggers deletion

- **WHEN** a target filesystem is full
- **THEN** qsnap performs no deletion it would not have performed under the configured
  retention policy on a healthy filesystem

### Requirement: Auto-resume contract after a space-limited run

After a run limited by space errors, the next run SHALL resume from the last good
checkpoint/baseline/state and complete the outstanding work without operator
intervention. This holds because checkpoints rotate only after success + verification,
onchange baselines update only after success, and state records are written only after
success. A resumed incremental SHALL transfer from the same prior checkpoint that was
valid before the failure.

#### Scenario: Next run resumes the interrupted incremental

- **WHEN** run N's incremental transfer for target A failed with ENOSPC
- **AND** free space is restored before run N+1
- **THEN** run N+1 transfers the incremental for target A from the same prior checkpoint
- **AND** the resulting backup passes verification
- **AND** checkpoint rotation and state recording complete in run N+1

#### Scenario: Next run retries a FULL that never started

- **WHEN** run N's FULL creation for a disk was skipped by the proactive gate (strict)
- **AND** free space is restored before run N+1
- **THEN** run N+1 creates the FULL for that disk
- **AND** no partial or phantom FULL state exists from run N

### Requirement: Proactive free-space gate before transfers

Before starting each FULL or incremental transfer, Core SHALL estimate the required
target space and compare it against free space on the target filesystem via
`shutil.disk_usage`. Estimation SHALL use `qemu-img info` data: FULL estimate = sum of
`actual-size` over the source backing chain (worst-case standalone size); incremental
estimate = `actual-size` of the active layer. New `GlobalConfig` options SHALL control
the gate: `free_space_check: str = "strict"` (values `strict` | `warn` | `off`),
`free_space_reserve: int = 0` (bytes), `free_space_factor: float = 1.0`. The gate
SHALL pass when `free >= estimate * free_space_factor + free_space_reserve`. In `strict`
mode a failed gate SHALL be handled exactly like a reactive space error (per-target
suspension, transfer not attempted). In `warn` mode Core SHALL log a WARNING and proceed.
In `off` mode the gate SHALL NOT run. If the estimate cannot be determined, Core SHALL
log a WARNING and proceed (never block on an undecidable estimate).

#### Scenario: Strict gate blocks a doomed FULL

- **WHEN** `free_space_check = "strict"` and the FULL estimate exceeds free space
- **THEN** the transfer is not attempted
- **AND** the target is suspended with a CRITICAL log exactly as for a reactive ENOSPC
- **AND** other targets continue

#### Scenario: Warn mode proceeds

- **WHEN** `free_space_check = "warn"` and the estimate exceeds free space
- **THEN** a WARNING is logged naming target, estimate, and free space
- **AND** the transfer proceeds

#### Scenario: Off mode skips the gate

- **WHEN** `free_space_check = "off"`
- **THEN** no free-space check runs before transfers

#### Scenario: Undecidable estimate proceeds with warning

- **WHEN** `qemu-img info` fails while estimating and `free_space_check = "strict"`
- **THEN** a WARNING is logged
- **AND** the transfer proceeds (availability over blocking)

#### Scenario: Reserve and factor applied

- **WHEN** `free_space_reserve = 1073741824` and `free_space_factor = 1.1`
- **THEN** the gate passes only when `free >= estimate * 1.1 + 1073741824`

### Requirement: Blockcommit space errors deferred with reason enospc

When a blockcommit (live or offline) fails with a space-classified error, Core SHALL NOT
raise `RuntimeError`; it SHALL record the failed snapshots as a per-disk deferred
operation with `reason="enospc"` via `add_deferred_blockcommit`. Snapshot state records
SHALL remain intact (they are removed only after successful commit). The deferred entry
SHALL be drained by the standard drain path on subsequent runs and SHALL be subject to
the standard deferred threshold monitoring.

#### Scenario: Offline commit hits ENOSPC

- **WHEN** `qemu-img commit` fails with "No space left on device"
- **THEN** no `RuntimeError` is raised
- **AND** a deferred entry with `reason="enospc"` and the affected disk is recorded
- **AND** the snapshot state records remain
- **AND** the VM pipeline continues

#### Scenario: Deferred enospc entry drained later

- **WHEN** the next run finds free space restored and the VM state allows the commit
- **THEN** the drain path commits the queued snapshots and removes them from state

### Requirement: State write resilience on ENOSPC

`JsonStateManager._save()` SHALL catch `OSError` raised while writing or replacing a
state file (including ENOSPC in the state directory). On failure it SHALL log a CRITICAL
message naming the path and the OS error, and propagate the failure as a `RuntimeError`
so the per-VM isolation in `Core._run_pipeline()` contains it to one VM. The process
SHALL NOT crash; remaining VMs SHALL continue. Losing the in-flight write is acceptable:
state only advances after successful operations, so the worst case is redone work on the
next run.

#### Scenario: ENOSPC in state directory does not crash the process

- **WHEN** `_save()` fails with `OSError: [Errno 28] No space left on device`
- **THEN** a CRITICAL log names the state path and the error
- **AND** the current VM's run is marked failed via per-VM isolation
- **AND** remaining VMs are processed normally

#### Scenario: Successful save unaffected

- **WHEN** `_save()` writes normally
- **THEN** behavior is identical to before (atomic `.tmp` + `os.replace` + rotation)

### Requirement: Disk-full exit code

The CLI SHALL return exit code `4` (`EXIT_DISKFULL`) when a run was limited by any
space-classified error (reactive transfer failure, proactive strict gate, blockcommit
deferral with reason `enospc`, or state-write ENOSPC). `PipelineResult` SHALL carry a
`space_limited: bool` field (default `False`) set by Core when any VM/target was
space-limited. Exit code `4` SHALL take precedence over generic failure (`1`) but NOT
over lockfile (`3`) or parse (`2`) errors. Exit codes 0/1/2/3/10 semantics remain
unchanged for runs without space errors.

#### Scenario: Space-limited run exits 4

- **WHEN** `qsnap run` completes with one target suspended by ENOSPC and everything else
  successful
- **THEN** exit code is 4

#### Scenario: Run without space errors unaffected

- **WHEN** a run fails with a broken backing chain (no space involvement)
- **THEN** exit code is 1, not 4

#### Scenario: Backup abort precedence unchanged when no space error

- **WHEN** a run has a `BackupAbortError` from a verification failure (not space)
- **THEN** exit code is 10 as before
