## MODIFIED Requirements

### Requirement: check=True for probing shell.run() calls

All `shell.run()` calls that are probing or testing (where command failure is expected and handled by the caller in conditional logic) SHALL use `check=True` to avoid misleading ERROR-level logs. The criterion is: if the calling code handles the failure result in a conditional branch (if/else, try/except) rather than treating it as an unexpected error, the call is a probe and SHALL use `check=True`.

The following call sites SHALL be audited and updated to `check=True` where applicable:
- `utils/nbd.py`: `is_vm_running()`, `is_libvirt_new_enough()`, `get_disk_targets()`
- `modules/snapshot/external.py`: `virsh domblklist`, `qemu-img info --backing-chain`
- `modules/change/allocation_detector.py`: `qemu-img info`
- `modules/change/map_detector.py`: `qemu-img map`
- `modules/backup/bitmap.py`: `qemu-img info`, `test -f` existence checks
- `core/__init__.py`: `qemu-img info`, `qemu-img check`, `virsh domstate` (outside `_validate_environment`)
- `utils/verification.py`: `qemu-img info`, `qemu-img check`, `qemu-img compare`
- `modules/lifecycle/blockcommit_manager.py`: `virsh domblklist`, `qemu-img check`
- `utils/space.py`: `estimate_full_size()`, `estimate_incremental_size()` (size-estimation probes — failure is expected and handled by returning `None`)

#### Scenario: Probing call with check=True logs at DEBUG on failure

- **WHEN** `shell.run(["virsh", "dominfo", "--domain", "nonexistent"], timeout=30, check=True)` is called and virsh returns non-zero
- **THEN** the failure is logged at DEBUG level, not ERROR
- **AND** `ShellResult(success=False, ...)` is returned

#### Scenario: Compress driver probe uses check=True

- **WHEN** `_validate_environment()` runs `qemu-nbd --image-opts driver=compress` probe
- **THEN** `shell.run()` is called with `check=True`
- **AND** the expected non-zero exit is logged at DEBUG level, not ERROR

#### Scenario: Size-estimation probes use check=True

- **WHEN** `estimate_full_size()` or `estimate_incremental_size()` runs `qemu-img info` and the command fails
- **THEN** `shell.run()` was called with `check=True`
- **AND** the failure is logged at DEBUG level, not ERROR
- **AND** the function returns `None`
