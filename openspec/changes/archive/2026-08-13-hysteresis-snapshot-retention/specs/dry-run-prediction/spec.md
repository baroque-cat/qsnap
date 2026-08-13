# Delta: dry-run-prediction (hysteresis-snapshot-retention)

## ADDED Requirements

### Requirement: Hysteresis retention prediction in dry-run

In dry-run mode, the hysteresis retention evaluation SHALL run with exactly the same decision
logic as a real run (mode selection, persisted collapse phase, trigger threshold, floor, and
per-run cap) but produce predictions instead of mutations:

- Grow phase (`N <= snapshot_chain_length` and no persisted collapse phase for the disk): the
  retention remove set is empty, no blockcommit prediction is recorded, and Core MAY log an
  informational note that the chain is within the hysteresis band.
- Triggered or continuing collapse (`N > snapshot_chain_length`, or the collapse phase is
  persisted for the disk while `N > snapshot_preserve_min`): Core SHALL record one prediction
  entry per disk naming exactly the snapshots that would be merged — the oldest
  `min(N − snapshot_preserve_min, max_commits_per_run)` entries when the cap applies,
  otherwise the oldest `N − snapshot_preserve_min` — consistent with the existing
  "Per-disk blockcommit prediction" requirement. No lifecycle manager call is executed.
- The `collapse_in_progress` phase key SHALL be read for decision-making but SHALL NOT be
  set, extended, or cleared by a dry-run; the zero-mutation invariant applies to it as to
  every other state key.

#### Scenario: Grow phase predicts no commits

- **WHEN** `qsnap -n run` executes for a VM with `snapshot_retention_mode = "hysteresis"`, `snapshot_chain_length = 72`, `snapshot_preserve_min = 24`, and 60 snapshots in state
- **THEN** no blockcommit prediction is recorded for the disk
- **AND** no `virsh blockcommit` command is executed
- **AND** the state file is byte-identical after the run

#### Scenario: Collapse prediction is capped and names the oldest snapshots

- **WHEN** `qsnap -n run` executes for a hysteresis VM with 73 snapshots, floor 24, and `max_commits_per_run = 12`
- **THEN** one per-disk prediction is recorded naming the 12 oldest snapshots as merge candidates
- **AND** the newest 24 snapshots never appear in any prediction
- **AND** no lifecycle manager `blockcommit()` is called

#### Scenario: Persisted collapse phase drives prediction below the trigger threshold

- **WHEN** `qsnap -n run` executes while `collapse_in_progress` contains the disk and the state holds 60 snapshots (below the trigger 72, above the floor 24)
- **THEN** a per-disk prediction naming the oldest `60 − 24 = 36` snapshots capped by `max_commits_per_run` is recorded
- **AND** the `collapse_in_progress` key remains set and byte-identical after the run
