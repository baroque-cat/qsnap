# Test Plan — runtime-safety-and-nbd-backup

Comprehensive test plan mapping every spec scenario to concrete test files and
function names, following the conventions in `TESTING.md`. The change
introduces 8 new capabilities and modifies 10 existing capabilities across 18
spec files.

**Test command (fast suite — no I/O):**
```bash
poetry run pytest tests/ -m "not integration and not stress and not e2e"
```

---

## 1. Coverage Map — Every Spec Scenario → Test File + Function

### 1.1 env-validation (NEW)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | All validations pass | `tests/core/test_validation.py` | `test_validate_environment_all_pass` |
| 2 | snapshot_dir does not exist | `tests/core/test_validation.py` | `test_validate_environment_snapshot_dir_missing` |
| 3 | virsh binary not in PATH | `tests/core/test_validation.py` | `test_validate_environment_virsh_not_in_path` |
| 4 | libvirt rejects dominfo — VM not defined | `tests/core/test_validation.py` | `test_validate_environment_vm_not_defined` |
| 5 | Ondemand target missing — skip target | `tests/core/test_validation.py` | `test_validate_environment_ondemand_target_missing_skipped` |
| 6 | Always mode target missing — error | `tests/core/test_validation.py` | `test_validate_environment_always_mode_target_missing_error` |

### 1.2 core-orchestrator (MODIFIED)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | All validations pass (Core level) | `tests/core/test_validation.py` | `test_pipeline_continues_after_validation_pass` |
| 2 | snapshot_dir does not exist (Core level) | `tests/core/test_validation.py` | `test_pipeline_returns_failure_on_missing_snapshot_dir` |
| 3 | Deferred blockcommits executed on shut-off VM | `tests/core/test_deferred.py` | `test_deferred_blockcommits_executed_on_shutoff_vm` |
| 4 | Deferred blockcommits skipped on running VM | `tests/core/test_deferred.py` | `test_deferred_blockcommits_skipped_on_running_vm` |
| 5 | Metadata verification failure marks backup as failed | `tests/core/test_pipeline.py` | `test_metadata_verification_failure_marks_backup_failed` |
| 6 | Pipeline with always mode (validation first) | `tests/core/test_pipeline.py` | `test_pipeline_always_mode_validation_first` |
| 7 | Pipeline with onchange mode, no changes (validation first) | `tests/core/test_pipeline.py` | `test_pipeline_onchange_no_changes_validation_first` |

### 1.3 backup-verification (NEW)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Default verification is metadata | `tests/config/test_model.py` | `test_target_config_default_verify_metadata` |
| 2 | Metadata verification passes | `tests/modules/backup/test_verification.py` | `test_metadata_verification_passes` |
| 3 | Metadata verification fails — wrong format | `tests/modules/backup/test_verification.py` | `test_metadata_verification_wrong_format` |
| 4 | Metadata verification fails — size mismatch | `tests/modules/backup/test_verification.py` | `test_metadata_verification_size_mismatch` |
| 5 | Full verification passes | `tests/modules/backup/test_verification.py` | `test_full_verification_passes` |
| 6 | Full verification detects corruption | `tests/modules/backup/test_verification.py` | `test_full_verification_detects_corruption` |
| 7 | No verification when verify=off | `tests/modules/backup/test_verification.py` | `test_no_verification_when_verify_off` |

### 1.4 snapshot-provider (MODIFIED)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Snapshot with quiesce enabled | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_with_quiesce_enabled` |
| 2 | Snapshot without quiesce (default) | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_without_quiesce_default` |
| 3 | Successful snapshot creation | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_success` *(existing — verify quiesce param defaults to False)* |
| 4 | virsh command fails | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_virsh_fails` *(existing — unchanged)* |
| 5 | virsh command times out | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_timeout` *(existing — verify 120s default, 180s quiesce)* |

### 1.5 quiesce-snapshot (NEW)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Quiesce enabled | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_quiesce_enabled` |
| 2 | Quiesce disabled (default) | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_quiesce_disabled_default` |
| 3 | Guest agent not installed | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_quiesce_guest_agent_not_installed` |
| 4 | Quiesce snapshot timeout (180s) | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_quiesce_timeout_180s` |

### 1.6 backup-provider (MODIFIED)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | First backup — full pull via NBD | `tests/modules/backup/test_bitmap.py` | `test_first_backup_full_pull_via_nbd` |
| 2 | Incremental backup — dirty blocks via NBD | `tests/modules/backup/test_bitmap.py` | `test_incremental_backup_dirty_blocks_via_nbd` |
| 3 | Libvirt too old — factory fallback | `tests/factory/test_default.py` | `test_factory_falls_back_on_old_libvirt` |
| 4 | Metadata verification passes (provider level) | `tests/modules/backup/test_verification.py` | `test_backup_provider_metadata_verification_passes` |
| 5 | Verification failure produces error (provider level) | `tests/modules/backup/test_verification.py` | `test_backup_provider_verification_failure` |
| 6 | Constructor accepts IShell | `tests/modules/backup/test_bitmap.py` | `test_constructor_accepts_ishell_implements_abc` *(existing — update for NBD version check)* |
| 7 | First backup — full NBD export (no prior checkpoint) | `tests/modules/backup/test_bitmap.py` | `test_first_backup_full_nbd_export` |
| 8 | Incremental backup — dirty blocks only | `tests/modules/backup/test_bitmap.py` | `test_incremental_backup_dirty_blocks_only` |
| 9 | Transfer failure preserves checkpoint | `tests/modules/backup/test_bitmap.py` | `test_transfer_failure_preserves_checkpoint` |

### 1.7 nbd-bitmap-backup (NEW)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | First backup — full pull via NBD | `tests/modules/backup/test_bitmap.py` | `test_nbd_first_backup_full_pull` |
| 2 | Incremental backup — dirty blocks via NBD checkpoint | `tests/modules/backup/test_bitmap.py` | `test_nbd_incremental_backup_dirty_blocks` |
| 3 | Socket cleanup on success | `tests/modules/backup/test_bitmap.py` | `test_nbd_socket_cleanup_on_success` |
| 4 | Socket cleanup on failure | `tests/modules/backup/test_bitmap.py` | `test_nbd_socket_cleanup_on_failure` |
| 5 | Stale socket from crashed process | `tests/modules/backup/test_bitmap.py` | `test_nbd_stale_socket_cleanup` |
| 6 | Libvirt too old | `tests/modules/backup/test_bitmap.py` | `test_nbd_constructor_rejects_old_libvirt` |

### 1.8 lifecycle-manager (MODIFIED)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Successful qemu-img commit | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_qemu_img_commit_success` |
| 2 | Default mode returns BlockCommitManager | `tests/factory/test_default.py` | `test_factory_default_lifecycle_returns_blockcommit` |
| 3 | Successful blockcommit of a single snapshot | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_single_snapshot_success` *(existing — unchanged)* |
| 4 | Blockcommit fails — virsh returns error | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_virsh_error` *(existing — unchanged)* |
| 5 | Blockcommit blocked by AppArmor | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_blocked_by_apparmor` |
| 6 | Blockcommit blocked by SELinux | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_blocked_by_selinux` |
| 7 | Empty snapshot list — nothing to merge | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_empty_list_no_op` *(existing — unchanged)* |
| 8 | Blockcommit times out | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_timeout` *(existing — unchanged)* |

