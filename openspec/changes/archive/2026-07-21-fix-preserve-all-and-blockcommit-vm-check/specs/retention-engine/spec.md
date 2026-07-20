## MODIFIED Requirements

### Requirement: TimeBasedRetention implements IRetentionEngine

The system SHALL provide a `TimeBasedRetention` class that evaluates retention based on item timestamps and a RetentionPolicy. The `preserve_min` field SHALL support the values `"all"`, `"latest"`, and duration strings (e.g., `"6h"`, `"2d"`). When `preserve_min` is `"all"`, ALL items SHALL be kept. When `preserve_min` is `"latest"`, only the single most recent item SHALL be kept.

`Core._parse_preserve()` SHALL map `preserve_str = "all"` to `RetentionPolicy(preserve_min="all")`. Previously, `"all"` was incorrectly mapped to `preserve_min = "0h"` (keep nothing), causing silent data loss. The fix ensures that `preserve = "all"` in the TOML config produces a policy that keeps everything.

#### Scenario: preserve_str "all" maps to preserve_min "all"
- **WHEN** `_parse_preserve("all", None)` is called
- **THEN** the returned `RetentionPolicy` has `preserve_min = "all"`
- **AND** all bucket counts are 0 (hourly=0, daily=0, weekly=0, monthly=0, yearly=0)

#### Scenario: preserve_str "all" with explicit preserve_min override
- **WHEN** `_parse_preserve("all", "6h")` is called
- **THEN** the returned `RetentionPolicy` has `preserve_min = "6h"` (explicit override wins)
- **AND** all bucket counts are 0

#### Scenario: preserve_str "all" with preserve_min "all" explicit
- **WHEN** `_parse_preserve("all", "all")` is called
- **THEN** the returned `RetentionPolicy` has `preserve_min = "all"`
- **AND** `evaluate()` keeps all items

#### Scenario: preserve_str "all" with preserve_min "0h" explicit (contradictory)
- **WHEN** `_parse_preserve("all", "0h")` is called
- **THEN** the returned `RetentionPolicy` has `preserve_min = "0h"` (explicit override wins)
- **AND** `evaluate()` keeps nothing (user explicitly set "0h")

#### Scenario: preserve_str None still defaults to preserve_min "all"
- **WHEN** `_parse_preserve(None, None)` is called
- **THEN** the returned `RetentionPolicy` has `preserve_min = "all"`
- **AND** all items are kept

#### Scenario: Bucket-string policy unaffected
- **WHEN** `_parse_preserve("24h 7d", None)` is called
- **THEN** the returned `RetentionPolicy` has `preserve_min = "0h"` and hourly=24, daily=7
- **AND** behavior is identical to before the fix
