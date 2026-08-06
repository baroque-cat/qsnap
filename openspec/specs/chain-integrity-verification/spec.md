# Chain Integrity Verification

## Purpose

Ensures backing chain integrity per disk before and after blockcommit operations by verifying chain consistency via `scan_backing_chain()` in `qsnap/utils/verification.py`. Prevents data loss by skipping or flagging commits when the chain is broken, cyclic, or contains unexpected formats.
## Requirements
### Requirement: Per-disk pre-commit backing chain integrity verification

Before executing blockcommit for a disk, Core SHALL verify the backing chain integrity of that disk's active image by calling `scan_backing_chain(self._shell, active_path)` from `qsnap/utils/verification.py`. The entry path SHALL be the most recent snapshot of that disk (or the disk's base image if no snapshots exist). The `--force-share` flag is REQUIRED internally by `scan_backing_chain()` because the active disk image is locked by the running VM. The function SHALL confirm: (a) every file referenced in the chain exists on the filesystem, (b) every file has format `"qcow2"`, (c) the backing-filename reference in each image matches the actual next file in the chain, (d) no file appears twice (no cycles). If the chain is broken, Core SHALL abort the VM pipeline (CRITICAL log + `RuntimeError`; see `blockcommit-recovery` capability) — no blockcommit is executed and no automatic recovery is attempted. `Core._verify_backing_chain(vm_config, disk)` SHALL call `scan_backing_chain()` and convert `ChainScanResult` → `ChainVerifyResult`, mapping `broken_files[0]` to `broken_file` when non-empty, and setting `ChainVerifyResult.disk` to the disk name.

#### Scenario: Intact chain — blockcommit proceeds

- **WHEN** the backing chain for disk `vda` has 5 files, all exist, all are qcow2, and references are consistent
- **THEN** `scan_backing_chain()` returns `ChainScanResult(success=True, broken_files=[])`
- **AND** `_verify_backing_chain()` returns `ChainVerifyResult(success=True, broken_file=None, disk="vda")`
- **AND** blockcommit executes normally for that disk

#### Scenario: Missing file in chain — VM pipeline aborts

- **WHEN** one file in the backing chain for disk `vda` does not exist on disk
- **THEN** `scan_backing_chain()` sets `broken_files` containing the missing path
- **AND** `_verify_backing_chain()` returns `ChainVerifyResult(success=False, broken_file=<missing path>, disk="vda")`
- **AND** Core emits a CRITICAL log with the broken file path and raises `RuntimeError`, aborting the VM pipeline

#### Scenario: Non-qcow2 file in chain — VM pipeline aborts

- **WHEN** `qemu-img info` reports a file with `format: "raw"` in the chain for disk `vda`
- **THEN** `scan_backing_chain()` adds the file to `broken_files`
- **AND** a CRITICAL log is emitted with the file path and its unexpected format
- **AND** `RuntimeError` is raised, aborting the VM pipeline (no blockcommit is executed)

#### Scenario: Cyclic reference detected — VM pipeline aborts

- **WHEN** the chain refers to a file path already seen earlier in the chain for a disk
- **THEN** `scan_backing_chain()` adds "cycle detected at {path}" to `broken_files`
- **AND** a CRITICAL log is emitted
- **AND** `RuntimeError` is raised, aborting the VM pipeline (no blockcommit is executed)

#### Scenario: Entry path falls back to base image when no snapshots exist

- **WHEN** `_verify_backing_chain(vm_config, "vda")` is called and `IStateManager` has no snapshots for disk `vda`
- **THEN** the entry path SHALL be `vm_config.get_disk("vda").base_image`
- **AND** `scan_backing_chain()` is called with that base image path

### Requirement: Post-cleanup chain integrity verification

After `Core._cleanup_backups()` completes, Core SHALL verify that all keep-set items with backing chains have intact chains. For each non-FULL backup in the keep-set, Core SHALL run `qemu-img info --force-share --backing-chain --output=json` via `IShell` (or delegate to `scan_backing_chain()` via `_verify_keep_set_chains()`). If the command fails, a CRITICAL log SHALL be emitted with the backup name and guidance to run `qsnap check --deep`. This verification SHALL run only when `chain_verify_before_commit` is `True` (reusing the existing config flag).

#### Scenario: All keep-set chains intact after cleanup

- **WHEN** cleanup deletes Chain A and keeps Chain B (FULL + inc1)
- **THEN** post-cleanup verification runs `scan_backing_chain()` on inc1
- **AND** the command succeeds (chain intact)
- **AND** no CRITICAL log is emitted

#### Scenario: Post-cleanup detects broken chain

- **WHEN** cleanup completes and a keep-set incremental has a broken backing chain
- **THEN** a CRITICAL log is emitted: "post-cleanup verification FAILED for {name}"
- **AND** the log includes guidance to run `qsnap check --deep`

### Requirement: Per-disk post-commit chain length verification

