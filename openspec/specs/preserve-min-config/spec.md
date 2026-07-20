# Preserve Minimum Configuration

## Purpose

Provides a first-class `preserve_min` configuration key for snapshots and backups at global, VM, and target levels with full TOML inheritance. The `preserve_min` value defines a minimum retention floor — recent snapshots/backups are always kept regardless of time-bucket counts. This was previously hardcoded to `"0h"` (no floor) or `"all"` (keep everything).

## Requirements

### Requirement: GlobalConfig accepts snapshot_preserve_min and target_preserve_min

`GlobalConfig` SHALL have optional `snapshot_preserve_min: str | None` and `target_preserve_min: str | None` fields, both defaulting to `None`.

#### Scenario: Default preserve_min absent
- **WHEN** global config has no `snapshot_preserve_min` key
- **THEN** `GlobalConfig.snapshot_preserve_min` is `None`

#### Scenario: Global preserve_min specified
- **WHEN** global config sets `snapshot_preserve_min = "3h"` and `target_preserve_min = "6h"`
- **THEN** `GlobalConfig.snapshot_preserve_min` is `"3h"` and `target_preserve_min` is `"6h"`

### Requirement: VMConfig inherits preserve_min from GlobalConfig

`VMConfig` SHALL have optional `snapshot_preserve_min: str | None` and `target_preserve_min: str | None` fields. When a VM-level value is absent, the VM SHALL inherit from `GlobalConfig`.

#### Scenario: VM inherits global preserve_min
- **WHEN** global config sets `snapshot_preserve_min = "3h"` and VM config omits the key
- **THEN** `VMConfig.snapshot_preserve_min` is `"3h"`

#### Scenario: VM overrides global preserve_min
- **WHEN** global config sets `snapshot_preserve_min = "3h"` and VM config sets `snapshot_preserve_min = "6h"`
- **THEN** `VMConfig.snapshot_preserve_min` is `"6h"`

### Requirement: TargetConfig inherits target_preserve_min from VMConfig

`TargetConfig` SHALL have an optional `target_preserve_min: str | None` field. When a target-level value is absent, the target SHALL inherit from its parent `VMConfig`'s `target_preserve_min`.

#### Scenario: Target inherits VM preserve_min
- **WHEN** VM config sets `target_preserve_min = "6h"` and target config omits the key
- **THEN** `TargetConfig.target_preserve_min` is `"6h"`

#### Scenario: Target overrides VM preserve_min
- **WHEN** VM config sets `target_preserve_min = "6h"` and target config sets `target_preserve_min = "12h"`
- **THEN** `TargetConfig.target_preserve_min` is `"12h"`

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

### Requirement: Retention evaluation uses per-VM and per-target preserve_min

`Core._evaluate_snapshot_retention()` SHALL pass `vm_config.snapshot_preserve_min` to `_parse_preserve()`. `Core._evaluate_backup_retention()` SHALL pass `target.target_preserve_min` to `_parse_preserve()`.

#### Scenario: Snapshot retention uses VM preserve_min
- **WHEN** VM has `snapshot_preserve_min = "3h"` and `snapshot_preserve = "24h 2d"`
- **THEN** snapshots created within the last 3 hours are kept regardless of time-bucket evaluation
