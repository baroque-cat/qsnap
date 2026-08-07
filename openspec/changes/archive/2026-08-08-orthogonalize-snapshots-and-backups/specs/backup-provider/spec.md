# Backup Provider — delta

## ADDED Requirements

### Requirement: Backup creation work unit run_backup

`BitmapBackupProvider.run_backup(vm_config, target, disk, *, compression_type, stall_timeout,
convert_parallel, convert_out_of_order) -> BackupResult` SHALL create exactly one backup for
the given disk: a FULL when no qsnap checkpoint exists for this VM+target+disk, otherwise a
delta of dirty blocks since the newest checkpoint. Every `backup-begin` SHALL receive a
checkpoint XML as its third positional argument so the successor checkpoint is created
atomically at the export's freeze point (running VMs). Deltas SHALL use the `INbdClient`
pread/pwrite engine with a backing-chained qcow2 delta onto the newest valid backup of this
disk; `qemu-img convert` is the sole FULL transfer engine (shared `_full_pull_lifecycle()`
helper for all FULL paths). Checkpoints and NBD sockets remain scoped per disk
(`qsnap-{target_hash}-{disk}-{yyyymmddTHHMMSS}-{6hex}`;
`/tmp/qsnap-backup-{pid}-{disk}.sock`, `/tmp/qsnap-write-{pid}-{disk}.sock`).

#### Scenario: First backup — full export via qemu-img convert

- **WHEN** no prior qsnap checkpoint exists for this VM+target+disk combination
- **THEN** `qemu-img convert` reads from `nbd:unix:<socket>:exportname=<disk>` (running VM)
  or the source file (stopped VM) and writes to the target qcow2
- **AND** the backup is a standalone qcow2 file named with the FULL scheme
- **AND** no `INbdClient` pread/pwrite loop runs

#### Scenario: Incremental backup — dirty blocks only

- **WHEN** a prior qsnap checkpoint exists for this VM+target+disk
- **AND** the VM has written data since that checkpoint
- **THEN** the `INbdClient` pread/pwrite engine transfers dirty∩allocated extents with
  `zero_skip=False`
- **AND** the resulting backup file size is proportional to the changed data, not the full
  disk

#### Scenario: Checkpoint rotation after successful transfer

- **WHEN** the backup completes successfully and verification passes
- **THEN** the successor checkpoint created atomically with this export exists
- **AND** all superseded (older) qsnap checkpoints for the same VM+target+disk are deleted
  via `virsh checkpoint-delete` with `--metadata` fallback
- **AND** exactly one qsnap checkpoint remains for this VM+target+disk

#### Scenario: Backup failure preserves prior checkpoint

- **WHEN** the backup fails (NBD error, stall, or verification)
- **THEN** the prior checkpoint is NOT deleted
- **AND** the successor checkpoint created by the failed run is deleted best-effort
- **AND** the provider returns `BackupResult(success=False, error=<message>, disk=<disk>)`
- **AND** the NBD sockets and qemu-nbd process are cleaned up

#### Scenario: A second run_backup in the same batch uses the successor as baseline

- **WHEN** Core invokes `run_backup` for the same disk again after a successful backup
- **THEN** the newest-wins discovery selects the successor checkpoint created by the previous
  invocation
- **AND** the new delta chains onto the previous backup file (gap-free chain)

### Requirement: Deferred backup result for stopped VMs

`run_backup` SHALL check the VM power state before any `backup-begin`. When the VM is NOT
running and a checkpoint exists for this VM+target+disk, the provider SHALL return
`BackupResult(success=True, deferred=True, disk=<disk>)` without transferring data and
without creating or deleting any checkpoint. Core SHALL NOT update the
`last_backup_allocation` baseline for deferred results, so the onchange gate remains open and
the first run after the VM boots transfers the complete delta since the last checkpoint
(gap-free coverage). When the VM is NOT running and NO checkpoint exists, `run_backup` SHALL
create an offline FULL via `qemu-img convert` from the source disk file (existing offline
path, no checkpoint).

#### Scenario: Stopped VM with checkpoint defers

- **WHEN** `run_backup` runs for a stopped VM and a checkpoint exists for the disk
- **THEN** the result is `BackupResult(success=True, deferred=True)`
- **AND** no `backup-begin`, no file creation, no checkpoint mutation occurs
- **AND** Core logs INFO "VM stopped — backup deferred" and does not update the baseline

#### Scenario: Stopped VM without checkpoint creates offline FULL

- **WHEN** `run_backup` runs for a stopped VM and no checkpoint exists for the disk
- **THEN** an offline FULL is created via `qemu-img convert` from the source file
- **AND** no checkpoint is created

#### Scenario: First run after boot closes the gap

- **WHEN** the VM boots and the next run executes `run_backup`
- **THEN** the delta since the last checkpoint is transferred
- **AND** all writes accumulated while the VM was stopped are included (no coverage gap)

