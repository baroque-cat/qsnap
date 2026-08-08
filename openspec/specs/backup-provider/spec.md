# Backup Provider

## Purpose

Backup transfer via NBD pull-model dirty-block extraction. Copies missing snapshots to a target directory, maintaining per-disk incremental backup semantics with backing-chained qcow2 deltas. The single backup provider is `BitmapBackupProvider`; `FileCopyBackupProvider` (rsync) and `rate_limit` have been removed.
## Requirements
### Requirement: List existing backups on target

The system SHALL scan the `target.path` directory for `.qcow2` files. For each file the system SHALL obtain metadata via `qemu-img info --output=json` and produce a `BackupInfo` with name, path, timestamp (from filename or mtime), disk (parsed from the filename via `parse_disk_from_snapshot_name`), and `is_full`.

#### Scenario: Target directory exists with backups

- **WHEN** `target.path` contains files `vm.20250101T000000_vda_a1b2c3.qcow2` and `vm.20250102T000000_vda_d4e5f6.qcow2`
- **THEN** `list()` returns a list of 2 `BackupInfo`, sorted by timestamp, each with `disk` set from the filename

#### Scenario: Target directory does not exist

- **WHEN** `target.path` does not exist
- **THEN** `list()` returns an empty list
- **AND** no shell commands are executed

#### Scenario: Target directory exists but is empty

- **WHEN** `target.path` exists but contains no `.qcow2` files
- **THEN** `list()` returns an empty list

### Requirement: Delete backup from target

The system SHALL delete a backup file via `rm -f`. The method accepts a `BackupInfo` and returns a `ShellResult`.

#### Scenario: Successful backup deletion

- **WHEN** `rm -f <backup.path>` completes successfully
- **THEN** the module returns `ShellResult(success=True)`

#### Scenario: Backup file does not exist

- **WHEN** the backup file does not exist
- **THEN** `rm -f` returns success
- **AND** the module returns `ShellResult(success=True)`

### Requirement: BitmapBackupProvider implements IBackupProvider

The system SHALL provide a `BitmapBackupProvider` class in `qsnap/modules/backup/bitmap.py` that implements `IBackupProvider`. It SHALL accept `IShell` and an optional `nbd: INbdClient | None = None` as constructor parameters. It SHALL NOT accept or consult `IStateManager` (stale snapshot-state healing belongs to the snapshot world; checkpoint discovery is newest-wins via `virsh checkpoint-list`). It SHALL use the `virsh backup-begin` NBD pull-model API. The interface methods SHALL be: `run_backup(vm_config, target, disk, *, opts) -> BackupResult`, `list(target) -> list[BackupInfo]`, `delete(backup: BackupInfo) -> ShellResult`, `list_checkpoints(vm_name) -> list[str]`, and static `target_hash(target_path) -> str`. No method SHALL accept or return `SnapshotInfo`.

#### Scenario: Constructor accepts IShell

- **WHEN** `BitmapBackupProvider(shell=mock_shell)` is instantiated
- **THEN** `isinstance(provider, IBackupProvider)` is True
- **AND** the provider is ready for backup operations

#### Scenario: Provider API carries no SnapshotInfo

- **WHEN** any `IBackupProvider` method is inspected
- **THEN** no parameter or return type references `SnapshotInfo`

### Requirement: Backup creation work unit run_backup

