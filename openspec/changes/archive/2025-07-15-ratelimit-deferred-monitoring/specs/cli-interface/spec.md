## ADDED Requirements

### Requirement: CLI `list deferred` subcommand

The system SHALL provide a `qsnap list deferred [vm...]` subcommand. It SHALL accept optional VM name arguments as filters. It SHALL dispatch to `Core.list_deferred(vm_filter)` and format the result as a table or raw output. See `specs/deferred-monitoring/spec.md` for full semantics.

#### Scenario: list deferred dispatches to Core

- **WHEN** `qsnap list deferred` is executed
- **THEN** `Core.list_deferred()` is called with no filter

#### Scenario: list deferred with VM filter dispatches to Core

- **WHEN** `qsnap list deferred vm-home debiantest` is executed
- **THEN** `Core.list_deferred()` is called with the matching filter

#### Scenario: list deferred with --format raw

- **WHEN** `qsnap list deferred --format raw` is executed
- **THEN** output is in `key=value` format

## MODIFIED Requirements

### Requirement: CLI entry point

The system SHALL provide a `qsnap` command-line entry point with subcommands `run`, `snapshot`, `backup`, `prune`, `list`, `stats`, `check`, and `restore`. The `list` subcommand SHALL support sub-subcommands: `snapshots`, `backups`, `config`, `latest`, and `deferred`. Each (sub-)subcommand SHALL map to a corresponding Core method. The CLI layer SHALL contain no business logic.

#### Scenario: Help text

- **WHEN** `qsnap --help` is executed
- **THEN** all subcommands and global flags are listed

#### Scenario: Subcommand dispatch

- **WHEN** `qsnap run` is executed with a valid config
- **THEN** `Core.run()` is called with the parsed arguments

#### Scenario: list deferred sub-subcommand

- **WHEN** `qsnap list deferred` is executed
- **THEN** `Core.list_deferred()` is called
