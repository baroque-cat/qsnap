## MODIFIED Requirements

### Requirement: Hysteresis retention prediction in dry-run

In dry-run mode, the hysteresis retention evaluation SHALL run with exactly the same decision logic as a real run (mode selection, trigger threshold, floor) but produce predictions instead of mutations:

- Grow phase (`N <= snapshot_chain_length`): the retention remove set is empty, no blockcommit prediction is recorded, and Core MAY log an informational note that the chain is within the hysteresis band.
- Triggered collapse (`N > snapshot_chain_length`): Core SHALL record one prediction entry per disk naming exactly the snapshots that would be merged — the FULL oldest `N − snapshot_preserve_min` set, uncapped — consistent with the existing "Per-disk blockcommit prediction" requirement, and SHALL indicate that they would be collapsed in a single bulk blockcommit. No lifecycle manager call is executed.
- No phase state exists: a dry-run neither reads nor writes any collapse-phase key (the key itself is removed by this change), and the zero-mutation invariant applies to every remaining state key.

#### Scenario: Grow phase predicts no commits
- **WHEN** `qsnap -n run` executes for a VM with `snapshot_retention_mode = "hysteresis"`, `snapshot_chain_length = 72`, `snapshot_preserve_min = 24`, and 60 snapshots in state
- **THEN** no blockcommit prediction is recorded for the disk
- **AND** no `virsh blockcommit` command is executed
- **AND** the state file is byte-identical after the run

#### Scenario: Collapse prediction names the full uncapped set
- **WHEN** `qsnap -n run` executes for a hysteresis VM with 73 snapshots and floor 24
- **THEN** one per-disk prediction is recorded naming ALL 49 oldest snapshots as merge candidates of a single bulk blockcommit
- **AND** the newest 24 snapshots never appear in any prediction
- **AND** no lifecycle manager `blockcommit()` is called

#### Scenario: Prediction below threshold stays silent even above floor
- **WHEN** `qsnap -n run` executes for a hysteresis VM whose state holds 60 snapshots (below the trigger 72, above the floor 24)
- **THEN** no blockcommit prediction is recorded
- **AND** the state file is byte-identical after the run
