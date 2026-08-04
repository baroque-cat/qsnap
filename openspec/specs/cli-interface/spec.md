# CLI Interface

## Purpose

The `qsnap` command-line surface: subcommands, global flags, output formatting, and exit codes. The CLI is a thin translation layer — it converts arguments into Core calls and formats results, with no business logic. All listing output is disk-aware.

## Requirements

### Requirement: CLI entry point
The system SHALL provide a `qsnap` command-line entry point with subcommands `run`, `snapshot`, `backup`, `prune`, `list`, `stats`, `check`, `estimate`, `restore`, and `fork`. The `deploy` subcommand is REMOVED. The `list` subcommand SHALL support sub-subcommands: `snapshots`, `backups`, `config`, `latest`, and `deferred`. Each (sub-)subcommand SHALL map to a corresponding Core method. The CLI layer SHALL contain no business logic.

#### Scenario: Help text
- **WHEN** `qsnap --help` is executed
- **THEN** all subcommands and global flags are listed
- **AND** `deploy` is NOT listed

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

In dry-run mode, environment validation (`_validate_environment()`) SHALL still be executed. Validation failures SHALL be logged as WARNING (non-fatal).

In dry-run mode, the FULL backup creation decision SHALL still be evaluated. The dry-run output SHALL log whether a FULL backup WOULD be created and via which transfer method.

#### Scenario: Dry-run logs actions without executing
- **WHEN** `qsnap -n run` is executed
- **THEN** planned snapshot names are logged, but no `virsh snapshot-create-as` or `qemu-img` commands are executed

#### Scenario: Dry-run runs environment validation
- **WHEN** `qsnap -n run` is executed
- **THEN** `_validate_environment()` is called for each VM
- **AND** if validation fails, the broken checks are logged as WARNING
- **AND** the dry-run does NOT abort

#### Scenario: Dry-run logs FULL-would-be-created
- **WHEN** `qsnap -n run` is executed
- **AND** `_should_create_bucket_full()` returns `(True, "weekly")` for a target
- **THEN** an INFO log is emitted indicating that a FULL would be created

### Requirement: Global flags --preserve / --preserve-snapshots / --preserve-backups
The system SHALL accept `--preserve` (sets both), `--preserve-snapshots`, and `--preserve-backups` flags. When active, retention deletion steps SHALL be skipped.

#### Scenario: --preserve skips all deletion
- **WHEN** `qsnap --preserve run` is executed and retention policy would remove 3 snapshots
- **THEN** those 3 snapshots are kept; no blockcommit or file deletion occurs

#### Scenario: --preserve-snapshots skips only snapshot deletion
- **WHEN** `qsnap --preserve-snapshots run` is executed
- **THEN** snapshot blockcommit is skipped but backup cleanup proceeds normally

### Requirement: Global flags --verbose / --quiet / --loglevel
The system SHALL accept `--verbose` / `-v` (DEBUG level), `--quiet` / `-q` (ERROR level), and `--loglevel` / `-l` (explicit: error, warn, info, debug) flags.

#### Scenario: Verbose logging
- **WHEN** `qsnap -v run` is executed
- **THEN** log level is set to DEBUG

#### Scenario: Quiet logging
- **WHEN** `qsnap -q run` is executed
- **THEN** log level is set to ERROR

### Requirement: Global flag --print-schedule / -S
The system SHALL accept a `--print-schedule` / `-S` flag that invokes `Core.schedule_summary()` and prints the result to stdout. When used without `--dry-run`, the command SHALL exit after printing without executing the pipeline.

#### Scenario: Schedule output
- **WHEN** `qsnap -S run` is executed
- **THEN** output shows each VM's retention schedule and the command exits without creating snapshots

#### Scenario: --print-schedule with --dry-run
- **WHEN** `qsnap run --print-schedule --dry-run` is executed
- **THEN** the schedule summary is printed before the dry-run pipeline output, and the pipeline runs in dry-run mode

### Requirement: Global flag --timer

The system SHALL accept a `--timer` flag on action subcommands (`run`, `snapshot`, `backup`, `prune`) that logs the schedule summary at INFO level before executing the pipeline normally.

