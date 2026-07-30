## MODIFIED Requirements

### Requirement: IStateManager per-target backup allocation tracking

`IStateManager` SHALL provide `get_last_backup_allocation(target_path: str) -> int | None` and `set_last_backup_allocation(target_path: str, alloc: int) -> None` methods. These methods persist a per-target baseline used by the `backup_create="onchange"` gate in `Core._backup_target()`. The baseline stores the source disk's current allocation value (either `actual-size` from `qemu-img info` when `change_detection_mode="allocation-size"`, or the allocation-map hash from `qemu-img map` when `change_detection_mode="allocation-map"`). When no prior baseline exists for a target, `get_last_backup_allocation()` SHALL return `None` (first-run behavior — backup always proceeds). After a successful backup, Core SHALL call `set_last_backup_allocation(target_path, current_allocation)` to update the baseline. If the backup fails, the baseline SHALL NOT be updated.

#### Scenario: Write and read per-target backup allocation

- **WHEN** `set_last_backup_allocation("/mnt/backup/vm1", 1048576)` is called, then `get_last_backup_allocation("/mnt/backup/vm1")`
- **THEN** the returned value is 1048576

#### Scenario: Missing target state returns None

- **WHEN** `get_last_backup_allocation("/mnt/backup/newtarget")` is called for a target with no prior state
- **THEN** the method returns `None`

#### Scenario: Per-target state is independent

- **WHEN** `set_last_backup_allocation("/mnt/backup/targetA", 1000)` is called
- **AND** `set_last_backup_allocation("/mnt/backup/targetB", 2000)` is called
- **THEN** `get_last_backup_allocation("/mnt/backup/targetA")` returns `1000`
- **AND** `get_last_backup_allocation("/mnt/backup/targetB")` returns `2000`

#### Scenario: Baseline updated after successful backup

- **WHEN** a backup transfer to a target with `backup_create="onchange"` succeeds
- **THEN** Core SHALL call `set_last_backup_allocation(target_path, current_allocation)`
- **AND** the next run's gate SHALL compare against this new baseline

#### Scenario: Baseline not updated after failed backup

- **WHEN** a backup transfer to a target with `backup_create="onchange"` fails
- **THEN** Core SHALL NOT call `set_last_backup_allocation`
- **AND** the next run's gate SHALL compare against the old baseline
