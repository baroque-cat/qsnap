## Context

qsnap uses `python3-libnbd` (system package) as the sole NBD transport for bitmap backups. Three reliability gaps were discovered through real-world deployment on Arch Linux:

1. **Import collision**: `is_libnbd_available()` uses `importlib.util.find_spec("nbd")` which returns `True` for both the system `python3-libnbd` bindings AND the unrelated PyPI `nbd` package (Jupyter notebook diffing tool). The PyPI package lacks `nbd.Error` and `nbd.NBD` attributes, causing an unhandled `AttributeError` in `LibnbdClient.connect()` that crashes the pipeline.

2. **Venv isolation**: When qsnap is installed in a venv (PEP 668 compliant), `sys.path` excludes `/usr/lib/python3.x/site-packages/`, making the system `libnbd` bindings invisible to `find_spec`. The pipeline fails with `MISSING_LIBNBD_ERROR` even though `libnbd` is installed.

3. **Misleading logs**: ~30+ probing `shell.run()` calls use `check=False` (default), producing ERROR-level logs for expected command failures (e.g., `qemu-nbd --image-opts driver=compress` probe always fails by design).

Additionally, there is no per-target change detection — every snapshot is always transferred to every target, even when the VM disk has not changed since the last backup to that specific target.

Current state: `pyproject.toml` has `dependencies = []` (libnbd is a system package, not PyPI). No PKGBUILD, no install.sh, no packaging files exist. `openspec/config.yaml` still references `FileCopyBackupProvider` and `qemu-img convert` in domain concepts.

## Goals / Non-Goals

**Goals:**
- Make `is_libnbd_available()` distinguish libnbd bindings from PyPI imposter
- Make `LibnbdClient.connect()` return `NbdResult` instead of crashing on wrong module
- Make venv installations work by discovering system site-packages
- Fix misleading ERROR-level logs from probing commands
- Add per-target `backup_create="onchange"` mode to skip unnecessary backup transfers
- Create PKGBUILD for Arch Linux system-package installation
- Update README and `openspec/config.yaml` to reflect NBD-only transport

**Non-Goals:**
- NBD transfer engine unification (FULL + incremental merge) — separate change
- Dead code removal (`content_hash`, `file_sha256`, `nbd_full_export`) — separate change
- Verify mode simplification (`hash`/`full` synonyms) — separate change
- Checkpoint validation (REDEFINE_VALIDATE) — separate change
- FS freeze/thaw — separate change
- Packages for Debian/Fedora (only Arch PKGBUILD in this change)
- Changing `pyproject.toml` `dependencies` (remains `[]` — libnbd is system-only)

## Decisions

### Decision 1: Verify attributes in `is_libnbd_available()` instead of switching to system import

**Rationale:** The PyPI `nbd` and system `python3-libnbd` both import as `import nbd`. System import (module-level) would NOT solve the name collision — both packages would be imported the same way. System import would also make the `qsnap` CLI crash at startup if libnbd is missing, instead of producing a graceful error at backup time.

**Alternatives considered:**
- System import (module-level `import nbd`): rejected — same name collision, crashes CLI at startup
- `pkg_resources` distribution check: rejected — adds setuptools dependency, overkill
- `nbd.__file__` path inspection: rejected — fragile, distro-specific paths

**AGENTS.md anti-patterns avoided:** "Catching broad exceptions" — `connect()` catches specific `AttributeError`, not broad `Exception`.

### Decision 2: `_ensure_system_site_packages()` as runtime fallback

**Rationale:** Even with PKGBUILD, some users will install via `pip install` in a venv. The function appends system site-packages to `sys.path` when `VIRTUAL_ENV` is set or `sys.prefix != sys.base_prefix`. Safe: venv packages take precedence (they appear earlier in `sys.path`).

**Alternatives considered:**
- Document "create venv with `--system-site-packages`": rejected — users forget, error message is unclear
- `.pth` file in venv site-packages: rejected — fragile, venv recreation loses it
- Symlinks: rejected — breaks on Python version upgrades

### Decision 3: Per-target onchange via `SnapshotInfo.allocation` comparison (Option A)

**Rationale:** The snapshot onchange mechanism uses `IStateManager.get_last_allocation(vm_name)` — a single per-VM integer. This cannot work for per-target gating because all targets share one baseline. Option A adds per-target state (`get/set_last_backup_allocation(target_path)`) and compares `snapshots[-1].allocation` against the per-target baseline. This works before NBD export (saves resources), reuses existing `SnapshotInfo.allocation` data, and supports different targets having different modes.

**Why not reuse `IChangeDetector` interface:** The interface has `has_changed(vm_config, disk)` — no `target_path` parameter. Extending it would mix per-VM and per-target responsibilities in one interface (SRP violation). The inline comparison in `_backup_target()` is simpler and doesn't require a new ABC.

**Why not dirty_bytes post-fact check (Option D):** The dirty bitmap is only available after `virsh backup-begin` starts the NBD export — too late to save resources. Option D can be added later as a secondary optimization (delete empty delta if `dirty_bytes==0`).

**AGENTS.md anti-patterns avoided:** "Modules accessing config directly" — the onchange gate reads `target.backup_create` from the immutable `TargetConfig` passed as a method parameter, not from a stored `IConfigFacade` reference.

### Decision 4: New `_target_state.json` file for per-target state

**Rationale:** `JsonStateManager` already uses global files for per-target data: `_full_backups.json` (keyed by `target_path`) and `_dependencies.json` (nested by `target_path`). A new `_target_state.json` follows the same pattern. Extending `_full_backups.json` would require changing its value type from `list` to `dict` (breaking change for the auto-migration code).

