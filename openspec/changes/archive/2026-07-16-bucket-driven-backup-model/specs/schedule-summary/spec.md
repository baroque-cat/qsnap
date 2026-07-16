## MODIFIED Requirements

### Requirement: Core.schedule_summary produces a human-readable retention preview

`Core.schedule_summary(vm_filter=None)` SHALL simulate the retention engine against a synthetic timestamp distribution and return a formatted string showing expected chain length, bucket breakdown, storage estimates, and real size projections for each VM and each target. The size projections SHALL include: current base image allocated size (via `qemu-img info`), average incremental size from state history, projected number of FULLs and incrementals based on bucket counts, projected total target size, and current target directory size.

#### Scenario: Empty state produces meaningful simulation
- **WHEN** `schedule_summary()` is called with zero recorded snapshots
- **THEN** it SHALL generate synthetic timestamps (one per hour for the configured retention window + 50% margin) and evaluate retention against them
- **AND** it SHALL include size projections using the base image actual-size

#### Scenario: Summary logs at INFO on every timer invocation
- **WHEN** the pipeline runs via systemd timer
- **THEN** `schedule_summary()` output SHALL be logged at INFO level in the pipeline entry log

#### Scenario: Summary shows snapshot and backup breakdown with size estimates
- **WHEN** `schedule_summary()` runs with `snapshot_preserve="24h 2d"` and `target_preserve="24h 7d 4w"`
- **THEN** output SHALL include separate sections for snapshots and per-target backups, each showing bucket counts (hourly, daily, weekly), expected total kept count, estimated storage, projected FULL count, projected incremental count, and projected total size

#### Scenario: Summary includes real base image size
- **WHEN** `schedule_summary()` runs for a VM with `base_image` pointing to a 100 GB qcow2
- **THEN** output SHALL include "Current allocated: ~100 GB" (from `qemu-img info` actual-size)

#### Scenario: Summary includes average incremental size from history
- **WHEN** `schedule_summary()` runs for a VM with 7 recorded snapshots averaging 1.5 GB
- **THEN** output SHALL include "Avg incremental: ~1.5 GB (last 7 snapshots)"
