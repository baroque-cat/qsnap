# CLI Interface — Delta Spec

## MODIFIED Requirements

### Requirement: Global flag --dry-run / -n
The system SHALL accept a `--dry-run` / `-n` flag that sets `Core.dry_run = True`. In dry-run mode, snapshot creation, blockcommit, file copy, file deletion, deferred-drain execution, and state writes SHALL NOT be executed. Planned actions SHALL be logged at INFO level, per VM and per disk, with an approximate size estimate wherever one can be computed read-only (capability `dry-run-prediction`). Read-only shell commands (`qemu-img info`, `virsh domstate` / `virsh dominfo`, `test`, `which`, `find`, `du`) remain permitted in dry-run; every shell command issued SHALL be read-only.

In dry-run mode, environment validation (`_validate_environment()`) SHALL still be executed. Validation failures SHALL be logged as WARNING (non-fatal).

In dry-run mode, the per-disk FULL backup creation decision SHALL still be evaluated. The dry-run output SHALL log, for each disk that would get a FULL, the disk target, the transfer method, the VM running state, and the estimated standalone size. The dry-run output SHALL also log each incremental snapshot that would be transferred, with its name and an approximate size.

#### Scenario: Dry-run logs actions without executing
- **WHEN** `qsnap -n run` is executed
- **THEN** planned snapshot names are logged per disk, but no mutating `virsh` or `qemu-img` command is executed
- **AND** every shell command issued during the run is read-only

#### Scenario: Dry-run runs environment validation
- **WHEN** `qsnap -n run` is executed
- **THEN** `_validate_environment()` is called for each VM
- **AND** if validation fails, the broken checks are logged as WARNING
- **AND** the dry-run does NOT abort

#### Scenario: Dry-run logs per-disk FULL prediction with size
- **WHEN** `qsnap -n run` is executed
- **AND** the per-disk FULL decision determines that disk `vda` needs a new FULL
- **THEN** an INFO log names the disk, the transfer method, the VM running state, and the estimated size of the FULL

#### Scenario: Dry-run logs incremental transfer predictions
- **WHEN** `qsnap -n run` is executed with snapshots that are missing on a target
- **THEN** each missing snapshot is logged with its name, target, disk, and an approximate size
- **AND** no NBD export or file write occurs
