## MODIFIED Requirements

### Requirement: TimeBasedRetention implements IRetentionEngine
The system SHALL provide a `TimeBasedRetention` class that evaluates retention based on item timestamps and a RetentionPolicy. The `preserve_min` field SHALL support the value `"latest"` in addition to `"all"` and duration strings. When `preserve_min` is `"latest"`, only the single most recent item SHALL be kept by the minimum preservation window.

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

#### Scenario: preserve_min "latest" keeps only the most recent item
- **WHEN** `evaluate()` is called with 10 items and `preserve_min="latest"`
- **THEN** only the single most recent item is in `keep`
