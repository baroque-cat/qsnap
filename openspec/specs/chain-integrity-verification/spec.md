## Purpose

Ensures backing chain integrity before and after blockcommit operations by verifying chain consistency via `qemu-img info --backing-chain`. Prevents data loss by skipping or flagging commits when the chain is broken, cyclic, or contains unexpected formats.

## Requirements

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
- **AND** partial blockcommit is attempted instead (see `blockcommit-recovery` capability)

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

### Requirement: Post-commit chain length verification
After a blockcommit, Core SHALL re-run `qemu-img info --force-share --backing-chain --output=json` on the **current active layer** (the most recent snapshot that survived the blockcommit, obtained from `IStateManager` after removing merged snapshots). The `--force-share` flag is used because the active layer may still be locked by QEMU.

The chain length after commit SHALL be directionally compared to the chain length before commit: if ``chain_length_after >= chain_length_before`` (the chain was not reduced), a CRITICAL log SHALL be emitted. Any actual reduction is accepted — this correctly handles both normal merging and intermediate file removal by ``virsh blockcommit --delete``.

The `use_base_image` parameter previously added to `Core._get_chain_length()` for this specific purpose SHALL be removed. The post-commit query SHALL use the same `_get_chain_length(vm_config)` method as the pre-commit query, relying on the updated state (merged snapshots already removed from `IStateManager`).

#### Scenario: Chain shortened as expected
- **WHEN** chain had 7 files before commit and 1 snapshot was merged
- **AND** the merged snapshot had no intermediate files between it and the base image
- **AND** `qemu-img info --backing-chain` on the current active layer after commit shows 6 files
- **THEN** verification passes silently

#### Scenario: Chain shortened with intermediate file removal
- **WHEN** chain had 7 files before commit and 1 snapshot was merged
- **AND** `virsh blockcommit --delete` also removed 3 intermediate files between the merged snapshot and the base
- **AND** `qemu-img info --backing-chain` on the current active layer after commit shows 3 files
- **THEN** verification passes (the actual reduction is accepted — `virsh --delete` semantics are respected)

#### Scenario: Chain length unchanged — CRITICAL
- **WHEN** chain had 7 files before commit and 1 snapshot should have been merged
- **AND** `qemu-img info --backing-chain` on the current active layer after commit still shows 7 files
- **THEN** a CRITICAL log is emitted: "Blockcommit may have failed: chain length unchanged"
- **AND** the snapshot file paths are included in the log for manual recovery

#### Scenario: Post-commit measurement fails — snapshots preserved
- **WHEN** `qemu-img info --backing-chain` on the current active layer fails after a successful blockcommit
- **THEN** `chain_length_after` is `None`
- **AND** verification is skipped with a WARNING log (the blockcommit succeeded but chain measurement failed)
- **AND** snapshot removal from `IStateManager` still proceeds (blockcommit itself succeeded)

#### Scenario: Pre-commit chain length unavailable — skip post-commit
- **WHEN** `chain_length_before` is `None` (measurement failed before blockcommit)
- **THEN** post-commit chain length comparison is skipped
- **AND** an INFO log is emitted

### Requirement: GlobalConfig chain verification fields
`GlobalConfig` SHALL include `chain_verify_before_commit: bool` and `chain_verify_after_commit: bool` fields, both defaulting to `True`.

#### Scenario: Chain verification enabled by default
- **WHEN** `GlobalConfig` is constructed without these fields
- **THEN** both `chain_verify_before_commit` and `chain_verify_after_commit` are `True`

#### Scenario: Chain verification disabled
- **WHEN** `chain_verify_before_commit = false`
- **THEN** no pre-commit verification is performed before blockcommit
- **AND** an INFO log states "chain_verify_before_commit is disabled — skipping pre-commit chain check"

### Requirement: --force-share on check_integrity qemu-img info

`Core.check()` SHALL use `--force-share` on all `qemu-img info` and `qemu-img info --backing-chain` calls that may target active-layer snapshots. This includes the iteration over snapshots from `IStateManager.get_snapshots()` where the most recent snapshot IS the active layer. Additionally, `Core.check()` SHALL parse the JSON output of `qemu-img info --backing-chain --output=json` (not just check exit codes) and verify: (a) every file in the chain exists, (b) every file has format `"qcow2"`, (c) `backing-filename` references are consistent, (d) no cycles.

#### Scenario: check uses --force-share on active layer
- **WHEN** `Core.check()` iterates over snapshots and the most recent is the active layer
- **THEN** `qemu-img info --force-share --backing-chain` is used for that snapshot
- **AND** the command succeeds despite the VM holding a write lock

#### Scenario: check parses JSON and detects inconsistent backing-filename
- **WHEN** `qemu-img info --backing-chain` returns exit code 0
- **AND** the JSON output shows a `backing-filename` that does not match the next file in the chain
- **THEN** `CheckResult(status="broken")` is returned with the inconsistency reported

#### Scenario: check parses JSON and detects cycle
- **WHEN** `qemu-img info --backing-chain` returns exit code 0
- **AND** the JSON output shows a file path appearing twice in the chain
- **THEN** `CheckResult(status="broken")` is returned with "cycle detected at <file>"

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
