## ADDED Requirements

### Requirement: PKGBUILD for Arch Linux

The project SHALL include a `PKGBUILD` file in the repository root for Arch Linux packaging. The PKGBUILD SHALL install qsnap to the system Python site-packages (not a venv), making system-installed `libnbd` bindings discoverable on `sys.path`. The `pkgver` SHALL match the `version` field in `pyproject.toml`. The `pkgrel` SHALL start at `1` and increment on re-builds without version changes.

#### Scenario: PKGBUILD installs to system Python

- **WHEN** `makepkg -si` is run in the repository root
- **THEN** qsnap is installed to `/usr/lib/python3.x/site-packages/qsnap/`
- **AND** the `qsnap` entry point is available at `/usr/bin/qsnap`
- **AND** `importlib.metadata.version("qsnap")` returns the installed version

#### Scenario: pkgver matches pyproject.toml

- **WHEN** the PKGBUILD is examined
- **THEN** `pkgver` equals the `version` field in `pyproject.toml`
- **AND** `pkgrel` is `1` for the initial build of a given version

### Requirement: System dependency declaration in PKGBUILD

The PKGBUILD SHALL declare runtime dependencies via the `depends` array: `python>=3.11`, `libnbd` (system package providing `python3-libnbd` bindings), `libvirt` (providing `virsh`), and `qemu-utils` (providing `qemu-img` and `qemu-nbd`). The `makedepends` SHALL include `python-poetry` and `git`.

#### Scenario: All runtime dependencies declared

- **WHEN** the PKGBUILD `depends` array is examined
- **THEN** it includes `python>=3.11`, `libnbd`, `libvirt`, and `qemu-utils`
- **AND** `makedepends` includes `python-poetry` and `git`

#### Scenario: libnbd on sys.path after installation

- **WHEN** qsnap is installed via `makepkg -si` and `python3 -c "import nbd; print(nbd.NBD)"` is run
- **THEN** the import succeeds and `nbd.NBD` is accessible
- **AND** `is_libnbd_available()` returns `True`

### Requirement: Systemd unit installation via PKGBUILD

The PKGBUILD SHALL install systemd unit files from the `systemd/` directory to `/usr/lib/systemd/system/`. The installed units SHALL include `qsnap.service`, `qsnap.timer`, `qsnap-check.service`, and `qsnap-check.timer`. The `ExecStart` in each service file SHALL reference `/usr/bin/qsnap` (matching the `[project.scripts]` entry point in `pyproject.toml`).

#### Scenario: Systemd units installed to correct path

- **WHEN** the package is installed via `makepkg -si`
- **THEN** `/usr/lib/systemd/system/qsnap.service` exists
- **AND** `/usr/lib/systemd/system/qsnap.timer` exists
- **AND** `/usr/lib/systemd/system/qsnap-check.service` exists
- **AND** `/usr/lib/systemd/system/qsnap-check.timer` exists

### Requirement: Config example and state directory via PKGBUILD

The PKGBUILD SHALL install `qsnap.toml.example` to `/etc/qsnap/qsnap.toml.example` and create the state directory `/var/lib/qsnap/state/` with mode `755`.

#### Scenario: Config example installed

- **WHEN** the package is installed
- **THEN** `/etc/qsnap/qsnap.toml.example` exists and is readable

#### Scenario: State directory created

- **WHEN** the package is installed
- **THEN** `/var/lib/qsnap/state/` exists and is writable by the qsnap process
