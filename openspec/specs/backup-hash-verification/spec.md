# Backup Hash Verification

## Purpose

SHA-256 hash verification tier for backup integrity checks. At snapshot creation time, the SHA-256 digest of the qcow2 file is computed and stored. After transfer to the backup target, the target file's hash is compared to the stored value to detect silent corruption or transfer errors. This sits between `"metadata"` (fast, structure-only) and `"full"` (slow, `qemu-img compare`) verification tiers.

## Requirements

### Requirement: SnapshotResult carries content_hash

`SnapshotResult` SHALL have an optional `content_hash: str | None` field defaulting to `None`. When non-None, it SHALL contain the hex-encoded SHA-256 digest of the created snapshot file.

#### Scenario: Hash present for newly created snapshot
- **WHEN** `ExternalSnapshotProvider.create()` creates a snapshot successfully
- **THEN** the returned `SnapshotResult.content_hash` SHALL be a 64-character hex string

#### Scenario: Hash is None on creation failure
- **WHEN** snapshot creation fails at any step (virsh, chmod, qemu-img info)
- **THEN** `SnapshotResult.content_hash` SHALL be `None`

### Requirement: SnapshotInfo stores content_hash in persistent state

`SnapshotInfo` SHALL have an optional `content_hash: str | None` field defaulting to `None`. `JsonStateManager` SHALL persist and restore this field in the per-VM state JSON.

#### Scenario: Hash stored and restored from state
- **WHEN** a `SnapshotInfo` with `content_hash="abc123..."` is recorded via `IStateManager.record_snapshot()`
- **THEN** subsequent `IStateManager.get_snapshots()` SHALL return a `SnapshotInfo` with the same `content_hash`

### Requirement: _file_sha256 computes binary hash efficiently

A module-level function `_file_sha256(path)` SHALL read the file in 8MB chunks and return the hex-encoded SHA-256 digest.

#### Scenario: Hash computed for a file
- **WHEN** `_file_sha256("/tmp/test.qcow2")` is called
- **THEN** it SHALL return a 64-character hex string