`BitmapBackupProvider.run_backup(vm_config, target, disk, *, compression_type, stall_timeout, convert_parallel, convert_out_of_order) -> BackupResult` SHALL create exactly one backup for the given disk: a FULL when no qsnap checkpoint exists for this VM+target+disk, otherwise a delta of dirty blocks since the newest checkpoint whose bitmap is healthy. Before choosing the delta path, the provider SHALL probe the newest checkpoint's bitmap health (capability `checkpoint-bitmap-health-probe`): HEALTHY proceeds to the delta; DEAD routes into bitmap-loss recovery (capability `bitmap-loss-recovery` — recovered delta when gates G1-G3 pass, else FULL); UNKNOWN attempts the delta with the reactive backstop as safety net. Every `backup-begin` SHALL receive a checkpoint XML as its third positional argument so the successor checkpoint is created atomically at the export's freeze point (running VMs). Deltas SHALL use the `INbdClient` pread/pwrite engine with a backing-chained qcow2 delta onto the newest valid backup of this disk; `qemu-img convert` is the sole FULL transfer engine (shared `_full_pull_lifecycle()` helper for all FULL paths). Checkpoints and NBD sockets remain scoped per disk (`qsnap-{target_hash}-{disk}-{yyyymmddTHHMMSS}-{6hex}`; `/tmp/qsnap-backup-{pid}-{disk}.sock`, `/tmp/qsnap-write-{pid}-{disk}.sock`).

#### Scenario: First backup — full export via qemu-img convert

- **WHEN** no prior qsnap checkpoint exists for this VM+target+disk combination
- **THEN** `qemu-img convert` reads from `nbd:unix:<socket>:exportname=<disk>` (running VM) or the source file (stopped VM) and writes to the target qcow2
- **AND** the backup is a standalone qcow2 file named with the FULL scheme
- **AND** no `INbdClient` pread/pwrite loop runs

#### Scenario: Incremental backup — dirty blocks only

- **WHEN** a prior qsnap checkpoint exists for this VM+target+disk with a HEALTHY bitmap
- **AND** the VM has written data since that checkpoint
- **THEN** the `INbdClient` pread/pwrite engine transfers dirty∩allocated extents with `zero_skip=False`
- **AND** the resulting backup file size is proportional to the changed data, not the full disk

#### Scenario: Dead bitmap routes to recovery instead of failing

- **WHEN** a prior checkpoint exists but its bitmap probe returns DEAD
- **THEN** `run_backup` does not return failure on this condition alone
- **AND** the recovery path produces either a recovered delta or a FULL
- **AND** the returned `BackupResult.kind` reflects the produced backup kind

#### Scenario: Checkpoint rotation after successful transfer

- **WHEN** the backup completes successfully and verification passes
- **THEN** the successor checkpoint created atomically with this export exists
- **AND** all superseded (older) qsnap checkpoints for the same VM+target+disk are deleted via `virsh checkpoint-delete` with `--metadata` fallback
- **AND** exactly one qsnap checkpoint remains for this VM+target+disk

#### Scenario: Backup failure preserves prior checkpoint

- **WHEN** the backup fails (NBD error, stall, or verification)
- **THEN** the prior checkpoint is NOT deleted
- **AND** the successor checkpoint created by the failed run is deleted best-effort
- **AND** the provider returns `BackupResult(success=False, error=<message>, disk=<disk>)`
- **AND** the NBD sockets and qemu-nbd process are cleaned up

#### Scenario: A second run_backup in the same batch uses the successor as baseline

- **WHEN** Core invokes `run_backup` for the same disk again after a successful backup
- **THEN** the newest-wins discovery selects the successor checkpoint created by the previous invocation
- **AND** the new delta chains onto the previous backup file (gap-free chain)

### Requirement: Deferred backup result for stopped VMs

`run_backup` SHALL check the VM power state before any `backup-begin`. When the VM is NOT running and a checkpoint exists for this VM+target+disk, the provider SHALL return `BackupResult(success=True, deferred=True, disk=<disk>)` without transferring data and without creating or deleting any checkpoint. Core SHALL NOT update the `last_backup_allocation` baseline for deferred results, so the onchange gate remains open and the first run after the VM boots transfers the complete delta since the last checkpoint (gap-free coverage). When the VM is NOT running and NO checkpoint exists, `run_backup` SHALL create an offline FULL via `qemu-img convert` from the source disk file (existing offline path, no checkpoint).

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

### Requirement: List checkpoints for target

`BitmapBackupProvider` SHALL provide a method `list_checkpoints(vm_name: str) -> list[str]` that discovers existing qsnap-owned checkpoints via `virsh checkpoint-list --name`. Only checkpoints with the `qsnap-` prefix SHALL be returned.

