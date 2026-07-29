# Spec: post-creation-validation

## Purpose

Post-creation validation ensures that snapshots and backups are verifiably correct immediately after creation, before state is recorded. This prevents silent failures where virsh or qemu-img reports success but the resulting image is corrupt, incomplete, or has an incorrect backing chain.

## Requirements

### Requirement: Post-creation snapshot validation

After `virsh snapshot-create-as` returns exit code 0, `ExternalSnapshotProvider.create()` SHALL perform the following validation steps before returning `SnapshotResult(success=True)`:

1. **File existence**: `test -f <snapshot_path>` via `IShell.run()` — verify the snapshot file landed on disk.
2. **qcow2 metadata**: Parse the already-obtained `qemu-img info --force-share --output=json` output and verify:
   - `format` equals `"qcow2"`
   - `virtual-size` matches the base image's `virtual-size` (if determinable)
   - `actual-size` is reasonable for a new overlay (not approximately equal to `virtual-size`, which would indicate a full copy instead of an overlay)
   - `incompatible-features` does not contain `"corrupt"`
3. **Backing-filename**: From the same `qemu-img info` output, verify `backing-filename` points to the previous active layer (the disk path before the snapshot was created).
4. **libvirt pivot**: `virsh domblklist --domain <vm>` — verify the source path for the snapshotted disk equals `<snapshot_path>`, confirming libvirt pivoted the active layer.

If ANY validation step fails, the method SHALL return `SnapshotResult(success=False, error=<descriptive message>)`. Core SHALL NOT call `record_snapshot()` for failed snapshots.

#### Scenario: All validation checks pass

- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** the snapshot file exists on disk
- **AND** `qemu-img info` reports format `"qcow2"`, matching `virtual-size`, no corrupt bit, and correct `backing-filename`
- **AND** `virsh domblklist` shows the snapshot path as the active source
- **THEN** `SnapshotResult(success=True, new_allocation=<actual-size>)` is returned

#### Scenario: Snapshot file missing despite virsh success

- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** `test -f <snapshot_path>` fails (file does not exist)
- **THEN** `SnapshotResult(success=False, error="snapshot file not found on disk after virsh success")` is returned
- **AND** Core does not record the snapshot in state

#### Scenario: Wrong backing-filename

- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** `qemu-img info` reports `backing-filename` pointing to a file that is NOT the previous active layer
- **THEN** `SnapshotResult(success=False, error="backing-filename mismatch: expected <previous>, got <actual>")` is returned

#### Scenario: Corrupt bit set

- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** `qemu-img info` reports `incompatible-features` containing `"corrupt"`
- **THEN** `SnapshotResult(success=False, error="snapshot has corrupt bit set")` is returned

#### Scenario: libvirt pivot not confirmed

- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** `virsh domblklist` still shows the previous active layer (not the new snapshot)
- **THEN** `SnapshotResult(success=False, error="libvirt pivot not confirmed: domblklist still shows <old_path>")` is returned

### Requirement: Post-transfer incremental backup validation

After `BitmapBackupProvider.transfer_missing()` successfully creates an incremental backup (atomic rename complete), the provider SHALL perform:

1. **Chain-to-FULL traversability**: `qemu-img info --force-share --backing-chain --output=json <incremental_path>` — verify the backing chain from the incremental traverses unbroken to the FULL anchor.
2. **Checkpoint existence**: `virsh checkpoint-list --name --domain <vm>` — verify at least one `qsnap-` prefixed checkpoint exists for this VM+target (dirty-bitmap baseline for next incremental).

If either check fails, the provider SHALL log a CRITICAL warning and return `BackupResult(success=False, error=<message>)`. Core SHALL NOT call `record_incremental_dependency()` for failed incrementals.

#### Scenario: Incremental chain traversable and checkpoint exists

- **WHEN** `transfer_missing()` creates an incremental backup
- **AND** `qemu-img info --backing-chain` shows an unbroken chain to the FULL
- **AND** `virsh checkpoint-list` shows a `qsnap-` checkpoint for this target
- **THEN** `BackupResult(success=True)` is returned

#### Scenario: Broken chain to FULL detected

- **WHEN** `transfer_missing()` creates an incremental backup
- **AND** `qemu-img info --backing-chain` fails or shows a broken chain
- **THEN** a CRITICAL log is emitted: "chain-to-FULL verification failed for <incremental>"
- **AND** `BackupResult(success=False, error="chain-to-FULL not traversable")` is returned

#### Scenario: Checkpoint missing after transfer

- **WHEN** `transfer_missing()` creates an incremental backup
- **AND** `virsh checkpoint-list` returns no `qsnap-` checkpoints for this VM
- **THEN** a CRITICAL log is emitted: "no checkpoint found after incremental transfer"
- **AND** `BackupResult(success=False, error="checkpoint missing — next incremental impossible")` is returned

### Requirement: Post-creation FULL backup validation

After `BitmapBackupProvider.create_full_backup()` successfully creates a FULL backup (atomic rename complete), the provider SHALL verify:

1. **No backing file**: `qemu-img info --force-share --output=json <full_path>` — verify `backing-filename` is absent or `<none>`.
2. **Checkpoint existence**: `virsh checkpoint-list --name --domain <vm>` — verify a `qsnap-` checkpoint exists (baseline for future incrementals).

If either check fails, the provider SHALL log a CRITICAL warning and return `BackupResult(success=False, error=<message>)`.

#### Scenario: FULL has no backing file and checkpoint exists

- **WHEN** `create_full_backup()` creates a FULL backup
- **AND** `qemu-img info` shows no `backing-filename` (standalone)
- **AND** `virsh checkpoint-list` shows a `qsnap-` checkpoint
- **THEN** `BackupResult(success=True)` is returned

#### Scenario: FULL has unexpected backing file

- **WHEN** `create_full_backup()` creates a FULL backup
- **AND** `qemu-img info` reports a `backing-filename` (not standalone)
- **THEN** `BackupResult(success=False, error="FULL backup has unexpected backing file")` is returned
