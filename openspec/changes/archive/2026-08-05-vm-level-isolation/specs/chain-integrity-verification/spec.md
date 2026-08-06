## ADDED Requirements

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

## RENAMED Requirements

- FROM: `### Requirement: Per-disk pre-commit backing chain integrity verification`
- TO: `### Requirement: Per-disk pre-commit backing chain integrity verification (superseded)`

- FROM: `### Requirement: Per-disk post-commit chain length verification`
- TO: `### Requirement: Per-disk post-commit chain length verification (superseded)`

## REMOVED Requirements

### Requirement: Per-disk pre-commit backing chain integrity verification (superseded)
**Reason**: Replaced by the re-added requirement of the same original name — broken chains now abort the VM pipeline instead of triggering partial blockcommit or skipping the disk. Scenario names changed, which MODIFIED cannot express.
**Migration**: None (behavioral change covered by the re-added requirement).

### Requirement: Per-disk post-commit chain length verification (superseded)
**Reason**: Replaced by the re-added requirement of the same original name — chain-length-unchanged after commit now raises `RuntimeError` (aborts the VM) in addition to the CRITICAL log.
**Migration**: None (behavioral change covered by the re-added requirement).
