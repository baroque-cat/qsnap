## ADDED Requirements

### Requirement: Core logs size estimation on every pipeline run

Core SHALL compute and log a projected target size estimate on every pipeline run (including dry-run mode). The estimate SHALL be based on: (a) `qemu-img info` actual-size of the VM's base image, (b) average incremental size from state history (last N snapshots), (c) retention policy bucket counts. The log SHALL be at INFO level and include: current allocated size, average incremental size, retention policy, projected number of FULLs and incrementals, projected total size, current target size, and estimated delta.

#### Scenario: Size estimation logged during normal run
- **WHEN** `qsnap run myvm` is executed
- **THEN** an INFO log entry is produced containing projected target size for each VM+target combination

#### Scenario: Size estimation logged during dry-run
- **WHEN** `qsnap -n run myvm` is executed
- **THEN** the same size estimation INFO log is produced, even though no pipeline actions are executed

#### Scenario: Size estimation with no state history
- **WHEN** a VM has no recorded snapshots in state (first run)
- **THEN** the estimate SHALL use the base image actual-size for both FULL and incremental projections, and log "no churn history available"

### Requirement: qsnap estimate CLI command

The system SHALL provide a `qsnap estimate [vm]` CLI subcommand that computes and prints the size estimation without executing any pipeline actions. It SHALL accept an optional VM name filter positional argument.

#### Scenario: Estimate for specific VM
- **WHEN** `qsnap estimate myvm` is executed
- **THEN** a formatted size projection is printed to stdout for that VM only

#### Scenario: Estimate for all VMs
- **WHEN** `qsnap estimate` is executed without a VM argument
- **THEN** size projections for all configured VMs are printed

### Requirement: Size estimation formula

The projected target size SHALL be computed as: `num_fulls × full_size + num_incs × inc_size`, where `num_fulls` is the count of the highest active retention bucket, `full_size` is the base image actual-size (multiplied by 0.3 if compression is enabled), `num_incs` is the sum of all other bucket counts, and `inc_size` is the rolling average of the last N incremental snapshot sizes from state history.

#### Scenario: Compressed FULL projection
- **WHEN** `compress = true` and base image actual-size is 100 GB
- **THEN** the projected FULL size SHALL be approximately 30 GB (100 × 0.3)

#### Scenario: Uncompressed FULL projection
- **WHEN** `compress = false` and base image actual-size is 100 GB
- **THEN** the projected FULL size SHALL be approximately 100 GB

#### Scenario: Incremental size from state history
- **WHEN** the last 7 snapshots in state history have sizes [1.2, 1.5, 1.3, 1.6, 1.4, 1.5, 1.3] GB
- **THEN** the projected incremental size SHALL be approximately 1.4 GB (rolling average)