#### Scenario: Existing qsnap checkpoints found

- **WHEN** `virsh checkpoint-list --name VM` returns `["qsnap-target1-vda-20250101", "manual-checkpoint", "qsnap-target1-vda-20250102"]`
- **THEN** `list_checkpoints("VM")` returns `["qsnap-target1-vda-20250101", "qsnap-target1-vda-20250102"]`

### Requirement: NBD pull-model backup via virsh backup-begin

`BitmapBackupProvider` SHALL use the libvirt pull-model backup API: (1) create backup XML with NBD Unix socket at `/tmp/qsnap-backup-{pid}-{disk}.sock` and a checkpoint XML naming the successor checkpoint, (2) `virsh backup-begin --domain VM backup.xml checkpoint.xml` to start the NBD export and atomically create the successor checkpoint at the export's freeze point, (3) for FULL exports: `qemu-img convert nbd:unix:<socket>:exportname=<disk> <target>`; for incremental exports: `INbdClient` pread/pwrite loop, (4) remove socket. Checkpoints SHALL persist for subsequent incremental runs.

#### Scenario: First backup — full pull via NBD

- **WHEN** no prior qsnap checkpoint exists for this VM+target+disk combination
- **THEN** `virsh backup-begin` starts a full NBD export and creates the successor checkpoint atomically
- **THEN** `qemu-img convert nbd:unix:<socket>:exportname=<disk> <target>` creates a standalone qcow2 file

#### Scenario: Incremental backup — dirty blocks via NBD

- **WHEN** a prior checkpoint exists and VM has written data since that checkpoint
- **THEN** `virsh backup-begin` exports only blocks changed since the checkpoint
- **THEN** the resulting backup file is smaller than a full copy
- **AND** a new successor checkpoint is created atomically at this export's freeze point

### Requirement: Backup verification step

`BitmapBackupProvider.transfer_missing()` SHALL perform post-transfer verification according to `target.verify`. Valid values are `"off"` (no verification), `"metadata"` (structural checks), `"check"` (metadata + `qemu-img check`), and `"compare"` (check + `qemu-img compare` chain-traversing content comparison). The legacy values `"hash"` and `"full"` SHALL log a deprecation WARNING and be treated as `"compare"`. Every verification failure SHALL produce `BackupResult(success=False, error="verification failed: ...")`. For incrementals, verification SHALL use `verify_bitmap_incremental()`. For full pulls (no prior checkpoint), verification SHALL use `verify_full_backup()`.

#### Scenario: Metadata verification passes

- **WHEN** `target.verify` is `"metadata"` and the backup passes structural checks
- **THEN** backup is marked success

#### Scenario: Compare verification passes

- **WHEN** `target.verify` is `"compare"` and `qemu-img compare` succeeds
- **THEN** backup is marked success

#### Scenario: Verification failure produces error

- **WHEN** verification detects a structural or content mismatch
- **THEN** `BackupResult(success=False, error="verification failed: ...")` is returned

#### Scenario: Deprecated verify values treated as compare

- **WHEN** `target.verify` is `"hash"` or `"full"` (deprecated)
- **THEN** a WARNING is logged naming the deprecated value
- **AND** `"compare"` behavior is applied (qemu-img compare)

### Requirement: Backup providers remain retry-unaware

`BitmapBackupProvider` SHALL NOT implement any retry logic internally. It SHALL return `BackupResult(success=False, error=...)` for any failure. The retry logic SHALL be handled by Core's `_backup_target()` method, which wraps the provider's `transfer_missing()` call.

#### Scenario: Provider returns error, Core handles retry

- **WHEN** `BitmapBackupProvider.transfer_missing()` returns `BackupResult(success=False, error="Connection refused")`
- **THEN** the provider itself does not retry
- **AND** Core's retry wrapper inspects the error and decides whether to retry

#### Scenario: BackupResult error is structured for retry detection

- **WHEN** a backup transfer fails
- **THEN** the `BackupResult.error` string contains the underlying error from `ShellResult.error`
- **AND** Core's retry logic can pattern-match against it to determine retryability

