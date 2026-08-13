# Core Orchestrator (DELTA)

## ADDED Requirements

### Requirement: Hysteresis retention evaluation flow

`Core._evaluate_disk_retention` SHALL branch on the VM's resolved `snapshot_retention_mode`.
In `"steady"` mode behavior is unchanged. In `"hysteresis"` mode Core SHALL: (1) read the
disk's snapshot count `N` and the phase marker; (2) if the phase is inactive and `N ≤ H`,
return an empty remove set; (3) if the phase is active or `N > H`, invoke the pure retention
engine with effective keep-count `L`, apply the oldest-prefix filter, apply the preserve_min
floor trim, then truncate to `max_commits_per_run` keeping the oldest entries; (4) persist
the phase marker for the disk before the blockcommit step when the trigger fires. The
retention engine itself SHALL remain a pure function unaware of modes and phases.

#### Scenario: Steady mode untouched

- **WHEN** the mode is `"steady"`
- **THEN** evaluation produces exactly the pre-existing keep/remove result

#### Scenario: Hysteresis collapse evaluation

- **WHEN** the mode is `"hysteresis"`, phase inactive, `H = 72`, `L = 24`, `N = 73`, cap 12
- **THEN** the engine is invoked with effective keep-count 24
- **AND** the final remove set is the 12 oldest snapshots
- **AND** the phase marker for the disk is persisted before any commit command

#### Scenario: Below threshold with inactive phase

- **WHEN** the mode is `"hysteresis"`, phase inactive, `N = 50`, `H = 72`
- **THEN** the remove set is empty and no phase marker is written

### Requirement: Collapse phase completion handling

After successful commit convergence for a disk in hysteresis mode, Core SHALL re-read the
disk's state count and, if it is `≤ L`, remove the disk from `collapse_in_progress` and log
the collapse-complete INFO line. If the count is still `> L` (cap reached or partial merge),
the phase SHALL remain and Core SHALL log the continuation INFO line with the remaining
count. Deferred or failed commits SHALL leave the phase intact for retry by subsequent runs.

#### Scenario: Floor reached clears the phase

- **WHEN** commits converge and the disk's state count is 24 with `L = 24`
- **THEN** the disk is removed from `collapse_in_progress`
- **AND** the collapse-complete INFO line is emitted

#### Scenario: Cap reached keeps the phase

- **WHEN** 12 of 49 marked snapshots were committed and the count is 61
- **THEN** the phase remains for the disk
- **AND** the continuation INFO line names the remaining count above the floor
