# chain-integrity-verification — Delta Spec

## MODIFIED Requirements

### Requirement: Pre-commit backing chain integrity verification

Before executing any blockcommit, Core SHALL verify the backing chain integrity of the active disk image by calling `scan_backing_chain(self._shell, active_path)` — a shared function in `qsnap/utils/verification.py`. The `--force-share` flag is REQUIRED internally by `scan_backing_chain()` because the active disk image is locked by the running VM. The function SHALL confirm: (a) every file referenced in the chain exists on the filesystem, (b) every file has format `"qcow2"`, (c) the backing-filename reference in each image matches the actual next file in the chain, (d) no file appears twice (no cycles). If the chain is broken, Core SHALL attempt partial blockcommit (see `blockcommit-recovery` capability) instead of skipping entirely. `Core._verify_backing_chain()` SHALL call `scan_backing_chain()` and convert `ChainScanResult` → `ChainVerifyResult`, mapping `broken_files[0]` to `broken_file` when non-empty.

#### Scenario: Intact chain — blockcommit proceeds

- **WHEN** the backing chain has 5 files, all exist, all are qcow2, and references are consistent
- **THEN** `scan_backing_chain()` returns `ChainScanResult(success=True, broken_files=[])`
- **AND** `_verify_backing_chain()` returns `ChainVerifyResult(success=True, broken_file=None)`
- **AND** blockcommit executes normally

#### Scenario: Missing file in chain — partial blockcommit attempted

- **WHEN** one file in the backing chain does not exist on disk
- **THEN** `scan_backing_chain()` sets `broken_files` containing the missing path
- **AND** `_verify_backing_chain()` returns `ChainVerifyResult(success=False, broken_file=<missing path>)`
- **AND** Core attempts partial blockcommit for snapshots before the break point

#### Scenario: Non-qcow2 file in chain — blockcommit skipped

- **WHEN** `qemu-img info` reports a file with `format: "raw"` in the chain
- **THEN** `scan_backing_chain()` adds the file to `broken_files`
- **AND** a CRITICAL log is emitted with the file path and its unexpected format
- **AND** blockcommit is NOT executed

#### Scenario: Cyclic reference detected — blockcommit skipped

- **WHEN** the chain refers to a file path already seen earlier in the chain
- **THEN** `scan_backing_chain()` adds "cycle detected at {path}" to `broken_files`
- **AND** a CRITICAL log is emitted
- **AND** blockcommit is NOT executed

### Requirement: Triple-source check uses scan_backing_chain

`Core._check_snapshot_chain()` SHALL call `scan_backing_chain()` instead of its own inline JSON parsing. It SHALL use `ChainScanResult.paths` as the return value and `ChainScanResult.broken_files` to populate the `broken: list[str]` side-effect parameter.

#### Scenario: _check_snapshot_chain delegates to scan_backing_chain

- **WHEN** `_check_snapshot_chain(vm, broken)` is called
- **THEN** it calls `scan_backing_chain(self._shell, active_layer)`
- **AND** returns `result.paths`
- **AND** appends `result.broken_files` items to the `broken` list

### Requirement: Target consistency check uses scan_backing_chain

`Core._check_target_consistency()` SHALL call `scan_backing_chain()` for the last incremental in each chain instead of its own inline `qemu-img info --backing-chain` call. It SHALL check `ChainScanResult.success` and log errors accordingly.

#### Scenario: _check_target_consistency delegates to scan_backing_chain

- **WHEN** `_check_target_consistency(vm, target)` is called for a chain with incrementals
- **THEN** it calls `scan_backing_chain(self._shell, last_incremental.path)`
- **AND** if `result.success is False`, logs the error
- **AND** if `result.broken_files` is non-empty, reports broken files

### Requirement: Post-cleanup verification uses scan_backing_chain

`Core._cleanup_backups()` post-cleanup verification SHALL call `scan_backing_chain()` instead of its own inline `qemu-img info --backing-chain` call. The existing `_verify_keep_set_chains()` method (extracted from the duplicated logic in this change) SHALL use `scan_backing_chain()`.

#### Scenario: Post-cleanup uses scan_backing_chain

- **WHEN** `_cleanup_backups()` completes and keep-set includes incrementals
- **THEN** `_verify_keep_set_chains()` calls `scan_backing_chain(self._shell, backup.path)` for each incremental
- **AND** if `result.success is False` or `result.broken_files` is non-empty, a CRITICAL log is emitted