### Requirement: BitmapBackupProvider.create_full_backup implemented via qemu-img convert

`BitmapBackupProvider` SHALL override `create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, compression_type: str = "zstd", stall_timeout: int = 1800, convert_parallel: int = 4, convert_out_of_order: bool = True) -> BackupResult` to create a standalone FULL backup via `qemu-img convert`. `qemu-img convert` is the sole FULL backup transfer engine. The method SHALL use `qemu-img convert` via `_qemu_img_convert_transfer()`. The method SHALL NOT raise `NotImplementedError`. The result SHALL be a standalone qcow2 file on the target.

FULL backup naming format (multi-disk refactor): `{vm_name}.FULL.{YYYYMMDDTHHMMSS}_{disk}_{6hex}.qcow2`.

For running VMs, a checkpoint named `qsnap-{target_hash}-{disk}-{yyyymmddTHHMMSS}-{6hex}` SHALL be created atomically with the FULL's `backup-begin`, so a bitmap-mode FULL always leaves a checkpoint baseline anchored at the FULL's freeze point. For stopped VMs, no checkpoint is created.

The returned `BackupResult.checkpoint` SHALL carry the exact successor checkpoint name when a checkpoint was created (running-VM path, after `backup-begin` succeeds), and SHALL be `None` when no checkpoint was created (stopped-VM path, or `backup-begin` failure). This lets Core's rollback delete precisely the checkpoint the failed attempt created.

The method SHALL NOT call `self._state.record_full_backup()` — state recording is Core's responsibility after post-create verification passes.

#### Scenario: Bitmap FULL with zstd compression via qemu-img convert

- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd")` is called
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p <source> <target>.tmp` is executed via `run_with_stall_detection()`
- **AND** the resulting FULL is compressed with zstd

#### Scenario: Bitmap FULL with custom convert_parallel

- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", convert_parallel=2)` is called
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 2 -W -p <source> <target>.tmp` is executed

#### Scenario: Bitmap FULL creates atomically with checkpoint (running VM)

- **WHEN** `BitmapBackupProvider.create_full_backup()` completes successfully for a running VM
- **THEN** a backup-begin/freeze checkpoint named `qsnap-{target_hash}-{disk}-{yyyymmddTHHMMSS}-{6hex}` exists
- **AND** its baseline equals the FULL export's freeze point
- **AND** no standalone `virsh checkpoint-create-as` call is made by the provider

#### Scenario: No checkpoint for stopped VM FULL

- **WHEN** `BitmapBackupProvider.create_full_backup()` completes successfully for a stopped VM
- **THEN** no checkpoint is created
- **AND** direct `qemu-img convert` is used from the source path

#### Scenario: Bitmap FULL does not self-record in state

- **WHEN** `BitmapBackupProvider.create_full_backup()` completes successfully
- **THEN** `self._state.record_full_backup()` is NOT called by the provider
- **AND** state recording is deferred to Core's `_backup_target()` after post-create verification

#### Scenario: Bitmap FULL with dotted VM name

- **WHEN** `BitmapBackupProvider.create_full_backup("3.Projects_opencode", snapshot, target, compress=False)` is called
- **THEN** the FULL backup file is named `3.Projects_opencode.FULL.YYYYMMDDTHHMMSS_{disk}_{6hex}.qcow2`

#### Scenario: Running-VM FULL reports its checkpoint name
- **WHEN** `create_full_backup()` completes successfully for a running VM
- **THEN** the returned `BackupResult.checkpoint` equals the successor checkpoint name created by `backup-begin`

#### Scenario: Stopped-VM FULL reports no checkpoint
- **WHEN** `create_full_backup()` completes successfully for a stopped VM
- **THEN** the returned `BackupResult.checkpoint` is `None`

#### Scenario: backup-begin failure reports no checkpoint
- **WHEN** `create_full_backup()` fails because `virsh backup-begin` returned non-zero (atomic — no checkpoint created)
- **THEN** the returned `BackupResult.checkpoint` is `None`
- **AND** `BackupResult.success` is `False`

### Requirement: BitmapBackupProvider domjobabort after NBD transfer

`BitmapBackupProvider.transfer_missing()` SHALL call `virsh domjobabort --domain <vm_name>` in its `finally` block before socket cleanup. The abort SHALL use a 30-second timeout. On abort failure, a WARNING SHALL be logged but the error SHALL NOT be propagated (the abort is best-effort).

#### Scenario: Domjobabort called after successful transfer

- **WHEN** `BitmapBackupProvider.transfer_missing()` completes a successful transfer
- **THEN** `virsh domjobabort --domain <vm_name>` is called in the `finally` block
- **AND** the NBD socket is removed after the abort

#### Scenario: Domjobabort called after failed transfer

- **WHEN** `BitmapBackupProvider.transfer_missing()` encounters a transfer failure
- **THEN** `virsh domjobabort --domain <vm_name>` is still called in the `finally` block
- **AND** the NBD socket is removed after the abort

#### Scenario: Domjobabort failure is non-fatal

- **WHEN** `virsh domjobabort` returns a non-zero exit code
- **THEN** a WARNING is logged with the error message
- **AND** execution continues to socket cleanup

### Requirement: BitmapBackupProvider does not consume IStateManager

`BitmapBackupProvider` SHALL NOT accept an `IStateManager` parameter and SHALL NOT perform stale snapshot-state cleanup. Checkpoint selection and backup decisions SHALL NOT consult `IStateManager`. `run_backup()` SHALL NOT call `self._state.record_full_backup()` — state recording is Core's responsibility after verification passes.

#### Scenario: Provider operates without state access

- **WHEN** `BitmapBackupProvider(shell=mock_shell)` is instantiated with no state reference
- **THEN** all provider operations (run_backup, list, delete, checkpoint discovery) work exclusively from target files, libvirt checkpoints, and `IShell`

### Requirement: Factory passes INbdClient to BitmapBackupProvider

`DefaultFactory.create_backup_provider(vm_config, target)` SHALL pass `LibnbdClient()` as the `nbd` parameter when constructing `BitmapBackupProvider` and SHALL NOT pass an `IStateManager`. `BitmapBackupProvider` is the single backup provider — there is no `incremental_mode` branch.

#### Scenario: Factory constructs BitmapBackupProvider with nbd

- **WHEN** the factory creates a backup provider
- **THEN** `BitmapBackupProvider(shell=self._shell, nbd=LibnbdClient())` is returned

### Requirement: Immediate deletion of failed backup files after verification failure

When a definitive per-disk failure occurs in `BitmapBackupProvider.run_backup()` (backup-begin failure, transfer error, verification error, chain-to-FULL not traversable, or checkpoint missing), the provider SHALL delete the partially-transferred target file via `self._shell.run(["rm", "-f", str(target_file)], timeout=10)` where applicable and return `BackupResult(success=False, disk=<disk>)` with the error. Core SHALL continue processing the remaining disks of the batch (no early abort at provider level); Core decides the VM-level abort after all disks were attempted (`BackupAbortError`, spec: `core-orchestrator` VM-level failure isolation). Immediate deletion prevents the failed file from being discovered by retention cleanup (which lists `*.qcow2` files and would delete it with a misleading `[delete] removed backup` log message).

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
- **THEN** the partial target file SHALL be deleted via `rm -f` before returning `BackupResult(success=False)`
- **AND** the NBD socket is cleaned up in the `finally` block

#### Scenario: One disk failure does not stop other disks

- **WHEN** `run_backup` fails for disk `vda` of a two-disk VM
- **AND** Core then invokes `run_backup` for disk `vdb`
- **THEN** the `vdb` backup proceeds normally and its success is recorded

### Requirement: Compression type parameter for backup providers

`IBackupProvider.create_full_backup()` SHALL accept a `compression_type: str = "zstd"` parameter. `IBackupProvider.transfer_missing()` SHALL accept a `compression_type: str = "zstd"` parameter. The `compression_type` parameter SHALL be passed from Core's `target.compression_type` config field. Valid values are `"zstd"` (default) and `"zlib"`. When `compress=False`, the `compression_type` parameter SHALL be ignored (no compression regardless of type). Compression applies to FULL backups only — bitmap incrementals are always uncompressed.

#### Scenario: create_full_backup with zstd compression via qemu-img convert

- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd")` is called
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p <source> <target>.tmp` is executed
- **AND** the resulting FULL backup is compressed with zstd

#### Scenario: create_full_backup with compression disabled

- **WHEN** `create_full_backup("myvm", snapshot, target, compress=False, compression_type="zstd")` is called
- **THEN** `qemu-img convert -O qcow2 -m 4 -W -p <source> <target>.tmp` is executed (no `-c` flag)
- **AND** the `compression_type` parameter is ignored

### Requirement: Stall detection for data transfer commands

`BitmapBackupProvider.transfer_missing()` SHALL use `IShell.run_with_stall_detection()` for the `qemu-img convert` FULL transfer step. The `output_file` parameter SHALL be the target `.tmp` file path. The `stall_timeout` parameter SHALL be passed from TargetConfig's `backup_stall_timeout` (parsed to seconds). When `stall_timeout` is `0`, stall detection is disabled.

#### Scenario: qemu-img convert uses stall detection

- **WHEN** `transfer_missing()` in BitmapBackupProvider is called with `stall_timeout=1800`
- **THEN** the `qemu-img convert` command is executed via `shell.run_with_stall_detection(cmd, output_file=tmp_file, stall_timeout=1800)`

### Requirement: Full transfer engine parameters for backup providers

`IBackupProvider.create_full_backup()` SHALL accept `convert_parallel: int = 4` and `convert_out_of_order: bool = True` as keyword parameters. `IBackupProvider.transfer_missing()` SHALL accept the same two keyword parameters. These parameters SHALL be passed from Core's `target.convert_parallel` and `target.convert_out_of_order` config fields. The `convert_parallel` and `convert_out_of_order` parameters control `qemu-img convert` behavior (parallel coroutines and out-of-order writes respectively). `qemu-img convert` is the sole FULL backup transfer engine.

#### Scenario: create_full_backup with default parameters

- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True)` is called without explicit `convert_parallel` or `convert_out_of_order`
- **THEN** `convert_parallel` defaults to `4`
- **AND** `convert_out_of_order` defaults to `True`
- **AND** `qemu-img convert` is used for the FULL transfer

