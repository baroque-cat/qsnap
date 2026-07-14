## ADDED Requirements

### Requirement: NBD pull-model backup via virsh backup-begin
`BitmapBackupProvider` v2 SHALL use the libvirt pull-model backup API: (1) create backup XML with NBD Unix socket at `/tmp/qsnap-backup-{pid}.sock`, (2) `virsh backup-begin --domain VM backup.xml` to start NBD export, (3) `qemu-img convert -n nbd:unix:<socket> <target>` to pull dirty blocks, (4) remove socket. Checkpoints SHALL persist for subsequent incremental runs. This replaces the previous `qemu-img convert --bitmap` direct-access approach.

#### Scenario: First backup — full pull via NBD
- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `virsh backup-begin` starts a full NBD export
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2 file

#### Scenario: Incremental backup — dirty blocks via NBD
- **WHEN** a prior checkpoint exists and VM has written data since that checkpoint
- **THEN** `virsh backup-begin` exports only blocks changed since the checkpoint
- **THEN** the resulting backup file is smaller than a full copy

### Requirement: Libvirt version check in BitmapBackupProvider
`BitmapBackupProvider.__init__()` SHALL verify libvirt >= 6.0 (required for `backup-begin`). If version is older, SHALL raise `RuntimeError`. `DefaultFactory.create_backup_provider()` SHALL catch `RuntimeError` and fall back to `FileCopyBackupProvider`.

#### Scenario: Libvirt too old — factory fallback
- **WHEN** libvirt version is 5.0 and `target.incremental_mode == "bitmap"`
- **THEN** `BitmapBackupProvider()` raises `RuntimeError`
- **THEN** `DefaultFactory` catches it, logs a warning, and returns `FileCopyBackupProvider`

### Requirement: Backup verification step
`FileCopyBackupProvider.transfer_missing()` and `BitmapBackupProvider.transfer_missing()` SHALL perform post-transfer verification according to `target.verify`. `"metadata"`: verify `qemu-img info` output (format, virtual-size, actual-size tolerance). `"full"`: additionally run `qemu-img compare`. Both failure cases SHALL produce `BackupResult(success=False, error="verification failed: ...")`.

#### Scenario: Metadata verification passes
- **WHEN** target.verify is "metadata" and qemu-img info shows matching format and size
- **THEN** backup is marked success

#### Scenario: Verification failure produces error
- **WHEN** verification detects wrong format or size mismatch
- **THEN** `BackupResult(success=False, error="verification failed: ...")` is returned

## MODIFIED Requirements

### Requirement: BitmapBackupProvider implements IBackupProvider
The system SHALL provide a `BitmapBackupProvider` class in `qsnap/modules/backup/bitmap.py` that implements `IBackupProvider`. It SHALL accept `IShell` as its sole constructor dependency. It SHALL use the `virsh backup-begin` NBD pull-model API.

#### Scenario: Constructor accepts IShell
- **WHEN** `BitmapBackupProvider(shell=mock_shell)` is instantiated
- **THEN** `isinstance(provider, IBackupProvider)` is True
- **THEN** the provider is ready for transfer operations

### Requirement: Transfer missing snapshots via dirty bitmap extraction
The system SHALL determine which snapshots are missing on the target and for each SHALL use `virsh backup-begin` with NBD export to transfer data. On first backup (no prior checkpoint), a full export is performed. On subsequent backups, only dirty blocks since the last checkpoint are exported.

#### Scenario: First backup — full NBD export (no prior checkpoint)
- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `BitmapBackupProvider` performs a full NBD export
- **THEN** the backup is a standalone qcow2 file on the target containing the complete virtual disk

#### Scenario: Incremental backup — dirty blocks only
- **WHEN** a prior qsnap checkpoint exists for this VM+target
- **AND** the VM has written data since that checkpoint
- **THEN** `virsh backup-begin` exports only changed blocks via NBD
- **THEN** the resulting backup file size is proportional to the changed data, not the full disk

#### Scenario: Transfer failure preserves checkpoint
- **WHEN** `qemu-img convert` from NBD fails
- **THEN** the checkpoint is NOT deleted
- **THEN** the module returns `BackupResult(success=False, error=<stderr>)`
- **THEN** the NBD socket is cleaned up via `rm -f`
