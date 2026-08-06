# Post-creation Validation — Delta

## MODIFIED Requirements

### Requirement: Post-creation snapshot validation

After `virsh snapshot-create-as` returns exit code 0, `ExternalSnapshotProvider.create()`
and `ExternalSnapshotProvider.create_multi()` SHALL perform the following validation
steps before returning `SnapshotResult(success=True)`:

1. **File existence**: `test -f <snapshot_path>` via `IShell.run()` — verify the snapshot file landed on disk.
2. **qcow2 metadata**: Parse the already-obtained `qemu-img info --force-share --output=json` output and verify:
   - `format` equals `"qcow2"`
   - `virtual-size` matches the base image's `virtual-size` (if determinable)
   - `actual-size` is reasonable for a new overlay (not approximately equal to `virtual-size`, which would indicate a full copy instead of an overlay)
   - `incompatible-features` does not contain `"corrupt"`
3. **Backing-filename**: From the same `qemu-img info` output, verify `backing-filename` points to the previous active layer (the disk path before the snapshot was created).
4. **libvirt pivot**: `virsh domblklist --domain <vm>` — verify the source path for the snapshotted disk equals `<snapshot_path>`, confirming libvirt pivoted the active layer.

If ANY validation step fails, the method SHALL return `SnapshotResult(success=False, error=<descriptive message>)`. Core SHALL NOT call `record_snapshot()` for failed snapshots.

For `create_multi()`, steps 1–3 SHALL run for EVERY spec's file, and step 4 SHALL run
once via a single `virsh domblklist` call covering all disks of the batch. Validation
SHALL have batch semantics: if ANY disk's file fails any check, the entire batch is
considered failed — Core SHALL record NONE of the batch's snapshots in state, and the
provider SHALL best-effort remove all batch files. Partial recording of a batch is
forbidden.

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

#### Scenario: Batch — one file fails validation, whole batch rejected

- **WHEN** `create_multi` is called for disks `vda` and `vdb`
- **AND** virsh returns exit code 0
- **AND** `vda`'s file passes all checks but `vdb`'s `backing-filename` check fails
- **THEN** the `SnapshotResult` for `vdb` has `success=False`
- **AND** Core records NEITHER snapshot in state
- **AND** the provider best-effort removes both batch files

#### Scenario: Batch — all files valid, all recorded

- **WHEN** `create_multi` is called for disks `vda` and `vdb`
- **AND** every file passes steps 1–3 and the single domblklist check confirms both pivots
- **THEN** both `SnapshotResult` entries have `success=True`
- **AND** Core records both snapshots in state with their disks
