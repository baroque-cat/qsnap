# State Recovery — Delta

## ADDED Requirements

### Requirement: State write survives ENOSPC without crashing the process

`JsonStateManager._save()` SHALL wrap the write-and-replace sequence (temp file write,
`os.replace`, rotation) in an `OSError` handler. On `OSError` (including ENOSPC in the
state directory), `_save()` SHALL log a CRITICAL message naming the state file path and
the OS error, then propagate the failure as a `RuntimeError` so that the per-VM
`try/except` in `Core._run_pipeline()` contains it to the current VM. The process SHALL
NOT terminate; remaining VMs SHALL be processed. The handler SHALL NOT delete, rename,
or truncate any state file or backup copy — recovery of a failed write is the operator's
decision. Load-path recovery (corrupt-file rename) and rotation behavior remain
unchanged.

#### Scenario: ENOSPC during save contained to one VM

- **WHEN** `_save("vm1")` raises `OSError: [Errno 28] No space left on device` during
  `os.replace`
- **THEN** a CRITICAL log names the state path and errno
- **AND** the current VM's pipeline step fails via the raised `RuntimeError`
- **AND** `Core._run_pipeline()` records `VMRunResult(success=False)` for "vm1"
- **AND** "vm2" and later VMs are still processed

#### Scenario: Partial temp file does not corrupt existing state

- **WHEN** the temp-file write fails before `os.replace`
- **THEN** the existing `{vm}.json` remains untouched
- **AND** no rotation has been performed for this failed save

#### Scenario: Successful save behavior unchanged

- **WHEN** `_save()` completes without OS errors
- **THEN** atomic `.tmp` + `os.replace` and rotation behave exactly as specified by the
  existing rotation requirement
