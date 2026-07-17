## MODIFIED Requirements

### Requirement: NBD pull-model backup via virsh backup-begin
`BitmapBackupProvider` v2 SHALL use the libvirt pull-model backup API: (1) create backup XML with NBD Unix socket at `/tmp/qsnap-backup-{pid}.sock`, (2) `virsh backup-begin --domain VM backup.xml` to start NBD export, (3) `qemu-img convert -n nbd:unix:<socket> <target>` to pull dirty blocks, (4) call `virsh domjobabort --domain VM` to terminate the backup job, (5) remove socket. Steps (4) and (5) SHALL execute in a `finally` block. Checkpoints SHALL persist for subsequent incremental runs. This replaces the previous `qemu-img convert --bitmap` direct-access approach.

#### Scenario: First backup — full pull via NBD
- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `virsh backup-begin` starts a full NBD export
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2 file

#### Scenario: Incremental backup — dirty blocks via NBD
- **WHEN** a prior checkpoint exists and VM has written data since that checkpoint
- **THEN** `virsh backup-begin` exports only blocks changed since the checkpoint
- **THEN** the resulting backup file is smaller than a full copy

#### Scenario: NBD backup job terminated after transfer
- **WHEN** `qemu-img convert` from NBD completes (success or failure)
- **THEN** `virsh domjobabort --domain <vm>` is called in the `finally` block
- **AND** the backup job is terminated and releases the state change lock

#### Scenario: Socket cleanup after job abort
- **WHEN** the NBD transfer completes
- **THEN** `virsh domjobabort` runs first
- **AND** then the Unix socket is removed via `rm -f`
- **AND** both execute regardless of success or failure of the transfer
