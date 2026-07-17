## MODIFIED Requirements

### Requirement: Post-commit chain length verification
After a blockcommit, Core SHALL re-run `qemu-img info --force-share --backing-chain --output=json` on the **current active layer** (the most recent snapshot that survived the blockcommit, obtained from `IStateManager` after removing merged snapshots). The `--force-share` flag is used because the active layer may still be locked by QEMU.

The chain length after commit SHALL be compared to the chain length before commit. The expected post-commit chain length SHALL be `chain_length_before - len(snapshots_merged)`, where `snapshots_merged` is the list of `SnapshotInfo` objects passed to `ILifecycleManager.blockcommit()`.

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

## REMOVED Requirements

### Requirement: Post-commit verification queries base image
**Reason**: `qemu-img info --backing-chain` on the base image always returns exactly 1 entry because the base image has no backing file. This measurement does not reflect the actual number of snapshots remaining in the chain above the base image.

**Migration**: The post-commit verification now queries the current active layer (the most recent surviving snapshot) instead. The `use_base_image` parameter on `_get_chain_length()` is removed. Existing tests that mock `_get_chain_length` with `side_effect` must be updated to use fixture-based `qemu-img info --backing-chain` outputs that reflect the active layer perspective.
