## MODIFIED Requirements

### Requirement: Pre-commit backing chain integrity verification

Before executing any blockcommit, Core SHALL verify the backing chain integrity of the active disk image via `qemu-img info --force-share --backing-chain --output=json`. The `--force-share` flag is REQUIRED because the active disk image is locked by the running VM. The verification SHALL confirm: (a) every file referenced in the chain exists on the filesystem, (b) every file has format `"qcow2"`, (c) the backing-filename reference in each image matches the actual next file in the chain, (d) no file appears twice (no cycles). If the chain is broken, the blockcommit SHALL be skipped and a CRITICAL log emitted with remediation guidance.

#### Scenario: Intact chain — blockcommit proceeds
- **WHEN** the backing chain has 5 files, all exist, all are qcow2, and references are consistent
- **THEN** the verification passes and blockcommit executes normally

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

### Requirement: Post-commit chain length verification

After a blockcommit, Core SHALL re-run `qemu-img info --force-share --backing-chain --output=json` on the base image and compare the chain length before and after the commit. The `--force-share` flag is used because the base image may still be locked by QEMU as a backing file. The chain length after commit SHALL be strictly less than before commit (minus the number of snapshots merged).

#### Scenario: Chain shortened as expected
- **WHEN** chain had 6 files before commit and 2 snapshots were merged
- **AND** `qemu-img info --force-share --backing-chain` after commit shows 4 files
- **THEN** verification passes silently

#### Scenario: Chain length unchanged — CRITICAL
- **WHEN** chain had 6 files before commit and 2 snapshots should have been merged
- **AND** `qemu-img info --force-share --backing-chain` after commit still shows 6 files
- **THEN** a CRITICAL log is emitted: "Blockcommit may have failed: chain length unchanged"
- **AND** the snapshot file paths are included in the log for manual recovery

#### Scenario: Post-commit verification fails — snapshots preserved
- **WHEN** post-commit verification detects chain length unchanged
- **THEN** the snapshot removal from `IStateManager` is NOT performed (snapshots remain in state for manual investigation)

## ADDED Requirements

### Requirement: --force-share on check_integrity qemu-img info

`Core.check_integrity()` SHALL use `--force-share` on all `qemu-img info` and `qemu-img info --backing-chain` calls that may target active-layer snapshots. This includes the iteration over snapshots from `IStateManager.get_snapshots()` where the most recent snapshot IS the active layer.

#### Scenario: check_integrity uses --force-share on active layer
- **WHEN** `Core.check_integrity()` iterates over snapshots and the most recent is the active layer
- **THEN** `qemu-img info --force-share --backing-chain` is used for that snapshot
- **AND** the command succeeds despite the VM holding a write lock

### Requirement: --force-share on _deep_check_file qemu-img check

`Core._deep_check_file()` SHALL use `--force-share` on `qemu-img check` when the file being checked may be the active layer. `qemu-img check` is a metadata-only operation (reads headers and refcount tables) and is safe with `--force-share`.

#### Scenario: Deep check on active layer uses --force-share
- **WHEN** `Core._deep_check_file()` is called on a snapshot that is the active layer
- **THEN** `qemu-img check --force-share --output=json` is used
- **AND** the command succeeds despite the VM holding a write lock
