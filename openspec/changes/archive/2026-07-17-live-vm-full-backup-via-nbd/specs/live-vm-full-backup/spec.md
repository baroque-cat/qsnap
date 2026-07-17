## ADDED Requirements

### Requirement: VM running-state detection for FULL backup method selection

`FileCopyBackupProvider.create_full_backup()` and `BitmapBackupProvider.create_full_backup()` SHALL detect whether the source VM is running by calling `virsh dominfo --domain <vm_name>` and parsing the `State:` line. If the VM state is `running`, the provider SHALL use the NBD pull-model to export a frozen point-in-time view of the disk. If the VM state is `shut off` (or any non-running state), the provider SHALL use direct `qemu-img convert` on the snapshot file (existing behavior, no lock conflict).

#### Scenario: Running VM triggers NBD-based FULL backup
- **WHEN** `virsh dominfo --domain myvm` returns `State: running`
- **AND** `create_full_backup()` is called
- **THEN** the provider uses `virsh backup-begin` + `qemu-img convert -n nbd:unix:<socket>` to create the FULL
- **AND** no direct `qemu-img convert` on the snapshot file is attempted

#### Scenario: Stopped VM triggers direct convert FULL backup
- **WHEN** `virsh dominfo --domain myvm` returns `State: shut off`
- **AND** `create_full_backup()` is called
- **THEN** the provider uses `qemu-img convert [-c] -f qcow2 -O qcow2 <source> <target>` directly
- **AND** no NBD export is started

#### Scenario: VM state detection failure falls back to direct convert with warning
- **WHEN** `virsh dominfo` fails (non-zero exit code)
- **THEN** the provider logs a WARNING and attempts direct `qemu-img convert`
- **AND** if direct convert fails with a lock error, `BackupResult(success=False, error="...lock...")` is returned

### Requirement: NBD full-export helper for FULL backups

The system SHALL provide a shared NBD full-export mechanism used by both `FileCopyBackupProvider.create_full_backup()` and `BitmapBackupProvider.create_full_backup()`. The mechanism SHALL: (1) remove any stale socket at `/tmp/qsnap-backup-{pid}.sock`, (2) write a backup XML with `<domainbackup mode='pull'><server transport='unix' path='<socket>'/></domainbackup>`, (3) run `virsh backup-begin --domain <vm> <xml>` WITHOUT `--incremental` (full export, no checkpoint), (4) run `qemu-img convert -n nbd:unix:<socket> <target_file>` to pull the full disk, (5) clean up the socket via `rm -f` in a `finally` block.

#### Scenario: NBD full export produces standalone qcow2
- **WHEN** the NBD full-export helper is called for a running VM
- **THEN** `virsh backup-begin` is called without `--incremental`
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2 file
- **THEN** the resulting file has no backing file (`qemu-img info` shows `backing file: <none>`)

#### Scenario: NBD socket cleaned up on success
- **WHEN** the NBD full export completes successfully
- **THEN** the Unix socket file is removed via `rm -f`

#### Scenario: NBD socket cleaned up on failure
- **WHEN** `qemu-img convert` from NBD fails
- **THEN** the Unix socket file is still removed via `rm -f` in the `finally` block
- **AND** `BackupResult(success=False, error=<stderr>)` is returned

#### Scenario: No checkpoint created for file-copy NBD FULL
- **WHEN** `FileCopyBackupProvider` uses NBD for a FULL backup
- **THEN** no `virsh checkpoint-create-as` is called
- **AND** no `virsh checkpoint-delete` is called
- **AND** the NBD export is a one-shot frozen view with no persistent checkpoint

### Requirement: NBD FULL exports current disk state

The NBD full-export mechanism exports the disk state at the moment of `virsh backup-begin`, which MAY be slightly newer than the last snapshot (writes between snapshot creation and FULL backup creation). The FULL backup timestamp SHALL be recorded as the snapshot's timestamp (for retention bucket alignment), NOT the NBD export time.

#### Scenario: FULL timestamp matches snapshot, not export time
- **WHEN** a FULL backup is created via NBD at time T_export
- **AND** the source snapshot was created at time T_snapshot
- **THEN** the FULL is recorded in state with `timestamp = T_snapshot`
- **AND** retention bucket alignment uses T_snapshot

### Requirement: Libvirt version check for NBD FULL path

Before attempting the NBD full-export path, the provider SHALL verify libvirt >= 6.0 (required for `backup-begin`). If libvirt is too old, the provider SHALL fall back to direct `qemu-img convert` and log a WARNING that the backup may fail due to lock conflict on running VMs.

#### Scenario: Old libvirt falls back to direct convert with warning
- **WHEN** libvirt version is < 6.0 and the VM is running
- **THEN** the provider logs a WARNING: "libvirt < 6.0 — NBD unavailable, attempting direct convert (may fail on running VM)"
- **THEN** direct `qemu-img convert` is attempted
- **AND** if it fails with a lock error, `BackupResult(success=False)` is returned

### Requirement: Atomic FULL file creation via NBD

When using NBD for FULL backup, the target file SHALL be created at a `.tmp` path first, then atomically renamed to the final `vm.FULL.YYYYMMDD.qcow2` name on success. This matches the existing `FileCopyBackupProvider` atomic-creation pattern.

#### Scenario: NBD FULL creates tmp then renames
- **WHEN** NBD full export succeeds
- **THEN** the data is written to `<target_path>/vm.FULL.YYYYMMDD.qcow2.tmp`
- **THEN** the file is renamed to `<target_path>/vm.FULL.YYYYMMDD.qcow2`
- **AND** `BackupResult(success=True, path=<final_path>)` is returned

#### Scenario: NBD FULL failure leaves no final file
- **WHEN** NBD full export fails
- **THEN** the `.tmp` file is removed
- **AND** no `vm.FULL.*.qcow2` file is created
- **AND** `BackupResult(success=False, error=<message>)` is returned
