## MODIFIED Requirements

### Requirement: NBD pull-model backup via virsh backup-begin

`BitmapBackupProvider` v2 SHALL use the libvirt pull-model backup API instead of `qemu-img convert --bitmap`. The transfer pipeline SHALL be: (1) create backup XML with NBD Unix socket, (2) `virsh backup-begin --domain VM backup.xml` to start NBD export, (3) `qemu-img convert -n nbd:unix:<socket> <target>` to pull dirty blocks, (4) cleanup socket. Checkpoints SHALL persist for subsequent incremental runs.

The incremental checkpoint SHALL be passed via an `<incremental>` element in the backup XML, NOT via a `--incremental` CLI flag. The `--incremental` flag does not exist in any version of virsh `backup-begin`. The `write_backup_xml()` function SHALL accept an optional `incremental: str | None = None` parameter. When non-None, the XML SHALL include `<incremental>{checkpoint_name}</incremental>` as a child of `<domainbackup>`, before the `<server>` element.

#### Scenario: First backup — full pull via NBD

- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `write_backup_xml(socket_path, incremental=None)` is called
- **THEN** the backup XML does NOT contain an `<incremental>` element
- **THEN** `virsh backup-begin --domain VM backup.xml` starts a full NBD export (no `--incremental` CLI flag)
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2 file on target

#### Scenario: Incremental backup — dirty blocks via NBD checkpoint

- **WHEN** a prior checkpoint exists and VM has written data
- **THEN** `write_backup_xml(socket_path, incremental=prior_checkpoint)` is called
- **THEN** the backup XML contains `<incremental>prior_checkpoint</incremental>`
- **THEN** `virsh backup-begin --domain VM backup.xml` exports only blocks changed since the checkpoint
- **THEN** `qemu-img convert` pulls only dirty blocks, producing a smaller backup
- **AND** no `--incremental` CLI flag is passed to `virsh backup-begin`

#### Scenario: Socket cleanup on success

- **WHEN** `qemu-img convert` completes successfully
- **THEN** the Unix socket is removed via `rm -f`
- **THEN** the checkpoint is preserved for the next incremental run

#### Scenario: Socket cleanup on failure

- **WHEN** `qemu-img convert` fails (non-zero exit or timeout)
- **THEN** the Unix socket is still removed via `rm -f` in a finally block
- **THEN** `BackupResult(success=False, ...)` is returned

### Requirement: Checkpoint-only creation when FULL exists and no prior checkpoint

`BitmapBackupProvider.transfer_missing()` SHALL check `self._state.get_full_backups(str(target.path))` when no prior checkpoint is found (`prior_checkpoints` is empty). If FULLs exist in state, the provider SHALL create a checkpoint via `virsh checkpoint-create-as --domain <vm_name> --name qsnap-{target_hash}-{snapshot_name}` without performing a data transfer, then `continue` to the next snapshot. This avoids a redundant full NBD export when the bucket strategy already created a FULL in the same run. The FULL already contains all data at this point in time; the checkpoint serves as the baseline for the next incremental run.

If `self._state` is `None` or no FULLs exist in state, the existing behavior SHALL be preserved: a full NBD export is performed (no `<incremental>` element in the backup XML).

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
- **THEN** the existing behavior is preserved: `write_backup_xml(socket_path, incremental=None)` is called
- **AND** `virsh backup-begin` is called without any `--incremental` CLI flag
- **AND** a full NBD export is performed

#### Scenario: Checkpoint-only path does not trigger when checkpoint exists
- **WHEN** `transfer_missing()` is called and a prior checkpoint exists
- **THEN** the existing incremental path is used: `write_backup_xml(socket_path, incremental=prior)` is called
- **AND** the backup XML contains `<incremental>prior</incremental>`
- **AND** the FULL-existence check is not performed (short-circuited by `prior is not None`)

#### Scenario: Checkpoint-only path skips snapshots already on target
- **WHEN** `transfer_missing()` is called and the snapshot name already exists on the target
- **THEN** the snapshot is skipped before reaching the checkpoint-only logic
- **AND** no checkpoint is created for it
