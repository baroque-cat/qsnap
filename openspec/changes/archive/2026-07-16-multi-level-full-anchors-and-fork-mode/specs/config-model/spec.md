# config-model — Delta Spec

## MODIFIED Requirements

### Requirement: RetentionPolicy dataclass
The system SHALL provide an immutable `RetentionPolicy` dataclass with fields for hourly, daily, weekly, monthly, and yearly retention counts, plus a `preserve_min` string, PLUS five anchor boolean fields: `anchor_hourly: bool = False`, `anchor_daily: bool = False`, `anchor_weekly: bool = False`, `anchor_monthly: bool = False`, `anchor_yearly: bool = False`. The `preserve_min` field SHALL accept the value `"latest"` in addition to `"all"` and duration strings like `"6h"`, `"2d"`.

#### Scenario: RetentionPolicy with hourly and daily limits
- **WHEN** a RetentionPolicy is created with `hourly=24`, `daily=2`, `weekly=0`, `monthly=0`, `yearly=0`, `preserve_min="6h"`
- **THEN** all fields are accessible and match the provided values
- **THEN** all `anchor_*` fields default to `False`

#### Scenario: RetentionPolicy defaults
- **WHEN** a RetentionPolicy is created with no arguments
- **THEN** all retention counts default to 0 and `preserve_min` defaults to `"all"`
- **THEN** all `anchor_*` fields default to `False`

#### Scenario: Anchor fields set from F-syntax
- **WHEN** a RetentionPolicy is created with `daily=7, anchor_daily=True, weekly=4, anchor_weekly=True`
- **THEN** `retention.anchor_daily` is `True` and `retention.anchor_weekly` is `True`
- **THEN** `retention.anchor_hourly`, `anchor_monthly`, `anchor_yearly` are `False`

#### Scenario: preserve_min = "latest"
- **WHEN** a RetentionPolicy is created with `preserve_min="latest"`
- **THEN** `retention.preserve_min` is `"latest"`
- **THEN** the retention engine keeps only the most recent item
