## Requirements

### Requirement: CLI entry point
The system SHALL provide a `qsnap` command-line entry point with subcommands `run`, `snapshot`, `backup`, `prune`, `list`, `stats`, `check`, and `restore`. Each subcommand SHALL map to a corresponding Core method. The CLI layer SHALL contain no business logic — it is a thin translation layer from CLI args to Core method calls to formatted output.

#### Scenario: Help text
- **WHEN** `qsnap --help` is executed
- **THEN** all subcommands and global flags are listed

#### Scenario: Subcommand dispatch
- **WHEN** `qsnap run` is executed with a valid config
- **THEN** `Core.run()` is called with the parsed arguments

### Requirement: Global flag --config / -c
The system SHALL accept a `--config` / `-c` flag specifying the path to the TOML configuration file. Default SHALL be `/etc/qsnap/qsnap.toml`.

#### Scenario: Explicit config path
- **WHEN** `qsnap -c /path/to/custom.toml run` is executed
- **THEN** ConfigFacade is constructed with `/path/to/custom.toml`

#### Scenario: Default config path
- **WHEN** `qsnap run` is executed without `--config`
- **THEN** ConfigFacade is constructed with `/etc/qsnap/qsnap.toml`

### Requirement: Global flag --dry-run / -n
The system SHALL accept a `--dry-run` / `-n` flag that sets `Core.dry_run = True`. In dry-run mode, snapshot creation, blockcommit, file copy, and file deletion SHALL NOT be executed. Planned actions SHALL be logged at INFO level.

#### Scenario: Dry-run logs actions without executing
- **WHEN** `qsnap -n run` is executed
- **THEN** planned snapshot names are logged, but no `virsh snapshot-create-as` or `qemu-img` commands are executed

### Requirement: Global flags --preserve / --preserve-snapshots / --preserve-backups
The system SHALL accept `--preserve` (sets both), `--preserve-snapshots`, and `--preserve-backups` flags. When active, retention deletion steps (blockcommit for snapshots, file deletion for backups) SHALL be skipped. Retention evaluation SHALL still be computed (for schedule printing).

#### Scenario: --preserve skips all deletion
- **WHEN** `qsnap --preserve run` is executed and retention policy would remove 3 snapshots
- **THEN** those 3 snapshots are kept; no blockcommit or file deletion occurs

#### Scenario: --preserve-snapshots skips only snapshot deletion
- **WHEN** `qsnap --preserve-snapshots run` is executed
- **THEN** snapshot blockcommit is skipped but backup cleanup proceeds normally

### Requirement: Global flags --verbose / --quiet / --loglevel
The system SHALL accept `--verbose` / `-v` (DEBUG level), `--quiet` / `-q` (ERROR level), and `--loglevel` / `-l` (explicit: error, warn, info, debug) flags to control logging verbosity.

#### Scenario: Verbose logging
- **WHEN** `qsnap -v run` is executed
- **THEN** log level is set to DEBUG

#### Scenario: Quiet logging
- **WHEN** `qsnap -q run` is executed
- **THEN** log level is set to ERROR

### Requirement: Global flag --print-schedule / -S
The system SHALL accept a `--print-schedule` / `-S` flag that prints detailed retention schedule information: which snapshots and backups will be kept or removed by the configured retention policy. Schedule printing SHALL NOT mutate any files.

#### Scenario: Schedule output
- **WHEN** `qsnap -S run` is executed
- **THEN** output shows each VM's snapshots grouped by retention bucket (hourly/daily/weekly) with keep/remove status for each

### Requirement: Global flag --format
The system SHALL accept a `--format` flag with values `table` (default), `long`, `raw`, and `col:<columns>`. `table` SHALL produce human-readable columns. `raw` SHALL produce `key=value` pairs for machine consumption. `col:` SHALL allow custom column selection.

#### Scenario: Table output
- **WHEN** `qsnap --format table list snapshots` is executed
- **THEN** output is formatted as a table with aligned columns and uppercase headers

