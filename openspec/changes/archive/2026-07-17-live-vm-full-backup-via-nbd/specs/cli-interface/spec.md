## MODIFIED Requirements

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
