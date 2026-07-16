## ADDED Requirements

### Requirement: qsnap restore subcommand
The CLI SHALL provide a `restore` subcommand with arguments: `SNAPSHOT_NAME` (positional, required), `TARGET_DIR` (positional, required), and optional `VM` filter. It SHALL map to `Core.restore(snapshot_name, target_dir, vm_filter)`.

#### Scenario: Restore command invocation
- **WHEN** `qsnap restore debiantest.20250101T1200 /restore` is executed
- **THEN** `Core.restore("debiantest.20250101T1200", Path("/restore"))` is called

### Requirement: qsnap check --deep flag
The `check` subcommand SHALL accept a `--deep` flag. When present, `Core.check(deep=True)` is called, which SHALL execute `qemu-img check` on each snapshot and backup file.

#### Scenario: Deep check invocation
- **WHEN** `qsnap check --deep` is executed
- **THEN** `Core.check(vm_filter=None, deep=True)` is called
- **THEN** output includes corruption status for each file

#### Scenario: Default check without --deep
- **WHEN** `qsnap check` is executed without `--deep`
- **THEN** `Core.check(deep=False)` is called (backing-file existence only)
