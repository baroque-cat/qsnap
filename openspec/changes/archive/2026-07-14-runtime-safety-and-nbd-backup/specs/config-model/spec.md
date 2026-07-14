## ADDED Requirements

### Requirement: TargetConfig verify field
`TargetConfig` SHALL gain a `verify: str` field with default value `"metadata"`. Accepted values SHALL be `"off"` (no verification), `"metadata"` (qemu-img info consistency check), and `"full"` (qemu-img compare byte-level verification). The field SHALL be immutable (`frozen=True`).

#### Scenario: Default verify is metadata
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and no `verify`
- **THEN** `target.verify` is `"metadata"`

#### Scenario: Explicit full verification
- **WHEN** a TargetConfig is created with `verify="full"`
- **THEN** `target.verify` is `"full"`

### Requirement: VMConfig snapshot_quiesce field
`VMConfig` SHALL gain a `snapshot_quiesce: bool` field with default `False`. When `True`, snapshot creation SHALL request guest-agent filesystem freeze via `--quiesce`.

#### Scenario: Quiesce default is disabled
- **WHEN** a VMConfig is created without `snapshot_quiesce`
- **THEN** `vm_config.snapshot_quiesce` is `False`

## MODIFIED Requirements

### Requirement: RetentionPolicy dataclass
The system SHALL provide an immutable `RetentionPolicy` dataclass with fields for hourly, daily, weekly, monthly, and yearly retention counts, plus a `preserve_min` string. The `preserve_min` field SHALL accept the value `"latest"` in addition to `"all"` and duration strings like `"6h"`, `"2d"`.

#### Scenario: RetentionPolicy with hourly and daily limits
- **WHEN** a RetentionPolicy is created with `hourly=24`, `daily=2`, `weekly=0`, `monthly=0`, `yearly=0`, `preserve_min="6h"`
- **THEN** all fields are accessible and match the provided values

#### Scenario: RetentionPolicy defaults
- **WHEN** a RetentionPolicy is created with no arguments
- **THEN** all retention counts default to 0 and `preserve_min` defaults to `"all"`

#### Scenario: preserve_min = "latest"
- **WHEN** a RetentionPolicy is created with `preserve_min="latest"`
- **THEN** `retention.preserve_min` is `"latest"`
- **THEN** the retention engine keeps only the most recent item
