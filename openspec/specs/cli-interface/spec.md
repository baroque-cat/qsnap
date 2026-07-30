## Requirements

### Requirement: CLI entry point
The system SHALL provide a `qsnap` command-line entry point with subcommands `run`, `snapshot`, `backup`, `prune`, `list`, `stats`, `check`, `estimate`, `restore`, and `fork`. The `deploy` subcommand is REMOVED. The `list` subcommand SHALL support sub-subcommands: `snapshots`, `backups`, `config`, `latest`, and `deferred`. Each (sub-)subcommand SHALL map to a corresponding Core method. The CLI layer SHALL contain no business logic — it is a thin translation layer from CLI args to Core method calls to formatted output.

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

In dry-run mode, environment validation (`_validate_environment()`) SHALL still be executed. Validation failures SHALL be logged as WARNING (non-fatal) — the dry-run SHALL NOT abort on validation failure. This ensures operators see permission issues, missing VMs, and missing directories during dry-run, not during the real run.

In dry-run mode, the FULL backup creation decision (`_should_create_bucket_full()`) SHALL still be evaluated. The dry-run output SHALL log whether a FULL backup WOULD be created and via which transfer method (NBD for running VM, direct convert for stopped VM). The actual `create_full_backup()` call SHALL NOT be executed.

#### Scenario: Dry-run logs actions without executing
- **WHEN** `qsnap -n run` is executed
- **THEN** planned snapshot names are logged, but no `virsh snapshot-create-as` or `qemu-img` commands are executed

#### Scenario: Dry-run runs environment validation
- **WHEN** `qsnap -n run` is executed
- **THEN** `_validate_environment()` is called for each VM
- **AND** if validation fails, the broken checks are logged as WARNING
- **AND** the dry-run does NOT abort (continues to log planned actions)

#### Scenario: Dry-run logs FULL-would-be-created
- **WHEN** `qsnap -n run` is executed
- **AND** `_should_create_bucket_full()` returns `(True, "weekly")` for a target
- **THEN** an INFO log is emitted: "[dry-run] Would create FULL backup (bucket=weekly, method=NBD, VM=running)"
- **OR** "[dry-run] Would create FULL backup (bucket=weekly, method=direct convert, VM=stopped)"
- **AND** `provider.create_full_backup()` is NOT called
- **AND** no `virsh backup-begin` or `qemu-img convert` is executed

#### Scenario: Dry-run detects VM running state for method selection
- **WHEN** dry-run evaluates FULL creation for a running VM
- **THEN** the log indicates `method=NBD` (because VM is running)
- **WHEN** dry-run evaluates FULL creation for a stopped VM
- **THEN** the log indicates `method=direct convert` (because VM is stopped)

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
The CLI layer (commands.py) SHALL NOT parse config, create snapshots, evaluate retention, or perform any business logic. It SHALL only translate CLI args into Core method calls and format the returned results. The summary formatting is handled by `qsnap/cli/summary.py` as a pure function, invoked from `_format_pipeline_result()` after exit code computation.

#### Scenario: No business logic in CLI
- **WHEN** reviewing `qsnap/cli/commands.py`
- **THEN** it contains no imports from `qsnap.modules`, `qsnap.config`, `qsnap.retention`, or `qsnap.state`
- **AND** `format_summary()` is called from `qsnap/cli/summary.py` as a pure function

### Requirement: Summary output after run command

The `handle_run()` function in `qsnap/cli/commands.py` SHALL, after computing the exit code via `_format_pipeline_result()`, call `format_summary(result)` from `qsnap/cli/summary.py` and print the result to stdout. The summary formatter SHALL be a pure function — no business logic in CLI.

#### Scenario: Summary printed after successful run
- **WHEN** `qsnap run` completes successfully
- **THEN** the return code is computed by `_format_pipeline_result()`
- **AND** `format_summary(result)` is called with the `PipelineResult`
- **AND** the formatted summary is printed to stdout

#### Scenario: Summary printed after run with backup failures
- **WHEN** `qsnap run` completes with exit code 10 (backup abort)
- **THEN** the summary table is still printed to stdout
- **AND** failed transfers are marked with `!!!` in the summary

#### Scenario: Summary printed after dry-run
- **WHEN** `qsnap -n run` completes
- **THEN** the summary table is printed with `Dryrun: YES` header
- **AND** the dry-run disclaimer footer is printed

### Requirement: CLI thin-layer constraint for summary

The CLI layer SHALL NOT parse `PipelineResult.actions` to compute any business logic. The `format_summary()` function SHALL only translate the `PipelineResult` data structure into a formatted string. It SHALL NOT access `IStateManager`, `IConfigFacade`, or any module.

