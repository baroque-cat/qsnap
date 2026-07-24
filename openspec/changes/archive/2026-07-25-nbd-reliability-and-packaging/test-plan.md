# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| env-validation | libnbd availability check | libnbd installed — validation passes | `tests/utils/test_nbd_client.py` | `test_is_libnbd_available_with_real_attributes` | nbd-import-hardening |
| env-validation | libnbd availability check | PyPI nbd imposter — hard failure | `tests/utils/test_nbd_client.py` | `test_is_libnbd_available_pypi_imposter_returns_false` | nbd-import-hardening |
| env-validation | libnbd availability check | libnbd missing — hard failure | `tests/utils/test_nbd_client.py` | `test_is_libnbd_available_no_module_returns_false` | nbd-import-hardening |
| env-validation | libnbd availability check | Dry-run downgrades the failure to a warning | `tests/core/test_validation.py` | `test_libnbd_missing_dry_run_warns_not_aborts` | env-validation |
| env-validation | libnbd availability check | Venv discovers system libnbd during validation | `tests/utils/test_nbd_client.py` | `test_ensure_system_site_packages_discovers_system_bindings` | nbd-import-hardening |
| env-validation | Pre-flight environment validation before pipeline | Cleanup and orphan detection execute before main checks | `tests/core/test_validation.py` | `test_validation_cleanup_before_existence_checks` | env-validation |
| env-validation | Pre-flight environment validation before pipeline | Cleanup skipped when auto_cleanup is false | `tests/core/test_validation.py` | `test_validation_cleanup_skipped_when_auto_cleanup_false` | env-validation |
| env-validation | Pre-flight environment validation before pipeline | All validations pass | `tests/core/test_validation.py` | `test_validation_all_pass` | env-validation |
| env-validation | Pre-flight environment validation before pipeline | snapshot_dir does not exist | `tests/core/test_validation.py` | `test_validation_snapshot_dir_missing` | env-validation |
| env-validation | Pre-flight environment validation before pipeline | virsh binary not in PATH | `tests/core/test_validation.py` | `test_validation_virsh_not_found` | env-validation |
| env-validation | Pre-flight environment validation before pipeline | libvirt rejects dominfo — VM not defined | `tests/core/test_validation.py` | `test_validation_vm_not_defined` | env-validation |
| env-validation | Pre-flight environment validation before pipeline | Compress driver probe uses check=True | `tests/core/test_validation.py` | `test_compress_probe_uses_check_true` | env-validation |
| state-management | IStateManager per-target backup allocation tracking | Write and read per-target backup allocation | `tests/state/test_manager.py` | `test_per_target_backup_allocation_write_read` | state-management |
| state-management | IStateManager per-target backup allocation tracking | Missing target state returns None | `tests/state/test_manager.py` | `test_per_target_backup_allocation_missing_returns_none` | state-management |
| state-management | IStateManager per-target backup allocation tracking | Per-target state is independent | `tests/state/test_manager.py` | `test_per_target_backup_allocation_independent` | state-management |
| state-management | JsonStateManager _target_state.json persistence | _target_state.json written atomically | `tests/state/test_manager.py` | `test_target_state_json_atomic_write` | state-management |
| state-management | JsonStateManager _target_state.json persistence | Missing _target_state.json returns None | `tests/state/test_manager.py` | `test_target_state_json_missing_returns_none` | state-management |
| state-management | JsonStateManager _target_state.json persistence | Corrupted _target_state.json is renamed | `tests/state/test_manager.py` | `test_target_state_json_corrupted_renamed` | state-management |
| shell-abstraction | check=True for probing shell.run() calls | Probing call with check=True logs at DEBUG on failure | `tests/utils/test_shell.py` | `test_probing_call_with_check_true_logs_debug` | shell-abstraction |
| shell-abstraction | check=True for probing shell.run() calls | Compress driver probe uses check=True | `tests/core/test_validation.py` | `test_compress_probe_uses_check_true` | env-validation |
| config-parsing | backup_create option resolution | Valid backup_create value | `tests/config/test_facade.py` | `test_backup_create_valid_onchange` | config-backup-create |
| config-parsing | backup_create option resolution | Invalid backup_create raises ConfigError | `tests/config/test_facade.py` | `test_backup_create_invalid_raises_config_error` | config-backup-create |
| config-parsing | backup_create option resolution | Default backup_create is always | `tests/config/test_facade.py` | `test_backup_create_default_always` | config-backup-create |
| config-parsing | backup_create option resolution | Global backup_create inherited by target | `tests/config/test_resolver.py` | `test_backup_create_global_inherited_by_target` | config-backup-create |
| config-parsing | backup_create option resolution | VM-level backup_create overrides global | `tests/config/test_resolver.py` | `test_backup_create_vm_overrides_global` | config-backup-create |
| config-parsing | backup_create option resolution | Target-level backup_create overrides VM | `tests/config/test_resolver.py` | `test_backup_create_target_overrides_vm` | config-backup-create |
| nbd-bitmap-backup | connect-retry in LibnbdClient | NBD server not ready on first attempt | `tests/utils/test_nbd_client.py` | `test_connect_retry_20_attempts_fresh_handle` | nbd-import-hardening |
| nbd-bitmap-backup | connect-retry in LibnbdClient | NBD server never starts | `tests/utils/test_nbd_client.py` | `test_connect_retry_exhausted_returns_failure` | nbd-import-hardening |
| nbd-bitmap-backup | connect-retry in LibnbdClient | PyPI nbd imposter installed | `tests/utils/test_nbd_client.py` | `test_connect_pypi_imposter_returns_actionable_error` | nbd-import-hardening |
| nbd-bitmap-backup | connect-retry in LibnbdClient | System site-packages discovered in venv | `tests/utils/test_nbd_client.py` | `test_connect_venv_discovers_system_libnbd` | nbd-import-hardening |
| nbd-bitmap-backup | libnbd module attribute verification | System libnbd installed — returns True | `tests/utils/test_nbd_client.py` | `test_is_libnbd_available_with_real_attributes` | nbd-import-hardening |
| nbd-bitmap-backup | libnbd module attribute verification | PyPI nbd imposter — returns False | `tests/utils/test_nbd_client.py` | `test_is_libnbd_available_pypi_imposter_returns_false` | nbd-import-hardening |
| nbd-bitmap-backup | libnbd module attribute verification | No nbd module at all — returns False | `tests/utils/test_nbd_client.py` | `test_is_libnbd_available_no_module_returns_false` | nbd-import-hardening |
| nbd-bitmap-backup | libnbd module attribute verification | Venv discovers system libnbd | `tests/utils/test_nbd_client.py` | `test_is_libnbd_available_venv_discovers_system_libnbd` | nbd-import-hardening |
| nbd-bitmap-backup | MISSING_LIBNBD_ERROR multi-distro message | Error message includes Arch instructions | `tests/utils/test_nbd_client.py` | `test_missing_libnbd_error_includes_arch_instructions` | nbd-import-hardening |
| nbd-bitmap-backup | MISSING_LIBNBD_ERROR multi-distro message | Error message warns about PyPI imposter | `tests/utils/test_nbd_client.py` | `test_missing_libnbd_error_warns_pypi_imposter` | nbd-import-hardening |
| arch-packaging | PKGBUILD for Arch Linux | PKGBUILD installs to system Python | `tests/systemd/test_units.py` | `test_pkgbuild_install_target_is_system_python` | arch-packaging |
| arch-packaging | PKGBUILD for Arch Linux | pkgver matches pyproject.toml | `tests/systemd/test_units.py` | `test_pkgbuild_pkgver_matches_pyproject` | arch-packaging |
| arch-packaging | System dependency declaration in PKGBUILD | All runtime dependencies declared | `tests/systemd/test_units.py` | `test_pkgbuild_depends_includes_required_packages` | arch-packaging |
| arch-packaging | System dependency declaration in PKGBUILD | libnbd on sys.path after installation | `tests/integration/test_pkgbuild_structure.py` | `test_pkgbuild_libnbd_on_syspath` | integration-pkgbuild |
| arch-packaging | Systemd unit installation via PKGBUILD | Systemd units installed to correct path | `tests/systemd/test_units.py` | `test_pkgbuild_installs_systemd_units` | arch-packaging |
| arch-packaging | Config example and state directory via PKGBUILD | Config example installed | `tests/systemd/test_units.py` | `test_pkgbuild_installs_config_example` | arch-packaging |
| arch-packaging | Config example and state directory via PKGBUILD | State directory created | `tests/systemd/test_units.py` | `test_pkgbuild_creates_state_directory` | arch-packaging |
| core-orchestrator | Per-target backup onchange gate | First backup — always proceeds | `tests/core/test_pipeline.py` | `test_onchange_backup_first_run_proceeds` | core-onchange-gate |
| core-orchestrator | Per-target backup onchange gate | No change — backup skipped | `tests/core/test_pipeline.py` | `test_onchange_backup_no_change_skipped` | core-onchange-gate |
| core-orchestrator | Per-target backup onchange gate | Allocation grew — backup proceeds | `tests/core/test_pipeline.py` | `test_onchange_backup_allocation_grew_proceeds` | core-onchange-gate |
| core-orchestrator | Per-target backup onchange gate | always mode — gate bypassed | `tests/core/test_pipeline.py` | `test_always_mode_backup_gate_bypassed` | core-onchange-gate |
| core-orchestrator | Per-target backup onchange gate | No snapshots — gate bypassed | `tests/core/test_pipeline.py` | `test_onchange_no_snapshots_skipped` | core-onchange-gate |
| core-orchestrator | backup_create baseline update after successful transfer | Baseline updated after successful transfer | `tests/core/test_pipeline.py` | `test_onchange_baseline_updated_after_successful_transfer` | core-onchange-gate |
| core-orchestrator | backup_create baseline update after successful transfer | Baseline NOT updated on transfer failure | `tests/core/test_pipeline.py` | `test_onchange_baseline_not_updated_on_failure` | core-onchange-gate |
| config-model | backup_create field in TargetConfig | Default backup_create is always | `tests/config/test_model.py` | `test_target_config_backup_create_default_always` | config-backup-create |
| config-model | backup_create field in TargetConfig | Explicit onchange mode | `tests/config/test_model.py` | `test_target_config_backup_create_explicit_onchange` | config-backup-create |
| config-model | backup_create field in TargetConfig | Target inherits backup_create from global | `tests/config/test_model.py` | `test_target_config_backup_create_inherits_from_global` | config-backup-create |
| config-model | backup_create field in TargetConfig | Target overrides global backup_create | `tests/config/test_model.py` | `test_target_config_backup_create_overrides_global` | config-backup-create |
| config-model | backup_create field in GlobalConfig | Global backup_create default | `tests/config/test_model.py` | `test_global_config_backup_create_default_always` | config-backup-create |
| config-model | backup_create field in GlobalConfig | Global backup_create set to onchange | `tests/config/test_model.py` | `test_global_config_backup_create_explicit_onchange` | config-backup-create |
| state-management | IStateManager contract enforcement | New methods are abstract | `tests/interfaces/test_state_manager.py` | `test_istate_manager_backup_allocation_methods_abstract` | state-management |
| state-management | IStateManager contract enforcement | InMemoryStateManager implements new methods | `tests/interfaces/test_state_manager.py` | `test_inmemory_manager_implements_backup_allocation` | state-management |
| state-management | IStateManager contract enforcement | JsonStateManager implements new methods | `tests/interfaces/test_state_manager.py` | `test_json_manager_implements_backup_allocation` | state-management |
| core-orchestrator | Integration: real NBD backup with onchange | Per-target onchange with real libvirt/qemu | `tests/integration/test_onchange_backup.py` | `test_nbd_onchange_skip_no_change` | integration-onchange |
| core-orchestrator | Integration: real NBD backup with onchange | Per-target onchange proceeds on first run | `tests/integration/test_onchange_backup.py` | `test_nbd_onchange_first_run_proceeds` | integration-onchange |
| nbd-bitmap-backup | Integration: real NBD import hardening | connect() survives PyPI imposter scenario | `tests/integration/test_nbd_import_hardening.py` | `test_nbd_connect_no_crash_on_missing_module` | integration-nbd-hardening |
| nbd-bitmap-backup | Integration: real qemu-nbd compress probe | Compress driver probe succeeds with real qemu-nbd | `tests/integration/test_env_validation.py` | `test_real_compress_driver_probe` | integration-nbd-hardening |
| shell-abstraction | Integration: log level verification | DEBUG logging on probing shell failure | `tests/integration/test_log_levels.py` | `test_probe_failure_logged_at_debug_not_error` | integration-nbd-hardening |

