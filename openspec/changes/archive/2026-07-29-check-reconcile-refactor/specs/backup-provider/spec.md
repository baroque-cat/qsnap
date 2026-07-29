## ADDED Requirements

### Requirement: Post-transfer chain-to-FULL verification

After `BitmapBackupProvider.transfer_missing()` successfully creates an incremental backup (atomic rename complete), the provider SHALL verify the backing chain from the incremental to the FULL anchor is traversable via `qemu-img info --force-share --backing-chain --output=json <incremental_path>`. If the chain is broken (any file in the chain missing), the provider SHALL log CRITICAL and return `BackupResult(success=False, error="chain-to-FULL not traversable")`.

#### Scenario: Chain to FULL traversable after incremental transfer

- **WHEN** `transfer_missing()` creates an incremental backup
- **AND** `qemu-img info --backing-chain` shows an unbroken chain to the FULL
- **THEN** `BackupResult(success=True)` is returned

#### Scenario: Broken chain to FULL detected after incremental transfer

- **WHEN** `transfer_missing()` creates an incremental backup
- **AND** `qemu-img info --backing-chain` fails or shows a broken chain
- **THEN** a CRITICAL log is emitted
- **AND** `BackupResult(success=False, error="chain-to-FULL not traversable")` is returned

### Requirement: Post-creation FULL backup verification

After `BitmapBackupProvider.create_full_backup()` successfully creates a FULL backup (atomic rename complete), the provider SHALL verify: (a) `backing-filename` is absent or `<none>` via `qemu-img info`, (b) a `qsnap-` checkpoint exists via `virsh checkpoint-list --name --domain <vm>`. If either check fails, return `BackupResult(success=False, error=<message>)`.

#### Scenario: FULL has no backing file and checkpoint exists

- **WHEN** `create_full_backup()` creates a FULL backup
- **AND** `qemu-img info` shows no `backing-filename`
- **AND** `virsh checkpoint-list` shows a `qsnap-` checkpoint
- **THEN** `BackupResult(success=True)` is returned

#### Scenario: FULL has unexpected backing file

- **WHEN** `create_full_backup()` creates a FULL backup
- **AND** `qemu-img info` reports a `backing-filename`
- **THEN** `BackupResult(success=False, error="FULL backup has unexpected backing file")` is returned

#### Scenario: Checkpoint missing after FULL creation

- **WHEN** `create_full_backup()` creates a FULL backup
- **AND** `virsh checkpoint-list` returns no `qsnap-` checkpoints
- **THEN** `BackupResult(success=False, error="checkpoint missing — next incremental impossible")` is returned
