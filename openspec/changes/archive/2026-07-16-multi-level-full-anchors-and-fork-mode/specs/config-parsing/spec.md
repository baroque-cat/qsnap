# config-parsing — Delta Spec

## MODIFIED Requirements

### Requirement: Config validation forbids preserve_min without buckets
`ConfigFacade` SHALL validate that if `target_preserve` is not `None` and not `"latest"` and all parsed bucket counts (hourly through yearly) are 0 AND no F-anchors are present, then `target_preserve_min` SHALL be `"all"`. If `target_preserve_min` is not `"all"` and all bucket counts are 0 and no F-anchors are present, `ConfigFacade` SHALL raise `ConfigError` with a message explaining that `preserve_min` without buckets requires a FULL anchor which cannot be created without at least one active bucket.

Additionally, `ConfigFacade` SHALL validate that any bucket with a non-zero count MAY have an `F` prefix in the parsed policy string. If an F-anchor is present on a bucket with `count = 0`, `ConfigFacade` SHALL raise `ConfigError` with message: "F-anchor on bucket '<bucket>' requires count > 0".

#### Scenario: preserve_min without buckets rejected
- **WHEN** a target has `target_preserve = "0h 0d 0w 0m 0y"` and `target_preserve_min = "48h"` and no F-anchors
- **THEN** `ConfigError` is raised with message: "preserve_min without active buckets is not allowed — at least one bucket must have count > 0"

#### Scenario: preserve_min=all without buckets allowed
- **WHEN** a target has `target_preserve = "0h 0d 0w 0m 0y"` and `target_preserve_min = "all"` and no F-anchors
- **THEN** no error is raised (chain grows indefinitely, nothing is deleted)

#### Scenario: F-anchor with count=0 rejected
- **WHEN** a target has `target_preserve = "0Fh 7d"` and ConfigFacade parses the policy
- **THEN** `ConfigError` is raised with message: "F-anchor on bucket 'h' requires count > 0"

## ADDED Requirements

### Requirement: F-syntax parsing in _parse_preserve
`Core._parse_preserve(preserve_str, preserve_min_str=None)` SHALL parse the `F` prefix in bucket tokens. The regex SHALL be extended to `(\d+)(F?)([hdwmy])`. When `F` is present, the corresponding `anchor_*` field on the returned `RetentionPolicy` SHALL be set to `True`.

#### Scenario: F-syntax parsed correctly
- **WHEN** `_parse_preserve("24h 7Fd 4Fw")` is called
- **THEN** returns `RetentionPolicy(hourly=24, daily=7, weekly=4, anchor_daily=True, anchor_weekly=True)`

#### Scenario: No F-prefix — anchors remain False
- **WHEN** `_parse_preserve("24h 7d 4w")` is called
- **THEN** returns `RetentionPolicy(hourly=24, daily=7, weekly=4, anchor_hourly=False, anchor_daily=False, anchor_weekly=False)`

#### Scenario: F-prefix with invalid bucket character
- **WHEN** `_parse_preserve("7Fx")` is called
- **THEN** the token is ignored (does not match regex `[hdwmy]`)
