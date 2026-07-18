# QA Strategy & Test Plan

## Coverage Map

### NEW: shared-utilities

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| shared-utilities | R1: Shared hash utility in qsnap.utils | File hashing used by ExternalSnapshotProvider | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_uses_hash_from_utils` | utils-unit |
| shared-utilities | R1: Shared hash utility in qsnap.utils | File hashing used by backup verification | `tests/utils/test_hash.py` | `test_file_sha256_hex_result` | utils-unit |
| shared-utilities | R2: Shared NBD utility functions in qsnap.utils | Core imports NBD utilities from utils | `tests/utils/test_nbd.py` | `test_nbd_public_functions_importable` | utils-unit |
| shared-utilities | R2: Shared NBD utility functions in qsnap.utils | FileCopyBackupProvider imports NBD utilities from utils | `tests/modules/backup/test_copy.py` | `test_nbd_imports_from_utils` | utils-unit |
| shared-utilities | R3: Shared verification functions in qsnap.utils | Core imports verify_full_backup from utils | `tests/utils/test_verification.py` | `test_verify_full_backup_imported_from_utils` | utils-unit |
| shared-utilities | R3: Shared verification functions in qsnap.utils | FileCopyBackupProvider imports verify_backup from utils | `tests/modules/backup/test_copy.py` | `test_verify_backup_imported_from_utils` | utils-unit |
| shared-utilities | R4: No domain module imports from qsnap.modules.backup.* | ExternalSnapshotProvider has no backup imports | `tests/modules/snapshot/test_external.py` | `test_external_snapshot_no_cross_domain_imports` | utils-unit |
| shared-utilities | R4: No domain module imports from qsnap.modules.backup.* | BlockCommitManager has no backup imports | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_no_cross_domain_imports` | utils-unit |

### NEW: bucket-full-strategy

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| bucket-full-strategy | R1: IBucketFullStrategy interface | Interface defines single method | `tests/interfaces/test_bucket_full_strategy.py` | `test_ibucketfullstrategy_is_abstract` | strategy-unit |
| bucket-full-strategy | R2: BucketFullStrategy implements IBucketFullStrategy | Bucket strategy returns True for first snapshot at new monthly period | `tests/modules/retention/test_bucket_full_strategy.py` | `test_should_create_full_first_monthly_returns_true` | strategy-unit |
| bucket-full-strategy | R2: BucketFullStrategy implements IBucketFullStrategy | Bucket strategy returns False when period unchanged | `tests/modules/retention/test_bucket_full_strategy.py` | `test_should_create_full_same_period_returns_false` | strategy-unit |
| bucket-full-strategy | R2: BucketFullStrategy implements IBucketFullStrategy | Bucket strategy with multi-level anchors | `tests/modules/retention/test_bucket_full_strategy.py` | `test_should_create_full_multi_level_anchors` | strategy-unit |
| bucket-full-strategy | R3: Factory creates IBucketFullStrategy | DefaultFactory returns BucketFullStrategy | `tests/factory/test_default.py` | `test_create_bucket_full_strategy_returns_bucketfullstrategy` | factory-guards |
| bucket-full-strategy | R3: Factory creates IBucketFullStrategy | MockFactory returns MockBucketFullStrategy | `tests/mocks/test_mock_factory.py` | `test_mock_factory_create_bucket_full_strategy_returns_mock` | mock-tests |
| bucket-full-strategy | R4: Core uses factory to obtain bucket strategy | Core delegates bucket decision to strategy | `tests/core/test_pipeline.py` | `test_core_delegates_bucket_decision_to_strategy` | core-orchestration |

