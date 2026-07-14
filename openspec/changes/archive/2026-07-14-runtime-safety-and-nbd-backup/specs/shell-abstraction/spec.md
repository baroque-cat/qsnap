## ADDED Requirements

### Requirement: IShell.run() optional check parameter
`IShell.run()` SHALL accept an optional `check: bool = False` parameter. When `True`, the method SHALL return `ShellResult` without logging command failure as an ERROR (useful for pre-flight checks where command failure is expected and not an error condition). The command SHALL still be logged at DEBUG level.

#### Scenario: Check mode does not log error on failure
- **WHEN** `shell.run(["virsh", "dominfo", "--domain", "nonexistent"], timeout=30, check=True)` is called and virsh returns non-zero
- **THEN** the result is logged at DEBUG level, not ERROR
- **THEN** `ShellResult(success=False, ...)` is still returned
