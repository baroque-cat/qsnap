## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b nbd-reliability-and-packaging`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Interfaces & Models

- [x] 2.1 Add `get_last_backup_allocation(target_path: str) -> int | None` and `set_last_backup_allocation(target_path: str, alloc: int) -> None` abstract methods to `IStateManager` in `qsnap/interfaces/state.py` (spec: state-management, **BREAKING** — all implementations must update)
- [x] 2.2 Add `backup_create: str = "always"` field to `TargetConfig` in `qsnap/models/config.py` (spec: config-model)
- [x] 2.3 Add `backup_create: str = "always"` field to `GlobalConfig` in `qsnap/models/config.py` (spec: config-model)

## 3. Domain Implementation

### NBD Import Hardening (spec: nbd-bitmap-backup)

- [x] 3.1 Add `_ensure_system_site_packages()` function to `qsnap/utils/nbd_client.py` — appends system site-packages to `sys.path` when running in a venv (checks `VIRTUAL_ENV` env var and `sys.prefix != sys.base_prefix`)
- [x] 3.2 Modify `is_libnbd_available()` in `qsnap/utils/nbd_client.py` — call `_ensure_system_site_packages()` before `find_spec`, then verify `hasattr(nbd, "Error")` and `hasattr(nbd, "NBD")` after import
- [x] 3.3 Modify `LibnbdClient.connect()` in `qsnap/utils/nbd_client.py` — catch `AttributeError` when accessing `nbd.NBD()` or `nbd.Error`, return `NbdResult(success=False, ...)` with actionable error message
- [x] 3.4 Update `MISSING_LIBNBD_ERROR` in `qsnap/utils/nbd_client.py` — add Arch (`pacman -S libnbd`), Fedora (`dnf install libnbd`), and PyPI imposter warning

### Log Level Fixes (spec: shell-abstraction, env-validation)

- [x] 3.5 Fix compress driver probe in `qsnap/core/__init__.py` `_validate_environment()` — change `check=False` to `check=True` on the `qemu-nbd --image-opts driver=compress` probe call
- [x] 3.6 Audit and fix ~30+ probing `shell.run()` calls across the codebase — add `check=True` to calls in: `utils/nbd.py` (3 calls), `modules/snapshot/external.py` (2 calls), `modules/change/allocation_detector.py` (1 call), `modules/change/map_detector.py` (1 call), `modules/backup/bitmap.py` (4 calls), `core/__init__.py` (7+ calls outside `_validate_environment`), `utils/verification.py` (6 calls), `modules/lifecycle/blockcommit_manager.py` (2 calls). Criterion: if caller handles failure in conditional logic → `check=True`

### Per-Target backup_create="onchange" (spec: config-parsing, state-management, core-orchestrator)

- [x] 3.7 Implement `backup_create` option resolution in `ConfigFacade._build_vm()` and `_build_target()` in `qsnap/config/facade.py` — resolve inheritance (global → VM → target), validate against `{"always", "onchange"}`
- [x] 3.8 Implement `get_last_backup_allocation()` and `set_last_backup_allocation()` in `JsonStateManager` at `qsnap/state/json_manager.py` — new `_target_state.json` file, atomic writes, corruption recovery
- [x] 3.9 Implement `_should_backup_onchange()` private method in `Core` at `qsnap/core/__init__.py` — compare `snapshots[-1].allocation` vs `get_last_backup_allocation(target_path)`
- [x] 3.10 Add onchange gate to `Core._backup_target()` in `qsnap/core/__init__.py` — call `_should_backup_onchange()` before transfer, skip if `False`, update baseline after successful transfer

### Mocks & Infrastructure

- [x] 3.11 Add `get_last_backup_allocation()` and `set_last_backup_allocation()` to `InMemoryStateManager` in `tests/mocks/mock_state.py` — `dict`-backed implementation
- [x] 3.12 Update `make_target` fixture in `tests/conftest.py` to accept `backup_create` kwarg
- [x] 3.13 Update `make_global_config` fixture in `tests/conftest.py` to accept `backup_create` kwarg

## 4. Factory & Core Wiring

- [x] 4.1 Verify `DefaultFactory.create_backup_provider()` in `qsnap/factory/default.py` — no changes needed (backup_create is on TargetConfig, not factory)
- [x] 4.2 Verify `Core._execute_backup_steps()` in `qsnap/core/__init__.py` — no ordering change needed (gate is inside `_backup_target()`, not between steps)

## 5. Packaging

- [x] 5.1 Create `PKGBUILD` in repository root — `pkgname=qsnap`, `pkgver=0.2.1` (sync with `pyproject.toml`), `depends=('python>=3.11' 'libnbd' 'libvirt' 'qemu-utils')`, `makedepends=('python-poetry' 'git')`, install systemd units, config example, state directory (spec: arch-packaging)

## 6. Documentation

- [x] 6.1 Update `README.md` — remove any rsync/file-copy references, add `libnbd` as hard runtime dependency, add Arch Linux installation instructions (`makepkg -si` or AUR)
- [x] 6.2 Update `openspec/config.yaml` — remove `FileCopyBackupProvider` from domain modules, remove `qemu-img convert` from FULL backup description, add `libnbd` to runtime deps, add `backup_create` to domain concepts, update backup verification description

## 7. Testing

