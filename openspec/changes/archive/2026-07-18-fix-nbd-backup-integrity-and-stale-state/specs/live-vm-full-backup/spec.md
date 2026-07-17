## MODIFIED Requirements

### Requirement: NBD full-export helper for FULL backups

The system SHALL provide a shared NBD full-export mechanism used by both `FileCopyBackupProvider.create_full_backup()` and `BitmapBackupProvider.create_full_backup()`. The mechanism SHALL: (1) remove any stale socket at `/tmp/qsnap-backup-{pid}.sock`, (2) write a backup XML with `<domainbackup mode='pull'><server transport='unix' path='<socket>'/></domainbackup>`, (3) run `virsh backup-begin --domain <vm> <xml>` WITHOUT `--incremental` (full export, no checkpoint), (4) run `qemu-img convert -n nbd:unix:<socket> <target_file>` to pull the full disk, (5) call `virsh domjobabort --domain <vm>` to terminate the backup job and release the state change lock, (6) clean up the socket via `rm -f`. Steps (5) and (6) SHALL execute in a `finally` block and SHALL run regardless of whether `qemu-img convert` succeeded or failed.

#### Scenario: NBD full export produces standalone qcow2
- **WHEN** the NBD full-export helper is called for a running VM
- **THEN** `virsh backup-begin` is called without `--incremental`
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2 file
- **THEN** the resulting file has no backing file (`qemu-img info` shows `backing file: <none>`)

#### Scenario: NBD socket cleaned up on success
- **WHEN** the NBD full export completes successfully
- **THEN** `virsh domjobabort --domain <vm>` is called to terminate the backup job
- **AND** the Unix socket file is removed via `rm -f`
- **AND** both cleanup steps execute in the `finally` block

#### Scenario: NBD cleanup on failure — backup job aborted
- **WHEN** `qemu-img convert` from NBD fails
- **THEN** `virsh domjobabort --domain <vm>` is called in the `finally` block
- **AND** the Unix socket file is still removed via `rm -f`
- **AND** `BackupResult(success=False, error=<stderr>)` is returned

#### Scenario: NBD backup job abort fails gracefully
- **WHEN** `virsh domjobabort --domain <vm>` fails (e.g., job already terminated, VM stopped)
- **THEN** a WARNING is logged: "virsh domjobabort failed (job may have already terminated)"
- **AND** the socket cleanup still proceeds
- **AND** the error from `domjobabort` is NOT propagated to the caller

#### Scenario: No checkpoint created for file-copy NBD FULL
- **WHEN** `FileCopyBackupProvider` uses NBD for a FULL backup
- **THEN** no `virsh checkpoint-create-as` is called
- **AND** no `virsh checkpoint-delete` is called
- **AND** the NBD export is a one-shot frozen view with no persistent checkpoint
