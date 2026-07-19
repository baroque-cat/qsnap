# NBD Bitmap Backup

## Purpose

NBD pull-model backup via virsh backup-begin — replaces qemu-img convert --bitmap with libvirt backup API for dirty-block extraction over Unix socket.

## Requirements

### Requirement: NBD pull-model backup via virsh backup-begin

`BitmapBackupProvider` v2 SHALL use the libvirt pull-model backup API instead of `qemu-img convert --bitmap`. The transfer pipeline SHALL be: (1) create backup XML with NBD Unix socket, (2) `virsh backup-begin --domain VM backup.xml` to start NBD export, (3) `qemu-img convert -n nbd:unix:<socket> <target>` to pull dirty blocks, (4) cleanup socket. Checkpoints SHALL persist for subsequent incremental runs.

#### Scenario: First backup — full pull via NBD

- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `virsh backup-begin` starts a full NBD export
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2 file on target

#### Scenario: Incremental backup — dirty blocks via NBD checkpoint

- **WHEN** a prior checkpoint exists and VM has written data
- **THEN** `virsh backup-begin` exports only blocks changed since the checkpoint
- **THEN** `qemu-img convert` pulls only dirty blocks, producing a smaller backup

#### Scenario: Socket cleanup on success

- **WHEN** `qemu-img convert` completes successfully
- **THEN** the Unix socket is removed via `rm -f`
- **THEN** the checkpoint is preserved for the next incremental run

#### Scenario: Socket cleanup on failure

- **WHEN** `qemu-img convert` fails (non-zero exit or timeout)
- **THEN** the Unix socket is still removed via `rm -f` in a finally block
- **THEN** `BackupResult(success=False, ...)` is returned

### Requirement: NBD socket path uniqueness

`BitmapBackupProvider` SHALL use a process-unique Unix socket path: `/tmp/qsnap-backup-{pid}.sock`. Before starting `backup-begin`, the provider SHALL remove any stale socket at that path.

#### Scenario: Stale socket from crashed process

- **WHEN** a previous qsnap process crashed leaving `/tmp/qsnap-backup-12345.sock`
- **THEN** the new process (different PID) removes the stale socket before starting

### Requirement: Libvirt version check for NBD API

`DefaultFactory.create_backup_provider()` SHALL call `is_libvirt_new_enough(shell)` from `qsnap.utils.nbd` before constructing `BitmapBackupProvider`. If the version is insufficient, the factory SHALL log a WARNING and return `FileCopyBackupProvider`. `BitmapBackupProvider.__init__()` SHALL NOT perform version checking — it SHALL NOT call `virsh --version` and SHALL NOT raise `RuntimeError` for an old libvirt.

#### Scenario: Libvirt too old
- **WHEN** `virsh --version` returns a version older than 6.0
- **THEN** `is_libvirt_new_enough(shell)` returns `False`
- **THEN** `DefaultFactory` does NOT construct `BitmapBackupProvider`
- **THEN** `DefaultFactory` logs a WARNING and returns `FileCopyBackupProvider(shell, state)`

#### Scenario: Libvirt sufficient
- **WHEN** `virsh --version` returns a version 6.0 or newer
- **THEN** `is_libvirt_new_enough(shell)` returns `True`
- **THEN** `DefaultFactory` constructs and returns `BitmapBackupProvider(shell)`

#### Scenario: BitmapBackupProvider constructor is version-check-free
- **WHEN** `BitmapBackupProvider(shell)` is instantiated
- **THEN** no `virsh --version` shell call is made in `__init__`
- **AND** no `RuntimeError` is raised for version reasons
- **AND** the only parameter is `shell: IShell`

### Requirement: BitmapBackupProvider.create_full_backup via NBD full export

`BitmapBackupProvider` SHALL implement `create_full_backup()` using the NBD full-export path (no `--incremental` flag). This produces a standalone qcow2 on the target. The method SHALL NOT raise `NotImplementedError`. No checkpoint SHALL be created or deleted for this FULL — the checkpoint lifecycle remains exclusively in `transfer_missing()` for incremental runs. When `compress=True` and `compression_type="zstd"`, the `-c -o compression_type=zstd` flags SHALL be passed to `qemu-img convert` in the NBD path. When `compress=True` and `compression_type="zlib"`, only `-c` SHALL be added. The `compression_type` parameter SHALL be passed through to `nbd_full_export()`.

#### Scenario: Bitmap FULL with zstd compression
- **WHEN** `BitmapBackupProvider.create_full_backup(snapshot, target, compress=True, compression_type="zstd", bucket_level="monthly")` is called
- **THEN** `qemu-img convert -c -o compression_type=zstd nbd:unix:<socket> <target>` is called
- **AND** the resulting FULL is compressed with zstd