### MODIFIED: snapshot-provider

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| snapshot-provider | External disk-only snapshot creation | Content hash computed via qsnap.utils.hash | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_uses_hash_from_utils` | utils-unit |

### MODIFIED: backup-provider — libvirt version check

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-provider | Libvirt version check in BitmapBackupProvider | Libvirt too old — factory fallback | `tests/factory/test_default.py` | `test_factory_falls_back_to_file_copy_on_old_libvirt` | factory-guards |
| backup-provider | Libvirt version check in BitmapBackupProvider | Libvirt sufficient — BitmapBackupProvider constructed | `tests/factory/test_default.py` | `test_factory_selects_bitmap_provider_for_bitmap_mode` | factory-guards |
| backup-provider | Libvirt version check in BitmapBackupProvider | BitmapBackupProvider constructor does not check version | `tests/modules/backup/test_bitmap.py` | `test_bitmap_constructor_no_version_check` | bitmap-unit |

### MODIFIED: nbd-bitmap-backup

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| nbd-bitmap-backup | Libvirt version check for NBD API | Libvirt too old | `tests/factory/test_default.py` | `test_factory_bitmap_mode_old_libvirt_falls_back` | factory-guards |
| nbd-bitmap-backup | Libvirt version check for NBD API | Libvirt sufficient | `tests/factory/test_default.py` | `test_factory_bitmap_mode_new_libvirt_returns_bitmap` | factory-guards |
| nbd-bitmap-backup | Libvirt version check for NBD API | BitmapBackupProvider constructor is version-check-free | `tests/modules/backup/test_bitmap.py` | `test_bitmap_constructor_no_version_check` | bitmap-unit |

### MODIFIED: backup-full-verification

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-full-verification | M1 metadata verification of FULL before rebase | Rebase with full_verify_before_rebase = "metadata" | `tests/core/test_full_verification_pipeline.py` | `test_rebase_verify_metadata_mode` | core-orchestration |
| backup-full-verification | M1 metadata verification of FULL before rebase | Rebase with full_verify_before_rebase = "off" | `tests/core/test_full_verification_pipeline.py` | `test_rebase_verify_off_mode` | core-orchestration |
| backup-full-verification | M1 metadata verification of FULL before rebase | Rebase with full_verify_before_rebase = "check" | `tests/core/test_full_verification_pipeline.py` | `test_rebase_verify_check_mode` | core-orchestration |
| backup-full-verification | M1 metadata verification of FULL before rebase | Verification mode passed as parameter | `tests/core/test_full_verification_pipeline.py` | `test_rebase_verify_mode_passed_as_parameter` | core-orchestration |

### MODIFIED: core-orchestrator

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| core-orchestrator | REMOVED: _should_create_bucket_full | N/A (method removal) | N/A (covered by strategy tests) | N/A | strategy-unit |
| core-orchestrator | MODIFIED: _backup_target triggers full via strategy | Full backup list passed to bucket strategy | `tests/core/test_pipeline.py` | `test_backup_target_passes_full_list_to_strategy` | core-orchestration |
| core-orchestrator | MODIFIED: _backup_target triggers full via strategy | First run creates full backup via strategy | `tests/core/test_pipeline.py` | `test_first_backup_creates_full_via_strategy` | core-orchestration |
| core-orchestrator | MODIFIED: _backup_target triggers full via strategy | Strategy obtained via factory | `tests/core/test_pipeline.py` | `test_core_delegates_bucket_decision_to_strategy` | core-orchestration |
| core-orchestrator | ADDED: Core imports shared utilities from qsnap.utils | Core has no domain module imports | `tests/core/test_engine.py` | `test_core_imports_from_utils_not_backup_modules` | core-orchestration |

### MODIFIED: module-factory

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| module-factory | MODIFIED: IVMModuleFactory ABC | IVMModuleFactory defines all creation methods (now includes create_bucket_full_strategy) | `tests/interfaces/test_factory.py` | `test_ivmmodulefactory_defines_all_creation_methods` | factory-guards |
| module-factory | MODIFIED: DefaultFactory receives IShell and IStateManager | DefaultFactory stores shell and state (existing) | `tests/factory/test_default.py` | `test_default_factory_stores_shell_and_state` | factory-guards |
| module-factory | ADDED: DefaultFactory gates BitmapBackupProvider on libvirt version | Bitmap mode with old libvirt falls back to FileCopy | `tests/factory/test_default.py` | `test_factory_bitmap_mode_old_libvirt_falls_back` | factory-guards |
| module-factory | ADDED: DefaultFactory gates BitmapBackupProvider on libvirt version | Bitmap mode with sufficient libvirt returns BitmapBackupProvider | `tests/factory/test_default.py` | `test_factory_bitmap_mode_new_libvirt_returns_bitmap` | factory-guards |
| module-factory | ADDED: DefaultFactory gates BitmapBackupProvider on libvirt version | Non-bitmap mode bypasses version check | `tests/factory/test_default.py` | `test_factory_non_bitmap_mode_no_version_check` | factory-guards |

---

## Delegation Groups

### Group: utils-unit
**Scope:** `tests/utils/` (new files: test_hash.py, test_nbd.py, test_verification.py); `tests/modules/snapshot/test_external.py`; `tests/modules/backup/test_copy.py`; `tests/modules/lifecycle/test_blockcommit.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_hash.py` | `test_file_sha256_hex_result` | CREATE — unit tests for `file_sha256()` in `qsnap.utils.hash` with MockShell |
| `tests/utils/test_nbd.py` | `test_nbd_public_functions_importable` | CREATE — unit tests verifying `is_vm_running()`, `nbd_full_export()` are importable from `qsnap.utils.nbd` |
| `tests/utils/test_verification.py` | `test_verify_full_backup_imported_from_utils` | CREATE — verify `verify_full_backup` is importable from `qsnap.utils.verification` (thin relocation test; deep logic stays in backup sub-dir tests) |
| `tests/modules/snapshot/test_external.py` | `test_create_snapshot_uses_hash_from_utils`, `test_external_snapshot_no_cross_domain_imports` | ADD — hash source traced to `qsnap.utils.hash`; import check that no `qsnap.modules.backup` appears |
| `tests/modules/backup/test_copy.py` | `test_nbd_imports_from_utils`, `test_verify_backup_imported_from_utils` | ADD — verify FileCopyBackupProvider imports NBD/verification from `qsnap.utils.*` |
| `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_no_cross_domain_imports` | ADD — verify BlockCommitManager has no `qsnap.modules.backup` imports |

### Group: strategy-unit
**Scope:** `tests/interfaces/test_bucket_full_strategy.py`; `tests/modules/retention/test_bucket_full_strategy.py`; `tests/core/test_pipeline.py` (bucket delegation tests)
| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_bucket_full_strategy.py` | `test_ibucketfullstrategy_is_abstract` | CREATE — contract test: IBucketFullStrategy is ABC, cannot instantiate, defines `should_create_full` |
| `tests/modules/retention/test_bucket_full_strategy.py` | `test_should_create_full_first_monthly_returns_true`, `test_should_create_full_same_period_returns_false`, `test_should_create_full_multi_level_anchors` | CREATE — extracted logic from `Core._should_create_bucket_full`, now tested as BucketFullStrategy unit |
| `tests/core/test_pipeline.py` | `test_core_delegates_bucket_decision_to_strategy`, `test_first_backup_creates_full_via_strategy`, `test_backup_target_passes_full_list_to_strategy` | REWRITE — existing bucket tests converted to use MockBucketFullStrategy via factory |