#### Scenario: Timer invocation logs summary
- **WHEN** `qsnap run --timer` is executed
- **THEN** the schedule summary output appears in the log at INFO level, then the pipeline executes normally

### Requirement: Global flag --format
The system SHALL accept a `--format` flag with values `table` (default), `long`, `raw`, and `col:<columns>`.

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
- **THEN** the lock is acquired on `/run/qsnap.lock`

### Requirement: VM filter positional argument
All action subcommands (`run`, `snapshot`, `backup`, `prune`) and informational subcommands (`list`, `stats`, `check`) SHALL accept optional positional VM name arguments.

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
- **AND** `format_summary()` is called from `qsnap/cli/summary.py` as a pure function

### Requirement: Summary output after run command

The `handle_run()` function SHALL, after computing the exit code via `_format_pipeline_result()`, call `format_summary(result)` and print the result to stdout.

#### Scenario: Summary printed after successful run
- **WHEN** `qsnap run` completes successfully
- **THEN** the return code is computed by `_format_pipeline_result()`
- **AND** `format_summary(result)` is called and the formatted summary is printed to stdout

#### Scenario: Summary printed after run with backup failures
- **WHEN** `qsnap run` completes with exit code 10 (backup abort)
- **THEN** the summary table is still printed to stdout

#### Scenario: Summary printed after dry-run
- **WHEN** `qsnap -n run` completes
- **THEN** the summary table is printed with `Dryrun: YES` header

### Requirement: CLI thin-layer constraint for summary

The `format_summary()` function SHALL only translate `PipelineResult` into a formatted string. It SHALL NOT access `IStateManager`, `IConfigFacade`, or any module.

#### Scenario: No business logic in summary formatter
- **WHEN** reviewing `qsnap/cli/summary.py`
- **THEN** it contains no imports from `qsnap.modules`, `qsnap.config`, `qsnap.retention`, or `qsnap.state`

### Requirement: qsnap restore subcommand
The CLI SHALL provide a `restore` subcommand with arguments: `SNAPSHOT_NAME` (positional, required), optional `VM` filter, `--dry-run` flag, and `--yes` flag. There is NO `--disk` flag — the disk is resolved from the snapshot/backup name. It SHALL map to `Core.restore(snapshot_name, vm_filter)`.

#### Scenario: Restore command invocation
- **WHEN** `qsnap restore debiantest.20250101T1200` is executed
- **THEN** `Core.restore("debiantest.20250101T1200", vm_filter=None)` is called

#### Scenario: Restore with --dry-run
- **WHEN** `qsnap restore debiantest.20250101T1200 --dry-run` is executed
- **THEN** `Core.restore("debiantest.20250101T1200")` is called with `core.dry_run = True`

#### Scenario: Restore with --yes skips confirmation
- **WHEN** `qsnap restore debiantest.20250101T1200 --yes` is executed
- **THEN** no confirmation prompt is displayed

### Requirement: qsnap check --deep flag
The `check` subcommand SHALL accept a `--deep` flag. When present, `Core.check(deep=True)` is called.

#### Scenario: Deep check invocation
- **WHEN** `qsnap check --deep` is executed
- **THEN** `Core.check(vm_filter=None, deep=True)` is called

#### Scenario: Default check without --deep
- **WHEN** `qsnap check` is executed without `--deep`
- **THEN** `Core.check(deep=False)` is called (backing-file existence only)

### Requirement: qsnap list snapshots --tree flag
The `list snapshots` subcommand SHALL accept a `--tree` flag. When present, the CLI SHALL display the backing chain as an indented tree grouped by disk.

#### Scenario: Tree output for backing chain
- **WHEN** `qsnap list snapshots --tree` is executed on a VM with 3 chain elements
- **THEN** output is indented with parent-child hierarchy, grouped by disk target

### Requirement: Global --long / -L flag
The CLI SHALL accept a global `--long` / `-L` flag as a shortcut for `--format long`.

