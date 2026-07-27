# Blockcommit Recovery

## Purpose

Recovers from broken snapshot chains during blockcommit. Instead of skipping the entire blockcommit when a chain is broken, Core attempts a partial blockcommit of snapshots before the break point and auto-rebases stuck snapshots after the break point.

## Requirements

### Requirement: Partial blockcommit on broken chain

When pre-commit chain verification fails, Core SHALL attempt a partial blockcommit instead of skipping entirely. Core SHALL identify the broken file from `ChainVerifyResult.broken_file`. The `to_merge` list SHALL be split into `committable` (snapshots before the break point) and `stuck` (snapshots at and after the break point). Committable snapshots SHALL be blockcommitted normally. Stuck snapshots SHALL be auto-rebased to skip the broken file.

#### Scenario: Partial blockcommit before break point

- **WHEN** the chain is `base -> s1 -> s2(broken) -> s3 -> s4 -> active`
- **AND** retention marks s1 and s3 for removal
- **THEN** `_split_at_break()` produces committable=[s1], stuck=[s3]
- **AND** s1 is blockcommitted (merged into base, deleted)
- **AND** s3 is auto-rebased to skip s2

#### Scenario: No committable snapshots before break

- **WHEN** the chain is `base -> s1(broken) -> s2 -> s3 -> active`
- **AND** retention marks s1 and s2 for removal
- **THEN** `_split_at_break()` produces committable=[], stuck=[s1, s2]
- **AND** a CRITICAL log is emitted (nothing can be committed before the break)
- **AND** auto-rebase is attempted for stuck snapshots

### Requirement: Auto-rebase for stuck snapshots

After partial blockcommit, Core SHALL rebase stuck snapshots whose backing file is the broken file. `qemu-img rebase -u -F qcow2 -b <ancestor> <stuck_path>` SHALL be used to skip the broken file and re-chain to a valid ancestor. The broken file's state entry SHALL be removed via `remove_snapshot()`. After rebase, stuck snapshots SHALL be added back to `to_merge` for blockcommit.

#### Scenario: Stuck snapshot rebased to valid ancestor

- **WHEN** the chain is `base -> s1 -> s2(broken) -> s3 -> active`
- **AND** s3's backing-filename points to s2
- **THEN** `qemu-img rebase -u -F qcow2 -b <base_path> <s3_path>` is executed
- **AND** s3's backing-filename now points to base
- **AND** s2 is removed from `IStateManager` via `remove_snapshot()`
- **AND** s3 is added to `to_merge` for blockcommit

#### Scenario: Rebase safe for snapshots (data in active layer)

- **WHEN** `qemu-img rebase -u` is used to skip a missing snapshot file
- **THEN** the active layer (running VM) contains all current data
- **AND** the missing file's point-in-time is already lost (file doesn't exist)
- **AND** the rebase acknowledges the loss and allows the pipeline to continue

### Requirement: ChainVerifyResult reports broken file

`ChainVerifyResult` SHALL include a `broken_file: str | None` field. When chain verification fails due to a missing file, `broken_file` SHALL be set to the absolute path of the missing file. When verification fails for other reasons (non-qcow2, cycle), `broken_file` SHALL be `None`.

#### Scenario: Broken file reported on missing file

- **WHEN** `qemu-img info --backing-chain` fails because `s2.qcow2` does not exist
- **THEN** `ChainVerifyResult(success=False, broken_file="/path/to/s2.qcow2")` is returned

#### Scenario: No broken file on other failures

- **WHEN** chain verification fails due to a cyclic reference
- **THEN** `ChainVerifyResult(success=False, broken_file=None)` is returned
