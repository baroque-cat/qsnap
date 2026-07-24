## ADDED Requirements

### Requirement: Per-target backup onchange gate

When `TargetConfig.backup_create == "onchange"` and snapshots exist, `Core._backup_target()` SHALL call `_should_backup_onchange(vm_config, target, snapshots)` before proceeding with backup transfer. If `_should_backup_onchange()` returns `False`, the backup transfer SHALL be skipped entirely for this target — no `create_full_backup()`, no `transfer_missing()`, no NBD export. The skip SHALL be logged at INFO level. If `_should_backup_onchange()` returns `True`, the existing backup logic SHALL proceed unchanged.

#### Scenario: First backup — always proceeds

- **WHEN** `backup_create = "onchange"` and `get_last_backup_allocation(target_path)` returns `None`
- **THEN** `_should_backup_onchange()` returns `True`
- **AND** the backup transfer proceeds

#### Scenario: No change — backup skipped

- **WHEN** `backup_create = "onchange"` and the latest snapshot's allocation equals `get_last_backup_allocation(target_path)`
- **THEN** `_should_backup_onchange()` returns `False`
- **AND** the backup transfer is skipped
- **AND** an INFO log message is emitted: "skipping target (no change since last backup)"

#### Scenario: Allocation grew — backup proceeds

- **WHEN** `backup_create = "onchange"` and the latest snapshot's allocation is greater than `get_last_backup_allocation(target_path)`
- **THEN** `_should_backup_onchange()` returns `True`
- **AND** the backup transfer proceeds

#### Scenario: always mode — gate bypassed

- **WHEN** `backup_create = "always"` (default)
- **THEN** `_should_backup_onchange()` is NOT called
- **AND** the backup transfer proceeds unconditionally

#### Scenario: No snapshots — backup skipped

- **WHEN** `backup_create = "onchange"` but no snapshots exist
- **THEN** `_should_backup_onchange()` returns `False` (nothing to transfer)
- **AND** the backup transfer is skipped
- **AND** an INFO log message is emitted

### Requirement: backup_create baseline update after successful transfer

After a successful backup transfer (`_transfer_with_retry()` returns results), when `TargetConfig.backup_create == "onchange"`, `Core._backup_target()` SHALL update the per-target baseline by calling `set_last_backup_allocation(str(target.path), latest_snapshot.allocation)` where `latest_snapshot` is the most recent snapshot by timestamp. The baseline SHALL NOT be updated on transfer failure.

#### Scenario: Baseline updated after successful transfer

- **WHEN** `backup_create = "onchange"` and the backup transfer succeeds
- **THEN** `set_last_backup_allocation(target_path, latest.allocation)` is called
- **AND** the next run's `_should_backup_onchange()` compares against the updated baseline

#### Scenario: Baseline NOT updated on transfer failure

- **WHEN** `backup_create = "onchange"` and the backup transfer fails
- **THEN** `set_last_backup_allocation()` is NOT called
- **AND** the baseline remains at the last successful backup's allocation
