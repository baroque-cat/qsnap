## MODIFIED Requirements

### Requirement: Core._parse_preserve accepts optional preserve_min parameter

`Core._parse_preserve(preserve_str, preserve_min_str=None)` SHALL accept an optional `preserve_min_str` parameter. When provided and non-None, it SHALL override the default `preserve_min` value. When `None`, existing behavior SHALL be preserved.

#### Scenario: Explicit preserve_min overrides default
- **WHEN** `_parse_preserve("24h 2d", "3h")` is called
- **THEN** returns `RetentionPolicy(hourly=24, daily=2, preserve_min="3h")`

#### Scenario: No preserve_min uses existing default
- **WHEN** `_parse_preserve("24h 2d", None)` is called
- **THEN** returns `RetentionPolicy(hourly=24, daily=2, preserve_min="0h")`

### Requirement: Core._evaluate_snapshot_retention uses vm_config.snapshot_preserve_min

`Core._evaluate_snapshot_retention(vm_config, snapshots)` SHALL pass `vm_config.snapshot_preserve_min` to `_parse_preserve()`.

#### Scenario: Snapshot retention with preserve_min
- **WHEN** VM has `snapshot_preserve_min = "3h"`
- **THEN** `_parse_preserve()` is called with that value

### Requirement: Core._evaluate_backup_retention uses target.target_preserve_min

`Core._evaluate_backup_retention(vm_config, target, backups)` SHALL pass `target.target_preserve_min` to `_parse_preserve()`.

#### Scenario: Backup retention with preserve_min
- **WHEN** target has `target_preserve_min = "6h"`
- **THEN** `_parse_preserve()` is called with that value

### Requirement: Core._backup_target triggers full backup when due

`Core._backup_target(vm_config, target, snapshots)` SHALL, before the incremental transfer loop, check `IStateManager.get_last_full_backup(target.path)`. If the configured `target.full_every` interval has elapsed (or no full backup exists), it SHALL call `provider.create_full_backup()` on the most recent snapshot and then `IStateManager.set_last_full_backup()`.

#### Scenario: First run creates full backup
- **WHEN** `full_every="7d"` and no previous full backup exists
- **THEN** a full backup is created on the target

#### Scenario: Interval not elapsed skips full backup
- **WHEN** `full_every="7d"` and last full backup was 3 days ago
- **THEN** no full backup is created

## ADDED Requirements

### Requirement: Core.schedule_summary produces retention simulation

`Core.schedule_summary(vm_filter=None) -> str` SHALL generate synthetic timestamp data for each VM, pass it through `TimeBasedRetention.evaluate()` and `explain()`, and format a human-readable summary showing expected chain length, bucket breakdown, and estimated storage for snapshots and per-target backups.

#### Scenario: Summary includes all VMs when no filter
- **WHEN** `schedule_summary()` is called with no filter
- **THEN** output includes sections for every configured VM and every target

#### Scenario: Summary filters by VM name
- **WHEN** `schedule_summary(vm_filter="debiantest")` is called
- **THEN** output includes only the "debiantest" VM section