#### Scenario: Raw output
- **WHEN** `qsnap --format raw list backups` is executed
- **THEN** output is space-separated `key=value` pairs, one snapshot per line

### Requirement: Global flag --lockfile
The system SHALL accept a `--lockfile` flag that overrides the lockfile path from the configuration file.

#### Scenario: Lockfile override
- **WHEN** `qsnap --lockfile /run/qsnap.lock run` is executed
- **THEN** the lock is acquired on `/run/qsnap.lock`, overriding any config value

### Requirement: VM filter positional argument
All action subcommands (`run`, `snapshot`, `backup`, `prune`) and informational subcommands (`list`, `stats`, `check`) SHALL accept optional positional VM name arguments to filter which VMs are processed.

#### Scenario: Filter by VM name
- **WHEN** `qsnap run debiantest` is executed
- **THEN** only the VM named "debiantest" is processed

#### Scenario: No filter processes all VMs
- **WHEN** `qsnap run` is executed with no VM arguments
- **THEN** all VMs in the configuration are processed

### Requirement: Exit codes
The CLI SHALL return structured exit codes: 0 for success, 1 for generic error, 2 for parse error, 3 for lockfile error, 10 for backup abort.

#### Scenario: Success exit code
- **WHEN** `qsnap run` completes with no errors
- **THEN** exit code is 0

#### Scenario: Lockfile error exit code
- **WHEN** `qsnap run` is executed and the lockfile is held by another process
- **THEN** exit code is 3, and a message is printed to stderr

### Requirement: CLI is a thin layer
The CLI layer (commands.py) SHALL NOT parse config, create snapshots, evaluate retention, or perform any business logic. It SHALL only translate CLI args into Core method calls and format the returned results.

#### Scenario: No business logic in CLI
- **WHEN** reviewing `qsnap/cli/commands.py`
- **THEN** it contains no imports from `qsnap.modules`, `qsnap.config`, `qsnap.retention`, or `qsnap.state`

### Requirement: qsnap restore subcommand
The CLI SHALL provide a `restore` subcommand with arguments: `SNAPSHOT_NAME` (positional, required), `TARGET_DIR` (positional, required), and optional `VM` filter. It SHALL map to `Core.restore(snapshot_name, target_dir, vm_filter)`.

#### Scenario: Restore command invocation
- **WHEN** `qsnap restore debiantest.20250101T1200 /restore` is executed
- **THEN** `Core.restore("debiantest.20250101T1200", Path("/restore"))` is called

#### Scenario: Target directory does not exist
- **WHEN** `qsnap restore snap.20250101 /nonexistent/path` is executed
- **THEN** the command exits with code 1 and an error message indicating the directory must exist

### Requirement: qsnap check --deep flag
The `check` subcommand SHALL accept a `--deep` flag. When present, `Core.check(deep=True)` is called, which SHALL execute `qemu-img check` on each snapshot and backup file.

#### Scenario: Deep check invocation
- **WHEN** `qsnap check --deep` is executed
- **THEN** `Core.check(vm_filter=None, deep=True)` is called
- **THEN** output includes corruption status for each file

#### Scenario: Default check without --deep
- **WHEN** `qsnap check` is executed without `--deep`
- **THEN** `Core.check(deep=False)` is called (backing-file existence only)

### Requirement: qsnap list snapshots --tree flag
The `list snapshots` subcommand SHALL accept a `--tree` flag. When present, the CLI SHALL display the backing chain as an indented tree showing parent-child relationships.

#### Scenario: Tree output for backing chain
- **WHEN** `qsnap list snapshots --tree` is executed on a VM with 3 chain elements
- **THEN** output is indented with parent-child hierarchy visible

### Requirement: Global --long / -L flag
The CLI SHALL accept a global `--long` / `-L` flag as a shortcut for `--format long`.

#### Scenario: -L translates to --format long
- **WHEN** `qsnap -L list snapshots` is executed
- **THEN** output is in long format with all columns