**Forward compatibility:** Old state files without `_target_state.json` are handled gracefully — `get_last_backup_allocation()` returns `None` (first-run behavior, always proceeds with backup).

**Backward compatibility:** Removing the new file reverts to "always backup" behavior — no data loss.

### Decision 5: `backup_create` field on both `GlobalConfig` and `TargetConfig`

**Rationale:** Follows the existing `snapshot_create` pattern: global default → VM override → target override. `GlobalConfig.backup_create` provides the default; `TargetConfig.backup_create` overrides per-target. `VMConfig` does NOT get a `backup_create` field — backups are per-target, not per-VM (unlike snapshots which are per-VM).

**Validation:** `ConfigFacade` validates against `{"always", "onchange"}` at parse time, same pattern as `snapshot_create` validation against `{"always", "onchange", "ondemand"}`.

### Decision 6: PKGBUILD installs to system Python

**Rationale:** System Python has `libnbd` on `sys.path` natively. No venv needed. Dependencies declared in `depends=('python>=3.11' 'libnbd' 'libvirt' 'qemu-utils')`. The `[project.scripts]` entry point in `pyproject.toml` creates `/usr/bin/qsnap` automatically via `pip install --prefix=/usr`.

**`pkgver` synchronization:** `PKGBUILD` `pkgver` must match `pyproject.toml` `version`. The version is NOT hardcoded in Python code — `qsnap/cli/summary.py:39-46` reads it dynamically via `importlib.metadata.version("qsnap")`.

### Decision 7: Log level fix via `check=True` on probing calls

**Rationale:** `SubprocessShell.run()` logs at ERROR when `check=False` and command fails; logs at DEBUG when `check=True` and command fails. Probing commands (where failure is expected and handled) should use `check=True`. The immediate fix is the `qemu-nbd --image-opts driver=compress` probe in `_validate_environment()`. The systematic audit covers ~30+ calls across 8 files.

**Not changing default:** Changing the default `check` value from `False` to `True` would be a breaking change affecting all callers. Instead, each probing call is individually fixed with explicit `check=True`.

## Risks / Trade-offs

- **[Risk] PyPI `nbd` package installed instead of system libnbd** → Mitigation: `is_libnbd_available()` now verifies `hasattr(nbd, "Error")` and `hasattr(nbd, "NBD")`; `connect()` catches `AttributeError` and returns `NbdResult` with actionable error message; `MISSING_LIBNBD_ERROR` warns about PyPI imposter.

- **[Risk] `_ensure_system_site_packages()` appends wrong path on non-standard distros** → Mitigation: Function checks `os.path.isdir(path)` before appending; only standard paths are checked (`/usr/lib/`, `/usr/local/lib/`, `/usr/lib/*/dist-packages`); safe no-op if path doesn't exist.

- **[Risk] `backup_create="onchange"` misses changes without allocation growth** → Mitigation: Allocation comparison detects growth (same as `AllocationSizeDetector` for snapshots); `MapChangeDetector` is not used (would require per-target hash state); users who need exact change detection can use `backup_create="always"` (default). Option D (dirty_bytes check) can be added later.

- **[Risk] IStateManager ABC change breaks existing implementations** → Mitigation: New methods have clear semantics; `JsonStateManager` gets `_target_state.json` implementation; `InMemoryStateManager` and `MockStateManager` get `dict`-backed implementations; old state files without `_target_state.json` are handled gracefully (returns `None`).

- **[Risk] PKGBUILD `pkgver` desync from `pyproject.toml` version** → Mitigation: `pkgver` is documented as "synchronize with pyproject.toml"; version is read dynamically via `importlib.metadata` in code; mismatch only affects package metadata, not runtime behavior.

- **[Risk] Log level audit misses some probing calls** → Mitigation: Systematic grep for `shell.run(` without `check=True` across all files; each call evaluated individually; criterion: if caller handles failure in conditional logic → `check=True`.

## Migration Plan

1. **Code changes** (no data migration needed):
   - `nbd_client.py`: Add `_ensure_system_site_packages()`, modify `is_libnbd_available()`, modify `connect()`, update `MISSING_LIBNBD_ERROR`
   - `core/__init__.py`: Fix `check=True` on probe, add `_should_backup_onchange()`, add gate in `_backup_target()`
   - `models/config.py`: Add `backup_create` to `TargetConfig` and `GlobalConfig`
   - `config/facade.py`: Resolve `backup_create` inheritance
   - `interfaces/state.py`: Add 2 new abstract methods
   - `state/json_manager.py`: Implement `_target_state.json`
   - ~30+ files: Add `check=True` to probing `shell.run()` calls

2. **State migration**: None required. New `_target_state.json` is created on first `set_last_backup_allocation()` call. Missing file → `get_last_backup_allocation()` returns `None` → first backup always proceeds.

3. **Rollback strategy**: Revert code changes. `_target_state.json` remains on disk but is never read — harmless. `backup_create` field reverts to default `"always"`. No pipeline ordering change (gate is before existing logic, not between steps).

4. **Packaging**: PKGBUILD is a new file — no rollback needed. Users who installed via PKGBUILD can `pacman -R qsnap` to remove.

## Open Questions

- Should `_ensure_system_site_packages()` also check for `nbd.SHUTDOWN_FLAG` and `nbd.CMD_FLAG` attributes for extra verification? Current decision: only `Error` and `NBD` (minimal viable check).
- Should `backup_create="ondemand"` be added (skip backup if no target reachable)? Current decision: only `"always"` and `"onchange"` — `"ondemand"` is a snapshot concept, not a backup concept.