### Requirement: Post-transfer chain-to-FULL verification

After `BitmapBackupProvider.transfer_missing()` successfully creates an incremental backup (atomic rename complete), the provider SHALL verify the backing chain from the incremental to the FULL anchor is traversable via `qemu-img info --force-share --backing-chain --output=json <incremental_path>`. If the chain is broken (any file in the chain missing), the provider SHALL log CRITICAL and return `BackupResult(success=False, error="chain-to-FULL not traversable")`. The provider SHALL also verify that at least one qsnap checkpoint exists for this VM+target+disk after a successful incremental transfer.

#### Scenario: Chain to FULL traversable after incremental transfer

- **WHEN** `transfer_missing()` creates an incremental backup
- **AND** `qemu-img info --backing-chain` shows an unbroken chain to the FULL
- **THEN** `BackupResult(success=True)` is returned

#### Scenario: Broken chain to FULL detected after incremental transfer

- **WHEN** `transfer_missing()` creates an incremental backup
- **AND** `qemu-img info --backing-chain` fails or shows a broken chain
- **THEN** a CRITICAL log is emitted
- **AND** `BackupResult(success=False, error="chain-to-FULL not traversable")` is returned

#### Scenario: Missing checkpoint after incremental transfer

- **WHEN** an incremental transfer succeeds and verification passes
- **AND** `virsh checkpoint-list` returns no `qsnap-` checkpoints for the VM+target+disk
- **THEN** `BackupResult(success=False, error="checkpoint missing — next incremental impossible")` is returned