## REMOVED Requirements

### Requirement: Transfer missing snapshots via dirty bitmap extraction

**Reason:** The snapshot-queue work unit is replaced by `run_backup` (one backup per disk per
run; capability `backup-target-orthogonality`). The physics allows exactly one delta per
checkpoint interval, so a per-snapshot transfer queue was never achievable; it caused the
temporal-mismatch production blockage and the ≥2-batch breakage.

**Migration:** Behavior carried forward: full-export-on-no-checkpoint, atomic successor
checkpoint, checkpoint rotation, and failure cleanup are specified in the new
"Backup creation work unit run_backup" requirement and in `nbd-bitmap-backup`.

### Requirement: transfer_missing SHALL NOT create FULL backups

**Reason:** `run_backup` decides backup kind autonomously: no checkpoint → FULL. The
prohibition existed only to separate two snapshot-driven entry points that no longer exist.

**Migration:** Core still owns the count-based FULL decision (`needs_full` via
`target_chain_length`) and invokes `run_backup` accordingly; FULL verification and state
recording remain Core's responsibility.

### Requirement: transfer_missing safety net when prior is None

**Reason:** Subsumed by `run_backup`'s primary rule (no checkpoint → FULL with proper FULL
naming). The old safety net produced a full copy mislabeled with a snapshot name; the new
path creates an honest `FULL.{freeze_ts}` file.

**Migration:** None required.

## MODIFIED Requirements

### Requirement: BitmapBackupProvider implements IBackupProvider

The system SHALL provide a `BitmapBackupProvider` class in `qsnap/modules/backup/bitmap.py`
that implements `IBackupProvider`. It SHALL accept `IShell` and an optional
`nbd: INbdClient | None = None` as constructor parameters. It SHALL NOT accept or consult
`IStateManager` (stale snapshot-state healing belongs to the snapshot world; checkpoint
discovery is newest-wins via `virsh checkpoint-list`). It SHALL use the `virsh backup-begin`
NBD pull-model API. The interface methods SHALL be: `run_backup(vm_config, target, disk, *,
opts) -> BackupResult`, `list(target) -> list[BackupInfo]`,
`delete(backup: BackupInfo) -> ShellResult`, `list_checkpoints(vm_name) -> list[str]`, and
static `target_hash(target_path) -> str`. No method SHALL accept or return `SnapshotInfo`.

#### Scenario: Constructor accepts IShell

- **WHEN** `BitmapBackupProvider(shell=mock_shell)` is instantiated
- **THEN** `isinstance(provider, IBackupProvider)` is True
- **AND** the provider is ready for backup operations

#### Scenario: Provider API carries no SnapshotInfo

- **WHEN** any `IBackupProvider` method is inspected
- **THEN** no parameter or return type references `SnapshotInfo`

### Requirement: BitmapBackupProvider does not consume IStateManager

`BitmapBackupProvider` SHALL NOT accept an `IStateManager` parameter and SHALL NOT perform
stale snapshot-state cleanup. Checkpoint selection and backup decisions SHALL NOT consult
`IStateManager`. `run_backup()` SHALL NOT call `self._state.record_full_backup()` — state
recording is Core's responsibility after verification passes.

#### Scenario: Provider operates without state access

- **WHEN** `BitmapBackupProvider(shell=mock_shell)` is instantiated with no state reference
- **THEN** all provider operations (run_backup, list, delete, checkpoint discovery) work
  exclusively from target files, libvirt checkpoints, and `IShell`

### Requirement: Factory passes INbdClient to BitmapBackupProvider

`DefaultFactory.create_backup_provider(vm_config, target)` SHALL pass `LibnbdClient()` as the
`nbd` parameter when constructing `BitmapBackupProvider` and SHALL NOT pass an
`IStateManager`. `BitmapBackupProvider` is the single backup provider — there is no
`incremental_mode` branch.

#### Scenario: Factory constructs BitmapBackupProvider with nbd

- **WHEN** the factory creates a backup provider
- **THEN** `BitmapBackupProvider(shell=self._shell, nbd=LibnbdClient())` is returned

### Requirement: Immediate deletion of failed backup files after verification failure

When a definitive per-disk failure occurs in `BitmapBackupProvider.run_backup()`
(backup-begin failure, transfer error, verification error, chain-to-FULL not traversable, or
checkpoint missing), the provider SHALL delete the partially-transferred target file via
`self._shell.run(["rm", "-f", str(target_file)], timeout=10)` where applicable and return
`BackupResult(success=False, disk=<disk>)` with the error. Core SHALL continue processing the
remaining disks of the batch (no early abort at provider level); Core decides the VM-level
abort after all disks were attempted (`BackupAbortError`, spec: `core-orchestrator` VM-level
failure isolation). Immediate deletion prevents the failed file from being discovered by
retention cleanup (which lists `*.qcow2` files and would delete it with a misleading
`[delete] removed backup` log message).