### Group: bitmap-unit
**Scope:** `tests/modules/backup/test_bitmap.py`; `tests/interfaces/test_backup_provider.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_constructor_no_version_check` | ADD — verify `BitmapBackupProvider.__init__` no longer calls `_check_libvirt_version()`; does not require version-specific MockShell setup |
| `tests/interfaces/test_backup_provider.py` | — | MODIFY — `_make_bitmap_shell()` helper and parametrized tests updated: construction no longer needs `virsh --version` in MockShell for BitmapBackupProvider |
| `tests/modules/backup/test_bitmap.py` | `test_constructor_accepts_ishell_and_implements_abc` | MODIFY — remove `mock_shell.expect("virsh --version")` from this test |

### Group: factory-guards
**Scope:** `tests/factory/test_default.py`; `tests/interfaces/test_factory.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_factory.py` | `test_ivmmodulefactory_defines_all_creation_methods` | MODIFY — add `create_bucket_full_strategy` to expected_methods set |
| `tests/factory/test_default.py` | `test_create_bucket_full_strategy_returns_bucketfullstrategy` | ADD — verify DefaultFactory returns BucketFullStrategy |
| `tests/factory/test_default.py` | `test_factory_bitmap_mode_old_libvirt_falls_back` | ADD — bitmap mode + libvirt < 6.0 → FileCopyBackupProvider (factory-level `is_libvirt_new_enough` check) |
| `tests/factory/test_default.py` | `test_factory_bitmap_mode_new_libvirt_returns_bitmap` | ADD — bitmap mode + libvirt >= 6.0 → BitmapBackupProvider |
| `tests/factory/test_default.py` | `test_factory_non_bitmap_mode_no_version_check` | ADD — verify non-bitmap mode skips libvirt version check entirely |
| `tests/factory/test_default.py` | `test_factory_falls_back_to_file_copy_on_old_qemu` | MERGE — absorbed into `test_factory_bitmap_mode_old_libvirt_falls_back` (same behavior, clearer name) |
| `tests/factory/test_default.py` | `test_factory_falls_back_on_old_libvirt` | MERGE — duplicate; absorbed into `test_factory_bitmap_mode_old_libvirt_falls_back` |
| `tests/factory/test_default.py` | `test_risk_factory_falls_back_to_file_copy_on_old_libvirt` | MERGE — duplicate; absorbed into `test_factory_bitmap_mode_old_libvirt_falls_back` |
| `tests/factory/test_default.py` | `test_risk_factory_fallback_logs_warning` | KEEP — but rename to `test_factory_bitmap_fallback_logs_warning` |
| `tests/factory/test_default.py` | `test_factory_selects_bitmap_provider_for_bitmap_mode` | MODIFY — update to assert factory gates via `is_libvirt_new_enough` before constructing |
| `tests/factory/test_default.py` | `test_factory_selects_file_copy_provider_for_default_mode` | KEEP — no change needed |