#### Scenario: Bitmap FULL with zlib compression
- **WHEN** `BitmapBackupProvider.create_full_backup(snapshot, target, compress=True, compression_type="zlib", bucket_level="monthly")` is called
- **THEN** `qemu-img convert -c nbd:unix:<socket> <target>` is called (default zlib)
- **AND** the resulting FULL is compressed with zlib

#### Scenario: Bitmap FULL socket cleanup
- **WHEN** the NBD full export completes (success or failure)
- **THEN** the Unix socket is removed via `rm -f` in a `finally` block

#### Scenario: Bucket-driven FULL no longer crashes bitmap targets
- **WHEN** `Core._backup_target()` triggers `_should_create_bucket_full()` for a bitmap-mode target
- **AND** it returns `(True, bucket_level)`
- **THEN** `BitmapBackupProvider.create_full_backup()` is called and succeeds
- **AND** the FULL is recorded in state with the given `bucket_level`

### Requirement: NBD backup job termination via domjobabort

`nbd_full_export()` SHALL call `virsh domjobabort --domain <vm>` in its `finally` block, before socket cleanup. On failure, a WARNING SHALL be logged but the error SHALL NOT propagate — socket cleanup proceeds regardless.

### Requirement: Checkpoint-only creation when FULL exists and no prior checkpoint

`BitmapBackupProvider.transfer_missing()` SHALL check `self._state.get_full_backups(str(target.path))` when no prior checkpoint is found (`prior_checkpoints` is empty). If FULLs exist in state, the provider SHALL create a checkpoint via `virsh checkpoint-create-as --domain <vm_name> --name qsnap-{target_hash}-{snapshot_name}` without performing a data transfer, then `continue` to the next snapshot. This avoids a redundant full NBD export when the bucket strategy already created a FULL in the same run. The FULL already contains all data at this point in time; the checkpoint serves as the baseline for the next incremental run.

If `self._state` is `None` or no FULLs exist in state, the existing behavior SHALL be preserved: a full NBD export is performed (no `--incremental` flag).

#### Scenario: Checkpoint created without transfer when FULL exists
- **WHEN** `transfer_missing()` is called and no prior checkpoint exists for this VM+target
- **AND** `self._state.get_full_backups(target_path)` returns a non-empty list
- **THEN** `virsh checkpoint-create-as --domain <vm> --name qsnap-{hash}-{snap}` is called
- **AND** no `virsh backup-begin` is called (no data transfer)
- **AND** no `qemu-img convert` is called
- **AND** the snapshot is skipped (no `BackupResult` appended for it)
- **AND** an INFO log is emitted: "Created checkpoint %s without transfer (FULL exists in state)"

#### Scenario: Full NBD export when no FULL and no checkpoint
- **WHEN** `transfer_missing()` is called and no prior checkpoint exists
- **AND** `self._state.get_full_backups(target_path)` returns an empty list (or `self._state` is `None`)
- **THEN** the existing behavior is preserved: `virsh backup-begin` is called without `--incremental`
- **AND** a full NBD export is performed

#### Scenario: Checkpoint-only path does not trigger when checkpoint exists
- **WHEN** `transfer_missing()` is called and a prior checkpoint exists
- **THEN** the existing incremental path is used (`virsh backup-begin --incremental <prior>`)
- **AND** the FULL-existence check is not performed (short-circuited by `prior is not None`)

#### Scenario: Checkpoint-only path skips snapshots already on target
- **WHEN** `transfer_missing()` is called and the snapshot name already exists on the target
- **THEN** the snapshot is skipped before reaching the checkpoint-only logic
- **AND** no checkpoint is created for it

### Requirement: Compression for NBD incremental transfers

`BitmapBackupProvider.transfer_missing()` SHALL pass the `-c` flag to `qemu-img convert` when `target.compress=True` (default). When `compression_type="zstd"`, the `-o compression_type=zstd` flag SHALL also be added. When `compression_type="zlib"`, only `-c` SHALL be added (default zlib). When `target.compress=False`, no compression flags SHALL be added.

#### Scenario: Incremental NBD transfer with zstd compression
- **WHEN** `transfer_missing()` is called with `target.compress=True`, `compression_type="zstd"`
- **THEN** `qemu-img convert -O qcow2 -c -o compression_type=zstd nbd:unix:<socket> <target>` is executed

#### Scenario: Incremental NBD transfer with zlib compression
- **WHEN** `transfer_missing()` is called with `target.compress=True`, `compression_type="zlib"`
- **THEN** `qemu-img convert -O qcow2 -c nbd:unix:<socket> <target>` is executed (default zlib)

#### Scenario: Incremental NBD transfer uses stall detection
- **WHEN** `transfer_missing()` is called with `target.backup_stall_timeout = "30m"`
- **THEN** the `qemu-img convert` command is executed via `shell.run_with_stall_detection(cmd, output_file=target_file, stall_timeout=1800)`
- **AND** if the output file stops growing for 30 minutes, the convert is killed
