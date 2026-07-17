## MODIFIED Requirements

### Requirement: Core logs size estimation on every pipeline run

Core SHALL compute and log a projected target size estimate on every pipeline run (including dry-run mode). The estimate SHALL be based on: (a) `qemu-img info --force-share` actual-size of the VM's base image, (b) average incremental size from state history (last N snapshots), (c) retention policy bucket counts. The log SHALL be at INFO level and include: current allocated size, average incremental size, retention policy, projected number of FULLs and incrementals, projected total size, current target size, and estimated delta.

In dry-run mode, the size estimation SHALL additionally log whether a FULL backup WOULD be created at this run (based on `_should_create_bucket_full()`) and which transfer method would be used (NBD for running VM, direct convert for stopped VM).

#### Scenario: Size estimation logged during normal run
- **WHEN** `qsnap run myvm` is executed
- **THEN** an INFO log entry is produced containing projected target size for each VM+target combination

#### Scenario: Size estimation logged during dry-run
- **WHEN** `qsnap -n run myvm` is executed
- **THEN** the same size estimation INFO log is produced, even though no pipeline actions are executed
- **AND** if a FULL would be created, the log includes: "[dry-run] FULL would be created (bucket=weekly, method=NBD)"

#### Scenario: Size estimation with no state history
- **WHEN** a VM has no recorded snapshots in state (first run)
- **THEN** the estimate SHALL use the base image actual-size for both FULL and incremental projections, and log "no churn history available"

#### Scenario: Size estimation uses --force-share on base image
- **WHEN** `qemu-img info` is called on the base image during size estimation
- **AND** the base image is locked by QEMU as a backing file of a running VM
- **THEN** `--force-share` is included in the command
- **AND** the command succeeds despite the VM holding a lock on the backing chain