### 1.9 offline-commit (NEW)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Constructor accepts IShell | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_qemu_img_commit_constructor_accepts_ishell` |
| 2 | Successful qemu-img commit | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_qemu_img_commit_success` |
| 3 | qemu-img commit fails | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_qemu_img_commit_fails` |
| 4 | Default mode returns BlockCommitManager | `tests/factory/test_default.py` | `test_factory_default_lifecycle_returns_blockcommit` |
| 5 | Qemu-img mode returns QemuImgCommitManager | `tests/factory/test_default.py` | `test_factory_qemu_img_mode_returns_qemu_img_commit` |

### 1.10 deferred-operations (NEW)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Add and retrieve deferred blockcommit | `tests/state/test_manager.py` | `test_add_and_retrieve_deferred_blockcommit` |
| 2 | Clear deferred operations | `tests/state/test_manager.py` | `test_clear_deferred_operations` |
| 3 | No deferred operations for VM | `tests/state/test_manager.py` | `test_no_deferred_operations_empty_list` |
| 4 | AppArmor blocks blockcommit | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_blocked_by_apparmor_returns_deferred` |
| 5 | SELinux blocks blockcommit | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_blocked_by_selinux_returns_deferred` |
| 6 | Normal virsh failure (not MAC-related) | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_normal_failure_no_deferral` |
| 7 | VM shut off — deferred blockcommits executed | `tests/core/test_deferred.py` | `test_deferred_blockcommits_executed_on_shutoff_vm` |
| 8 | VM running — deferred blockcommits skipped | `tests/core/test_deferred.py` | `test_deferred_blockcommits_skipped_on_running_vm` |
| 9 | Deferred blockcommit still fails on retry | `tests/core/test_deferred.py` | `test_deferred_blockcommit_fails_on_retry_remains_queued` |

### 1.11 state-management (MODIFIED)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Add and retrieve deferred operations | `tests/state/test_manager.py` | `test_add_and_retrieve_deferred_operations` |
| 2 | Clear deferred operations | `tests/state/test_manager.py` | `test_clear_deferred_operations` |
| 3 | Deferred operations persisted to JSON | `tests/state/test_manager.py` | `test_deferred_operations_persisted_to_json` |
| 4 | No deferred operations — empty list | `tests/state/test_manager.py` | `test_no_deferred_operations_empty_list` |

### 1.12 shell-abstraction (MODIFIED)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Check mode does not log error on failure | `tests/utils/test_shell.py` | `test_check_mode_no_error_log_on_failure` |

### 1.13 config-model (MODIFIED)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Default verify is metadata | `tests/config/test_model.py` | `test_target_config_default_verify_metadata` |
| 2 | Explicit full verification | `tests/config/test_model.py` | `test_target_config_explicit_verify_full` |
| 3 | Quiesce default is disabled | `tests/config/test_model.py` | `test_vm_config_snapshot_quiesce_default_false` |
| 4 | RetentionPolicy with hourly and daily limits | `tests/config/test_model.py` | `test_retention_policy_hourly_daily` *(existing — unchanged)* |
| 5 | RetentionPolicy defaults | `tests/config/test_model.py` | `test_retention_policy_defaults` *(existing — unchanged)* |
| 6 | preserve_min = "latest" | `tests/config/test_model.py` | `test_retention_policy_preserve_min_latest` |

### 1.14 retention-engine (MODIFIED)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Hourly retention with 24h policy | `tests/modules/retention/test_time_based.py` | `test_hourly_retention_24h` *(existing — unchanged)* |
| 2 | preserve_min keeps all recent items | `tests/modules/retention/test_time_based.py` | `test_preserve_min_keeps_recent` *(existing — unchanged)* |
| 3 | Daily retention identifies first snapshot of each day | `tests/modules/retention/test_time_based.py` | `test_daily_retention_first_per_day` *(existing — unchanged)* |
| 4 | preserve_min "all" keeps everything | `tests/modules/retention/test_time_based.py` | `test_preserve_min_all_keeps_everything` *(existing — unchanged)* |
| 5 | preserve_min "latest" keeps only the most recent item | `tests/modules/retention/test_time_based.py` | `test_preserve_min_latest_keeps_only_most_recent` |

### 1.15 change-detection (MODIFIED)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Allocation map differs — changes detected | `tests/modules/change/test_map_detector.py` | `test_map_changed_detected` |
| 2 | Map command fails — fail-safe | `tests/modules/change/test_map_detector.py` | `test_map_command_fails_failsafe` |

### 1.16 map-change-detection (NEW)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Allocation map unchanged — no changes | `tests/modules/change/test_map_detector.py` | `test_map_unchanged_no_changes` |
| 2 | Allocation map changed — new region added | `tests/modules/change/test_map_detector.py` | `test_map_changed_new_region` |
| 3 | Zero-fill changes allocation map without total size change | `tests/modules/change/test_map_detector.py` | `test_zero_fill_changes_map_not_size` |
| 4 | qemu-img map command fails | `tests/modules/change/test_map_detector.py` | `test_map_command_fails_failsafe` |
| 5 | Map mode selected | `tests/factory/test_default.py` | `test_factory_map_mode_returns_map_detector` |
| 6 | Unrecognized mode falls back to allocation-size | `tests/factory/test_default.py` | `test_factory_unrecognized_mode_falls_back` |

### 1.17 cli-interface (MODIFIED)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Tree output for backing chain | `tests/cli/test_app.py` | `test_tree_flag_parses` |
| 2 | -L translates to --format long | `tests/cli/test_app.py` | `test_long_flag_translates_to_format_long` |

### 1.18 tree-listing (NEW)

| # | Spec Scenario | Test File | Test Function |
|---|---|---|---|
| 1 | Tree output for a 3-level backing chain | `tests/cli/test_tree.py` | `test_tree_output_3_level_chain` |
| 2 | Flat output without --tree | `tests/cli/test_tree.py` | `test_flat_output_without_tree` |
| 3 | -L with list command | `tests/cli/test_app.py` | `test_long_flag_with_list` |

---

## 2. Contract Tests — Interface Compliance for New Implementations

Per `TESTING.md` rule: every new concrete implementation is added to the
parametrized contract tests so that adding a new implementation passes existing
contract tests without changes.

| Interface | New Implementation | Contract Test File | New Parametrize Entry |
|---|---|---|---|
| `ILifecycleManager` | `QemuImgCommitManager` | `tests/interfaces/test_lifecycle_manager.py` | `(QemuImgCommitManager, {"shell": MockShell()})` |
| `IChangeDetector` | `MapChangeDetector` | `tests/interfaces/test_change_detector.py` | `(MapChangeDetector, {"shell": MockShell(), "state": InMemoryStateManager()})` |
| `IBackupProvider` | `BitmapBackupProvider` v2 (NBD) | `tests/interfaces/test_backup_provider.py` | Update `_make_bitmap_shell()` to return libvirt >= 6.0 version string |
| `ISnapshotProvider` | `ExternalSnapshotProvider` (quiesce) | `tests/interfaces/test_snapshot_provider.py` | Update `test_snapshot_provider_create_returns_result` to pass `quiesce=False` |
| `IStateManager` | `InMemoryStateManager` + `JsonStateManager` (deferred ops) | `tests/interfaces/test_state_manager.py` | `test_istate_manager_deferred_operations_methods_exist` |
| `IShell` | `SubprocessShell` (check param) | `tests/interfaces/test_shell.py` | `test_ishell_run_accepts_check_parameter` |

### New Contract Test Functions

| Test File | Test Function | Verifies |
|---|---|---|
| `tests/interfaces/test_lifecycle_manager.py` | `test_qemu_img_commit_manager_is_ilifecycle_manager` | `issubclass(QemuImgCommitManager, ILifecycleManager)` |
| `tests/interfaces/test_lifecycle_manager.py` | `test_qemu_img_commit_manager_no_core_inheritance` | `not issubclass(QemuImgCommitManager, Core)` |
| `tests/interfaces/test_lifecycle_manager.py` | `test_qemu_img_commit_manager_requires_shell` | Constructor requires `IShell` |
| `tests/interfaces/test_change_detector.py` | `test_map_change_detector_is_ichange_detector` | `issubclass(MapChangeDetector, IChangeDetector)` |
| `tests/interfaces/test_change_detector.py` | `test_map_change_detector_no_core_inheritance` | `not issubclass(MapChangeDetector, Core)` |
| `tests/interfaces/test_state_manager.py` | `test_istate_manager_deferred_operations_methods_exist` | `get_deferred_operations`, `add_deferred_blockcommit`, `clear_deferred_operations` are abstract methods |
| `tests/interfaces/test_shell.py` | `test_ishell_run_accepts_check_parameter` | `IShell.run()` signature includes `check: bool = False` |

---

## 3. Mock Tests — Updates to Mocks for New ABC Methods

Per `TESTING.md` rule: every ABC gets at least one mock implementation, and
every mock method returns a valid result type.

| Mock File | Mock Class | New Methods / Changes |
|---|---|---|
| `tests/mocks/mock_state.py` | `InMemoryStateManager` | Add `get_deferred_operations()`, `add_deferred_blockcommit()`, `clear_deferred_operations()` backed by in-memory `dict` |
| `tests/mocks/mock_modules.py` | `MockChangeDetector` | No change needed (already returns `ChangeResult`) |
| `tests/mocks/mock_modules.py` | `MockLifecycleManager` | No change needed (already returns `CommitResult`) |
| `tests/mocks/mock_factory.py` | `MockVMModuleFactory` | `create_change_detector("allocation-map")` returns `MockChangeDetector`; `create_lifecycle_manager(mode="qemu-img")` returns `MockLifecycleManager` |

### New Mock Test Functions

| Test File | Test Function | Verifies |
|---|---|---|
| `tests/mocks/test_mock_state.py` | `test_in_memory_state_manager_implements_deferred_operations` | `isinstance(InMemoryStateManager(), IStateManager)` after adding deferred methods |
| `tests/mocks/test_mock_state.py` | `test_in_memory_state_manager_add_get_clear_deferred` | Round-trip: add → get → clear → get empty |

---

## 4. Delegation Groups — Non-Overlapping File Groups for Parallel Execution

Each group is a self-contained set of test files with no import or fixture
dependencies on files in other groups. Groups can be assigned to separate
Mr.Tester subagents running in parallel.

### Group A — Core Orchestration (validation + deferred + pipeline)

**Files:**
- `tests/core/test_validation.py` *(new)*
- `tests/core/test_deferred.py` *(new)*
- `tests/core/test_pipeline.py` *(modified)*
- `tests/core/test_engine.py` *(modified — verify quiesce wiring)*

**Scope:** `Core._validate_environment()`, deferred operations integration in
`_execute_snapshot_steps()`, post-transfer verification in `_backup_target()`,
pipeline step ordering with new validation/deferral steps.

**Dependencies:** `mock_factory`, `mock_state`, `mock_shell`, `make_vm_config`,
`make_target` from `conftest.py`.

**Run command:**
```bash
poetry run pytest tests/core/ -v
```

### Group B — Backup Providers (NBD bitmap + verification + file copy)

**Files:**
- `tests/modules/backup/test_bitmap.py` *(modified — full rewrite for NBD)*
- `tests/modules/backup/test_verification.py` *(new)*
- `tests/modules/backup/test_copy.py` *(modified — add verification step)*

**Scope:** `BitmapBackupProvider` v2 NBD pull-model, socket lifecycle,
libvirt version check, post-transfer metadata/full verification in both
`FileCopyBackupProvider` and `BitmapBackupProvider`.

**Dependencies:** `mock_shell`, `make_vm_config`, `make_target`, `tmp_path`
from `conftest.py`.

**Run command:**
```bash
poetry run pytest tests/modules/backup/ -v
```

### Group C — Lifecycle Managers (blockcommit MAC + qemu-img commit)

**Files:**
- `tests/modules/lifecycle/test_blockcommit.py` *(modified — AppArmor/SELinux detection)*
- `tests/modules/lifecycle/test_qemu_img_commit.py` *(new)*

**Scope:** `BlockCommitManager` MAC denial detection (AppArmor/SELinux),
`QemuImgCommitManager` offline commit via `qemu-img commit -b -d`.

**Dependencies:** `mock_shell`, `make_vm_config` from `conftest.py`. Uses
local `CountingShell` helper (defined in `test_blockcommit.py`).

**Run command:**
```bash
poetry run pytest tests/modules/lifecycle/ -v
```

### Group D — Change Detection (map detector + allocation detector)

**Files:**
- `tests/modules/change/test_map_detector.py` *(new)*
- `tests/modules/change/test_allocation.py` *(unchanged — verify no regressions)*

**Scope:** `MapChangeDetector` via `qemu-img map --output=json`, allocated-region
comparison, zero-fill detection, fail-safe on command failure.

**Dependencies:** `mock_state`, `make_vm_config` from `conftest.py`. Uses local
`CallTrackingShell` helper (defined in `test_allocation.py`).

**Run command:**
```bash
poetry run pytest tests/modules/change/ -v
```

### Group E — Snapshot Provider (quiesce support)

**Files:**
- `tests/modules/snapshot/test_external.py` *(modified — quiesce param)*

**Scope:** `ExternalSnapshotProvider.create()` with `quiesce=True` passes
`--quiesce` to virsh, 180s timeout for quiesce, guest-agent failure handling,
no silent fallback.

**Dependencies:** `mock_shell`, `make_vm_config` from `conftest.py`.

**Run command:**
```bash
poetry run pytest tests/modules/snapshot/ -v
```

### Group F — State Management (deferred operations persistence)

**Files:**
- `tests/state/test_manager.py` *(modified — deferred operations)*

**Scope:** `JsonStateManager` deferred operations: add/get/clear,
JSON persistence under `deferred_operations` key, round-trip across runs.

**Dependencies:** `tmp_path` from pytest builtin.

**Run command:**
```bash
poetry run pytest tests/state/ -v
```

### Group G — Factory (new branches for all new modules)

**Files:**
- `tests/factory/test_default.py` *(modified — new branches)*

**Scope:** `DefaultFactory.create_change_detector("allocation-map")` returns
`MapChangeDetector`; `create_lifecycle_manager(mode="qemu-img")` returns
`QemuImgCommitManager`; `create_backup_provider()` with bitmap mode falls back
to `FileCopyBackupProvider` on old libvirt; unrecognized change-detection mode
falls back to `AllocationSizeDetector`.

**Dependencies:** `mock_shell`, `mock_state`, `make_vm_config`, `make_target`
from `conftest.py`.

**Run command:**
```bash
poetry run pytest tests/factory/ -v
```

### Group H — Config Model (new fields + immutability)

**Files:**
- `tests/config/test_model.py` *(modified — new fields)*

**Scope:** `TargetConfig.verify` field (default `"metadata"`, values
`"off"`/`"metadata"`/`"full"`, frozen), `VMConfig.snapshot_quiesce` field
(default `False`, frozen), `RetentionPolicy.preserve_min` accepts `"latest"`.

**Dependencies:** None (pure dataclass tests).

**Run command:**
```bash
poetry run pytest tests/config/test_model.py -v
```

### Group I — Retention Engine (preserve_min="latest")

**Files:**
- `tests/modules/retention/test_time_based.py` *(modified — "latest" support)*

**Scope:** `TimeBasedRetention` with `preserve_min="latest"` keeps only the
single most recent item. Existing tests remain unchanged.

**Dependencies:** Timestamp fixtures in `tests/fixtures/timestamps/`.

**Run command:**
```bash
poetry run pytest tests/modules/retention/ -v
```

### Group J — Shell Abstraction (check parameter)

**Files:**
- `tests/utils/test_shell.py` *(modified — check param)*

**Scope:** `IShell.run(check=True)` returns `ShellResult` without logging
ERROR on failure; logs at DEBUG instead.

**Dependencies:** None (uses real `SubprocessShell` with safe commands).

**Run command:**
```bash
poetry run pytest tests/utils/test_shell.py -v
```

### Group K — CLI Interface (tree + long flags)

**Files:**
- `tests/cli/test_app.py` *(modified — --tree, -L flags)*
- `tests/cli/test_commands.py` *(modified — tree dispatch)*
- `tests/cli/test_tree.py` *(new)*

**Scope:** `--tree` flag on `list snapshots`, `--long`/`-L` global flag,
tree-format backing-chain visualization, flat output without `--tree`.

**Dependencies:** `cli_app` fixture from `conftest.py`.

**Run command:**
```bash
poetry run pytest tests/cli/ -v
```

### Group L — Contract Tests (interface compliance)

**Files:**
- `tests/interfaces/test_lifecycle_manager.py` *(modified — QemuImgCommitManager)*
- `tests/interfaces/test_change_detector.py` *(modified — MapChangeDetector)*
- `tests/interfaces/test_backup_provider.py` *(modified — NBD BitmapBackupProvider)*
- `tests/interfaces/test_snapshot_provider.py` *(modified — quiesce param)*
- `tests/interfaces/test_state_manager.py` *(modified — deferred ops methods)*
- `tests/interfaces/test_shell.py` *(modified — check param)*

**Scope:** ABC enforcement, `isinstance` checks, no Core inheritance (D1),
correct return types for all new implementations.

**Dependencies:** `MockShell`, `InMemoryStateManager` from `tests/mocks/`.

**Run command:**
```bash
poetry run pytest tests/interfaces/ -v
```

### Group M — Mock Tests (mock compliance)

**Files:**
- `tests/mocks/test_mock_state.py` *(modified — deferred ops)*
- `tests/mocks/test_mock_factory.py` *(modified — new branches)*

**Scope:** `InMemoryStateManager` implements deferred operations methods;
`MockVMModuleFactory` returns correct types for new factory branches.

**Dependencies:** None (mock self-tests).

**Run command:**
```bash
poetry run pytest tests/mocks/ -v
```

---

## 5. Modified Existing Tests — Files Needing Additions

The following existing test files require new test functions or modifications
to existing ones. Each entry lists the specific additions.

### 5.1 `tests/modules/snapshot/test_external.py`

| Type | Function | Change |
|---|---|---|
| NEW | `test_create_snapshot_with_quiesce_enabled` | Verify `--quiesce` flag in virsh command when `quiesce=True` |
| NEW | `test_create_snapshot_without_quiesce_default` | Verify `--quiesce` absent when `quiesce` omitted |
| NEW | `test_create_snapshot_quiesce_guest_agent_not_installed` | virsh returns non-zero with guest-agent error; no silent fallback |
| NEW | `test_create_snapshot_quiesce_timeout_180s` | Timeout is 180s for quiesce (vs 120s default); verify error contains "timed out" |
| MODIFY | `test_create_snapshot_success` | Pass `quiesce=False` explicitly to verify backward-compatible signature |
| MODIFY | `test_create_snapshot_timeout` | Verify 120s default timeout (non-quiesce path) |

### 5.2 `tests/modules/backup/test_bitmap.py`

| Type | Function | Change |
|---|---|---|
| REWRITE | `test_constructor_accepts_ishell_implements_abc` | Update version check mock from `qemu-img --version` to `virsh --version` (libvirt >= 6.0) |
| REWRITE | `test_first_backup_full_copy_no_prior_checkpoint` | Replace `qemu-img convert` (no `--bitmap`) with `virsh backup-begin` + `qemu-img convert -n nbd:unix:<socket>` |
| REWRITE | `test_incremental_backup_extracts_dirty_blocks_only` | Replace `--bitmap` flag with NBD checkpoint-based incremental export |
| REWRITE | `test_checkpoint_cleanup_after_successful_transfer` | Update for NBD checkpoint lifecycle (checkpoint persists, not deleted) |
| REWRITE | `test_transfer_failure_preserves_checkpoint` | Update for NBD: socket cleanup in finally block, checkpoint preserved |
| REWRITE | `test_list_checkpoints_filters_qsnap_prefix` | Update for NBD checkpoint naming |
| REWRITE | `test_constructor_rejects_unsupported_qemu_version` | Change from QEMU version check to libvirt version check (>= 6.0) |
| NEW | `test_nbd_socket_cleanup_on_success` | Socket removed via `rm -f` after successful convert |
| NEW | `test_nbd_socket_cleanup_on_failure` | Socket removed in finally block even when convert fails |
| NEW | `test_nbd_stale_socket_cleanup` | Stale socket from crashed process removed before `backup-begin` |
| NEW | `test_nbd_socket_path_uses_pid` | Socket path is `/tmp/qsnap-backup-{pid}.sock` |

### 5.3 `tests/modules/backup/test_copy.py`

| Type | Function | Change |
|---|---|---|
| NEW | `test_transfer_missing_metadata_verification_default` | After `cp`, `qemu-img info` is called on target (verify="metadata" default) |
| NEW | `test_transfer_missing_full_verification` | After metadata check, `qemu-img compare` is called (verify="full") |
| NEW | `test_transfer_missing_no_verification_when_off` | No qemu-img calls after cp when verify="off" |
| MODIFY | `test_transfer_missing_new_snapshot_empty_target` | Add mock for `qemu-img info` verification step (default metadata) |

### 5.4 `tests/modules/lifecycle/test_blockcommit.py`

| Type | Function | Change |
|---|---|---|
| NEW | `test_blockcommit_blocked_by_apparmor` | stderr contains "Permission denied" + "apparmor" → `CommitResult(success=False, error="blocked by apparmor")` |
| NEW | `test_blockcommit_blocked_by_selinux` | stderr contains "Operation not permitted" + "AVC" → `CommitResult(success=False, error="blocked by selinux")` |
| NEW | `test_blockcommit_blocked_by_apparmor_returns_deferred` | Verify `CommitResult` error string enables Core to record deferral |
| NEW | `test_blockcommit_blocked_by_selinux_returns_deferred` | Verify `CommitResult` error string enables Core to record deferral |
| NEW | `test_blockcommit_normal_failure_no_deferral` | stderr "No such file or directory" → normal error, no MAC deferral |

### 5.5 `tests/factory/test_default.py`

| Type | Function | Change |
|---|---|---|
| NEW | `test_factory_map_mode_returns_map_detector` | `create_change_detector("allocation-map")` returns `MapChangeDetector` |
| NEW | `test_factory_unrecognized_mode_falls_back` | `create_change_detector("unknown")` returns `AllocationSizeDetector` |
| NEW | `test_factory_qemu_img_mode_returns_qemu_img_commit` | `create_lifecycle_manager(mode="qemu-img")` returns `QemuImgCommitManager` |
| NEW | `test_factory_default_lifecycle_returns_blockcommit` | `create_lifecycle_manager()` returns `BlockCommitManager` |
| NEW | `test_factory_falls_back_on_old_libvirt` | bitmap mode + libvirt < 6.0 → `RuntimeError` caught → `FileCopyBackupProvider` |
| MODIFY | `test_factory_selects_bitmap_provider_for_bitmap_mode` | Update version mock from `qemu-img --version` to `virsh --version` (libvirt >= 6.0) |
| MODIFY | `test_factory_falls_back_to_file_copy_on_old_qemu` | Update to libvirt version check (was QEMU version check) |

### 5.6 `tests/config/test_model.py`

| Type | Function | Change |
|---|---|---|
| NEW | `test_target_config_default_verify_metadata` | `TargetConfig(path=...)` → `verify == "metadata"` |
| NEW | `test_target_config_explicit_verify_full` | `TargetConfig(path=..., verify="full")` → `verify == "full"` |
| NEW | `test_target_config_verify_off` | `TargetConfig(path=..., verify="off")` → `verify == "off"` |
| NEW | `test_target_config_verify_immutable` | Frozen dataclass: `target.verify = "off"` raises `FrozenInstanceError` |
| NEW | `test_vm_config_snapshot_quiesce_default_false` | `VMConfig(...)` → `snapshot_quiesce is False` |
| NEW | `test_vm_config_snapshot_quiesce_true` | `VMConfig(..., snapshot_quiesce=True)` → `snapshot_quiesce is True` |
| NEW | `test_vm_config_snapshot_quiesce_immutable` | Frozen: `vm.snapshot_quiesce = False` raises `FrozenInstanceError` |
| NEW | `test_retention_policy_preserve_min_latest` | `RetentionPolicy(preserve_min="latest")` → `preserve_min == "latest"` |

### 5.7 `tests/modules/retention/test_time_based.py`

| Type | Function | Change |
|---|---|---|
| NEW | `test_preserve_min_latest_keeps_only_most_recent` | 10 items, `preserve_min="latest"` → only 1 in `keep` (the most recent) |

### 5.8 `tests/state/test_manager.py`

| Type | Function | Change |
|---|---|---|
| NEW | `test_add_and_retrieve_deferred_blockcommit` | `add_deferred_blockcommit("vm1", ["snap1.qcow2"], "apparmor")` → `get_deferred_operations("vm1")` returns 1 item |
| NEW | `test_clear_deferred_operations` | Add 2 items → `clear_deferred_operations("vm1")` → `get_deferred_operations("vm1")` returns `[]` |
| NEW | `test_no_deferred_operations_empty_list` | `get_deferred_operations("vm_new")` → `[]` |
| NEW | `test_deferred_operations_persisted_to_json` | Write deferred ops → reload `JsonStateManager` → operations loaded correctly |
| NEW | `test_deferred_blockcommit_dataclass_fields` | `DeferredBlockcommit` is frozen with `snapshots`, `reason`, `since` fields |

### 5.9 `tests/utils/test_shell.py`

| Type | Function | Change |
|---|---|---|
| NEW | `test_check_mode_no_error_log_on_failure` | `shell.run(["false"], timeout=30, check=True)` → `ShellResult(success=False)` logged at DEBUG, not ERROR |
| NEW | `test_check_mode_default_false_logs_error_on_failure` | `shell.run(["false"], timeout=30)` (check=False default) → logged at ERROR |
| NEW | `test_check_mode_returns_shellresult` | `shell.run(..., check=True)` returns `ShellResult` with `success=False` |

### 5.10 `tests/cli/test_app.py`

| Type | Function | Change |
|---|---|---|
| NEW | `test_tree_flag_parses` | `parser.parse_args(["list", "snapshots", "--tree"])` → `ns.tree is True` |
| NEW | `test_long_flag_translates_to_format_long` | `parser.parse_args(["-L", "list", "snapshots"])` → `ns.format == "long"` |
| NEW | `test_long_flag_with_list` | `parser.parse_args(["--long", "list", "snapshots"])` → `ns.format == "long"` |
| MODIFY | `test_help_text_lists_subcommands_and_flags` | Add `"--tree"` and `"--long"` / `"-L"` to expected flags |

### 5.11 `tests/cli/test_commands.py`

| Type | Function | Change |
|---|---|---|
| NEW | `test_list_snapshots_tree_dispatches_to_core_list_snapshots` | `handle_list` with `tree=True` → `core.list_snapshots()` called |
| MODIFY | `_make_list_args` | Add `"tree": False` to defaults |

### 5.12 `tests/mocks/mock_state.py`

| Type | Function | Change |
|---|---|---|
| NEW method | `get_deferred_operations` | Return `list[DeferredBlockcommit]` from in-memory dict |
| NEW method | `add_deferred_blockcommit` | Append `DeferredBlockcommit` to in-memory dict |
| NEW method | `clear_deferred_operations` | Clear deferred list for a VM |

### 5.13 `tests/mocks/mock_factory.py`

| Type | Function | Change |
|---|---|---|
| MODIFY | `create_change_detector` | Accept `mode` param; return `MockChangeDetector` for all modes |
| MODIFY | `create_lifecycle_manager` | Accept optional `mode: str = "virsh"` param; return `MockLifecycleManager` for all modes |

### 5.14 `tests/core/test_pipeline.py`

| Type | Function | Change |
|---|---|---|
| NEW | `test_metadata_verification_failure_marks_backup_failed` | `transfer_missing` returns `BackupResult(success=False, error="verification failed: ...")` → `backup_failed=True` |
| MODIFY | `test_pipeline_always_mode_creates_snapshot` | Rename to `test_pipeline_always_mode_validation_first`; verify validation runs before snapshot |
| MODIFY | `test_pipeline_onchange_no_changes_skips_snapshot` | Rename to `test_pipeline_onchange_no_changes_validation_first`; verify validation runs first |

### 5.15 `tests/interfaces/test_backup_provider.py`

| Type | Function | Change |
|---|---|---|
| MODIFY | `_make_bitmap_shell` | Update version string to libvirt >= 6.0 (e.g. `"virsh 7.2.0"`) |

### 5.16 `tests/interfaces/test_lifecycle_manager.py`

| Type | Function | Change |
|---|---|---|
| MODIFY | parametrize in `test_lifecycle_manager_blockcommit_returns_commit_result` | Add `(QemuImgCommitManager, {"shell": MockShell()})` entry |

### 5.17 `tests/interfaces/test_change_detector.py`

| Type | Function | Change |
|---|---|---|
| MODIFY | parametrize in `test_change_detector_has_changed_returns_change_result` | Add `(MapChangeDetector, {"shell": MockShell(), "state": InMemoryStateManager()})` entry |
| MODIFY | parametrize in `test_change_detector_has_changed_accepts_disk_parameter` | Add `MapChangeDetector` entry |

### 5.18 `tests/interfaces/test_snapshot_provider.py`

| Type | Function | Change |
|---|---|---|
| MODIFY | `test_snapshot_provider_create_returns_result` | Pass `quiesce=False` to `provider.create()` |

### 5.19 `tests/interfaces/test_state_manager.py`

| Type | Function | Change |
|---|---|---|
| NEW | `test_istate_manager_deferred_operations_methods_exist` | Verify `get_deferred_operations`, `add_deferred_blockcommit`, `clear_deferred_operations` are in `__abstractmethods__` |

### 5.20 `tests/interfaces/test_shell.py`

| Type | Function | Change |
|---|---|---|
| NEW | `test_ishell_run_accepts_check_parameter` | Verify `IShell.run` signature includes `check: bool = False` |

---

## 6. Risk Mitigation Tests — For Each Risk in design.md

Each risk from the design's Risks/Trade-offs table maps to one or more test
functions that verify the mitigation is implemented.

### Risk 1 — NBD socket cleanup failure

> **Risk:** Crash during `backup-begin` leaves stale Unix socket. Next backup
> fails with "address already in use".
> **Mitigation:** Always `rm -f` socket before `backup-begin`. Socket path
> uses PID: `/tmp/qsnap-backup-{pid}.sock`.

| Test File | Test Function | Verifies |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | `test_risk_nbd_socket_cleanup_before_backup_begin` | `rm -f /tmp/qsnap-backup-{pid}.sock` is called before `virsh backup-begin` |
| `tests/modules/backup/test_bitmap.py` | `test_risk_nbd_socket_path_uses_pid` | Socket path contains current PID, ensuring uniqueness across processes |
| `tests/modules/backup/test_bitmap.py` | `test_risk_nbd_stale_socket_removed` | Pre-existing socket file at the PID-based path is removed before backup-begin |

### Risk 2 — `qemu-img compare` on multi-TB disk

> **Risk:** `verify = "full"` can take hours. Backup pipeline hangs,
> subsequent runs delayed.
> **Mitigation:** Timeout: 7200s (2h). Document as "use only on fast storage,
> not as default."

| Test File | Test Function | Verifies |
|---|---|---|
| `tests/modules/backup/test_verification.py` | `test_risk_full_verification_timeout_7200s` | `qemu-img compare` is invoked with `timeout=7200`; if it times out, `BackupResult(success=False, error="verification failed: timed out")` |
| `tests/modules/backup/test_verification.py` | `test_risk_full_verification_not_default` | Default `verify` is `"metadata"`, not `"full"` — prevents accidental multi-hour hangs |

### Risk 3 — Deferred blockcommit accumulation

> **Risk:** VM runs for weeks, dozens of snapshots pile up. Disk fills,
> VM pauses.
> **Mitigation:** `qsnap check` warns when chain length exceeds configurable
> threshold. `qsnap list` shows deferred count.

| Test File | Test Function | Verifies |
|---|---|---|
| `tests/core/test_deferred.py` | `test_risk_deferred_accumulation_logs_warning` | When deferred queue has > threshold entries, a WARNING is logged |
| `tests/core/test_deferred.py` | `test_risk_deferred_count_visible_in_list` | `core.list_snapshots()` includes deferred count in output |
| `tests/core/test_deferred.py` | `test_risk_deferred_queue_grows_across_runs` | Multiple MAC-denied blockcommits accumulate in state across pipeline runs |

### Risk 4 — `--quiesce` failure on agent timeout

> **Risk:** Snapshot creation hangs waiting for frozen FS. Pipeline stalls.
> **Mitigation:** `virsh snapshot-create-as` timeout extended to 180s for
> quiesce. `ShellResult` captures timeout.

| Test File | Test Function | Verifies |
|---|---|---|
| `tests/modules/snapshot/test_external.py` | `test_risk_quiesce_timeout_180s_not_120s` | When `quiesce=True`, timeout passed to `shell.run()` is 180, not 120 |
| `tests/modules/snapshot/test_external.py` | `test_risk_quiesce_agent_timeout_returns_failure` | Timeout produces `SnapshotResult(success=False, error="timed out")` — no hang |
| `tests/modules/snapshot/test_external.py` | `test_risk_quiesce_no_silent_fallback` | Guest-agent failure returns error, does NOT retry without `--quiesce` |

### Risk 5 — `qemu-img map` JSON parsing on fragmented disk

> **Risk:** 100K+ JSON entries. Memory spike, slow change detection.
> **Mitigation:** Stream JSON with `ijson` if output exceeds threshold. Fall
> back to `allocation-size` mode.

| Test File | Test Function | Verifies |
|---|---|---|
| `tests/modules/change/test_map_detector.py` | `test_risk_map_large_json_handled` | 10K+ region JSON parses without memory spike (completes under 5s) |
| `tests/modules/change/test_map_detector.py` | `test_risk_map_fallback_on_parse_error` | Malformed JSON → `ChangeResult(changed=True)` (fail-safe, no crash) |

### Risk 6 — `virsh backup-begin` API not available

> **Risk:** Older libvirt (< 6.0). NBD backup fails, factory must fall back.
> **Mitigation:** `BitmapBackupProvider.__init__` checks libvirt version.
> Factory catches `RuntimeError` → falls back to `FileCopyBackupProvider`
> with warning.

| Test File | Test Function | Verifies |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | `test_risk_backup_begin_unavailable_raises_runtime_error` | libvirt < 6.0 → `BitmapBackupProvider()` raises `RuntimeError("virsh backup-begin not available")` |
| `tests/factory/test_default.py` | `test_risk_factory_falls_back_to_file_copy_on_old_libvirt` | Factory catches `RuntimeError` → returns `FileCopyBackupProvider` + logs warning |
| `tests/factory/test_default.py` | `test_risk_factory_fallback_logs_warning` | Warning log message contains "falling back" or "FileCopyBackupProvider" |

---

## 7. New Test Files Summary

| File | Group | Purpose |
|---|---|---|
| `tests/core/test_validation.py` | A | Pre-flight environment validation scenarios |
| `tests/core/test_deferred.py` | A | Deferred blockcommit integration in Core |
| `tests/modules/backup/test_verification.py` | B | Post-transfer backup verification (metadata/full/off) |
| `tests/modules/lifecycle/test_qemu_img_commit.py` | C | `QemuImgCommitManager` offline commit |
| `tests/modules/change/test_map_detector.py` | D | `MapChangeDetector` via `qemu-img map` |
| `tests/cli/test_tree.py` | K | Tree-format backing-chain listing |

---

## 8. Execution Order Recommendation

For sequential execution (if parallel groups are not available), run in this
order to surface failures early:

1. **Group H** — Config model (pure dataclass tests, fastest, surfaces schema issues)
2. **Group J** — Shell abstraction (check param, isolated)
3. **Group F** — State management (deferred ops persistence)
4. **Group I** — Retention engine (pure logic)
5. **Group E** — Snapshot provider (quiesce)
6. **Group D** — Change detection (map detector)
7. **Group C** — Lifecycle managers (blockcommit MAC + qemu-img commit)
8. **Group B** — Backup providers (NBD + verification)
9. **Group G** — Factory (depends on all new modules existing)
10. **Group A** — Core orchestration (depends on all modules)
11. **Group L** — Contract tests (depends on all implementations)
12. **Group M** — Mock tests (depends on mock updates)
13. **Group K** — CLI interface (depends on Core methods)

---

## 9. Test Count Summary

| Category | New Files | Modified Files | New Functions | Modified Functions |
|---|---|---|---|---|
| Core | 2 | 2 | 12 | 3 |
| Backup modules | 1 | 2 | 14 | 8 |
| Lifecycle modules | 1 | 1 | 6 | 0 |
| Change detection | 1 | 0 | 8 | 0 |
| Snapshot modules | 0 | 1 | 4 | 2 |
| State management | 0 | 1 | 5 | 0 |
| Factory | 0 | 1 | 5 | 2 |
| Config model | 0 | 1 | 8 | 0 |
| Retention | 0 | 1 | 1 | 0 |
| Shell abstraction | 0 | 1 | 3 | 0 |
| CLI | 1 | 2 | 5 | 2 |
| Contract tests | 0 | 6 | 7 | 6 |
| Mock tests | 0 | 2 | 2 | 2 |
| Risk mitigation | 0 | 0 | 14 | 0 |
| **Total** | **6** | **20** | **94** | **25** |
