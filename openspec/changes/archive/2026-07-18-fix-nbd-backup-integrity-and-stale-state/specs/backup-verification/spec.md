## ADDED Requirements

### Requirement: verify_full_backup function for standalone FULL verification

`qsnap/modules/backup/verification.py` SHALL provide a `verify_full_backup(shell: IShell, target_path: Path, verify_mode: str, source_path: Path | None = None, expected_virtual_size: int | None = None) -> str | None` function. Unlike `verify_backup()` (which compares source and target), this function verifies a standalone FULL backup file.

Supported `verify_mode` values:
- `"metadata"` (M1): Run `qemu-img info --output=json`, verify format is `"qcow2"`, and check that `incompatible-features` does NOT contain bit with name `"corrupt"`. If `expected_virtual_size` is provided, verify virtual-size matches.
- `"check"` (M2): Run M1, then additionally run `qemu-img check --output=json` and verify both `errors` and `leaks` are 0.
- `"hash"` (M3): Run M1 + M2, then run `qemu-img compare -q --force-share <source_path> <target_path>` to perform byte-level content comparison between the source snapshot and the FULL backup. When `source_path` is None, M3 is skipped. NOTE: This replaces the previous SHA-256 hash comparison which was broken because SHA-256 of a snapshot delta file (with backing chain) will never match SHA-256 of a standalone NBD-converted FULL file.
- `"off"`: Skip all verification, return `None`.

The function SHALL return `None` on success (all enabled checks pass) or an error string describing the failure.

#### Scenario: Metadata mode — valid qcow2 with no corrupt bit
- **WHEN** `verify_full_backup(shell, path, "metadata")` is called
- **AND** `qemu-img info` returns format="qcow2" with no "corrupt" feature
- **THEN** the function returns `None`

#### Scenario: Metadata mode — corrupt bit detected
- **WHEN** `verify_full_backup(shell, path, "metadata")` is called
- **AND** `qemu-img info` shows `incompatible-features: [{name: "corrupt"}]`
- **THEN** the function returns `"verification failed: FULL backup has corrupt bit set — file is damaged"`

#### Scenario: Metadata mode — wrong format
- **WHEN** `verify_full_backup(shell, path, "metadata")` is called
- **AND** `qemu-img info` returns format="raw"
- **THEN** the function returns `"verification failed: expected format qcow2, got raw"`

#### Scenario: Metadata mode — qemu-img info fails entirely
- **WHEN** `verify_full_backup(shell, path, "metadata")` is called
- **AND** `qemu-img info` returns non-zero exit code
- **THEN** the function returns `"verification failed: qemu-img info returned <stderr>"`

#### Scenario: Check mode — passes after M1 and M2
- **WHEN** `verify_full_backup(shell, path, "check")` is called
- **AND** M1 passes
- **AND** `qemu-img check --output=json` returns `{errors: 0, leaks: 0}`
- **THEN** the function returns `None`

#### Scenario: Check mode — M2 detects errors
- **WHEN** `verify_full_backup(shell, path, "check")` is called
- **AND** M1 passes
- **AND** `qemu-img check --output=json` returns `{errors: 5}`
- **THEN** the function returns `"verification failed: qemu-img check found 5 errors"`

#### Scenario: Hash mode — content comparison matches
- **WHEN** `verify_full_backup(shell, target_path, "hash", source_path=snap_path)` is called
- **AND** M1 and M2 pass
- **AND** `qemu-img compare -q --force-share <snap_path> <target_path>` returns exit code 0
- **THEN** the function returns `None`

#### Scenario: Hash mode — content comparison mismatch
- **WHEN** `verify_full_backup(shell, target_path, "hash", source_path=snap_path)` is called
- **AND** M1 and M2 pass
- **AND** `qemu-img compare -q --force-share <snap_path> <target_path>` returns non-zero
- **THEN** the function returns `"verification failed: content comparison mismatch"`

#### Scenario: Hash mode with no source_path — skips M3
- **WHEN** `verify_full_backup(shell, path, "hash", source_path=None)` is called
- **AND** M1 and M2 pass
- **THEN** the function returns `None` (M3 skipped, no source to compare)

#### Scenario: Off mode — no verification
- **WHEN** `verify_full_backup(shell, path, "off")` is called
- **THEN** no qemu-img commands are executed
- **AND** the function returns `None`

#### Scenario: Content comparison consumed at post-create verification
- **WHEN** `create_full_backup()` completes and `full_verify_after_create = "hash"`
- **AND** the source snapshot exists at `most_recent.path`
- **THEN** `verify_full_backup()` is called with `verify_mode="hash"` and `source_path=most_recent.path`
- **AND** `qemu-img compare` compares the source snapshot content to the FULL backup content