#### Scenario: -L translates to --format long
- **WHEN** `qsnap -L list snapshots` is executed
- **THEN** output is in long format with all columns

### Requirement: CLI `list deferred` subcommand

The system SHALL provide a `qsnap list deferred [vm...]` subcommand. It SHALL accept optional VM name arguments as filters. It SHALL dispatch to `Core.list_deferred(vm_filter)` and format the result as a table (columns: `VM`, `DISK`, `SNAPSHOTS`, `REASON`, `AGE`) or raw output (keys: `vm_name`, `disk`, `snapshots`, `reason`, `since`).

#### Scenario: list deferred dispatches to Core

- **WHEN** `qsnap list deferred` is executed
- **THEN** `Core.list_deferred()` is called with no filter
- **AND** output includes a `DISK` column

#### Scenario: list deferred with VM filter dispatches to Core

- **WHEN** `qsnap list deferred vm-home debiantest` is executed
- **THEN** `Core.list_deferred()` is called with the matching filter

#### Scenario: list deferred with --format raw

- **WHEN** `qsnap list deferred --format raw` is executed
- **THEN** output includes `disk=` in each line

### Requirement: list config shows per-VM disks and safety settings
`qsnap list config` SHALL display a `DISKS` column showing each disk as `target=base_image` (comma-separated). It SHALL also display per-VM safety column: `BLOCKCOMMIT_DEEP_VERIFY` (ON/OFF). Global safety settings SHALL be shown in a header.

#### Scenario: list config shows multi-disk configuration
- **WHEN** `qsnap list config` is executed on a VM with disks vda and vdb
- **THEN** the `DISKS` column shows `vda=/path/to/vda.qcow2, vdb=/path/to/vdb.qcow2`

#### Scenario: list config shows OFF for default deep verify
- **WHEN** `qsnap list config` is executed and no VM has deep verify enabled
- **THEN** each VM shows `BLOCKCOMMIT_DEEP_VERIFY: OFF`

### Requirement: qsnap check reports safety configuration status
`qsnap check` output SHALL include a summary of current safety configuration.

#### Scenario: check output shows disabled safety features
- **WHEN** `deep_check_schedule = "off"` and `qsnap check` is executed
- **THEN** output includes `Deep check schedule: OFF` or equivalent

### Requirement: qsnap check --deep provides per-image results
`qsnap check --deep` SHALL run `qemu-img check --output=json` on every snapshot and backup.

#### Scenario: Deep check exit code
- **WHEN** all images pass
- **THEN** exit code is 0
- **WHEN** any image is unreadable
- **THEN** exit code is 1

### Requirement: qsnap estimate subcommand
The CLI SHALL provide an `estimate` subcommand with an optional `VM` positional filter argument. It SHALL map to `Core.estimate()` with per-disk size reporting. It SHALL NOT execute any pipeline actions.

#### Scenario: Estimate for specific VM
- **WHEN** `qsnap estimate myvm` is executed
- **THEN** per-disk factual size info is printed to stdout for that VM only

#### Scenario: Estimate for all VMs
- **WHEN** `qsnap estimate` is executed without a VM argument
- **THEN** per-disk factual summaries for all configured VMs are printed

### Requirement: qsnap fork subcommand
The system SHALL provide a `qsnap fork` subcommand accepting positional argument `SNAPSHOT_NAME`, a required `--output <path>` flag, an optional VM filter, and a `--dry-run` flag. It SHALL call `Core.fork(name, output_path, vm_filter)`. When the local `--dry-run` flag or the global `--dry-run` / `-n` flag is active, the CLI handler SHALL ensure `core.dry_run = True` before calling `Core.fork()`. The `--as-vm`, `--storage`, and `--add-to-config` flags are REMOVED.

#### Scenario: Fork command succeeds
- **WHEN** `qsnap fork myvm.20260701T120000_a1b2c3 --output /var/lib/libvirt/images/myvm-clone.qcow2` is executed
- **THEN** `Core.fork("myvm.20260701T120000_a1b2c3", Path("/var/lib/libvirt/images/myvm-clone.qcow2"), vm_filter=None)` is called
- **THEN** exit code is 0

