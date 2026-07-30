## MODIFIED Requirements

### Requirement: RetentionPolicy has three fields

The `RetentionPolicy` dataclass SHALL have exactly three fields: `chain_length: int = 0`, `keep_generations: int = 1`, and `preserve_min: int = 0`. No bucket fields, no anchor fields. The `preserve_min` field is a transport-only field — it is consumed by Core's post-processing in `_evaluate_snapshot_retention()`, NOT by the retention engine's `evaluate()` method. The engine SHALL ignore `preserve_min`.

#### Scenario: Default policy

- **WHEN** RetentionPolicy() is constructed with no arguments
- **THEN** chain_length SHALL be 0, keep_generations SHALL be 1, and preserve_min SHALL be 0

#### Scenario: Snapshot policy with preserve_min

- **WHEN** RetentionPolicy(chain_length=168, keep_generations=1, preserve_min=24) is constructed for snapshot retention
- **THEN** the engine SHALL use chain_length=168 as the keep count and ignore keep_generations and preserve_min

#### Scenario: Target policy

- **WHEN** RetentionPolicy(chain_length=0, keep_generations=2) is constructed for target retention
- **THEN** the engine SHALL use keep_generations=2 as the keep count (at chain level) and ignore chain_length and preserve_min

#### Scenario: preserve_min defaults to zero (inactive)

- **WHEN** RetentionPolicy(chain_length=72) is constructed without preserve_min
- **THEN** preserve_min SHALL be 0 (inactive — no preservation floor)
