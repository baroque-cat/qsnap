## MODIFIED Requirements

### Requirement: verify_backup supports verify="hash" mode

`verify_backup(shell, source_path, target_path, verify_mode, expected_hash=None)` SHALL accept `verify_mode="hash"`. When `expected_hash` is provided and non-None, it SHALL compute the SHA-256 of the target file via `_file_sha256()` and compare to `expected_hash`. A mismatch SHALL return `"verification failed: hash mismatch"`. When `expected_hash` is `None`, verification SHALL be skipped (return `None`). Existing behavior for `"metadata"`, `"full"`, and `"off"` SHALL remain unchanged.

#### Scenario: Hash match passes
- **WHEN** `verify_mode="hash"`, `expected_hash="abc123"`, and `_file_sha256(target)` returns `"abc123"`
- **THEN** function returns `None`

#### Scenario: Hash mismatch fails
- **WHEN** `verify_mode="hash"`, `expected_hash="abc123"`, and `_file_sha256(target)` returns `"def456"`
- **THEN** function returns `"verification failed: hash mismatch"`

#### Scenario: Metadata mode unchanged
- **WHEN** `verify_mode="metadata"` with valid files
- **THEN** function returns `None` (existing behavior preserved)
