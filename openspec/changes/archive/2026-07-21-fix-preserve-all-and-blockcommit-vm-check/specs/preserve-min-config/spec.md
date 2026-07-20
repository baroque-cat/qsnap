## MODIFIED Requirements

### Requirement: Core._parse_preserve accepts optional preserve_min parameter

`Core._parse_preserve(preserve_str, preserve_min_str=None)` SHALL accept an optional `preserve_min_str` parameter. When provided and non-None, it SHALL override the default `preserve_min` in the returned `RetentionPolicy`. When `None`, the default SHALL be: `"all"` for `preserve_str = None`, `"latest"` for `preserve_str = "latest"`, `"all"` for `preserve_str = "all"`, and `"0h"` for all other bucket-string values.

The value `"all"` as a `preserve_str` SHALL be recognized as a valid input meaning "keep everything". It SHALL produce `RetentionPolicy(preserve_min="all")` with all bucket counts set to 0. The early-return guard SHALL include `"all"` alongside `None` and `"latest"` so that the regex bucket parser is not invoked for this value.

#### Scenario: Explicit preserve_min overrides default
- **WHEN** `_parse_preserve("24h 2d", "3h")` is called
- **THEN** returns `RetentionPolicy(hourly=24, daily=2, preserve_min="3h")`

#### Scenario: No preserve_min uses existing default
- **WHEN** `_parse_preserve("24h 2d", None)` is called
- **THEN** returns `RetentionPolicy(hourly=24, daily=2, preserve_min="0h")`

#### Scenario: preserve_str "all" without preserve_min defaults to "all"
- **WHEN** `_parse_preserve("all")` is called (preserve_min_str defaults to None)
- **THEN** returns `RetentionPolicy(preserve_min="all")`
- **AND** all bucket counts are 0
- **AND** no regex parsing is attempted on the string "all"

#### Scenario: preserve_str "all" with explicit preserve_min "all"
- **WHEN** `_parse_preserve("all", "all")` is called
- **THEN** returns `RetentionPolicy(preserve_min="all")`

#### Scenario: preserve_str "all" with explicit preserve_min "6h" respects override
- **WHEN** `_parse_preserve("all", "6h")` is called
- **THEN** returns `RetentionPolicy(preserve_min="6h")` (override wins)
