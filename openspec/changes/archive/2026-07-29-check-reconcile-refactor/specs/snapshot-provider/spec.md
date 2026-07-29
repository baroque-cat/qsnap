## MODIFIED Requirements

### Requirement: External disk-only snapshot creation

The system SHALL create external disk-only snapshots via `virsh snapshot-create-as` with flags `--disk-only --atomic --no-metadata`. After snapshot creation, the system SHALL determine the allocation-size of the new image via `qemu-img info --force-share --output=json`. The `--force-share` flag is REQUIRED because the newly created snapshot file IS the active layer — the running VM holds an exclusive write lock on it. Without `--force-share`, `qemu-img info` fails with a lock error. The method SHALL accept an optional `quiesce: bool = False` parameter to request guest-agent filesystem freeze. The method SHALL NOT compute a `content_hash` — the SHA-256 hash of the raw qcow2 file is semantically incorrect for NBD-created backups and has no consumers. The `content_hash` field is removed from `SnapshotResult`.

After `virsh snapshot-create-as` returns exit code 0, the method SHALL perform post-creation validation before returning `SnapshotResult(success=True)`:

1. **File existence**: `test -f <snapshot_path>` — verify the file landed on disk
2. **qcow2 metadata** (from already-obtained `qemu-img info`): verify `format == "qcow2"`, `incompatible-features` does not contain `"corrupt"`, `backing-filename` points to the previous active layer
3. **libvirt pivot**: `virsh domblklist --domain <vm>` — verify source path = snapshot_path

If ANY validation step fails, return `SnapshotResult(success=False, error=<message>)`.

#### Scenario: Successful snapshot creation with validation

- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** the snapshot file exists on disk (`test -f` succeeds)
- **AND** `qemu-img info` reports format `"qcow2"`, no corrupt bit, correct backing-filename
- **AND** `virsh domblklist` shows the snapshot path as the active source
- **THEN** the module returns `SnapshotResult(success=True, new_allocation=<parsed actual-size>)`
- **AND** no SHA-256 hash is computed

#### Scenario: virsh command fails

- **WHEN** `virsh snapshot-create-as` returns a non-zero exit code
- **THEN** the module returns `SnapshotResult(success=False, error=<stderr from virsh>)`

#### Scenario: virsh command times out

- **WHEN** `virsh snapshot-create-as` exceeds the timeout (120 seconds, 180 for quiesce)
- **THEN** the module returns `SnapshotResult(success=False)` with error containing "timed out"

#### Scenario: Post-snapshot qemu-img info uses --force-share on running VM

- **WHEN** a snapshot is created on a running VM
- **THEN** the subsequent `qemu-img info` command includes `--force-share`
- **AND** the command succeeds despite the VM holding a write lock on the new active layer

#### Scenario: Post-snapshot qemu-img info without --force-share fails (regression guard)

- **WHEN** a snapshot is created on a running VM
- **AND** the `qemu-img info` command does NOT include `--force-share`
- **THEN** the command fails with "Failed to get shared lock" or similar lock error
- **AND** `SnapshotResult(success=False)` would be returned (this scenario documents the bug being fixed)

#### Scenario: Validation fails — file missing despite virsh success

- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** `test -f <snapshot_path>` fails (file does not exist)
- **THEN** `SnapshotResult(success=False, error="snapshot file not found on disk after virsh success")` is returned

#### Scenario: Validation fails — wrong backing-filename

- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** `qemu-img info` reports `backing-filename` pointing to a file that is NOT the previous active layer
- **THEN** `SnapshotResult(success=False, error="backing-filename mismatch")` is returned

#### Scenario: Validation fails — corrupt bit set

- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** `qemu-img info` reports `incompatible-features` containing `"corrupt"`
- **THEN** `SnapshotResult(success=False, error="snapshot has corrupt bit set")` is returned

#### Scenario: Validation fails — libvirt pivot not confirmed

- **WHEN** `virsh snapshot-create-as` returns exit code 0
- **AND** `virsh domblklist` still shows the previous active layer (not the new snapshot)
- **THEN** `SnapshotResult(success=False, error="libvirt pivot not confirmed")` is returned
