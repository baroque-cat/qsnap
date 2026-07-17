# State Consistency Check

## Purpose

Provides a `qsnap check --state` command that cross-references recorded snapshots, FULL backups, and incremental dependencies against the actual files on disk, detecting and reporting phantom entries (state records pointing to non-existent files) and corrupt state files.

## Requirements

### Requirement: Phantom snapshot detection in state

`Core.check_state()` SHALL iterate all recorded snapshots in `IStateManager` for each VM and verify the snapshot file exists on disk via `os.path.exists()`. Entries where the file does not exist SHALL be reported as phantom snapshots with status `"stale"`. The check SHALL NOT automatically remove phantom entries.

#### Scenario: All snapshot files exist — clean state
- **WHEN** `qsnap check --state` is run
- **AND** all recorded snapshots have corresponding files on disk
- **THEN** no phantom entries are reported
- **AND** status is `"ok"`

#### Scenario: Phantom snapshot detected — reported but not auto-cleaned
- **WHEN** `qsnap check --state` is run
- **AND** a recorded snapshot's file does not exist on disk
- **THEN** the snapshot is reported as phantom with path and VM name
- **AND** status is `"stale_snapshots"`
- **AND** the phantom entry is NOT automatically removed

### Requirement: Phantom FULL backup detection in state

`Core.check_state()` SHALL iterate all recorded FULL backups and verify each FULL file exists on disk. Phantom FULLs SHALL be reported with status `"stale_fulls"`.

### Requirement: Orphaned incremental dependency detection

`Core.check_state()` SHALL iterate all recorded incremental→FULL dependencies and verify both files exist on disk. Dependencies where either file is missing SHALL be reported.

### Requirement: State file integrity check

`Core.check_state()` SHALL verify that state JSON files are readable and parseable. Corrupted or unreadable state files SHALL be reported with status `"corrupt_state"`.
