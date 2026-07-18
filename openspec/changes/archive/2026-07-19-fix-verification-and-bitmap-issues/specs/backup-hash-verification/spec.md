## MODIFIED Requirements

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