#### Scenario: Fork command fails on missing snapshot
- **WHEN** `qsnap fork nonexistent --output /tmp/test.qcow2` is executed
- **THEN** exit code is 1

#### Scenario: Fork without --output fails
- **WHEN** `qsnap fork myvm.20260701T120000_a1b2c3` is executed without `--output`
- **THEN** argparse reports a missing required argument error

#### Scenario: Fork with --dry-run previews without converting
- **WHEN** `qsnap fork myvm.20260701T120000_a1b2c3 --output /tmp/clone.qcow2 --dry-run` is executed
- **THEN** the CLI handler ensures `core.dry_run = True` before calling `Core.fork()`
- **AND** the planned conversion is logged with the estimated chain size
- **AND** no output file is created and exit code is 0

### Requirement: qsnap list backups --tree flag
The `list backups` subcommand SHALL accept a `--tree` flag. When present, the CLI SHALL display backup chains grouped by VM → Target → Disk → FULL chain with indented hierarchy.

#### Scenario: Tree output for backup chains
- **WHEN** `qsnap list backups myvm --tree` is executed on a VM with 2 FULL chains across 2 disks
- **THEN** output shows each disk grouped, with FULLs and their incrementals indented

#### Scenario: Tree output for orphan backups
- **WHEN** `qsnap list backups myvm --tree` is executed and orphan backups exist
- **THEN** orphans are displayed under a `(orphan)` header per disk

### Requirement: Reconcile CLI subcommand

The CLI SHALL provide a `reconcile` subcommand that dispatches to `Core.reconcile()`. The subcommand SHALL accept an optional VM name filter, a `--dry-run` flag, and a `--format` flag.

#### Scenario: Reconcile command dispatches to Core

- **WHEN** `qsnap reconcile` is invoked
- **THEN** the CLI handler SHALL call `core.reconcile(vm_filter)` and format results as a table

#### Scenario: Reconcile with dry-run

- **WHEN** `qsnap reconcile --dry-run` is invoked
- **THEN** the CLI handler SHALL set `core.dry_run = True` before calling `core.reconcile()`

#### Scenario: Reconcile with VM filter

- **WHEN** `qsnap reconcile myvm` is invoked
- **THEN** the CLI handler SHALL pass `myvm` as the VM filter

#### Scenario: Reconcile exit code

- **WHEN** `qsnap reconcile` completes
- **THEN** the exit code SHALL be 0 if no errors, or 1 if any VM had errors

### Requirement: List snapshots includes disk column
The `qsnap list snapshots` flat table output SHALL include a `DISK` column identifying the disk target each snapshot belongs to.

#### Scenario: Flat table shows disk column
- **WHEN** `qsnap list snapshots` is executed on a VM with snapshots on disks `vda` and `vdb`
- **THEN** the table columns are `VM`, `DISK`, `NAME`, `PATH`, `TIMESTAMP`, `ALLOCATION`
- **AND** each row shows the correct disk target

### Requirement: List latest shows one row per disk
The `qsnap list latest` flat table output SHALL include a `DISK` column and SHALL print one row per (VM, disk): every configured disk of a VM appears, with `-` placeholder values for disks that have no snapshots.

#### Scenario: Latest shows one row per disk
- **WHEN** `qsnap list latest` is executed and VM "vm1" has disks `vda` (with snapshots) and `vdb` (without)
- **THEN** the table shows a `vda` row with the latest snapshot data
- **AND** a `vdb` row with `-` placeholders for name/timestamp/allocation

### Requirement: List backups flat output includes target and disk columns
The `qsnap list backups` flat table output SHALL include a `TARGET` column (the backup target path) and a `DISK` column from each backup's `SnapshotInfo.disk`.

#### Scenario: Flat backups table shows target and disk
- **WHEN** `qsnap list backups` is executed for a VM with two targets
- **THEN** the table columns are `VM`, `TARGET`, `DISK`, `NAME`, `PATH`, `TIMESTAMP`, `ALLOCATION`
- **AND** each row shows the target path the backup belongs to
