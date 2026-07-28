# Count-Based Retention

## Purpose

Count-based retention replaces time-bucket retention with simple integer limits: `chain_length` controls how many snapshots or incrementals to keep in a chain, and `keep_generations` controls how many FULL backup chains to retain on a target.

## Requirements

### Requirement: Count-based retention policy

The system SHALL use a count-based retention policy with two integer fields: `chain_length` (how many items to keep in a chain before triggering blockcommit or new FULL) and `keep_generations` (how many FULL chains to keep on targets, minimum 1). Snapshots use only `chain_length`; targets use both `chain_length` (incrementals per chain) and `keep_generations` (FULL chains to keep).

#### Scenario: Snapshot chain length triggers blockcommit

- **WHEN** the number of snapshots in the backing chain exceeds `snapshot_chain_length` (e.g., 169 > 168)
- **THEN** the retention engine SHALL mark the oldest snapshots (beyond `chain_length`) for removal via blockcommit
- **AND** the oldest-prefix post-processing SHALL ensure only a contiguous oldest prefix is blockcommitted

#### Scenario: Snapshot chain length not exceeded

- **WHEN** the number of snapshots is less than or equal to `snapshot_chain_length` (e.g., 100 <= 168)
- **THEN** the retention engine SHALL keep all snapshots and mark none for removal

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
- **AND** the FULL SHALL be verified before any old data is deleted

### Requirement: Count-based retention engine

The `IRetentionEngine.evaluate()` method SHALL implement count-based logic: sort items by timestamp ascending, keep the newest N items, mark the rest for removal. The engine is a pure function with no I/O, no side effects, and deterministic output given the same inputs.

#### Scenario: Keep newest N items

- **WHEN** evaluate() is called with 10 items and policy.chain_length=5
- **THEN** the engine SHALL keep the 5 newest items (by timestamp) and mark the 5 oldest for removal

#### Scenario: All items within chain length

- **WHEN** evaluate() is called with 3 items and policy.chain_length=5
- **THEN** the engine SHALL keep all 3 items and mark none for removal

#### Scenario: Empty item list

- **WHEN** evaluate() is called with an empty item list
- **THEN** the engine SHALL return RetentionResult(keep=[], remove=[])

#### Scenario: Chain length zero

- **WHEN** evaluate() is called with policy.chain_length=0
- **THEN** the engine SHALL keep no items and mark all for removal

### Requirement: Explain method returns count-based summary

The `IRetentionEngine.explain()` method SHALL return a dictionary with `keep_count` and `remove_count` keys, not per-bucket breakdowns.

#### Scenario: Explain returns counts

- **WHEN** explain() is called with 10 items and policy.chain_length=5
- **THEN** the method SHALL return `{"keep_count": 5, "remove_count": 5}`

### Requirement: No preserve_day_of_week parameter

The `IRetentionEngine.evaluate()` method SHALL NOT accept a `preserve_day_of_week` parameter. The count-based engine does not use calendar boundaries.

#### Scenario: Evaluate without preserve_day_of_week

- **WHEN** evaluate() is called
- **THEN** the method signature SHALL be `evaluate(items, policy, now) -> RetentionResult` with no `preserve_day_of_week` parameter

### Requirement: RetentionPolicy has two fields

The `RetentionPolicy` dataclass SHALL have exactly two fields: `chain_length: int = 0` and `keep_generations: int = 1`. No bucket fields, no anchor fields, no preserve_min field.

#### Scenario: Default policy

- **WHEN** RetentionPolicy() is constructed with no arguments
- **THEN** chain_length SHALL be 0 and keep_generations SHALL be 1

#### Scenario: Snapshot policy

- **WHEN** RetentionPolicy(chain_length=168, keep_generations=1) is constructed for snapshot retention
- **THEN** the engine SHALL use chain_length=168 as the keep count and ignore keep_generations

#### Scenario: Target policy

- **WHEN** RetentionPolicy(chain_length=0, keep_generations=2) is constructed for target retention
- **THEN** the engine SHALL use keep_generations=2 as the keep count (at chain level) and ignore chain_length