After a blockcommit for a disk, Core SHALL re-run `qemu-img info --force-share --backing-chain --output=json` on the current active layer of that disk (the most recent snapshot that survived the blockcommit for that disk, obtained from `IStateManager` after removing merged snapshots). The `--force-share` flag is used because the active layer may still be locked by QEMU.

The chain length after commit SHALL be directionally compared to the chain length before commit for that disk: if `chain_length_after >= chain_length_before` (the chain was not reduced), a CRITICAL log SHALL be emitted and `RuntimeError` SHALL be raised, aborting the VM pipeline — an unchanged chain length means the commit did not take effect and the chain is potentially damaged. Any actual reduction is accepted — this correctly handles both normal merging and intermediate file removal by `virsh blockcommit --delete`.

The post-commit query SHALL use `_get_chain_length(vm_config, disk)` — the same per-disk method as the pre-commit query.

#### Scenario: Chain shortened as expected

- **WHEN** chain for disk `vda` had 7 files before commit and 1 snapshot was merged
- **AND** `qemu-img info --backing-chain` on the current active layer after commit shows 6 files
- **THEN** verification passes silently for that disk

#### Scenario: Chain shortened with intermediate file removal

- **WHEN** chain for disk `vda` had 7 files before commit and 1 snapshot was merged
- **AND** `virsh blockcommit --delete` also removed 3 intermediate files between the merged snapshot and the base
- **AND** `qemu-img info --backing-chain` on the current active layer after commit shows 3 files
- **THEN** verification passes (the actual reduction is accepted — `virsh --delete` semantics are respected)

#### Scenario: Chain length unchanged — CRITICAL and VM aborts

- **WHEN** chain for disk `vda` had 7 files before commit and 1 snapshot should have been merged
- **AND** `qemu-img info --backing-chain` on the current active layer after commit still shows 7 files
- **THEN** a CRITICAL log is emitted: "Blockcommit may have failed: chain length unchanged"
- **AND** the snapshot file paths are included in the log for manual recovery
- **AND** `RuntimeError` is raised, aborting the remaining steps of this VM

#### Scenario: Post-commit measurement fails — snapshots preserved

- **WHEN** `qemu-img info --backing-chain` on the current active layer fails after a successful blockcommit for a disk
- **THEN** `chain_length_after` is `None`
- **AND** verification is skipped with a WARNING log
- **AND** snapshot removal from `IStateManager` still proceeds (blockcommit itself succeeded)

#### Scenario: Pre-commit chain length unavailable — skip post-commit

- **WHEN** `chain_length_before` is `None` for a disk (measurement failed before blockcommit)
- **THEN** post-commit chain length comparison is skipped for that disk
- **AND** an INFO log is emitted

### Requirement: GlobalConfig chain verification fields

`GlobalConfig` SHALL include `chain_verify_before_commit: bool` and `chain_verify_after_commit: bool` fields, both defaulting to `True`.

#### Scenario: Chain verification enabled by default

- **WHEN** `GlobalConfig` is constructed without these fields
- **THEN** both `chain_verify_before_commit` and `chain_verify_after_commit` are `True`

#### Scenario: Chain verification disabled

- **WHEN** `chain_verify_before_commit = false`
- **THEN** no pre-commit verification is performed for any disk before blockcommit
- **AND** an INFO log states "chain_verify_before_commit is disabled — skipping pre-commit chain check for VM <vm> disk <disk>"

### Requirement: --force-share on check_integrity qemu-img info

`Core.check()` SHALL use `--force-share` on all `qemu-img info` and `qemu-img info --backing-chain` calls that may target active-layer snapshots. `Core.check()` SHALL delegate backing chain scanning to `scan_backing_chain()` (in `qsnap/utils/verification.py`) which parses the JSON output and verifies: (a) every file in the chain exists, (b) every file has format `"qcow2"`, (c) `backing-filename` references are consistent, (d) no cycles.

#### Scenario: check uses --force-share on active layer

- **WHEN** `Core.check()` iterates over disks and the most recent snapshot for a disk is the active layer
- **THEN** `scan_backing_chain()` uses `qemu-img info --force-share --backing-chain` internally
- **AND** the command succeeds despite the VM holding a write lock

#### Scenario: check parses JSON and detects inconsistent backing-filename

- **WHEN** `scan_backing_chain()` runs and the JSON output shows a `backing-filename` that does not match the next file in the chain
- **THEN** the inconsistency is reported in `ChainScanResult.broken_files`
- **AND** `CheckResult` reports the issue

#### Scenario: check parses JSON and detects cycle

- **WHEN** `scan_backing_chain()` runs and the JSON output shows a file path appearing twice in the chain
- **THEN** "cycle detected" is reported in `ChainScanResult.broken_files`
- **AND** `CheckResult` reports the cycle

### Requirement: --force-share on _deep_check_file qemu-img check

`Core._deep_check_file()` SHALL use `--force-share` on `qemu-img check` when the file being checked may be the active layer. `qemu-img check` is a metadata-only operation (reads headers and refcount tables) and is safe with `--force-share`. The method SHALL check `corruptions`, `errors`, AND `leaks` fields (not just `corruptions`). The timeout SHALL be 7200 seconds (was 60 seconds).

