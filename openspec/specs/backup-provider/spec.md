# Backup Provider

## Purpose

Backup transfer to external storage via file copy (`cp`) with backing chain rebase (`qemu-img rebase -u`).
Copies missing snapshots to a target directory on a separate filesystem (e.g. XFS), maintaining incremental backup semantics.

## Requirements

### Requirement: Transfer missing snapshots to backup target

The system SHALL copy snapshots missing from the target storage into the `target.path` directory. Before copying, the system SHALL determine which snapshots already exist on the target (via `list()`). For incremental backups (`target.incremental == True`) the system SHALL execute `qemu-img rebase -u -b <new_backing_path>` to rebuild the backing file path on the target.

#### Scenario: New snapshot copied to empty target

- **WHEN** target is empty (list() returns [])
- **AND** there is one snapshot to copy
- **THEN** the snapshot is copied (`cp`) to `target.path/<snapshot.name>.qcow2`
- **AND** `BackupResult(success=True, bytes_transferred=<file_size>)` is returned

#### Scenario: Snapshot already exists on target — skipped

- **WHEN** target already contains a snapshot with the same name
- **THEN** that snapshot is NOT copied again
- **AND** it does not appear in the returned `BackupResult` list

#### Scenario: Incremental backup — rebase backing path

- **WHEN** `target.incremental == True`
- **AND** the copied snapshot has a backing file
- **THEN** after copying, `qemu-img rebase -u -b <new_relative_path> <target_file>` is executed
- **AND** `<new_relative_path>` is the backing file name (without path) in the same target directory

#### Scenario: Non-incremental backup — no rebase

- **WHEN** `target.incremental == False`
- **THEN** the snapshot is copied without calling `qemu-img rebase`
- **AND** the backing path remains as-is (absolute source path)

#### Scenario: Copy fails — disk full or permission error

- **WHEN** `cp` returns a non-zero exit code
- **THEN** the module returns `BackupResult(success=False, error=<stderr>)`

### Requirement: List existing backups on target

The system SHALL scan the `target.path` directory for `.qcow2` files. For each file the system SHALL obtain metadata via `qemu-img info --output=json` and produce a `SnapshotInfo` with name, path, timestamp (from filename or mtime), and allocation.

#### Scenario: Target directory exists with backups

- **WHEN** `target.path` contains files `vm.20250101T000000.qcow2` and `vm.20250102T000000.qcow2`
- **THEN** `list()` returns a list of 2 `SnapshotInfo`, sorted by timestamp

#### Scenario: Target directory does not exist

- **WHEN** `target.path` does not exist
- **THEN** `list()` returns an empty list
- **AND** no shell commands are executed

#### Scenario: Target directory exists but is empty

- **WHEN** `target.path` exists but contains no `.qcow2` files
- **THEN** `list()` returns an empty list

### Requirement: Delete backup from target

The system SHALL delete a backup file via `rm -f`. The method accepts a `SnapshotInfo` and returns a `ShellResult`.

#### Scenario: Successful backup deletion

- **WHEN** `rm -f <backup.path>` completes successfully
- **THEN** the module returns `ShellResult(success=True)`

#### Scenario: Backup file does not exist

- **WHEN** the backup file does not exist
- **THEN** `rm -f` returns success
- **AND** the module returns `ShellResult(success=True)`

### Requirement: BitmapBackupProvider implements IBackupProvider
The system SHALL provide a `BitmapBackupProvider` class in `qsnap/modules/backup/bitmap.py` that implements `IBackupProvider`. It SHALL accept `IShell` as its sole constructor dependency.

#### Scenario: Constructor accepts IShell
- **WHEN** `BitmapBackupProvider(shell=mock_shell)` is instantiated
- **THEN** `isinstance(provider, IBackupProvider)` is True
- **THEN** the provider is ready for transfer operations

### Requirement: Transfer missing snapshots via dirty bitmap extraction
The system SHALL determine which snapshots are missing on the target and for each SHALL: (1) use the prior backup's checkpoint to extract dirty blocks via `qemu-img convert --bitmap`, (2) delete the prior checkpoint via `virsh checkpoint-delete --metadata`, (3) create a new checkpoint via `virsh checkpoint-create-as`. Checkpoint names SHALL use the `qsnap-{target_hash}-{snapshot_name}` format.

#### Scenario: First backup — full copy (no prior checkpoint)
- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `BitmapBackupProvider` performs a full `qemu-img convert` (no `--bitmap` flag)
- **THEN** the backup is a standalone qcow2 file on the target containing the complete virtual disk

#### Scenario: Incremental backup — dirty blocks only
- **WHEN** a prior qsnap checkpoint exists for this VM+target
- **AND** the VM has written data since that checkpoint
- **THEN** `qemu-img convert --bitmap "<checkpoint>"` extracts only changed blocks
- **THEN** the resulting backup file size is proportional to the changed data, not the full disk

#### Scenario: Checkpoint cleanup after successful transfer
- **WHEN** `qemu-img convert` completes successfully
- **THEN** the prior checkpoint is deleted via `virsh checkpoint-delete --metadata`
- **THEN** a new checkpoint is created for the next incremental run

#### Scenario: Transfer failure preserves checkpoint
- **WHEN** `qemu-img convert` fails (non-zero exit, timeout)
- **THEN** the checkpoint is NOT deleted
- **THEN** the module returns `BackupResult(success=False, error=<stderr>)`
- **THEN** the next run can retry using the preserved checkpoint

### Requirement: Rebase error handling in FileCopyBackupProvider
`FileCopyBackupProvider.transfer_missing()` SHALL return `BackupResult(success=False, error=<message>)` when `qemu-img rebase -u` fails. It SHALL NOT silently swallow the error.

#### Scenario: Rebase fails due to invalid backing path
- **WHEN** `qemu-img rebase -u -b /nonexistent/base.qcow2 /target/snap.qcow2` returns non-zero
- **THEN** the backup for that snapshot is marked `success=False` with the rebase error message

### Requirement: List checkpoints for target
`BitmapBackupProvider` SHALL provide a method `list_checkpoints(vm_name: str) -> list[str]` that discovers existing qsnap-owned checkpoints via `virsh checkpoint-list --name`. Only checkpoints with the `qsnap-` prefix SHALL be returned.

#### Scenario: Existing qsnap checkpoints found
- **WHEN** `virsh checkpoint-list --name VM` returns `["qsnap-target1-20250101", "manual-checkpoint", "qsnap-target1-20250102"]`
- **THEN** `list_checkpoints("VM")` returns `["qsnap-target1-20250101", "qsnap-target1-20250102"]`

### Requirement: Factory selects BitmapBackupProvider for bitmap mode
`DefaultFactory.create_backup_provider(vm_config, target)` SHALL return `BitmapBackupProvider` when `target.incremental_mode == "bitmap"`. It SHALL return `FileCopyBackupProvider` when `target.incremental_mode == "file-copy"` (default). On `BitmapBackupProvider` construction failure (QEMU < 5.1), it SHALL log a warning and fall back to `FileCopyBackupProvider`.

#### Scenario: Bitmap mode selected via TargetConfig
- **WHEN** a target has `incremental_mode = "bitmap"`
- **THEN** `factory.create_backup_provider(vm_config, target)` returns a `BitmapBackupProvider` instance

#### Scenario: File-copy mode is the default
- **WHEN** a target has `incremental_mode` unset or set to `"file-copy"`
- **THEN** `factory.create_backup_provider(vm_config, target)` returns a `FileCopyBackupProvider` instance
