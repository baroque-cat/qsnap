## MODIFIED Requirements

### Requirement: CLI entry point
The system SHALL provide a `qsnap` command-line entry point with subcommands `run`, `snapshot`, `backup`, `prune`, `list`, `stats`, `check`, `estimate`, `restore`, and `fork`. The `deploy` subcommand is REMOVED. The `list` subcommand SHALL support sub-subcommands: `snapshots`, `backups`, `config`, `latest`, and `deferred`. Each (sub-)subcommand SHALL map to a corresponding Core method. The CLI layer SHALL contain no business logic — it is a thin translation layer from CLI args to Core method calls to formatted output.

#### Scenario: Help text
- **WHEN** `qsnap --help` is executed
- **THEN** all subcommands and global flags are listed
- **AND** `deploy` is NOT listed

#### Scenario: Subcommand dispatch
- **WHEN** `qsnap run` is executed with a valid config
- **THEN** `Core.run()` is called with the parsed arguments

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

## REMOVED Requirements

### Requirement: qsnap deploy subcommand
**Reason**: `deploy` was a thin wrapper around `fork()`. With fork simplified, deploy is redundant.
**Migration**: Use `qsnap fork <backup_name> --output <path>` directly.

## ADDED Requirements

### Requirement: qsnap list backups --tree flag
The `list backups` subcommand SHALL accept a `--tree` flag. When present, the CLI SHALL display backup chains grouped by FULL anchor with indented hierarchy, showing parent-child relationships in the backing chain. Each FULL backup is displayed at the top level, with its dependent incrementals indented beneath.

#### Scenario: Tree output for backup chains
- **WHEN** `qsnap list backups myvm --tree` is executed on a VM with 2 FULL chains
- **THEN** output shows each FULL at the top level with its incrementals indented beneath
- **AND** the hierarchy reflects actual backing chain relationships (not just timestamp order)

#### Scenario: Tree output for orphan backups
- **WHEN** `qsnap list backups myvm --tree` is executed and orphan backups exist (no FULL anchor)
- **THEN** orphans are displayed under a `(orphan)` header
