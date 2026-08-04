# Timestamp Formatting

## Purpose

The unified naming and timestamp format for snapshots and backups. Names embed a seconds-resolution timestamp, the disk target, and a 6-hex uniqueness suffix; timestamps are parsed back from names via regex.

## Requirements

### Requirement: Unified snapshot naming format
`Core._generate_snapshot_name()` SHALL generate snapshot names using the format `{vm_name}.{YYYYMMDDTHHMMSS}_{disk}_{6hex}` where the timestamp has seconds resolution and the 6-character hex suffix (`secrets.token_hex(3)`) guarantees uniqueness even when two snapshots are created within the same second.

#### Scenario: Standard snapshot name
- **WHEN** a snapshot is created for VM `myvm` disk `vda` at 2025-07-13 15:31:23
- **THEN** the snapshot name matches pattern `myvm.20250713T153123_vda_<6_hex>`

#### Scenario: Multi-disk VM
- **WHEN** a snapshot is created for VM `myvm` with disks `vda` and `vdb`
- **THEN** two snapshots are created, each with the disk name in the suffix: `myvm.<ts>_vda_<hex>` and `myvm.<ts>_vdb_<hex>`

### Requirement: Collision suffix for duplicate names
If a snapshot file with the same name (including hex suffix) already exists (astronomically unlikely with random hex), the system SHALL append `_N` (starting at 1) to the name.

#### Scenario: Duplicate name resolution
- **WHEN** a snapshot named `vm.20250713T153123_vda_a1b2c3.qcow2` already exists and another snapshot generates the same name
- **THEN** the new snapshot is named `vm.20250713T153123_vda_a1b2c3_1.qcow2`

### Requirement: FULL backup naming format
`BitmapBackupProvider.create_full_backup()` SHALL generate FULL backup names using the format `{vm_name}.FULL.{YYYYMMDDTHHMMSS}_{6hex}` where the timestamp has seconds resolution (from `source_snapshot.timestamp`) and the 6-character hex suffix guarantees uniqueness.

#### Scenario: Standard FULL backup name
- **WHEN** a FULL backup is created for VM `myvm` from a snapshot with timestamp 2025-07-13 15:31:23
- **THEN** the FULL backup file is named `myvm.FULL.20250713T153123_<6_hex>.qcow2`

### Requirement: Incremental backup naming format
`BitmapBackupProvider.transfer_missing()` SHALL use the source snapshot name verbatim as the incremental backup filename. The format is `{snapshot.name}.qcow2` on the target.

#### Scenario: Incremental backup name
- **WHEN** an incremental backup is created from snapshot `myvm.20250713T153123_vda_a1b2c3`
- **THEN** the incremental backup file is named `myvm.20250713T153123_vda_a1b2c3.qcow2` on the target

### Requirement: Checkpoint naming format
`BitmapBackupProvider._new_checkpoint_name()` SHALL generate checkpoint names using the format `qsnap-{target_hash}-{YYYYMMDDTHHMMSS}-{6_hex}` where `target_hash` is the first 8 hex characters of the MD5 of the target path, and the 6-character hex suffix (`secrets.token_hex(3)`) prevents collisions.

#### Scenario: Standard checkpoint name
- **WHEN** a checkpoint is created for target path `/mnt/backup` at 2025-07-13 15:31:23
- **THEN** the checkpoint name matches pattern `qsnap-<8_hex_hash>-20250713T153123-<6_hex>`
