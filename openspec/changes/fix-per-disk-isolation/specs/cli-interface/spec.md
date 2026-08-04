## MODIFIED Requirements

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
