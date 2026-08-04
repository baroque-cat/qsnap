# Schedule Summary

## Purpose

Human-readable retention schedule preview that displays current snapshot/backup counts, retention configuration, and real size data. Iterates per-disk for base image sizes. Used by `--print-schedule`/`-S` for manual preview and `--timer` for automated cron/systemd timer logging.

## Requirements

### Requirement: Core.schedule_summary produces a human-readable retention preview

`Core.schedule_summary(vm_filter=None)` SHALL display count-based retention information for each VM and each target. The output SHALL iterate per-disk for base image actual-size (`[disk.target] Current allocated: ~X.X GB` from `qemu-img info`). It SHALL show `chain_length`, `keep_generations`, current snapshot/chain counts, average incremental size from history, and per-target backup info. The method SHALL NOT generate synthetic timestamps or compute retention windows.

#### Scenario: Summary shows per-disk base image size
- **WHEN** `schedule_summary()` runs for a VM with disks `vda` (25 GB) and `vdb` (100 GB)
- **THEN** output includes:
  ```
  === vm1 ===
    [vda] Current allocated: ~25.0 GB
    [vdb] Current allocated: ~100.0 GB
  ```

#### Scenario: Empty state produces meaningful summary
- **WHEN** `schedule_summary()` is called with zero recorded snapshots
- **THEN** it SHALL display `chain_length` and `keep_generations` from config
- **AND** show `Current chain: 0 snapshots`

#### Scenario: Summary logs at INFO on every timer invocation
- **WHEN** the pipeline runs via systemd timer
- **THEN** `schedule_summary()` output SHALL be logged at INFO level in the pipeline entry log

#### Scenario: Summary shows snapshot and backup counts
- **WHEN** `schedule_summary()` runs with `snapshot_chain_length=168` and `target_keep_generations=2`
- **THEN** output SHALL include separate sections for snapshots and per-target backups, each showing chain_length, keep_generations, current count, and compression info

#### Scenario: Summary includes average incremental size from history
- **WHEN** `schedule_summary()` runs for a VM with 7 recorded snapshots averaging 1.5 GB
- **THEN** output SHALL include `Avg incremental: ~1.5 GB (last 7 snapshots)`

### Requirement: TimeBasedRetention.explain returns structured metadata

`IRetentionEngine.explain(items, policy, now)` SHALL return a dict with `keep_count` (number of items kept) and `remove_count` (number of items removed). The method SHALL NOT return per-bucket breakdowns.

#### Scenario: explain returns counts
- **WHEN** `explain(items, policy, now)` is called with 10 items and `policy.chain_length=5`
- **THEN** the result SHALL contain `{"keep_count": 5, "remove_count": 5}`

#### Scenario: explain is a pure function
- **WHEN** `explain()` is called twice with identical arguments
- **THEN** it SHALL return identical results