### Group: core-orchestration
**Scope:** `tests/core/test_pipeline.py`; `tests/core/test_full_verification_pipeline.py`; `tests/core/test_engine.py`; `tests/core/test_full_anchor.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | `test_first_backup_creates_full_via_strategy` | REWRITE — `test_first_backup_creates_full_via_bucket` → use factory strategy mock |
| `tests/core/test_pipeline.py` | `test_new_monthly_period_triggers_full` | MOVE to `tests/modules/retention/test_bucket_full_strategy.py` — becomes unit test |
| `tests/core/test_pipeline.py` | `test_same_bucket_period_skips_full` | MOVE to `tests/modules/retention/test_bucket_full_strategy.py` — becomes unit test |
| `tests/core/test_pipeline.py` | `test_no_buckets_preserve_min_all_no_full_created` | MOVE to `tests/modules/retention/test_bucket_full_strategy.py` — becomes unit test |
| `tests/core/test_pipeline.py` | `test_backup_target_passes_full_list_to_strategy` | REWRITE — `test_backup_target_passes_full_list_to_bucket_check` → verify strategy receives full list |
| `tests/core/test_full_anchor.py` | `test_f_anchor_weekly_only_full_on_week_boundary_not_day` | MOVE to `tests/modules/retention/test_bucket_full_strategy.py` — becomes unit test |
| `tests/core/test_full_anchor.py` | `test_should_create_bucket_full_highest_yearly` | MOVE to `tests/modules/retention/test_bucket_full_strategy.py` — becomes unit test |
| `tests/core/test_full_anchor.py` | `test_should_create_bucket_full_highest_daily` | MOVE to `tests/modules/retention/test_bucket_full_strategy.py` — becomes unit test |
| `tests/core/test_full_anchor.py` | `test_should_create_bucket_full_no_active_buckets` | MOVE to `tests/modules/retention/test_bucket_full_strategy.py` — becomes unit test |
| `tests/core/test_full_anchor.py` | `test_new_weekly_period_triggers_full_all_buckets` | MOVE to `tests/modules/retention/test_bucket_full_strategy.py` — becomes unit test |
| `tests/core/test_full_verification_pipeline.py` | `test_rebase_verify_metadata_mode` | ADD — verify Core passes `full_verify_before_rebase` from global_config to rebase path |
| `tests/core/test_full_verification_pipeline.py` | `test_rebase_verify_off_mode` | ADD — verify rebase with `full_verify_before_rebase = "off"` skips verification |
| `tests/core/test_full_verification_pipeline.py` | `test_rebase_verify_check_mode` | ADD — verify rebase with `full_verify_before_rebase = "check"` runs structural check |
| `tests/core/test_full_verification_pipeline.py` | `test_rebase_verify_mode_passed_as_parameter` | ADD — verify verification mode is passed through to provider method call |
| `tests/core/test_engine.py` | `test_core_imports_from_utils_not_backup_modules` | ADD — verify Core imports `is_vm_running`, `verify_full_backup` from `qsnap.utils.*` not `qsnap.modules.backup.*` |

### Group: mock-tests
**Scope:** `tests/mocks/test_mock_factory.py`; `tests/mocks/mock_factory.py`; `tests/mocks/mock_modules.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/mocks/mock_factory.py` | — | MODIFY — add `create_bucket_full_strategy()` returning `MockBucketFullStrategy` |
| `tests/mocks/mock_modules.py` | — | MODIFY — add `MockBucketFullStrategy` implementing `IBucketFullStrategy` |
| `tests/mocks/test_mock_factory.py` | `test_mock_factory_create_bucket_full_strategy_returns_mock` | ADD — verify MockVMModuleFactory creates and returns MockBucketFullStrategy |
| `tests/mocks/test_mock_factory.py` | `test_mock_factory_satisfies_new_interface` | ADD — verify updated MockVMModuleFactory passes `isinstance(mock_factory, IVMModuleFactory)` |

---

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/modules/snapshot/test_external.py` | ADD `test_create_snapshot_uses_hash_from_utils`, ADD `test_external_snapshot_no_cross_domain_imports` | Verify content hash imported from `qsnap.utils.hash` per Decision 1; verify no cross-domain import |
| `tests/modules/backup/test_verification.py` | MODIFY import path from `qsnap.modules.backup.verification` → `qsnap.utils.verification` for `_file_sha256`, `verify_backup` | Import path change (Decision 1) |
| `tests/modules/backup/test_full_verification.py` | MODIFY import path from `qsnap.modules.backup.verification` → `qsnap.utils.verification` for `verify_full_backup` | Import path change (Decision 1) |
| `tests/modules/backup/test_bitmap.py` | MODIFY `test_constructor_accepts_ishell_and_implements_abc` — remove `virsh --version` expectation; ADD `test_bitmap_constructor_no_version_check` | Constructor no longer checks libvirt version (Decision 2) |
| `tests/modules/backup/test_copy.py` | ADD `test_nbd_imports_from_utils`, `test_verify_backup_imported_from_utils` | Verify FileCopyBackupProvider uses new import paths (Decision 1) |
| `tests/modules/lifecycle/test_blockcommit.py` | ADD `test_blockcommit_no_cross_domain_imports` | Verify BlockCommitManager has no `qsnap.modules.backup` imports (Decision 1 scenario) |
| `tests/interfaces/test_factory.py` | MODIFY `expected_methods` set to include `create_bucket_full_strategy` | IVMModuleFactory gains new abstract method (Decision 3) |
| `tests/interfaces/test_backup_provider.py` | MODIFY remove `_make_bitmap_shell()` and `virsh --version` from BitmapBackupProvider parametrize setup | BitmapBackupProvider constructor no longer calls version check (Decision 2) |
| `tests/factory/test_default.py` | ADD `test_create_bucket_full_strategy_returns_bucketfullstrategy`, `test_factory_bitmap_mode_old_libvirt_falls_back`, `test_factory_bitmap_mode_new_libvirt_returns_bitmap`, `test_factory_non_bitmap_mode_no_version_check`; RENAME `test_risk_factory_fallback_logs_warning` → `test_factory_bitmap_fallback_logs_warning`; REMOVE duplicate fallback tests | Factory gating logic (Decision 2) + strategy creation (Decision 3) |
| `tests/mocks/mock_factory.py` | ADD `create_bucket_full_strategy()` method returning `MockBucketFullStrategy` | MockFactory must satisfy updated IVMModuleFactory ABC (Decision 3) |
| `tests/mocks/mock_modules.py` | ADD `MockBucketFullStrategy` class | Needed for Core-level pipeline tests with mocked strategy (Decision 3) |
| `tests/mocks/test_mock_factory.py` | ADD `test_mock_factory_create_bucket_full_strategy_returns_mock`, `test_mock_factory_satisfies_new_interface` | Verify mocks correctly implement new interface (Decision 3) |
| `tests/core/test_pipeline.py` | MOVE 7 bucket-logic tests to `tests/modules/retention/test_bucket_full_strategy.py`; REWRITE `test_first_backup_creates_full_via_bucket` → `test_first_backup_creates_full_via_strategy`; REWRITE `test_backup_target_passes_full_list_to_bucket_check` → `test_backup_target_passes_full_list_to_strategy`; ADD `test_core_delegates_bucket_decision_to_strategy` | Bucket logic extracted to separate strategy (Decision 3) |
| `tests/core/test_full_anchor.py` | MOVE all F-anchor / `_should_create_bucket_full` tests to `tests/modules/retention/test_bucket_full_strategy.py` | Extracted from Core static methods to strategy unit (Decision 3) |
| `tests/core/test_full_verification_pipeline.py` | ADD `test_rebase_verify_metadata_mode`, `test_rebase_verify_off_mode`, `test_rebase_verify_check_mode`, `test_rebase_verify_mode_passed_as_parameter` | Configurable `full_verify_before_rebase` value (Decision 4) |
| `tests/core/test_engine.py` | ADD `test_core_imports_from_utils_not_backup_modules` | Core imports change from `qsnap.modules.backup.*` to `qsnap.utils.*` (Decision 1) |
| `tests/integration/test_nbd_full_backup.py` | MODIFY import from `qsnap.modules.backup.nbd_helper` → `qsnap.utils.nbd` | Import path change (Decision 1) |

