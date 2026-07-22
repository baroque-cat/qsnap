## MODIFIED Requirements

### Requirement: ExternalSnapshotProvider.create() return value

`ExternalSnapshotProvider.create()` SHALL return a `SnapshotResult` with `success`, `name`, `path`, `new_allocation`, and `error` fields. The method SHALL NOT compute a `content_hash` — the SHA-256 hash of the raw qcow2 file is semantically incorrect for NBD-created backups and has no consumers. The `content_hash` field is removed from `SnapshotResult`.

#### Scenario: Successful snapshot creation

- **WHEN** `virsh snapshot-create-as` succeeds
- **THEN** `SnapshotResult(success=True, name=..., path=..., new_allocation=..., error=None)` is returned
- **AND** no SHA-256 hash is computed

#### Scenario: Snapshot creation fails

- **WHEN** `virsh snapshot-create-as` fails
- **THEN** `SnapshotResult(success=False, name=..., path=..., new_allocation=0, error=...)` is returned