## Delegation Groups

### Group: nbd-import-hardening
**Scope:** `tests/utils/test_nbd_client.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_nbd_client.py` | 11 (is_libnbd_available attribute verification, connect PyPI imposter, _ensure_system_site_packages, MISSING_LIBNBD_ERROR message, connect retry) | MODIFY |
| `tests/core/test_validation.py` | 1 (libnbd missing in dry-run warning) | MODIFY |

### Group: env-validation
**Scope:** `tests/core/test_validation.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_validation.py` | 8 (cleanup ordering, auto_cleanup skip, all pass, missing dirs, missing virsh, VM not defined, compress probe check=True, libnbd dry-run) | MODIFY |

### Group: state-management
**Scope:** `tests/state/test_manager.py`, `tests/interfaces/test_state_manager.py`, `tests/mocks/mock_state.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/state/test_manager.py` | 6 (per-target allocation read/write, missing returns None, independent state, _target_state.json atomic, missing, corrupted) | MODIFY |
| `tests/interfaces/test_state_manager.py` | 3 (new methods abstract, InMemory implements, JsonStateManager implements) | MODIFY |
| `tests/mocks/mock_state.py` | 0 (adds `get_last_backup_allocation` / `set_last_backup_allocation`) | MODIFY |

### Group: shell-abstraction
**Scope:** `tests/utils/test_shell.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_shell.py` | 1 (probing call with check=True logs at DEBUG) | MODIFY |

