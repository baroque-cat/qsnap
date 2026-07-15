## Requirements

### Requirement: CLI entry point
The system SHALL provide a `qsnap` command-line entry point with subcommands `run`, `snapshot`, `backup`, `prune`, `list`, `stats`, `check`, and `restore`. The `list` subcommand SHALL support sub-subcommands: `snapshots`, `backups`, `config`, `latest`, and `deferred`. Each (sub-)subcommand SHALL map to a corresponding Core method. The CLI layer SHALL contain no business logic — it is a thin translation layer from CLI args to Core method calls to formatted output.

#### Scenario: Help text
- **WHEN** `qsnap --help` is executed
- **THEN** all subcommands and global flags are listed

#### Scenario: Subcommand dispatch
- **WHEN** `qsnap run` is executed with a valid config
- **THEN** `Core.run()` is called with the parsed arguments

#### Scenario: list deferred sub-subcommand
- **WHEN** `qsnap list deferred` is executed
- **THEN** `Core.list_deferred()` is called

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
The system SHALL accept a `--print-schedule` / `-S` flag that invokes `Core.schedule_summary()` and prints the result to stdout. Schedule printing SHALL NOT mutate any files. When used without `--dry-run`, the command SHALL exit after printing the schedule without executing the pipeline.

#### Scenario: Schedule output
- **WHEN** `qsnap -S run` is executed
- **THEN** output shows each VM's retention schedule and the command exits without creating snapshots

#### Scenario: --print-schedule with --dry-run
- **WHEN** `qsnap run --print-schedule --dry-run` is executed
- **THEN** the schedule summary is printed before the dry-run pipeline output, and the pipeline runs in dry-run mode

### Requirement: Global flag --timer

The system SHALL accept a `--timer` flag on action subcommands (`run`, `snapshot`, `backup`, `prune`) that logs the schedule summary at INFO level before executing the pipeline normally. Designed for cron/systemd timer use.

#### Scenario: Timer invocation logs summary
- **WHEN** `qsnap run --timer` is executed
- **THEN** the schedule summary output appears in the log at INFO level, then the pipeline executes normally

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

### Requirement: CLI `list deferred` subcommand

The system SHALL provide a `qsnap list deferred [vm...]` subcommand. It SHALL accept optional VM name arguments as filters. It SHALL dispatch to `Core.list_deferred(vm_filter)` and format the result as a table or raw output.

#### Scenario: list deferred dispatches to Core

- **WHEN** `qsnap list deferred` is executed
- **THEN** `Core.list_deferred()` is called with no filter

#### Scenario: list deferred with VM filter dispatches to Core

- **WHEN** `qsnap list deferred vm-home debiantest` is executed
- **THEN** `Core.list_deferred()` is called with the matching filter

#### Scenario: list deferred with --format raw

- **WHEN** `qsnap list deferred --format raw` is executed
- **THEN** output is in `key=value` format

### Requirement: list config shows per-VM safety settings
`qsnap list config` SHALL display per-VM safety columns: `blockcommit_deep_verify` (ON/OFF) and `snapshot_deep_verify` (ON/OFF). Global safety settings (`auto_cleanup`, `chain_verify_before_commit`, `chain_verify_after_commit`, `deep_check_schedule`) SHALL be shown in a header or summary section.

#### Scenario: list config shows OFF for default deep verify
- **WHEN** `qsnap list config` is executed and no VM has deep verify enabled
- **THEN** each VM shows `blockcommit_deep_verify: OFF`, `snapshot_deep_verify: OFF`

#### Scenario: list config shows ON for enabled deep verify
- **WHEN** VM "critical-db" has `blockcommit_deep_verify = true`
- **THEN** `qsnap list config` shows `blockcommit_deep_verify: ON` for that VM

### Requirement: qsnap check reports safety configuration status
`qsnap check` output SHALL include a summary of current safety configuration: whether `auto_cleanup`, chain verification, and deep check schedule are active.

#### Scenario: check output shows disabled safety features
- **WHEN** `deep_check_schedule = "off"` and `qsnap check` is executed
- **THEN** output includes "Deep check schedule: OFF" or equivalent

### Requirement: qsnap check --deep provides per-image results
`qsnap check --deep` SHALL run `qemu-img check --output=json` on every snapshot and backup. See `specs/deep-verification-circuit/spec.md`.

#### Scenario: Deep check exit code
- **WHEN** all images pass with 0 corruptions
- **THEN** exit code is 0
- **WHEN** any image has corruptions > 0 but is readable
- **THEN** exit code is still 0 (WARNING, non-fatal)
- **WHEN** any image is unreadable
- **THEN** exit code is 1
