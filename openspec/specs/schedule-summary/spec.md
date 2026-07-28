# Schedule Summary

## Purpose

Human-readable retention schedule preview that simulates the retention engine against synthetic timestamp data. Provides visibility into what the retention policy will keep or remove before actual pipeline execution. Used by `--print-schedule`/`-S` for manual preview and `--timer` for automated cron/systemd timer logging.

## Requirements

### Requirement: Core.schedule_summary produces a human-readable retention preview

`Core.schedule_summary(vm_filter=None)` SHALL display count-based retention information for each VM and each target. The output SHALL show `chain_length`, `keep_generations`, current snapshot/chain counts, and real size projections. The method SHALL NOT generate synthetic timestamps or compute retention windows. The methods `_retention_window()` and `_generate_synthetic_items()` SHALL NOT exist.

#### Scenario: Empty state produces meaningful summary
- **WHEN** `schedule_summary()` is called with zero recorded snapshots
- **THEN** it SHALL display `chain_length` and `keep_generations` from config
- **AND** show "Current chain: 0 snapshots"

#### Scenario: Summary logs at INFO on every timer invocation
- **WHEN** the pipeline runs via systemd timer
- **THEN** `schedule_summary()` output SHALL be logged at INFO level in the pipeline entry log

#### Scenario: Summary shows snapshot and backup counts
- **WHEN** `schedule_summary()` runs with `snapshot_chain_length=168` and `target_keep_generations=2`
- **THEN** output SHALL include separate sections for snapshots and per-target backups, each showing chain_length, keep_generations, current count, and estimated storage

#### Scenario: Summary includes real base image size
- **WHEN** `schedule_summary()` runs for a VM with `base_image` pointing to a 100 GB qcow2
- **THEN** output SHALL include "Current allocated: ~100 GB" (from `qemu-img info` actual-size)

#### Scenario: Summary includes average incremental size from history
- **WHEN** `schedule_summary()` runs for a VM with 7 recorded snapshots averaging 1.5 GB
- **THEN** output SHALL include "Avg incremental: ~1.5 GB (last 7 snapshots)"

### Requirement: TimeBasedRetention.explain returns structured metadata

`IRetentionEngine.explain(items, policy, now)` SHALL return a dict with `keep_count` (number of items kept) and `remove_count` (number of items removed). The method SHALL NOT return per-bucket breakdowns.

#### Scenario: explain returns counts
- **WHEN** `explain(items, policy, now)` is called with 10 items and `policy.chain_length=5`
- **THEN** the result SHALL contain `{"keep_count": 5, "remove_count": 5}`

#### Scenario: explain is a pure function
- **WHEN** `explain()` is called twice with identical arguments
- **THEN** it SHALL return identical results