### Group: config-backup-create
**Scope:** `tests/config/test_model.py`, `tests/config/test_facade.py`, `tests/config/test_resolver.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | 6 (TargetConfig.backup_create default, explicit, inherit, override; GlobalConfig.backup_create default, onchange) | MODIFY |
| `tests/config/test_facade.py` | 3 (backup_create valid, invalid, default) | MODIFY |
| `tests/config/test_resolver.py` | 3 (backup_create global→target inheritance, VM overrides global, target overrides VM) | MODIFY |

### Group: core-onchange-gate
**Scope:** `tests/core/test_pipeline.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | 7 (onchange first run, no change skip, allocation grew, always bypassed, no snapshots bypassed, baseline updated, baseline not updated on failure) | MODIFY |

### Group: arch-packaging
**Scope:** `tests/systemd/test_units.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/systemd/test_units.py` | 5 (PKGBUILD system Python install, pkgver match, depends array, systemd units, config example, state dir) | MODIFY |

### Group: integration-onchange
**Scope:** `tests/integration/test_onchange_backup.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_onchange_backup.py` | 2 (real NBD onchange skip when no change, first run proceeds) | NEW |

### Group: integration-nbd-hardening
**Scope:** `tests/integration/test_nbd_import_hardening.py`, `tests/integration/test_log_levels.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_nbd_import_hardening.py` | 1 (connect() no crash on missing module) | NEW |
| `tests/integration/test_env_validation.py` | 1 (real compress driver probe) | MODIFY |
| `tests/integration/test_log_levels.py` | 1 (probe failure logged at DEBUG) | NEW |