---

## Risks & Edge Cases

- **[Import Breakage — 15+ test files]** Changing import paths for `qsnap.utils.hash`, `qsnap.utils.nbd`, and `qsnap.utils.verification` will break every file that imports from `qsnap.modules.backup.verification` and `qsnap.modules.backup.nbd_helper`. Mitigation: use `rg "from qsnap\.modules\.backup\.(verification|nbd_helper)" tests/ -l` to find every affected file; update imports in lockstep with production code. Run `pytest tests/ -x` after each batch of import changes.
- **[MockVMModuleFactory API break]** Adding `create_bucket_full_strategy()` to `IVMModuleFactory` will cause `TypeError: Can't instantiate abstract class` for `MockVMModuleFactory` and any other subclass. Mitigation: update `mock_factory.py` FIRST, before changing `interfaces/factory.py`, to avoid cascading test failures.
- **[Public name change — `_file_sha256` → `file_sha256`]** The underscore-to-public rename in Decision 1 changes the symbol name used by consumers (ExternalSnapshotProvider, verification tests). Mitigation: search-and-replace `_file_sha256` → `file_sha256` across all files simultaneously.
- **[Core._should_create_bucket_full removal]** All tests that call `Core._should_create_bucket_full()` directly (7 tests across `test_pipeline.py` and `test_full_anchor.py`) must be moved or rewritten. Mitigation: migrate these to `test_bucket_full_strategy.py` first, verify they pass against the new strategy class, then remove the old Core static method and its tests.
- **[BitmapBackupProvider constructor contract change]** Removing the `_check_libvirt_version()` call from `__init__` means numerous tests must stop setting up `virsh --version` expectations. Mitigation: update `_make_bitmap_shell()` in `test_backup_provider.py` to no longer pre-configure version check; remove version expectations from bitmap unit tests except the factory-level gating test.
- **[Existing test_pipeline.py bucket tests rely on Core static method]** Tests like `test_new_monthly_period_triggers_full` instantiate `Core(...)` and call `core._backup_target(vm, target, [snap])`. After extracting `_should_create_bucket_full` to strategy, these tests must either inject a MockBucketFullStrategy through the factory or be moved to strategy unit tests. Mitigation: prefer moving pure-logic tests to the strategy test file; keep only integration-style pipeline tests in core.
- **[Threading `full_verify_before_rebase` through FileCopyBackupProvider]** If the provider's `transfer_missing()` method gains a new parameter, all mock implementations must be updated. Mitigation: use a keyword-only argument with a default to minimize breakage.
