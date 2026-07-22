## MODIFIED Requirements

### Requirement: Pre-flight environment validation before pipeline

Core SHALL execute `_validate_environment(vm_config)` before `_execute_pipeline(vm_config)`. Validation SHALL verify: (a) stale `.tmp`, `.partial`, and NBD socket files are cleaned up per `auto_cleanup` config, (b) orphan `.qcow2` snapshots are detected (WARNING only), (c) `snapshot_dir` exists and is a writable directory, (d) `base_image` file exists, (e) `virsh` and `qemu-img` binaries are in PATH, (f) VM is defined in libvirt (`virsh dominfo` returns 0), (g) the Python `nbd` module (libnbd bindings) is importable (hard requirement, pipeline aborts if missing), (h) the `qemu-nbd` compress driver is available (hard requirement for compressed FULL backups). Failure on checks (c)-(h) SHALL return immediately — no partial pipeline execution. Cleanup steps (a)-(b) SHALL be skipped when `auto_cleanup = false`. Checks (a)-(b) SHALL NOT block pipeline execution — they are defensive, not critical.

In dry-run mode, `_validate_environment()` SHALL still be called. Validation failures SHALL be logged as WARNING (non-fatal) in dry-run mode. The pipeline SHALL NOT abort on validation failure in dry-run mode. In non-dry-run mode, validation failure SHALL raise `RuntimeError` and abort the pipeline as before.

#### Scenario: Compress driver available — validation passes

- **WHEN** `qemu-nbd --image-opts driver=compress` is supported
- **THEN** validation passes for the compress driver check

#### Scenario: Compress driver missing — hard failure

- **WHEN** `qemu-nbd` does not support the compress driver
- **THEN** validation fails with an error naming the missing driver
- **AND** the pipeline does NOT proceed (non-dry-run)

#### Scenario: Compress driver missing in dry-run — warning

- **WHEN** `qemu-nbd` does not support the compress driver and the pipeline runs in dry-run mode
- **THEN** the failure is logged as a WARNING
- **AND** the dry-run continues

#### Scenario: All validations pass

- **WHEN** `_validate_environment()` checks a properly configured VM
- **THEN** the method returns without error and pipeline execution continues

#### Scenario: snapshot_dir does not exist

- **WHEN** `snapshot_dir` path does not exist as a directory
- **THEN** validation returns with an error and pipeline does NOT proceed to change detection or snapshot creation

#### Scenario: libnbd missing — hard failure

- **WHEN** the `nbd` module is not importable
- **THEN** validation fails with an error naming `python3-libnbd`
- **AND** the pipeline does NOT proceed (non-dry-run)
- **AND** no fallback to any other transfer mechanism occurs
