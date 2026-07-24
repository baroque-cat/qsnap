## MODIFIED Requirements

### Requirement: connect-retry in LibnbdClient

`LibnbdClient.connect()` SHALL retry up to 20 times with a 1-second sleep between attempts. On each failed attempt, a fresh `nbd.NBD()` handle SHALL be created (the old handle from the failed attempt is discarded). This handles the race between `virsh backup-begin` (which starts the NBD server asynchronously) and the client connect. Before attempting the import, `connect()` SHALL call `_ensure_system_site_packages()` to make system-installed libnbd bindings discoverable when running inside a venv. After importing `nbd`, `connect()` SHALL verify that the module has `Error` and `NBD` attributes — if not, it SHALL return `NbdResult(success=False, ...)` with an actionable error message indicating the wrong package is installed. `connect()` SHALL catch `AttributeError` raised by missing attributes on the imported module and return `NbdResult` instead of propagating the exception.

#### Scenario: NBD server not ready on first attempt

- **WHEN** `virsh backup-begin` has been called but the NBD server is not yet listening
- **THEN** `connect()` retries up to 20 times with 1-second sleep
- **AND** a fresh `nbd.NBD()` handle is created on each retry
- **AND** on success, the connection is established

#### Scenario: NBD server never starts

- **WHEN** the NBD server never becomes available after 20 retries
- **THEN** `connect()` returns `NbdResult(success=False, error="...")` with a timeout message

#### Scenario: PyPI nbd imposter installed

- **WHEN** the PyPI `nbd` package (Jupyter notebook diffing tool) is installed instead of system `python3-libnbd`
- **AND** `connect()` successfully imports `nbd` but `hasattr(nbd, "NBD")` returns `False`
- **THEN** `connect()` returns `NbdResult(success=False, ...)` with an error message instructing to uninstall the PyPI package and install the system package
- **AND** no `AttributeError` propagates to the caller

#### Scenario: System site-packages discovered in venv

- **WHEN** `connect()` is called while running inside a venv (VIRTUAL_ENV is set or sys.prefix != sys.base_prefix)
- **THEN** `_ensure_system_site_packages()` appends system site-packages paths to `sys.path` before the import attempt
- **AND** the system `libnbd` bindings become importable

## ADDED Requirements

### Requirement: libnbd module attribute verification

`is_libnbd_available()` SHALL verify that the `nbd` module has the required libnbd attributes (`Error` and `NBD`) after import, not just check module existence via `find_spec`. This prevents false positives from the unrelated PyPI `nbd` package (Jupyter notebook diffing tool) which imports as `import nbd` but lacks `nbd.Error` and `nbd.NBD`. The function SHALL call `_ensure_system_site_packages()` before the import attempt to make system bindings discoverable in venv environments.

#### Scenario: System libnbd installed — returns True

- **WHEN** system `python3-libnbd` is installed and `is_libnbd_available()` is called
- **THEN** `find_spec("nbd")` returns non-None
- **AND** `import nbd` succeeds
- **AND** `hasattr(nbd, "Error")` returns `True`
- **AND** `hasattr(nbd, "NBD")` returns `True`
- **AND** the function returns `True`

#### Scenario: PyPI nbd imposter — returns False

- **WHEN** the PyPI `nbd` package is installed (no `nbd.Error` or `nbd.NBD` attributes)
- **AND** `is_libnbd_available()` is called
- **THEN** `find_spec("nbd")` returns non-None
- **AND** `import nbd` succeeds
- **AND** `hasattr(nbd, "Error")` returns `False` or `hasattr(nbd, "NBD")` returns `False`
- **AND** the function returns `False`

#### Scenario: No nbd module at all — returns False

- **WHEN** neither system libnbd nor PyPI nbd is installed
- **AND** `is_libnbd_available()` is called
- **THEN** `find_spec("nbd")` returns `None`
- **AND** the function returns `False` without attempting an import

#### Scenario: Venv discovers system libnbd

- **WHEN** qsnap runs in a venv without `--system-site-packages`
- **AND** system `libnbd` is installed at `/usr/lib/python3.x/site-packages/`
- **AND** `is_libnbd_available()` is called
- **THEN** `_ensure_system_site_packages()` appends the system path to `sys.path`
- **AND** `find_spec("nbd")` returns non-None
- **AND** the function returns `True`

### Requirement: MISSING_LIBNBD_ERROR multi-distro message

The `MISSING_LIBNBD_ERROR` constant SHALL include install instructions for multiple distributions (Arch, Debian, Fedora) and SHALL explicitly warn against `pip install nbd` (the unrelated PyPI package).

#### Scenario: Error message includes Arch instructions

- **WHEN** `MISSING_LIBNBD_ERROR` is displayed
- **THEN** the message includes `pacman -S libnbd` for Arch Linux

#### Scenario: Error message warns about PyPI imposter

- **WHEN** `MISSING_LIBNBD_ERROR` is displayed
- **THEN** the message includes a warning that `pip install nbd` installs an unrelated package