#### Scenario: Failed backup file deleted immediately after verification failure

- **WHEN** verification returns an error string for a disk backup
- **THEN** a WARNING is logged: "backup failed for <vm> target <target> disk <disk>: <error>"
- **AND** `rm -f <target_file>` is executed via `IShell.run()` with a 10-second timeout
- **AND** `BackupResult(success=False, error=<error>, disk=<disk>)` is returned
- **AND** the target file does NOT exist on disk after this step

#### Scenario: Failed backup file not found by retention cleanup

- **WHEN** a backup fails and the file is deleted immediately
- **AND** retention cleanup runs `provider.list(target)` via `glob("*.qcow2")`
- **THEN** the failed file is NOT in the list of backups
- **AND** no `[delete] removed backup` log is emitted for the failed file

#### Scenario: Bitmap NBD convert failure does not leave partial file

- **WHEN** `qemu-img convert` from NBD fails in `run_backup()`
- **THEN** the partial target file SHALL be deleted via `rm -f` before returning
  `BackupResult(success=False)`
- **AND** the NBD socket is cleaned up in the `finally` block

#### Scenario: One disk failure does not stop other disks

- **WHEN** `run_backup` fails for disk `vda` of a two-disk VM
- **AND** Core then invokes `run_backup` for disk `vdb`
- **THEN** the `vdb` backup proceeds normally and its success is recorded

### Requirement: Per-disk FULL backup creation in Core

`Core._backup_target()` SHALL iterate `for disk_cfg in vm_config.disks` and decide per-disk
whether a FULL is due. The decision SHALL count that disk's incrementals via
`get_incremental_dependencies(target, newest_full.name)` and compare against
`target.target_chain_length`; dependency keys in both legacy snapshot-name format and
backup-name format SHALL be counted. When no FULLs exist for a disk, a FULL SHALL be created
unconditionally. FULL creation is performed by `provider.run_backup()` for that disk when the
decision says FULL is due. State recording SHALL use `record_full_backup(target, name, ts,
disk)` and `set_last_backup_allocation(target, disk, alloc)` — both accept a per-disk
parameter.

#### Scenario: First backup creates per-disk FULLs

- **WHEN** no FULL exists for any disk at this target
- **THEN** a FULL is created for each configured disk
- **AND** state records each FULL with its disk identifier

#### Scenario: Incremental count exceeds chain length for one disk

- **WHEN** disk `vda` has 10 incrementals and `target_chain_length=5`
- **AND** disk `vdb` has 2 incrementals and `target_chain_length=5`
- **THEN** a new FULL is created for disk `vda`
- **AND** no new FULL is created for disk `vdb`

### Requirement: Per-disk backup naming

The system SHALL include the disk identifier in backup filenames. FULL backups SHALL use
`{vm_name}.FULL.{YYYYMMDDTHHMMSS}_{disk}_{6hex}.qcow2`. Incremental backups SHALL use
`{vm_name}.{YYYYMMDDTHHMMSS}_{disk}_{6hex}.qcow2`. In both cases the timestamp SHALL be the
backup's own freeze point (never a snapshot timestamp) and the hex suffix SHALL be generated
via `secrets.token_hex(3)`. The `list()` method SHALL parse the disk from each backup
filename via `parse_disk_from_snapshot_name()`.

#### Scenario: FULL backup named with disk and freeze timestamp

- **WHEN** a FULL backup is created for disk `vda` with freeze point 2026-08-08T03:00:00
- **THEN** the filename matches `vm.FULL.20260808T030000_vda_{6hex}.qcow2`

#### Scenario: Incremental backup named with disk and freeze timestamp

- **WHEN** a delta is created for disk `vda` with freeze point 2026-08-08T03:15:42
- **THEN** the filename matches `vm.20260808T031542_vda_{6hex}.qcow2`
- **AND** the filename contains no snapshot name

### Requirement: Backup results carry the source disk

`BitmapBackupProvider.run_backup()` SHALL return its `BackupResult` with `disk` set to the
disk target being backed up. Core's backup bookkeeping in `_backup_target()` SHALL propagate
the same disk into the `ActionRecord(action="backup_full" | "backup_transfer")` it appends.
A `BackupResult` produced for a known disk SHALL NOT leave `disk` as `None`.

#### Scenario: Backup result carries disk

- **WHEN** `run_backup()` backs up disk `vdb`
- **THEN** the returned `BackupResult` has `disk="vdb"`

#### Scenario: Multi-disk run returns per-disk results

- **WHEN** Core runs backups for disks `vda` and `vdb`
- **THEN** each returned `BackupResult.disk` matches the disk it reports on
