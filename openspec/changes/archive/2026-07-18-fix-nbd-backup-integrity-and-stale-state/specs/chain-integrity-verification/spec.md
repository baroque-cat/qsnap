## ADDED Requirements

### Requirement: File existence guard before blockcommit

Before `Core._blockcommit_snapshots()` passes `to_merge` list to the lifecycle manager, it SHALL verify that each snapshot's file path exists on disk via `os.path.exists()`. If a file does not exist, the snapshot was already blockcommitted (by a prior run that failed to update state due to the pre-`acde50c` bug). For each missing file, Core SHALL call `IStateManager.remove_snapshot()` to clean the stale entry and remove it from `to_merge`. If `to_merge` becomes empty after removing stale entries, the blockcommit step SHALL be skipped entirely.

#### Scenario: All snapshot files exist — blockcommit proceeds normally
- **WHEN** `to_merge` contains 3 snapshots
- **AND** `os.path.exists()` returns True for all 3
- **THEN** all 3 snapshots are passed to `BlockCommitManager.blockcommit()`
- **AND** no entries are removed from state

#### Scenario: One stale entry removed — remaining blockcommitted
- **WHEN** `to_merge` contains snapshots [A, B, C]
- **AND** snapshot B's file does not exist (`os.path.exists()` returns False)
- **THEN** `remove_snapshot()` is called for B
- **AND** a WARNING is logged: "Stale state entry: snapshot B file not found — removed from state"
- **AND** `to_merge` becomes [A, C]
- **AND** only A and C are passed to blockcommit

#### Scenario: All entries stale — blockcommit skipped entirely
- **WHEN** `to_merge` contains 3 snapshots
- **AND** none of the files exist on disk
- **THEN** all 3 entries are removed from state via `remove_snapshot()`
- **AND** `to_merge` becomes empty
- **AND** the blockcommit step is skipped entirely (no call to lifecycle manager)
- **AND** an INFO log is emitted: "All snapshots in to_merge were stale — skipping blockcommit"

#### Scenario: Stale entry does NOT cause short-circuit
- **WHEN** `to_merge` contains snapshots [A, B, C, D] (oldest-first)
- **AND** snapshot B's file is missing
- **THEN** B is removed from `to_merge`
- **AND** blockcommit proceeds with [A, C, D]
- **AND** a single stale entry does NOT block blockcommit of subsequent snapshots