#### Scenario: No business logic in summary formatter
- **WHEN** reviewing `qsnap/cli/summary.py`
- **THEN** it contains no imports from `qsnap.modules`, `qsnap.config`, `qsnap.retention`, or `qsnap.state`
- **AND** it contains only `from qsnap.models.results import PipelineResult, ActionRecord`

### Requirement: qsnap restore subcommand
The CLI SHALL provide a `restore` subcommand with arguments: `SNAPSHOT_NAME` (positional, required), optional `VM` filter, `--dry-run` flag, and `--yes` flag. The `TARGET_DIR` positional argument is REMOVED. It SHALL map to `Core.restore(snapshot_name, vm_filter)`.

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
`qsnap list config` SHALL display per-VM safety column: `blockcommit_deep_verify` (ON/OFF). Global safety settings (`auto_cleanup`, `chain_verify_before_commit`, `chain_verify_after_commit`, `deep_check_schedule`) SHALL be shown in a header or summary section.

#### Scenario: list config shows OFF for default deep verify
- **WHEN** `qsnap list config` is executed and no VM has deep verify enabled
- **THEN** each VM shows `blockcommit_deep_verify: OFF`

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

### Requirement: qsnap estimate subcommand
The CLI SHALL provide an `estimate` subcommand with an optional `VM` positional filter argument. It SHALL map to `Core.schedule_summary()` with size estimation enabled. It SHALL NOT execute any pipeline actions. Output SHALL include per-VM and per-target size projections.

#### Scenario: Estimate for specific VM
- **WHEN** `qsnap estimate myvm` is executed
- **THEN** a size projection is printed to stdout for that VM only
- **AND** no pipeline actions are executed

#### Scenario: Estimate for all VMs
- **WHEN** `qsnap estimate` is executed without a VM argument
- **THEN** size projections for all configured VMs are printed
- **AND** no pipeline actions are executed

#### Scenario: Estimate respects --format flag
- **WHEN** `qsnap estimate --format raw` is executed
- **THEN** output is in `key=value` format for machine consumption

### Requirement: qsnap fork subcommand
The system SHALL provide a `qsnap fork` subcommand accepting positional argument `SNAPSHOT_NAME` (or backup name), a required `--output <path>` flag specifying the output file path, and an optional VM filter for snapshot resolution. It SHALL call `Core.fork(name, output_path, vm_filter)` and output the result. The `--as-vm`, `--storage`, and `--add-to-config` flags are REMOVED.

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

### Requirement: qsnap list backups --tree flag
The `list backups` subcommand SHALL accept a `--tree` flag. When present, the CLI SHALL display backup chains grouped by FULL anchor with indented hierarchy, showing parent-child relationships in the backing chain. Each FULL backup is displayed at the top level, with its dependent incrementals indented beneath.

#### Scenario: Tree output for backup chains
- **WHEN** `qsnap list backups myvm --tree` is executed on a VM with 2 FULL chains
- **THEN** output shows each FULL at the top level with its incrementals indented beneath
- **AND** the hierarchy reflects actual backing chain relationships (not just timestamp order)

#### Scenario: Tree output for orphan backups
- **WHEN** `qsnap list backups myvm --tree` is executed and orphan backups exist (no FULL anchor)
- **THEN** orphans are displayed under a `(orphan)` header

### Requirement: Reconcile CLI subcommand

The CLI SHALL provide a `reconcile` subcommand that dispatches to `Core.reconcile()`. The subcommand SHALL accept an optional VM name filter positional argument, a `--dry-run` flag, and a `--format` flag (table/long/raw).

#### Scenario: Reconcile command dispatches to Core

- **WHEN** `qsnap reconcile` is invoked
- **THEN** the CLI handler SHALL call `core.reconcile(vm_filter)` and format the results as a table

#### Scenario: Reconcile with dry-run

- **WHEN** `qsnap reconcile --dry-run` is invoked
- **THEN** the CLI handler SHALL set `core.dry_run = True` before calling `core.reconcile()`
- **AND** the output SHALL indicate what would be fixed without making changes

#### Scenario: Reconcile with VM filter

- **WHEN** `qsnap reconcile myvm` is invoked
- **THEN** the CLI handler SHALL pass `myvm` as the VM filter to `core.reconcile()`

#### Scenario: Reconcile exit code

- **WHEN** `qsnap reconcile` completes
- **THEN** the exit code SHALL be 0 if no errors occurred, or 1 if any VM had errors in its `ReconcileResult`

#### Scenario: Reconcile in dispatch map

- **WHEN** the CLI dispatch map is constructed
- **THEN** `"reconcile"` SHALL map to `commands.handle_reconcile`