**CRITICAL INSTRUCTION FOR THE IMPLEMENTATION AGENT:** You MUST delegate ALL test work to @Mr.Tester subagents. You MUST NOT write tests yourself. For EACH delegation group below, launch one @Mr.Tester subagent with the following:

1. The group's scope (file paths from test-plan.md)
2. The group's scenario list from the Coverage Map in `test-plan.md`
3. The instruction: "Write or fix ONLY these specific tests. Report source bugs, don't fix them."
4. **MANDATORY: Pass the document `/home/openuser/vm/qsnap/TESTING.md` to each @Mr.Tester agent.** This document describes the testing paradigm, directory structure, mock strategy, contract test rules, fixtures, and test markers. Each tester MUST read TESTING.md before writing any tests.
5. **MANDATORY: Tell each tester they have FULL ACCESS to libvirt and qemu for real integration tests** — not just mocks. Integration tests should create disposable test VMs and use real `virsh`/`qemu-img`/`qemu-nbd` commands.
6. **MANDATORY: Tell each tester to CLEAN UP old rsync-related tests** — search for and delete any test files or functions referencing `FileCopyBackupProvider`, `file_copy`, `rsync`, `rate_limit`, `incremental_mode="file-copy"`, `full_verify_before_rebase`, or `content_hash`. List deletions in their report.

Launch ALL groups IN PARALLEL (single message, multiple @Mr.Tester calls).

- [x] 7.1 Read `test-plan.md` Delegation Groups section
- [x] 7.2 Delegate group `nbd-import-hardening` to @Mr.Tester — scope: `tests/utils/test_nbd_client.py`, `tests/core/test_validation.py` (11 scenarios). **Pass TESTING.md.** Tell tester: full libvirt/qemu access for integration tests; clean up old rsync tests.
- [x] 7.3 Delegate group `env-validation` to @Mr.Tester — scope: `tests/core/test_validation.py` (8 scenarios). **Pass TESTING.md.** Tell tester: full libvirt/qemu access; clean up old rsync tests.
- [x] 7.4 Delegate group `state-management` to @Mr.Tester — scope: `tests/state/test_manager.py`, `tests/interfaces/test_state_manager.py`, `tests/mocks/mock_state.py` (9 scenarios). **Pass TESTING.md.** Tell tester: clean up old rsync tests.
- [x] 7.5 Delegate group `shell-abstraction` to @Mr.Tester — scope: `tests/utils/test_shell.py` (1 scenario). **Pass TESTING.md.**
- [x] 7.6 Delegate group `config-backup-create` to @Mr.Tester — scope: `tests/config/test_model.py`, `tests/config/test_facade.py`, `tests/config/test_resolver.py` (12 scenarios). **Pass TESTING.md.**
- [x] 7.7 Delegate group `core-onchange-gate` to @Mr.Tester — scope: `tests/core/test_pipeline.py` (7 scenarios). **Pass TESTING.md.** Tell tester: full libvirt/qemu access.
- [x] 7.8 Delegate group `arch-packaging` to @Mr.Tester — scope: `tests/systemd/test_units.py` (6 scenarios). **Pass TESTING.md.**
- [x] 7.9 Delegate group `integration-onchange` to @Mr.Tester — scope: `tests/integration/test_onchange_backup.py` (2 scenarios, NEW file). **Pass TESTING.md.** Tell tester: FULL ACCESS to libvirt and qemu — create real disposable VMs, use real `virsh backup-begin`, real NBD transfers.
- [x] 7.10 Delegate group `integration-nbd-hardening` to @Mr.Tester — scope: `tests/integration/test_nbd_import_hardening.py`, `tests/integration/test_env_validation.py`, `tests/integration/test_log_levels.py` (3 scenarios, 2 NEW files). **Pass TESTING.md.** Tell tester: FULL ACCESS to libvirt and qemu.
- [x] 7.11 Delegate group `integration-pkgbuild` to @Mr.Tester — scope: `tests/integration/test_pkgbuild_structure.py` (1 scenario, NEW file). **Pass TESTING.md.**
- [x] 7.12 Review ALL @Mr.Tester reports and fix any source-level bugs discovered
- [x] 7.13 Re-delegate any groups affected by source fixes
- [x] 7.14 Verify all groups pass and coverage matches `test-plan.md`
- [x] 7.15 Run full test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 7.16 Run integration tests: `poetry run pytest tests/integration/ -m integration`

<!--
  TEST ORCHESTRATION PROTOCOL (followed by the apply phase agent):

  1. Read test-plan.md → Delegation Groups section
  2. For EACH group listed, launch one @Mr.Tester subagent with:
     - The group's scope (file paths)
     - The group's scenario list from Coverage Map
     - Instruction: "Write or fix ONLY these specific tests. Report source bugs, don't fix them."
     - MANDATORY: Pass TESTING.md (/home/openuser/vm/qsnap/TESTING.md) to each tester
     - MANDATORY: Tell each tester they have FULL ACCESS to libvirt and qemu for real integration tests
     - MANDATORY: Tell each tester to CLEAN UP old rsync-related tests (FileCopyBackupProvider, rsync, etc.)
  3. Launch ALL groups IN PARALLEL (single message)
  4. After all testers return: fix any reported source bugs, re-delegate affected groups
  5. Repeat until all groups pass
-->
