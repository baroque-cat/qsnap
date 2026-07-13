## ADDED Requirements

### Requirement: BitmapBackupProvider implements IBackupProvider
The system SHALL provide a `BitmapBackupProvider` class in `qsnap/modules/backup/bitmap.py` that implements `IBackupProvider`. It SHALL accept `IShell` as its sole constructor dependency. It SHALL use `virsh checkpoint-create-as` and `qemu-img convert --bitmap` to transfer only dirty blocks between checkpoints.

#### Scenario: Constructor accepts IShell
- **WHEN** `BitmapBackupProvider(shell=mock_shell)` is instantiated
- **THEN** `isinstance(provider, IBackupProvider)` is True
- **THEN** the provider is ready for transfer operations

### Requirement: Transfer missing snapshots via dirty bitmap extraction
The system SHALL determine which snapshots are missing on the target (via `list()`). For each missing snapshot, it SHALL:
1. Create a checkpoint via `virsh checkpoint-create-as --domain <vm> --name "qsnap-<target_hash>-<timestamp>"`
2. Extract dirty blocks via `qemu-img convert -f qcow2 -O qcow2 --bitmap "<checkpoint_name>" <source> <target_path>`
3. Delete the checkpoint via `virsh checkpoint-delete --domain <vm> "<checkpoint_name>" --metadata`

#### Scenario: First backup — full copy (no prior checkpoint)
- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `BitmapBackupProvider` creates a checkpoint and performs a full `qemu-img convert` (no `--bitmap` flag)
- **THEN** the backup is a standalone qcow2 file on the target containing the complete virtual disk

#### Scenario: Incremental backup — dirty blocks only
- **WHEN** a prior qsnap checkpoint exists for this VM+target
- **AND** the VM has written data since that checkpoint
- **THEN** `qemu-img convert --bitmap "<checkpoint>"` extracts only changed blocks
- **THEN** the resulting backup file size is proportional to the changed data, not the full disk

#### Scenario: Checkpoint cleanup after successful transfer
- **WHEN** `qemu-img convert` completes successfully
- **THEN** the checkpoint is deleted via `virsh checkpoint-delete --metadata`
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
`BitmapBackupProvider` SHALL provide a method `list_checkpoints(vm_name: str) -> list[str]` that discovers existing qsnap-owned checkpoints for the given VM using `virsh checkpoint-list --name`. Only checkpoints with the `qsnap-` prefix SHALL be returned.

#### Scenario: Existing qsnap checkpoints found
- **WHEN** `virsh checkpoint-list --name VM` returns `["qsnap-target1-20250101", "manual-checkpoint", "qsnap-target1-20250102"]`
- **THEN** `list_checkpoints("VM")` returns `["qsnap-target1-20250101", "qsnap-target1-20250102"]`

### Requirement: Factory selects BitmapBackupProvider for bitmap mode
`DefaultFactory.create_backup_provider(vm_config, target)` SHALL return `BitmapBackupProvider` when `target.incremental_mode == "bitmap"`. It SHALL return `FileCopyBackupProvider` when `target.incremental_mode == "file-copy"` (default).

#### Scenario: Bitmap mode selected via TargetConfig
- **WHEN** a target has `incremental_mode = "bitmap"`
- **THEN** `factory.create_backup_provider(vm_config, target)` returns a `BitmapBackupProvider` instance

#### Scenario: File-copy mode is the default
- **WHEN** a target has `incremental_mode` unset or set to `"file-copy"`
- **THEN** `factory.create_backup_provider(vm_config, target)` returns a `FileCopyBackupProvider` instance