#### Scenario: Deep check on active layer uses --force-share

- **WHEN** `Core._deep_check_file()` is called on a snapshot that is the active layer
- **THEN** `qemu-img check --force-share --output=json` is used
- **AND** the command succeeds despite the VM holding a write lock

#### Scenario: Deep check detects errors (not just corruptions)

- **WHEN** `qemu-img check` reports `errors: 2` (but `corruptions: 0`)
- **THEN** the file is reported as "warning" status
- **AND** the file name is added to the broken list

#### Scenario: Deep check detects leaks

- **WHEN** `qemu-img check` reports `leaks: 5` (but `corruptions: 0` and `errors: 0`)
- **THEN** the file is reported as "warning" status
- **AND** the file name is added to the broken list

#### Scenario: Deep check timeout is 7200 seconds

- **WHEN** `Core._deep_check_file()` runs `qemu-img check`
- **THEN** the timeout parameter is 7200 seconds
- **AND** large disks are not prematurely killed

### Requirement: File existence guard before blockcommit (stale state self-healing)

Before passing `to_merge` to the lifecycle manager, `Core._blockcommit_snapshots()` SHALL iterate entries and verify each snapshot file exists on disk via `os.path.exists()`. For entries where the file does not exist: remove the entry from state via `remove_snapshot()`, log WARNING, and remove from `to_merge`. If `to_merge` becomes empty after filtering, skip the blockcommit step entirely.

#### Scenario: Stale snapshot file removed before blockcommit

- **WHEN** a snapshot in `to_merge` no longer exists on disk
- **THEN** it is removed from state and from `to_merge` with a WARNING
- **AND** blockcommit proceeds with the remaining entries

#### Scenario: All entries stale — blockcommit skipped

- **WHEN** every snapshot in `to_merge` is missing on disk
- **THEN** `to_merge` becomes empty and the blockcommit step is skipped

### Requirement: Triple-source check uses scan_backing_chain per disk

`Core._check_snapshot_chain()` SHALL iterate all configured disks, call `_detect_active_layer_path(vm, disk.target)` for each, then call `scan_backing_chain(self._shell, Path(active_layer))`. It SHALL use `ChainScanResult.paths` as the return value (union across all disks) and `ChainScanResult.broken_files` to populate the `broken: list[str]` side-effect parameter. When the scan command itself fails (`success is False`), the active layer name SHALL be appended to `broken`.

#### Scenario: _check_snapshot_chain iterates per-disk

- **WHEN** `_check_snapshot_chain(vm, broken)` is called for a VM with disks vda and vdb
- **THEN** it calls `scan_backing_chain()` once per disk
- **AND** returns the union of all `ChainScanResult.paths`
- **AND** appends all `broken_files` items to the `broken` list

### Requirement: Target consistency check uses scan_backing_chain

`Core._check_target_consistency()` SHALL call `scan_backing_chain()` for the last incremental in each chain instead of its own inline `qemu-img info --backing-chain` call. It SHALL check `ChainScanResult.success` and log errors accordingly.

#### Scenario: _check_target_consistency delegates to scan_backing_chain

- **WHEN** `_check_target_consistency(vm, target)` is called for a chain with incrementals
- **THEN** it calls `scan_backing_chain(self._shell, last_incremental.path)`
- **AND** if `result.success is False`, logs the error
- **AND** if `result.broken_files` is non-empty, reports broken files

### Requirement: Post-cleanup verification uses scan_backing_chain

`Core._cleanup_backups()` post-cleanup verification SHALL call `scan_backing_chain()` instead of its own inline `qemu-img info --backing-chain` call. The `_verify_keep_set_chains()` method SHALL use `scan_backing_chain()`.

#### Scenario: Post-cleanup uses scan_backing_chain

- **WHEN** `_cleanup_backups()` completes and keep-set includes incrementals
- **THEN** `_verify_keep_set_chains()` calls `scan_backing_chain(self._shell, backup.path)` for each incremental
- **AND** if `result.success is False` or `result.broken_files` is non-empty, a CRITICAL log is emitted

### Requirement: ChainVerifyResult has optional disk field

`ChainVerifyResult` SHALL include an optional `disk: str | None` field (defaulting to `None`). When `_verify_backing_chain(vm_config, disk)` is called, the returned `ChainVerifyResult` SHALL have `disk` set to the disk name. When `_verify_backing_chain` is called without a disk context, `disk` SHALL be `None`.

#### Scenario: ChainVerifyResult includes disk when available

- **WHEN** `_verify_backing_chain(vm_config, "vda")` is called and the chain is intact
- **THEN** the returned `ChainVerifyResult` has `disk="vda"`

#### Scenario: ChainVerifyResult disk is None without disk context

- **WHEN** `ChainVerifyResult` is constructed without a `disk` argument
- **THEN** `disk` is `None`

