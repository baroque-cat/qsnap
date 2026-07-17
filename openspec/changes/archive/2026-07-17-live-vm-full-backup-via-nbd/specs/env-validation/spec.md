## MODIFIED Requirements

### Requirement: Pre-flight environment validation before pipeline

Core SHALL execute `_validate_environment(vm_config)` before `_execute_pipeline(vm_config)`. Validation SHALL verify: (a) stale `.tmp`, `.partial`, and NBD socket files are cleaned up per `auto_cleanup` config, (b) orphan `.qcow2` snapshots are detected (WARNING only), (c) `snapshot_dir` exists and is a writable directory, (d) `base_image` file exists, (e) `virsh` and `qemu-img` binaries are in PATH, (f) VM is defined in libvirt (`virsh dominfo` returns 0), (g) `rsync` is in PATH (hard requirement, pipeline aborts if missing). Failure on checks (c)-(f) SHALL return immediately — no partial pipeline execution. Cleanup steps (a)-(b) SHALL be skipped when `auto_cleanup = false`. Checks (a)-(b) SHALL NOT block pipeline execution — they are defensive, not critical.

In dry-run mode, `_validate_environment()` SHALL still be called. Validation failures SHALL be logged as WARNING (non-fatal) in dry-run mode. The pipeline SHALL NOT abort on validation failure in dry-run mode. In non-dry-run mode, validation failure SHALL raise `RuntimeError` and abort the pipeline as before.

#### Scenario: Cleanup and orphan detection execute before main checks
- **WHEN** `_validate_environment()` is called and `auto_cleanup = true`
- **THEN** stale file cleanup and orphan detection run first
- **AND** then directory/file/binary existence checks run

#### Scenario: Cleanup skipped when auto_cleanup is false
- **WHEN** `_validate_environment()` is called and `auto_cleanup = false`
- **THEN** stale file cleanup is skipped
- **AND** an INFO log states "auto_cleanup is disabled"
- **AND** the remaining existence checks proceed normally

#### Scenario: All validations pass
- **WHEN** `_validate_environment()` checks a properly configured VM
- **THEN** the method returns without error and pipeline execution continues

#### Scenario: snapshot_dir does not exist
- **WHEN** `snapshot_dir` path does not exist as a directory
- **THEN** validation returns with an error and pipeline does NOT proceed to change detection or snapshot creation

#### Scenario: virsh binary not in PATH
- **WHEN** `which virsh` returns non-zero
- **THEN** validation returns with an error message indicating `virsh` is required

#### Scenario: libvirt rejects dominfo — VM not defined
- **WHEN** `virsh dominfo --domain <name>` returns non-zero
- **THEN** validation returns with an error indicating the VM is not defined in libvirt

#### Scenario: Dry-run runs validation as non-fatal warnings
- **WHEN** `Core._execute_pipeline()` is called in dry-run mode
- **THEN** `_validate_environment()` is called (NOT skipped)
- **AND** if validation fails, the broken checks are logged as WARNING
- **AND** the pipeline does NOT abort (continues to dry-run snapshot/backup steps)
- **AND** the dry-run output includes the validation warnings

#### Scenario: Non-dry-run aborts on validation failure
- **WHEN** `Core._execute_pipeline()` is called in non-dry-run mode
- **AND** `_validate_environment()` returns `status="validation_failed"`
- **THEN** a `RuntimeError` is raised with the broken checks
- **AND** the pipeline does NOT proceed to snapshot or backup steps
