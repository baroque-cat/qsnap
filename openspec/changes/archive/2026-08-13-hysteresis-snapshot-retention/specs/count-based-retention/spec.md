# Count-Based Retention (DELTA)

## MODIFIED Requirements

### Requirement: Count-based retention policy

The system SHALL use a count-based retention policy with two integer fields: `chain_length`
(how many items to keep in a chain before triggering blockcommit or new FULL) and
`keep_generations` (how many FULL chains to keep on targets, minimum 1). Snapshots use only
`chain_length`; targets use both `chain_length` (incrementals per chain) and
`keep_generations` (FULL chains to keep). Snapshot retention operates under
`snapshot_retention_mode` (default `"hysteresis"`): in `"steady"` mode `chain_length` is the
keep count exactly as before; in `"hysteresis"` mode `chain_length` is the trigger threshold
and the collapse semantics of the `hysteresis-retention` capability apply instead. Target
retention is unaffected by the mode.

#### Scenario: Snapshot chain length triggers blockcommit (steady mode)

- **WHEN** `snapshot_retention_mode` is `"steady"` and the number of snapshots in the backing chain exceeds `snapshot_chain_length` (e.g., 169 > 168)
- **THEN** the retention engine SHALL mark the oldest snapshots (beyond `chain_length`) for removal via blockcommit
- **AND** the oldest-prefix post-processing SHALL ensure only a contiguous oldest prefix is blockcommitted

#### Scenario: Snapshot chain length not exceeded

- **WHEN** the number of snapshots is less than or equal to `snapshot_chain_length` (e.g., 100 <= 168)
- **THEN** the retention engine SHALL keep all snapshots and mark none for removal

#### Scenario: Hysteresis mode defers to the hysteresis capability

- **WHEN** `snapshot_retention_mode` is `"hysteresis"`
- **THEN** snapshot remove sets are determined by the `hysteresis-retention` capability (threshold/floor/phase/cap)
- **AND** the steady "excess beyond chain_length" rule does not apply to snapshots

#### Scenario: Target chain length triggers new FULL

- **WHEN** the number of incrementals in the newest chain exceeds `target_chain_length` (e.g., 169 > 168)
- **THEN** Core SHALL create a new FULL backup
- **AND** verify the FULL (M1/M2)
- **AND** only after verification succeeds, evaluate retention and delete old generations

#### Scenario: Target keep generations limits chains

- **WHEN** the number of FULL chains on a target exceeds `target_keep_generations` (e.g., 3 chains, keep_generations=2)
- **THEN** the retention engine SHALL mark the oldest chain(s) for removal
- **AND** all members of removed chains (FULL + incrementals) SHALL be deleted atomically

#### Scenario: First backup to target creates FULL

- **WHEN** no FULL backups exist for a target (first run or after cleanup)
- **THEN** Core SHALL create a FULL backup regardless of `target_chain_length`
- **AND** verify the FULL before any old data is deleted
