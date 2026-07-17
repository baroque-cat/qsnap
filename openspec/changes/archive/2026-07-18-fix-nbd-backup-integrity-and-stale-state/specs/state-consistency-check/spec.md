# State Consistency Check

## Purpose

Ensure that the persistent state (`IStateManager`) remains consistent with the filesystem. Provides a `qsnap check --state` command that cross-references recorded snapshots, FULL backups, and incremental dependencies against the actual files on disk, detecting and reporting phantom entries (state records pointing to non-existent files) and orphan files (files on disk not recorded in state).

## ADDED Requirements

### Requirement: Phantom snapshot detection in state

`Core.check_state()` SHALL iterate all recorded snapshots in `IStateManager` for each VM and verify the snapshot file exists on disk via `os.path.exists()`. Entries where the file does not exist SHALL be reported as phantom snapshots with status `"stale"`. The check SHALL NOT automatically remove phantom entries (unlike the runtime stale guard in `_blockcommit_snapshots()` which removes them during pipeline execution).

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
- **AND** the phantom entry is NOT automatically removed (operator must run `qsnap run` for automatic staleguard cleanup)

### Requirement: Phantom FULL backup detection in state

`Core.check_state()` SHALL iterate all recorded FULL backups in `IStateManager.get_full_backups()` for each target and verify each FULL file exists on disk. Phantom FULLs SHALL be reported with status `"stale_fulls"`.

#### Scenario: Phantom FULL detected
- **WHEN** `qsnap check --state` is run
- **AND** a recorded FULL's file does not exist on disk
- **THEN** the FULL is reported as phantom with target path and name
- **AND** status includes `"stale_fulls"`

### Requirement: Orphaned incremental dependency detection

`Core.check_state()` SHALL iterate all recorded incremental→FULL dependencies and verify both the incremental file and the referenced FULL file exist on disk. Dependencies where either file is missing SHALL be reported with status `"stale_deps"`.

#### Scenario: Incremental dependency with deleted incremental
- **WHEN** a dependency record references an incremental file that does not exist on disk
- **AND** the FULL file does exist
- **THEN** the orphaned dependency is reported
- **AND** status includes `"stale_deps"`

#### Scenario: Incremental dependency with deleted FULL
- **WHEN** a dependency record references a FULL file that does not exist on disk
- **THEN** the dependency is reported as detached (no anchor)
- **AND** status includes `"stale_deps"`

### Requirement: State file integrity check

`Core.check_state()` SHALL verify that the state JSON files in `GlobalConfig.state_dir` are readable and parseable. Corrupted or unreadable state files SHALL be reported with status `"corrupt_state"` and the specific file path.

#### Scenario: Corrupted state file detected
- **WHEN** `qsnap check --state` is run
- **AND** a state JSON file cannot be parsed
- **THEN** the file is reported with path and parsing error
- **AND** status includes `"corrupt_state"`
