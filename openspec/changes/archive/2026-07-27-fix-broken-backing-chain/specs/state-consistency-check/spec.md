## ADDED Requirements

### Requirement: Broken backing chain detection in check --state

`Core.check_state()` SHALL detect broken backing chains on backup files at each target. For each non-FULL backup file (filename not containing `.FULL.`), the method SHALL run `qemu-img info --force-share --backing-chain --output=json <path>` via `IShell.run()` and check whether the command succeeds. Files where the command fails SHALL be reported as `broken_chains` with the backup name and target path. The status string SHALL include `"broken_chains"` when any broken chains are detected. FULL backups (standalone files with no backing) SHALL be skipped — they have no backing chain to validate.

The `StateCheckResult` dataclass SHALL include a `broken_chains: list[str]` field (defaulting to an empty list) containing human-readable descriptions of each broken chain (format: `"{backup_name} (target: {target_path})"`).

#### Scenario: Broken backing chain detected
- **WHEN** `qsnap check --state` is run
- **AND** a non-FULL backup file at a target has a broken backing chain (its backing file was deleted)
- **THEN** the backup is reported in `broken_chains`
- **AND** the status string includes `"broken_chains"`

#### Scenario: All backing chains intact — clean state
- **WHEN** `qsnap check --state` is run
- **AND** all non-FULL backup files have intact backing chains
- **THEN** `broken_chains` is an empty list
- **AND** the status string does NOT include `"broken_chains"`

#### Scenario: FULL backups skipped in chain validation
- **WHEN** `qsnap check --state` is run
- **AND** a FULL backup exists at the target
- **THEN** the FULL backup is NOT checked for backing-chain integrity (it has no backing file)
- **AND** only non-FULL backups are validated
