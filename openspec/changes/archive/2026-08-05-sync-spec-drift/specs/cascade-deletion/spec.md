## ADDED Requirements

### Requirement: IStateManager tracks multiple FULLs per target

`IStateManager` SHALL provide `get_full_backups(target_path: str) -> list[FullBackupInfo]` returning ALL FULL backups for a target (not just the most recent). Each `FullBackupInfo` SHALL include `name`, `path`, `timestamp`, and `disk`. `record_full_backup(target_path, name, timestamp, disk)` SHALL append to the list (not overwrite). The `disk` field identifies the disk target (e.g. `"vda"`) this FULL anchors — each disk owns its own FULL chain.

#### Scenario: Multiple FULLs tracked per target
- **WHEN** two FULL backups are created for the same target at different times
- **THEN** `get_full_backups(target_path)` returns a list of 2 `FullBackupInfo` entries

#### Scenario: FULL recorded for a disk
- **WHEN** a FULL is created for disk `vda`
- **THEN** the recorded `FullBackupInfo` has `disk="vda"`

## RENAMED Requirements

- FROM: `### Requirement: IStateManager tracks multiple FULLs per target`
- TO: `### Requirement: IStateManager tracks multiple FULLs per target (superseded)`

## REMOVED Requirements

### Requirement: IStateManager tracks multiple FULLs per target (superseded)
**Reason**: Replaced by the re-added requirement of the same original name — the obsolete `bucket_level` field/parameter is documented as `disk`, matching `FullBackupInfo.disk` and `record_full_backup(target_path, name, timestamp, disk)` in code. Scenario name changed, which MODIFIED cannot express.
**Migration**: None (spec-only; code already uses `disk`).
