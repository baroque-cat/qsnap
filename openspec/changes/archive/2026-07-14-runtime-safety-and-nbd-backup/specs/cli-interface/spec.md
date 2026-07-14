## ADDED Requirements

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