### Requirement: Post-creation FULL backup validation

After `BitmapBackupProvider.create_full_backup()` successfully creates a FULL backup (atomic rename complete), the provider SHALL validate: (a) `backing-filename` is absent via `qemu-img info` (FULL must be standalone), (b) for running VMs, a `qsnap-` checkpoint exists via `virsh checkpoint-list --name --domain <vm>`. If either check fails, return `BackupResult(success=False, error=<message>)`. Stopped VMs do not create checkpoints — checkpoint validation is skipped.

#### Scenario: FULL has no backing file and checkpoint exists

- **WHEN** `create_full_backup()` creates a FULL backup for a running VM
- **AND** `qemu-img info` shows no `backing-filename`
- **AND** `virsh checkpoint-list` shows a `qsnap-` checkpoint for the VM+target+disk
- **THEN** `BackupResult(success=True)` is returned

#### Scenario: FULL has unexpected backing file

- **WHEN** `create_full_backup()` creates a FULL backup
- **AND** `qemu-img info` reports a `backing-filename`
- **THEN** `BackupResult(success=False, error="FULL backup has unexpected backing file")` is returned

#### Scenario: Checkpoint missing after FULL creation for running VM

- **WHEN** `create_full_backup()` creates a FULL backup for a running VM
- **AND** `virsh checkpoint-list` returns no `qsnap-` checkpoints
- **THEN** `BackupResult(success=False, error="checkpoint missing — next incremental impossible")` is returned

### Requirement: Per-disk FULL backup creation in Core

`Core._backup_target()` SHALL iterate `for disk_cfg in vm_config.disks` and decide per-disk whether a FULL is due. The decision SHALL count that disk's incrementals via `get_incremental_dependencies(target, newest_full.name)` and compare against `target.target_chain_length`; dependency keys in both legacy snapshot-name format and backup-name format SHALL be counted. When no FULLs exist for a disk, a FULL SHALL be created unconditionally. FULL creation is performed by `provider.run_backup()` for that disk when the decision says FULL is due. State recording SHALL use `record_full_backup(target, name, ts, disk)` and `set_last_backup_allocation(target, disk, alloc)` — both accept a per-disk parameter.

#### Scenario: First backup creates per-disk FULLs

- **WHEN** no FULL exists for any disk at this target
- **THEN** a FULL is created for each configured disk
- **AND** state records each FULL with its disk identifier

#### Scenario: Incremental count exceeds chain length for one disk

- **WHEN** disk `vda` has 10 incrementals and `target_chain_length=5`
- **AND** disk `vdb` has 2 incrementals and `target_chain_length=5`
- **THEN** a new FULL is created for disk `vda`
- **AND** no new FULL is created for disk `vdb`

### Requirement: Per-disk onchange gate

`Core._should_backup_onchange(vm, target)` SHALL run change detection per-disk. The gate opens (`should_proceed=True`) when ANY disk has changed since its last backup to this target. The returned dict maps each disk target to its `ChangeResult`.

#### Scenario: One disk changed — backup proceeds

- **WHEN** disk `vda` has changed since last backup and disk `vdb` has not
- **THEN** `should_proceed` is `True`
- **AND** the change_results dict contains both disks

#### Scenario: No disk changed — backup skipped

- **WHEN** no disk has changed since its last backup
- **THEN** `should_proceed` is `False`
- **AND** `skip_transfer` is set to `True`

### Requirement: Per-disk backup naming

The system SHALL include the disk identifier in backup filenames. FULL backups SHALL use `{vm_name}.FULL.{YYYYMMDDTHHMMSS}_{disk}_{6hex}.qcow2`. Incremental backups SHALL use `{vm_name}.{YYYYMMDDTHHMMSS}_{disk}_{6hex}.qcow2`. In both cases the timestamp SHALL be the backup's own freeze point (never a snapshot timestamp) and the hex suffix SHALL be generated via `secrets.token_hex(3)`. The `list()` method SHALL parse the disk from each backup filename via `parse_disk_from_snapshot_name()`.

