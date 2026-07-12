## Requirements

### Requirement: IRetentionEngine ABC
The system SHALL provide an `IRetentionEngine` ABC with a pure `evaluate` method that determines which items to keep and which to remove based on a retention policy. IRetentionEngine does NOT inherit from Core.

#### Scenario: IRetentionEngine is a standalone ABC
- **WHEN** IRetentionEngine is defined
- **THEN** it does not have Core in its MRO (does not inherit from Core)

### Requirement: TimeBasedRetention implements IRetentionEngine
The system SHALL provide a `TimeBasedRetention` class that evaluates retention based on item timestamps and a RetentionPolicy.

#### Scenario: Hourly retention with 24h policy
- **WHEN** `evaluate()` is called with 48 items spaced 1 hour apart and `RetentionPolicy(hourly=24, daily=0, weekly=0, monthly=0, yearly=0, preserve_min="0h")`
- **THEN** the result has exactly 24 items in `keep` and 24 in `remove`

#### Scenario: preserve_min keeps all recent items
- **WHEN** `evaluate()` is called with 48 items spaced 1 hour apart and `RetentionPolicy(hourly=1, daily=0, weekly=0, monthly=0, yearly=0, preserve_min="12h")`
- **THEN** at least 12 items are kept (the preserve_min window), plus possibly 1 hourly

#### Scenario: Daily retention identifies first snapshot of each day
- **WHEN** `evaluate()` is called with items spanning multiple days and `RetentionPolicy(hourly=0, daily=7, weekly=0, monthly=0, yearly=0, preserve_min="0h")`
- **THEN** at most one item per day is kept (the earliest that day), up to 7 items

#### Scenario: preserve_min "all" keeps everything
- **WHEN** `evaluate()` is called with `preserve_min="all"`
- **THEN** all items are in `keep` and none in `remove`

### Requirement: Retention engine is deterministic
`evaluate()` SHALL be a pure function: given the same inputs, it always returns the same output. No I/O, no random, no external state.

#### Scenario: Deterministic output
- **WHEN** `evaluate()` is called twice with identical items and policy
- **THEN** both calls return identical keep and remove lists
