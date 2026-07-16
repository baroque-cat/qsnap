# cli-interface — Delta Spec

## ADDED Requirements

### Requirement: qsnap fork subcommand
The system SHALL provide a `qsnap fork` subcommand accepting positional argument `SNAPSHOT_NAME` and flags `--as-vm <name>` (required), `--storage <dir>` (default: `/var/lib/libvirt/images`), `--add-to-config` (optional), and an optional VM filter for snapshot resolution. It SHALL call `Core.fork(snapshot_name, as_vm, storage, add_to_config, vm_filter)` and output the result.

#### Scenario: Fork command succeeds
- **WHEN** `qsnap fork myvm.20260701T1200 --as-vm myvm-clone` is executed
- **THEN** `Core.fork("myvm.20260701T1200", "myvm-clone", Path("/var/lib/libvirt/images"), add_to_config=False, vm_filter=None)` is called
- **THEN** exit code is 0

#### Scenario: Fork command fails on missing snapshot
- **WHEN** `qsnap fork nonexistent --as-vm test` is executed
- **THEN** exit code is 1

#### Scenario: Fork with --add-to-config
- **WHEN** `qsnap fork myvm.20260701T1200 --as-vm myvm-clone --add-to-config` is executed
- **THEN** `Core.fork(..., add_to_config=True)` is called

### Requirement: qsnap deploy subcommand
The system SHALL provide a `qsnap deploy` subcommand accepting positional argument `BACKUP_NAME` and flags `--as-vm <name>` (required), `--storage <dir>` (default: `/var/lib/libvirt/images`), `--add-to-config` (optional). It SHALL call `Core.deploy(backup_name, as_vm, storage, add_to_config)` and output the result.

#### Scenario: Deploy command succeeds
- **WHEN** `qsnap deploy vm.FULL.20260701.monthly --as-vm recovered-vm` is executed
- **THEN** `Core.deploy("vm.FULL.20260701.monthly", "recovered-vm", Path("/var/lib/libvirt/images"), add_to_config=False)` is called
- **THEN** exit code is 0

#### Scenario: Deploy with --storage and --add-to-config
- **WHEN** `qsnap deploy backup.20260715T1200 --as-vm recovered-vm --storage /mnt/vms --add-to-config` is executed
- **THEN** `Core.deploy(..., storage_dir=Path("/mnt/vms"), add_to_config=True)` is called
