## ADDED Requirements

### Requirement: Tree-format snapshot listing
The `list snapshots` subcommand SHALL accept a `--tree` flag. When present, the CLI SHALL display the backing chain as a tree rather than a flat table. The tree SHALL show parent-child relationships in the backing chain, indented for visual hierarchy.

#### Scenario: Tree output for a 3-level backing chain
- **WHEN** `qsnap list snapshots --tree` is executed on a VM with base ← snap1 ← snap2
- **THEN** output shows snap2 indented under snap1, snap1 indented under base

#### Scenario: Flat output without --tree
- **WHEN** `qsnap list snapshots` is executed without `--tree`
- **THEN** output is the standard flat table format

### Requirement: Global --long / -L flag
The CLI SHALL accept a global `--long` / `-L` flag as a shortcut for `--format long`. All subcommands that support `--format` SHALL accept `-L`.

#### Scenario: -L with list command
- **WHEN** `qsnap -L list snapshots` is executed
- **THEN** output is in long format (all available columns displayed)
