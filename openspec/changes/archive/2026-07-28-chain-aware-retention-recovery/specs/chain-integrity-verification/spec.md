## MODIFIED Requirements

### Requirement: Pre-commit backing chain integrity verification

Before executing any blockcommit, Core SHALL verify the backing chain integrity of the active disk image via `qemu-img info --force-share --backing-chain --output=json`. The `--force-share` flag is REQUIRED because the active disk image is locked by the running VM. The verification SHALL confirm: (a) every file referenced in the chain exists on the filesystem, (b) every file has format `"qcow2"`, (c) the backing-filename reference in each image matches the actual next file in the chain, (d) no file appears twice (no cycles). If the chain is broken, Core SHALL attempt partial blockcommit (see `blockcommit-recovery` capability) instead of skipping entirely. The `ChainVerifyResult` SHALL include a `broken_file` field set to the path of the missing file when verification fails due to a missing file.

The JSON parsing SHALL accept both `"image"` (legacy QEMU, e.g. QEMU < 11.0) and `"filename"` (QEMU 11.0+) as the key for the disk image file path in each chain entry. The parser SHALL try `"image"` first, and fall back to `"filename"` if `"image"` is absent or falsy. The `"children"` nested array (added in QEMU 11.0+) SHALL be ignored — only the top-level fields (`"image"`/`"filename"`, `"format"`, `"backing-filename"`) are used.

#### Scenario: Intact chain — blockcommit proceeds

- **WHEN** the backing chain has 5 files, all exist, all are qcow2, and references are consistent
- **THEN** the verification passes and blockcommit executes normally

#### Scenario: Intact chain with new QEMU format — blockcommit proceeds

- **WHEN** the backing chain has 5 files and `qemu-img info` output uses `"filename"` keys with nested `"children"` arrays (QEMU 11.0+ format)
- **AND** all files exist, all are qcow2, and references are consistent
- **THEN** the verification parses the chain correctly and passes
- **AND** blockcommit executes normally

#### Scenario: Missing file in chain — partial blockcommit attempted

- **WHEN** one file in the backing chain does not exist on disk
- **THEN** verification returns `ChainVerifyResult(success=False, broken_file="/path/to/missing.qcow2")`
- **AND** Core attempts partial blockcommit for snapshots before the break point
- **AND** stuck snapshots after the break point are auto-rebased (see `blockcommit-recovery` capability)

#### Scenario: Non-qcow2 file in chain — blockcommit skipped

- **WHEN** `qemu-img info` reports a file with `format: "raw"` in the chain
- **THEN** verification returns `ChainVerifyResult(success=False, broken_file=None)`
- **AND** a CRITICAL log is emitted with the file path and its unexpected format
- **AND** blockcommit is NOT executed (no partial recovery for format errors)

#### Scenario: Cyclic reference detected — blockcommit skipped

- **WHEN** the chain refers to a file path already seen earlier in the chain
- **THEN** verification returns `ChainVerifyResult(success=False, broken_file=None)`
- **AND** a CRITICAL log is emitted: "Backing Chain contains a cycle at /path/to/file.qcow2"
- **AND** blockcommit is NOT executed

#### Scenario: Broken chain does NOT defer the operation

- **WHEN** chain verification fails
- **THEN** the blockcommit operation is NOT added to deferred operations
- **AND** partial blockcommit is attempted instead (see `blockcommit-recovery` capability)

## ADDED Requirements

### Requirement: Post-cleanup chain integrity verification

After `Core._cleanup_backups()` completes, Core SHALL verify that all keep-set items with backing chains have intact chains. For each non-FULL backup in the keep-set, Core SHALL run `qemu-img info --force-share --backing-chain --output=json` via `IShell`. If the command fails, a CRITICAL log SHALL be emitted with the backup name and guidance to run `qsnap check --deep`. This verification SHALL run only when `chain_verify_before_commit` is `True` (reusing the existing config flag).

#### Scenario: All keep-set chains intact after cleanup

- **WHEN** cleanup deletes Chain A and keeps Chain B (FULL + inc1)
- **THEN** post-cleanup verification runs `qemu-img info --backing-chain` on inc1
- **AND** the command succeeds (chain intact)
- **AND** no CRITICAL log is emitted

#### Scenario: Post-cleanup detects broken chain

- **WHEN** cleanup completes and a keep-set incremental has a broken backing chain
- **THEN** a CRITICAL log is emitted: "post-cleanup verification FAILED for {name}"
- **AND** the log includes guidance to run `qsnap check --deep`
