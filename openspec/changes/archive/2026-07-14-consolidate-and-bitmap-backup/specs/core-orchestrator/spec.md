## ADDED Requirements

### Requirement: Dynamic disk resolution in snapshot creation
`Core._create_snapshot()` SHALL resolve the active disk(s) via `virsh domblklist --domain <vm>` rather than using a hardcoded `"vda"` string. It SHALL iterate over all discovered disks, creating one snapshot file per disk.

#### Scenario: VM with a single disk named sda
- **WHEN** `virsh domblklist` returns `sda /path/to/image.qcow2`
- **THEN** snapshot is created for disk `sda`, not `vda`

#### Scenario: VM with multiple disks (vda, vdb)
- **WHEN** `virsh domblklist` returns both `vda` and `vdb`
- **THEN** two snapshots are created: one for each disk
- **THEN** snapshot files are named `{vm}.{ts}_vda.qcow2` and `{vm}.{ts}_vdb.qcow2`

#### Scenario: Explicit disk list in config overrides auto-discovery
- **WHEN** `VMConfig.disks` is `["vda"]` for a VM that also has `vdb`
- **THEN** only `vda` is snapshotted

### Requirement: Multi-disk snapshot result collection
When multiple disks are snapshotted, the `_create_snapshot()` method SHALL collect all `SnapshotResult` objects. If any disk fails, the partial results SHALL be logged, but the method SHALL continue processing the next VM in the pipeline.

#### Scenario: vda succeeds, vdb fails
- **WHEN** snapshot of `vda` succeeds but `vdb` fails
- **THEN** `vda` snapshot is recorded in state; `vdb` error is logged
- **THEN** the pipeline continues to retention evaluation

### Requirement: Backup retention in print_schedule
`Core.print_schedule()` SHALL evaluate and return retention decisions for backup targets in addition to snapshots. The result SHALL include per-target keep/remove lists.

#### Scenario: Schedule shows snapshot and backup decisions
- **WHEN** `core.print_schedule("vm1")` is called and VM has one target
- **THEN** the result shows snapshot retention (keep/remove) AND per-target backup retention (keep/remove)

### Requirement: check --deep via qemu-img check
`Core.check()` SHALL accept a `deep: bool = False` parameter. When `deep=True`, it SHALL execute `qemu-img check --output=json` on each snapshot and backup file. Files with `corruptions > 0` SHALL be reported as broken in `CheckResult`.

#### Scenario: Deep check finds corruption
- **WHEN** `qemu-img check --output=json` returns `{"corruptions": 2}`
- **THEN** the snapshot is marked as broken in `CheckResult` with status `"corrupted"`

#### Scenario: Deep check on clean image
- **WHEN** `qemu-img check` returns `{"corruptions": 0}`
- **THEN** the snapshot is marked as healthy

### Requirement: EXIT_BACKUP_ABORT wired into PipelineResult
`PipelineResult` SHALL track whether any backup task failed. When at least one backup task failed, `Core` SHALL return exit code 10. `VMRunResult` SHALL gain an optional `backup_failed: bool` field.

#### Scenario: Backup abort exit code
- **WHEN** `qsnap run` completes with one snapshot success and one backup failure
- **THEN** exit code is 10 (EXIT_BACKUP_ABORT)

#### Scenario: All backups succeed
- **WHEN** all backup tasks succeed
- **THEN** exit code is determined by overall pipeline success (0 or 1), not backup-specific

### Requirement: snapshot_create ondemand support
When `VMConfig.snapshot_create == "ondemand"`, `Core` SHALL check whether at least one backup target is reachable before creating a snapshot. If no targets are reachable, the snapshot step SHALL be skipped.

#### Scenario: Ondemand with reachable target
- **WHEN** `snapshot_create = "ondemand"` and the target directory exists
- **THEN** snapshot is created normally

#### Scenario: Ondemand with no reachable targets
- **WHEN** `snapshot_create = "ondemand"` and no target directory exists
- **THEN** snapshot creation is skipped with an INFO log message
