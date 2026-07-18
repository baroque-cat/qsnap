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

### Requirement: verify_backup supports verify="hash" mode

`verify_backup(shell, source_path, target_path, verify_mode, expected_hash=None)` SHALL accept `verify_mode="hash"`. When `expected_hash` is provided and non-None, it SHALL compute the SHA-256 of the target file and compare. Hash verification is the recommended default for file-copy (rsync) mode because the hash is computed at snapshot creation time and is immune to race conditions. Hash verification is NOT supported in bitmap (NBD) mode because NBD-converted qcow2 files have different internal structure than the source snapshot — their SHA-256 digests will never match. Users of bitmap mode SHALL use `verify="metadata"` or `verify="full"` instead.

#### Scenario: Hash match passes verification
- **WHEN** `verify_mode="hash"`, `expected_hash="abc123"`, and the target file's SHA-256 is `"abc123"`
- **THEN** the function SHALL return `None` (success)

#### Scenario: Hash mismatch fails verification
- **WHEN** `verify_mode="hash"`, `expected_hash="abc123"`, and the target file's SHA-256 is `"def456"`
- **THEN** the function SHALL return `"verification failed: hash mismatch"`

#### Scenario: Hash verification skipped when no expected hash
- **WHEN** `verify_mode="hash"` and `expected_hash` is `None`
- **THEN** the function SHALL return `None` (skip verification, no failure)

#### Scenario: Hash is default for file-copy mode
- **WHEN** a `TargetConfig` is created with `incremental_mode="file-copy"` without explicit `verify`
- **THEN** `target.verify` SHALL be `"hash"`

#### Scenario: Metadata is default for bitmap mode
- **WHEN** a `TargetConfig` is created with `incremental_mode="bitmap"` without explicit `verify`
- **THEN** `target.verify` SHALL be `"metadata"`

#### Scenario: Explicit verify overrides mode-dependent default
- **WHEN** a `TargetConfig` is created with `incremental_mode="file-copy"` and `verify="metadata"`
- **THEN** `target.verify` SHALL be `"metadata"` (explicit value takes precedence)

#### Scenario: Bitmap mode with verify="hash" warns and downgrades
- **WHEN** `ConfigFacade._build_target()` processes a target with `incremental_mode="bitmap"` and `verify="hash"` explicitly set
- **THEN** a WARNING SHALL be logged: "verify='hash' is not supported in bitmap mode (NBD-converted qcow2 has different internal structure). Downgrading to verify='metadata'. Use verify='full' for content-level verification."
- **AND** the resulting `TargetConfig.verify` SHALL be `"metadata"` (auto-downgraded from `"hash"`)
- **AND** the `incremental_mode` SHALL remain `"bitmap"` (unchanged)

### Requirement: _file_sha256 computes binary hash efficiently

A module-level function `_file_sha256(path)` SHALL read the file in 8MB chunks and return the hex-encoded SHA-256 digest.

#### Scenario: Hash computed for a file
- **WHEN** `_file_sha256("/tmp/test.qcow2")` is called
- **THEN** it SHALL return a 64-character hex string