### Group: integration-pkgbuild
**Scope:** `tests/integration/test_pkgbuild_structure.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_pkgbuild_structure.py` | 1 (libnbd on sys.path after PKGBUILD install) | NEW |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/mocks/mock_state.py` | ADD `get_last_backup_allocation(target_path)` and `set_last_backup_allocation(target_path, alloc)` methods to `InMemoryStateManager` | New abstract methods on `IStateManager` — mock must satisfy the contract |
| `tests/interfaces/test_state_manager.py` | ADD `test_istate_manager_backup_allocation_methods_abstract`, `test_inmemory_manager_implements_backup_allocation`, `test_json_manager_implements_backup_allocation` | Contract test for the two new abstract methods |
| `tests/utils/test_nbd_client.py` | ADD `test_is_libnbd_available_with_real_attributes` (monkeypatches `find_spec` + sets real fake nbd module in `sys.modules`), `test_is_libnbd_available_pypi_imposter_returns_false` (fake module without `Error`/`NBD`), `test_is_libnbd_available_no_module_returns_false` (existing `test_returns_false_when_not_installed` updated), `test_is_libnbd_available_venv_discovers_system_libnbd`, `test_connect_pypi_imposter_returns_actionable_error`, `test_connect_venv_discovers_system_libnbd`, `test_ensure_system_site_packages_discovers_system_bindings`, `test_missing_libnbd_error_includes_arch_instructions`, `test_missing_libnbd_error_warns_pypi_imposter` | NBD import hardening — verify attribute checks, venv fallback, and error message content |
| `tests/utils/test_nbd_client.py` | MODIFY `test_returns_true_when_package_found` to also verify attribute checks (hasattr for Error and NBD) | Old test only checked `find_spec`; new logic also verifies attributes |
| `tests/utils/test_shell.py` | ADD `test_probing_call_with_check_true_logs_debug` | Verify that `shell.run(cmd, check=True)` logs failures at DEBUG level using `caplog` |
| `tests/core/test_validation.py` | ADD `test_compress_probe_uses_check_true`, `test_validation_all_pass`, `test_validation_cleanup_before_existence_checks`, `test_validation_cleanup_skipped_when_auto_cleanup_false`, `test_validation_snapshot_dir_missing`, `test_validation_virsh_not_found`, `test_validation_vm_not_defined`, `test_libnbd_missing_dry_run_warns_not_aborts` | Pre-flight validation spec scenarios — compress probe check=True, cleanup ordering, dry-run behavior |
| `tests/state/test_manager.py` | ADD `test_per_target_backup_allocation_write_read`, `test_per_target_backup_allocation_missing_returns_none`, `test_per_target_backup_allocation_independent`, `test_target_state_json_atomic_write`, `test_target_state_json_missing_returns_none`, `test_target_state_json_corrupted_renamed` | Per-target backup allocation tracking and `_target_state.json` persistence |
| `tests/config/test_model.py` | ADD `test_target_config_backup_create_default_always`, `test_target_config_backup_create_explicit_onchange`, `test_target_config_backup_create_inherits_from_global`, `test_target_config_backup_create_overrides_global`, `test_global_config_backup_create_default_always`, `test_global_config_backup_create_explicit_onchange` | New `backup_create` field on `TargetConfig` and `GlobalConfig` |
| `tests/config/test_facade.py` | ADD `test_backup_create_valid_onchange`, `test_backup_create_invalid_raises_config_error`, `test_backup_create_default_always` | `ConfigFacade` validates and default-resolves `backup_create` |
| `tests/config/test_resolver.py` | ADD `test_backup_create_global_inherited_by_target`, `test_backup_create_vm_overrides_global`, `test_backup_create_target_overrides_vm` | `backup_create` inheritance: global → VM → target |
| `tests/config/test_resolver.py` | ADD TOML fixture `tests/fixtures/configs/backup_create.toml` with global `backup_create = "onchange"`, VM override, and target override | Fixture for inheritance tests |
| `tests/core/test_pipeline.py` | ADD `test_onchange_backup_first_run_proceeds`, `test_onchange_backup_no_change_skipped`, `test_onchange_backup_allocation_grew_proceeds`, `test_always_mode_backup_gate_bypassed`, `test_onchange_no_snapshots_gate_bypassed`, `test_onchange_baseline_updated_after_successful_transfer`, `test_onchange_baseline_not_updated_on_failure` | Per-target onchange gate in `Core._backup_target()` |
| `tests/systemd/test_units.py` | ADD `test_pkgbuild_install_target_is_system_python`, `test_pkgbuild_pkgver_matches_pyproject`, `test_pkgbuild_depends_includes_required_packages`, `test_pkgbuild_installs_systemd_units`, `test_pkgbuild_installs_config_example`, `test_pkgbuild_creates_state_directory` | PKGBUILD structural validation |
| `tests/integration/test_onchange_backup.py` | NEW FILE — ADD `test_nbd_onchange_skip_no_change`, `test_nbd_onchange_first_run_proceeds` | Integration: real VM, create snapshot, run NBD backup with onchange, verify state |
| `tests/integration/test_nbd_import_hardening.py` | NEW FILE — ADD `test_nbd_connect_no_crash_on_missing_module` | Integration: verify `connect()` returns `NbdResult` gracefully instead of crashing |
| `tests/integration/test_env_validation.py` | ADD `test_real_compress_driver_probe` | Integration: verify real `qemu-nbd --image-opts driver=compress` probe succeeds |
| `tests/integration/test_log_levels.py` | NEW FILE — ADD `test_probe_failure_logged_at_debug_not_error` | Integration: verify probe command failures log at DEBUG level |
| `tests/integration/test_pkgbuild_structure.py` | NEW FILE — ADD `test_pkgbuild_libnbd_on_syspath` | Integration: verify PKGBUILD structure allows libnbd import |
| `tests/conftest.py` | UPDATE `make_target` to accept `backup_create` kwarg | Tests need to create `TargetConfig` with `backup_create="onchange"` |
| `tests/conftest.py` | UPDATE `make_global_config` to accept `backup_create` kwarg | Tests need to create `GlobalConfig` with `backup_create="onchange"` |
| `tests/conftest.py` | UPDATE `_setup_validation_expectations` — the compress driver probe expectation should accept `check=True` being passed (MockShell ignores `check` parameter already, but verify no change needed) | MockShell `run()` signature already has `check=False` default — no change needed at mock level, just verify |
| `tests/mocks/mock_modules.py` | No change needed — `MockBackupProvider` and `MockBitmapBackupProvider` are already named generically and don't reference file-copy | N/A |

## Risks & Edge Cases

- **[Risk] PyPI `nbd` package installed instead of system libnbd** → `test_is_libnbd_available_pypi_imposter_returns_false` (unit), `test_connect_pypi_imposter_returns_actionable_error` (unit), `test_missing_libnbd_error_warns_pypi_imposter` (unit). Also: verify `AttributeError` never propagates from `connect()` — the fake module without `NBD` attribute must produce `NbdResult(success=False)`.
- **[Risk] `_ensure_system_site_packages()` appends wrong path on non-standard distros** → `test_ensure_system_site_packages_discovers_system_bindings` (unit). Also: edge case where `os.path.isdir()` returns `False` for all known paths — function should be a no-op, verify `sys.path` is unchanged.
- **[Risk] `backup_create="onchange"` misses changes without allocation growth** → `test_onchange_backup_allocation_grew_proceeds` (unit, pipeline level) covers the happy path. Edge case: zero allocation growth but data changed (e.g., in-place overwrite). This is a known limitation documented in design.md — no test needed (the behavior is "onchange skips" which is correct per the spec).
- **[Risk] IStateManager ABC change breaks existing implementations** → `test_istate_manager_backup_allocation_methods_abstract` (contract) verifies the new methods are abstract. `test_inmemory_manager_implements_backup_allocation` (contract) verifies `InMemoryStateManager` compiles. Edge case: subclass missing only the new methods must fail to instantiate (`TypeError`) — test in contract suite.
- **[Risk] PKGBUILD `pkgver` desync from `pyproject.toml` version** → `test_pkgbuild_pkgver_matches_pyproject` (structural) reads both files and asserts equality. Edge case: version in `pyproject.toml` is changed without updating `PKGBUILD` — caught by CI.
- **[Risk] Log level audit misses some probing calls** → Covered by systematic test: `test_probing_call_with_check_true_logs_debug` (unit, shell level) verifies the logging behavior. Integration test `test_probe_failure_logged_at_debug_not_error` does end-to-end log capture.
- **[Risk] Race condition: NBD server starts between connect retries** → `test_connect_retry_20_attempts_fresh_handle` (unit) already covers retry loop with fresh handles. Edge case: connect succeeds on attempt 10 (not 1 or 20) — verify only one `nbd.NBD()` handle is used for the successful attempt, and previous handles are discarded.
- **[Risk] `_target_state.json` race on concurrent writes** → Covered by `test_target_state_json_atomic_write` (unit) which verifies `.tmp` + `os.replace` pattern. Edge case: two processes write concurrently — `os.replace` on Linux is atomic; `.tmp` files are per-write (unique filename or same `.tmp` overwritten atomically).
- **[Risk] `backup_create="onchange"` with no prior state AND failed transfer** → `test_onchange_baseline_not_updated_on_failure` (unit) verifies baseline is NOT written on failure. Edge case: future run after a failed transfer should still return `None` (no baseline written), so the next run proceeds as first-run.
- **[Risk] `backup_create` field missing from old `TargetConfig` frozen dataclass** → `test_target_config_backup_create_default_always` (unit) verifies default `"always"`. Backward-compatible: existing tests that construct `TargetConfig(path=...)` will get `backup_create="always"` without changes.