#### Scenario: FULL backup named with disk and freeze timestamp

- **WHEN** a FULL backup is created for disk `vda` with freeze point 2026-08-08T03:00:00
- **THEN** the filename matches `vm.FULL.20260808T030000_vda_{6hex}.qcow2`

#### Scenario: Incremental backup named with disk and freeze timestamp

- **WHEN** a delta is created for disk `vda` with freeze point 2026-08-08T03:15:42
- **THEN** the filename matches `vm.20260808T031542_vda_{6hex}.qcow2`
- **AND** the filename contains no snapshot name

### Requirement: record_incremental_dependency does not take disk parameter

Core's `record_incremental_dependency(target, incremental_name, full_name)` SHALL NOT accept a separate disk parameter. The disk is encoded in the FULL name, and `IStateManager` discovers the relevant FULL via the name.

#### Scenario: Dependency recorded after incremental transfer

- **WHEN** an incremental chains to a FULL named `vm.FULL.20250801T120000_vda_a1b2c3`
- **THEN** `record_incremental_dependency(target_path, incremental_name, "vm.FULL.20250801T120000_vda_a1b2c3")` is called
- **AND** no disk parameter is passed

### Requirement: Backup results carry the source disk

`BitmapBackupProvider.run_backup()` SHALL return its `BackupResult` with `disk` set to the disk target being backed up. Core's backup bookkeeping in `_backup_target()` SHALL propagate the same disk into the `ActionRecord(action="backup_full" | "backup_transfer")` it appends. A `BackupResult` produced for a known disk SHALL NOT leave `disk` as `None`.

#### Scenario: Backup result carries disk

- **WHEN** `run_backup()` backs up disk `vdb`
- **THEN** the returned `BackupResult` has `disk="vdb"`

#### Scenario: Multi-disk run returns per-disk results

- **WHEN** Core runs backups for disks `vda` and `vdb`
- **THEN** each returned `BackupResult.disk` matches the disk it reports on

### Requirement: Read-only baseline assessment for dry-run parity

`IBackupProvider` SHALL provide a read-only method `assess_baseline(vm_config, target, disk)` returning a frozen assessment result with: baseline status (`no_checkpoint | healthy | dead | unknown`), the newest checkpoint name when present, the recovery gate outcome and failed-gate reason when the checkpoint is dead, and the size estimate for the backup kind a real run would produce. The method SHALL issue only read-only shell commands and SHALL perform zero mutations. Core's dry-run prediction SHALL consume this method instead of re-implementing checkpoint inspection. All implementations and mocks SHALL implement the method (BREAKING interface change).

#### Scenario: Dry-run consumes assessment without mutation

- **WHEN** Core's dry-run calls `assess_baseline` for a disk with a dead checkpoint
- **THEN** the assessment reports `dead`, the gate outcome, and the predicted size
- **AND** no checkpoint, file, or state mutation occurs anywhere in the call

#### Scenario: Mock implements the assessment contract

- **WHEN** `MockBackupProvider.assess_baseline` is called in Core tests
- **THEN** it returns a valid assessment result object (never None)
- **AND** passes the contract tests parametrized over all implementations

### Requirement: Backup results carry the backup kind

`BackupResult` SHALL carry a `kind` field with value `"full"`, `"delta"`, or `"recovered_delta"` identifying how the backup was produced. Core SHALL record the kind in the action audit trail, and the summary renderer SHALL display recovered deltas distinctly. The field SHALL default to `"delta"`-compatible behavior for existing constructors only if explicitly defaulted; otherwise every producer SHALL set it.

#### Scenario: Recovered delta is auditable

- **WHEN** a recovered delta completes successfully
- **THEN** `BackupResult.kind` is `"recovered_delta"`
- **AND** the action record and summary identify it as a recovered delta

#### Scenario: Regular paths keep their kinds

- **WHEN** a normal FULL or delta completes
- **THEN** `BackupResult.kind` is `"full"` or `"delta"` respectively

