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

`BitmapBackupProvider.__init__()` SHALL verify that `virsh` supports `backup-begin` (libvirt >= 6.0). If not supported, the constructor SHALL raise `RuntimeError("virsh backup-begin not available — libvirt 6.0+ required")`.

#### Scenario: Libvirt too old

- **WHEN** `virsh --version` returns a version older than 6.0
- **THEN** `BitmapBackupProvider()` raises `RuntimeError`
- **THEN** `DefaultFactory` catches and falls back to `FileCopyBackupProvider`

### Requirement: BitmapBackupProvider.create_full_backup via NBD full export

`BitmapBackupProvider` SHALL implement `create_full_backup()` using the NBD full-export path (no `--incremental` flag). This produces a standalone qcow2 on the target. The method SHALL NOT raise `NotImplementedError`. No checkpoint SHALL be created or deleted for this FULL — the checkpoint lifecycle remains exclusively in `transfer_missing()` for incremental runs. When `compress=True`, the `-c` flag SHALL be passed to `qemu-img convert` in the NBD path.

#### Scenario: Bitmap FULL via NBD succeeds
- **WHEN** `BitmapBackupProvider.create_full_backup(snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** `virsh backup-begin` is called without `--incremental`
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2
- **AND** no `virsh checkpoint-create-as` is called
- **AND** no `virsh checkpoint-delete` is called

#### Scenario: Bitmap FULL with compression succeeds
- **WHEN** `BitmapBackupProvider.create_full_backup(snapshot, target, compress=True, bucket_level="monthly")` is called
- **THEN** `qemu-img convert -c nbd:unix:<socket> <target>` is called with the `-c` flag
- **AND** the resulting FULL is compressed

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
