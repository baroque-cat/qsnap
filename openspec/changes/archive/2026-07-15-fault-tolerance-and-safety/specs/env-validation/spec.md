## MODIFIED Requirements

### Requirement: Pre-flight environment validation before pipeline
Core SHALL execute `_validate_environment(vm_config)` before `_execute_pipeline(vm_config)`. Validation SHALL verify: (a) stale `.tmp`, `.partial`, and NBD socket files are cleaned up per `auto_cleanup` config, (b) orphan `.qcow2` snapshots are detected (WARNING only), (c) `snapshot_dir` exists and is a writable directory, (d) `base_image` file exists, (e) `virsh` and `qemu-img` binaries are in PATH, (f) VM is defined in libvirt (`virsh dominfo` returns 0), (g) if any target has `rate_limit != "no"`, `rsync` is in PATH (WARNING on missing, non-blocking). Failure on checks (c)-(f) SHALL return immediately — no partial pipeline execution. Cleanup steps (a)-(b) SHALL be skipped when `auto_cleanup = false`. Checks (a)-(b) SHALL NOT block pipeline execution — they are defensive, not critical.

#### Scenario: Cleanup and orphan detection execute before main checks
- **WHEN** `_validate_environment()` is called and `auto_cleanup = true`
- **THEN** stale file cleanup and orphan detection run first
- **AND** then directory/file/binary existence checks run

#### Scenario: Cleanup skipped when auto_cleanup is false
- **WHEN** `_validate_environment()` is called and `auto_cleanup = false`
- **THEN** stale file cleanup is skipped
- **AND** an INFO log states "auto_cleanup is disabled"
- **AND** the remaining existence checks proceed normally
