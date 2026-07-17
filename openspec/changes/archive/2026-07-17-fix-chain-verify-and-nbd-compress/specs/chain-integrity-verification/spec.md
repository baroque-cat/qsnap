## MODIFIED Requirements

### Requirement: Pre-commit backing chain integrity verification
Before executing any blockcommit, Core SHALL verify the backing chain integrity of the active disk image via `qemu-img info --force-share --backing-chain --output=json`. The `--force-share` flag is REQUIRED because the active disk image is locked by the running VM. The verification SHALL confirm: (a) every file referenced in the chain exists on the filesystem, (b) every file has format `"qcow2"`, (c) the backing-filename reference in each image matches the actual next file in the chain, (d) no file appears twice (no cycles). If the chain is broken, the blockcommit SHALL be skipped and a CRITICAL log emitted with remediation guidance.

The JSON parsing SHALL accept both `"image"` (legacy QEMU, e.g. QEMU < 11.0) and `"filename"` (QEMU 11.0+) as the key for the disk image file path in each chain entry. The parser SHALL try `"image"` first, and fall back to `"filename"` if `"image"` is absent or falsy. The `"children"` nested array (added in QEMU 11.0+) SHALL be ignored — only the top-level fields (`"image"`/`"filename"`, `"format"`, `"backing-filename"`) are used.

#### Scenario: Intact chain — blockcommit proceeds
- **WHEN** the backing chain has 5 files, all exist, all are qcow2, and references are consistent
- **THEN** the verification passes and blockcommit executes normally

#### Scenario: Intact chain with new QEMU format — blockcommit proceeds
- **WHEN** the backing chain has 5 files and `qemu-img info` output uses `"filename"` keys with nested `"children"` arrays (QEMU 11.0+ format)
- **AND** all files exist, all are qcow2, and references are consistent
- **THEN** the verification parses the chain correctly and passes
- **AND** blockcommit executes normally

#### Scenario: Missing file in chain — blockcommit skipped
- **WHEN** one file in the backing chain does not exist on disk
- **THEN** verification returns failure with the missing file path
- **AND** a CRITICAL log is emitted: "Backing chain broken: missing file /path/to/snap.qcow2"
- **AND** blockcommit is NOT executed for this VM

#### Scenario: Non-qcow2 file in chain — blockcommit skipped
- **WHEN** `qemu-img info` reports a file with `format: "raw"` in the chain
- **THEN** verification returns failure
- **AND** a CRITICAL log is emitted with the file path and its unexpected format
- **AND** blockcommit is NOT executed

#### Scenario: Cyclic reference detected — blockcommit skipped
- **WHEN** the chain refers to a file path already seen earlier in the chain
- **THEN** verification returns failure
- **AND** a CRITICAL log is emitted: "Backing Chain contains a cycle at /path/to/file.qcow2"
- **AND** blockcommit is NOT executed

#### Scenario: Broken chain does NOT defer the operation
- **WHEN** chain verification fails
- **THEN** the blockcommit operation is NOT added to deferred operations
- **AND** the CRITICAL message includes guidance: "Check file existence, run qemu-img check, or restore from backup"
