## Why

The NBD transport layer has three reliability gaps discovered through real-world deployment: (1) `is_libnbd_available()` only checks module existence via `find_spec` — it cannot distinguish the system `python3-libnbd` bindings from the unrelated PyPI `nbd` package (Jupyter notebook diffing tool), causing unhandled `AttributeError` crashes at backup time; (2) ~30+ probing `shell.run()` calls across the codebase use `check=False` (default), producing misleading ERROR-level logs for expected command failures; (3) there is no per-target change detection — every snapshot is always transferred to every target, even when the VM disk has not changed since the last backup to that target. Additionally, qsnap has no packaging strategy — PEP 668 prevents `pip install` to system Python, and `libnbd` is a system package not on PyPI, making venv-based installations unable to find the NBD bindings.

## What Changes

- **NBD import hardening**: `is_libnbd_available()` now verifies `nbd.Error` and `nbd.NBD` attributes after import, not just `find_spec`. `LibnbdClient.connect()` catches `AttributeError` and returns `NbdResult` instead of crashing. `MISSING_LIBNBD_ERROR` updated with multi-distro install instructions and PyPI imposter warning.
- **Runtime venv fallback**: New `_ensure_system_site_packages()` function appends system site-packages to `sys.path` when running inside a venv, making `libnbd` bindings discoverable regardless of installation method.
- **Log level audit**: Probe command in `_validate_environment()` (`qemu-nbd --image-opts driver=compress`) changed from `check=False` to `check=True`. Systematic audit of ~30+ probing `shell.run()` calls across the codebase to use `check=True` where failure is expected and handled.
- **Per-target backup change detection**: New `backup_create` config field (`"always"` | `"onchange"`) on `TargetConfig` and `GlobalConfig`. When `"onchange"`, Core compares the latest snapshot's allocation against a per-target baseline stored in `IStateManager` — if unchanged, the backup transfer is skipped entirely.
- **IStateManager schema extension** **BREAKING**: Two new abstract methods on `IStateManager`: `get_last_backup_allocation(target_path)` and `set_last_backup_allocation(target_path, alloc)`. `JsonStateManager` persists per-target state in a new `_target_state.json` file. All implementations (production + mocks) must implement the new methods.
- **Arch Linux packaging**: New `PKGBUILD` installs qsnap to system Python (where `libnbd` is on `sys.path`), declares `depends=('libnbd' 'libvirt' 'qemu-utils')`, installs systemd units, config example, and state directory.
- **Documentation update**: README and `openspec/config.yaml` updated to reflect NBD-only transport (no rsync/file-copy), `libnbd` as hard runtime dependency, and Arch packaging instructions.

## Capabilities

### New Capabilities

- `arch-packaging`: PKGBUILD for Arch Linux — system Python installation, dependency declaration, systemd unit installation, config example, state directory creation

### Modified Capabilities

- `nbd-bitmap-backup`: `is_libnbd_available()` verifies module attributes (`nbd.Error`, `nbd.NBD`); `LibnbdClient.connect()` catches `AttributeError` and returns `NbdResult`; `_ensure_system_site_packages()` appends system site-packages to `sys.path` in venv; `MISSING_LIBNBD_ERROR` updated with multi-distro instructions and PyPI imposter warning
- `env-validation`: compress driver probe uses `check=True` (expected failure logged at DEBUG, not ERROR); `is_libnbd_available()` now called after `_ensure_system_site_packages()`; libnbd check verifies attributes, not just `find_spec`
- `state-management`: **BREAKING** — `IStateManager` gains `get_last_backup_allocation(target_path)` and `set_last_backup_allocation(target_path, alloc)`; `JsonStateManager` persists per-target state in `_target_state.json`
- `config-model`: `TargetConfig` gains `backup_create: str = "always"` field; `GlobalConfig` gains `backup_create: str = "always"` field
- `config-parsing`: `ConfigFacade` resolves `backup_create` inheritance (global → VM → target) with validation against `{"always", "onchange"}`
- `core-orchestrator`: `_backup_target()` gains per-target onchange gate before transfer; new `_should_backup_onchange()` private method; baseline updated after successful transfer
- `shell-abstraction`: clarifies that probing/testing `shell.run()` calls must use `check=True` to avoid misleading ERROR-level logs for expected failures

## Impact

- **ABC interfaces**: `IStateManager` gains 2 new abstract methods (**BREAKING** — `JsonStateManager`, `InMemoryStateManager`, `MockStateManager` must implement them)
- **Config dataclasses**: `TargetConfig` and `GlobalConfig` gain `backup_create` field (non-breaking — has default)
- **Core pipeline**: `_backup_target()` gains a pre-transfer gate (no ordering change — gate is before existing logic)
- **State files**: New `_target_state.json` file alongside existing `_full_backups.json` and `_dependencies.json`
- **External dependencies**: `libnbd` (system package) now declared in PKGBUILD `depends`; `pyproject.toml` `dependencies` remains `[]` (libnbd is not a PyPI package)
- **Packaging**: New `PKGBUILD` file in repo root; systemd units installed to `/usr/lib/systemd/system/`
- **Documentation**: README and `openspec/config.yaml` updated to remove rsync/file-copy references, add libnbd dependency, add Arch packaging instructions
- **Test impact**: Old rsync-related tests must be cleaned up; new tests for import hardening, log levels, onchange gate, and PKGBUILD structure; integration tests have full access to libvirt and qemu for real NBD transfer testing
