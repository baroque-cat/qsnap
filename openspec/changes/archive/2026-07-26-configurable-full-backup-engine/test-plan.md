# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| config-model | full_transfer_engine field in GlobalConfig | GlobalConfig default full_transfer_engine is qemu-img-convert | tests/config/test_model.py | test_global_config_full_transfer_engine_default_is_qemu_img_convert | config-unit |
| config-model | full_transfer_engine field in GlobalConfig | GlobalConfig full_transfer_engine is immutable | tests/config/test_model.py | test_global_config_full_transfer_engine_is_immutable | config-unit |
| config-model | full_transfer_engine field in GlobalConfig | GlobalConfig full_transfer_engine set to libnbd | tests/config/test_model.py | test_global_config_full_transfer_engine_set_to_libnbd | config-unit |
| config-model | full_transfer_engine field in TargetConfig | TargetConfig full_transfer_engine inherits from global | tests/config/test_resolver.py | test_target_full_transfer_engine_inherits_from_global | config-unit |
| config-model | full_transfer_engine field in TargetConfig | TargetConfig full_transfer_engine overrides global | tests/config/test_resolver.py | test_target_full_transfer_engine_overrides_global | config-unit |
| config-model | full_transfer_engine field in TargetConfig | TargetConfig default full_transfer_engine is qemu-img-convert | tests/config/test_resolver.py | test_target_full_transfer_engine_default_is_qemu_img_convert | config-unit |
| config-model | full_transfer_engine validation | Valid full_transfer_engine value | tests/config/test_parser.py | test_valid_full_transfer_engine_accepted | config-unit |
| config-model | full_transfer_engine validation | Invalid full_transfer_engine raises ConfigError | tests/config/test_parser.py | test_invalid_full_transfer_engine_raises_config_error | config-unit |
| config-model | convert_parallel field in GlobalConfig | GlobalConfig default convert_parallel is 4 | tests/config/test_model.py | test_global_config_convert_parallel_default_is_4 | config-unit |
| config-model | convert_parallel field in GlobalConfig | GlobalConfig convert_parallel is immutable | tests/config/test_model.py | test_global_config_convert_parallel_is_immutable | config-unit |
| config-model | convert_parallel field in TargetConfig | TargetConfig convert_parallel inherits from global | tests/config/test_resolver.py | test_target_convert_parallel_inherits_from_global | config-unit |
| config-model | convert_parallel field in TargetConfig | TargetConfig convert_parallel overrides global | tests/config/test_resolver.py | test_target_convert_parallel_overrides_global | config-unit |
| config-model | convert_parallel validation | Valid convert_parallel value | tests/config/test_parser.py | test_valid_convert_parallel_accepted | config-unit |
| config-model | convert_parallel validation | convert_parallel below range raises ConfigError | tests/config/test_parser.py | test_convert_parallel_below_range_raises_config_error | config-unit |
| config-model | convert_parallel validation | convert_parallel above range raises ConfigError | tests/config/test_parser.py | test_convert_parallel_above_range_raises_config_error | config-unit |
| config-model | convert_out_of_order field in GlobalConfig | GlobalConfig default convert_out_of_order is true | tests/config/test_model.py | test_global_config_convert_out_of_order_default_is_true | config-unit |
| config-model | convert_out_of_order field in GlobalConfig | GlobalConfig convert_out_of_order is immutable | tests/config/test_model.py | test_global_config_convert_out_of_order_is_immutable | config-unit |
| config-model | convert_out_of_order field in TargetConfig | TargetConfig convert_out_of_order inherits from global | tests/config/test_resolver.py | test_target_convert_out_of_order_inherits_from_global | config-unit |
| config-model | convert_out_of_order field in TargetConfig | TargetConfig convert_out_of_order overrides global | tests/config/test_resolver.py | test_target_convert_out_of_order_overrides_global | config-unit |
| backup-provider | Transfer missing snapshots via dirty bitmap extraction | First backup — full NBD export via qemu-img convert (default engine) | tests/modules/backup/test_bitmap.py | test_no_checkpoints_triggers_full_export | bitmap-unit |
| backup-provider | Transfer missing snapshots via dirty bitmap extraction | First backup — full NBD export via libnbd engine | tests/modules/backup/test_bitmap.py | test_first_full_transfer_via_libnbd_engine | bitmap-unit |
| backup-provider | Transfer missing snapshots via dirty bitmap extraction | Incremental backup — dirty blocks only (unaffected by engine selection) | tests/modules/backup/test_bitmap_incremental.py | test_incremental_unaffected_by_full_transfer_engine | bitmap-unit |
| backup-provider | Transfer missing snapshots via dirty bitmap extraction | Checkpoint rotation after successful transfer | tests/modules/backup/test_bitmap.py | test_checkpoint_cleanup_after_successful_transfer | bitmap-unit |
| backup-provider | Transfer missing snapshots via dirty bitmap extraction | Transfer failure preserves prior checkpoint | tests/modules/backup/test_bitmap.py | test_transfer_failure_preserves_checkpoint | bitmap-unit |
| backup-provider | Transfer missing snapshots via dirty bitmap extraction | Scaffolding dedup — both FULL paths use shared helper with engine branch | tests/modules/backup/test_bitmap.py | test_full_pull_lifecycle_shared_by_both_paths | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via configurable engine | Bitmap FULL with zstd compression via qemu-img convert | tests/modules/backup/test_bitmap.py | test_create_full_backup_with_compression | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via configurable engine | Bitmap FULL with zstd compression via libnbd | tests/modules/backup/test_bitmap.py | test_create_full_backup_libnbd_with_zstd_compression | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via configurable engine | Bitmap FULL with custom convert_parallel | tests/modules/backup/test_bitmap.py | test_create_full_backup_custom_convert_parallel | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via configurable engine | Bitmap FULL with convert_out_of_order disabled | tests/modules/backup/test_bitmap.py | test_create_full_backup_convert_out_of_order_disabled | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via configurable engine | Bitmap FULL no longer raises NotImplementedError | tests/modules/backup/test_bitmap.py | test_bitmap_full_backup_does_not_raise_not_implemented | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via configurable engine | Bitmap FULL creates checkpoint atomically | tests/modules/backup/test_bitmap.py | test_create_full_backup_unified_engine_succeeds | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via configurable engine | Bitmap FULL does not self-record in state | tests/modules/backup/test_bitmap.py | test_create_full_backup_does_not_self_record | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via configurable engine | Bitmap FULL with dotted VM name | tests/modules/backup/test_bitmap.py | test_create_full_backup_dotted_vm_name_passed_untruncated | bitmap-unit |
| backup-provider | Compression type parameter for backup providers | create_full_backup with zstd compression via qemu-img convert | tests/modules/backup/test_bitmap.py | test_create_full_backup_with_compression | bitmap-unit |
| backup-provider | Compression type parameter for backup providers | create_full_backup with zstd compression via libnbd | tests/modules/backup/test_bitmap.py | test_create_full_backup_libnbd_with_zstd_compression | bitmap-unit |
| backup-provider | Compression type parameter for backup providers | create_full_backup with compression disabled | tests/modules/backup/test_bitmap.py | test_create_full_backup_no_compress_driver_when_compress_false | bitmap-unit |
| backup-provider | Full transfer engine parameters for backup providers | create_full_backup with default engine | tests/modules/backup/test_bitmap.py | test_create_full_backup_defaults_to_qemu_img_convert | bitmap-unit |
| backup-provider | Full transfer engine parameters for backup providers | create_full_backup with libnbd engine | tests/modules/backup/test_bitmap.py | test_create_full_backup_libnbd_engine_selected | bitmap-unit |
| backup-provider | Full transfer engine parameters for backup providers | transfer_missing with default engine | tests/modules/backup/test_bitmap.py | test_transfer_missing_defaults_to_qemu_img_convert | bitmap-unit |
| backup-provider | Full transfer engine parameters for backup providers | transfer_missing with libnbd engine | tests/modules/backup/test_bitmap.py | test_transfer_missing_libnbd_full_engine | bitmap-unit |
| backup-provider | Stall detection for data transfer commands | qemu-img convert uses stall detection | tests/modules/backup/test_bitmap.py | test_qemu_img_convert_uses_stall_detection | bitmap-unit |
| backup-provider | Stall detection for data transfer commands | libnbd transfer uses stall detection | tests/modules/backup/test_bitmap.py | test_libnbd_full_uses_stall_detection | bitmap-unit |
| backup-provider | Stall detection for data transfer commands | Stall timeout disabled falls back to fixed timeout | tests/modules/backup/test_bitmap.py | test_stall_timeout_disabled_falls_back_to_fixed_timeout | bitmap-unit |
| qemu-img-convert-full-backup | qemu-img convert as FULL backup transfer engine | Running VM FULL with zstd compression and default flags | tests/modules/backup/test_bitmap.py | test_create_full_backup_with_compression | bitmap-unit |
| qemu-img-convert-full-backup | qemu-img convert as FULL backup transfer engine | Running VM FULL without compression | tests/modules/backup/test_bitmap.py | test_create_full_backup_no_compress_driver_when_compress_false | bitmap-unit |
| qemu-img-convert-full-backup | qemu-img convert as FULL backup transfer engine | Running VM FULL with custom parallel count | tests/modules/backup/test_bitmap.py | test_create_full_backup_custom_convert_parallel | bitmap-unit |
| qemu-img-convert-full-backup | qemu-img convert as FULL backup transfer engine | Running VM FULL with out-of-order disabled | tests/modules/backup/test_bitmap.py | test_create_full_backup_convert_out_of_order_disabled | bitmap-unit |
| qemu-img-convert-full-backup | qemu-img convert as FULL backup transfer engine | Stopped VM FULL with compression and custom flags | tests/modules/backup/test_bitmap.py | test_create_full_backup_stopped_vm_custom_flags | bitmap-unit |
| qemu-img-convert-full-backup | qemu-img convert as FULL backup transfer engine | Stopped VM FULL without compression | tests/modules/backup/test_bitmap.py | test_create_full_backup_stopped_vm_no_compression | bitmap-unit |
| qemu-img-convert-full-backup | qemu-img convert as FULL backup transfer engine | FULL failure leaves no final file | tests/modules/backup/test_bitmap.py | test_create_full_backup_failure_removes_tmp | bitmap-unit |
| qemu-img-convert-full-backup | qemu-img convert as FULL backup transfer engine | FULL success atomically renames tmp to final | tests/modules/backup/test_bitmap.py | test_create_full_backup_atomic_rename_tmp_to_final | bitmap-unit |
| qemu-img-convert-full-backup | VM state detection in create_full_backup | Running VM triggers NBD-based convert | tests/modules/backup/test_bitmap.py | test_create_full_backup_unified_engine_succeeds | bitmap-unit |
| qemu-img-convert-full-backup | VM state detection in create_full_backup | Stopped VM triggers direct convert | tests/modules/backup/test_bitmap.py | test_create_full_backup_stopped_vm_direct_convert | bitmap-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | First backup — full via qemu-img convert with atomic checkpoint | tests/modules/backup/test_bitmap.py | test_atomic_full_export_passes_checkpoint_xml | bitmap-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | First backup — full via libnbd pread/pwrite with atomic checkpoint | tests/modules/backup/test_bitmap.py | test_atomic_full_export_libnbd_with_checkpoint | bitmap-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | Incremental backup — dirty blocks via NBD checkpoint | tests/modules/backup/test_bitmap_incremental.py | test_atomic_incremental_passes_checkpoint_xml_and_incremental | bitmap-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | _start_write_server does not accept compression_type | tests/modules/backup/test_bitmap.py | test_start_write_server_signature_no_compression_type | bitmap-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | Scaffolding dedup — shared _full_pull_lifecycle helper with engine branch | tests/modules/backup/test_bitmap.py | test_full_pull_lifecycle_branches_on_engine | bitmap-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | Socket cleanup on success | tests/modules/backup/test_bitmap.py | test_socket_cleanup_on_success | bitmap-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | Socket cleanup on failure | tests/modules/backup/test_bitmap.py | test_socket_cleanup_on_failure | bitmap-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via configurable engine | Bitmap FULL with zstd compression via qemu-img convert | tests/modules/backup/test_bitmap.py | test_create_full_backup_with_compression | bitmap-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via configurable engine | Bitmap FULL without compression via qemu-img convert | tests/modules/backup/test_bitmap.py | test_create_full_backup_no_compress_driver_when_compress_false | bitmap-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via configurable engine | Bitmap FULL with zstd compression via libnbd | tests/modules/backup/test_bitmap.py | test_create_full_backup_libnbd_with_zstd_compression | bitmap-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via configurable engine | Bitmap FULL without compression via libnbd | tests/modules/backup/test_bitmap.py | test_create_full_backup_libnbd_no_compression | bitmap-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via configurable engine | Bitmap FULL leaves an atomic checkpoint baseline | tests/modules/backup/test_bitmap.py | test_bitmap_first_full_pull_via_unified_engine | bitmap-unit |
| nbd-bitmap-backup | zero-skip for standalone FULL | All-zero chunk skipped in FULL via libnbd | tests/modules/backup/test_bitmap.py | test_libnbd_full_zero_skip_all_zero | bitmap-unit |
| nbd-bitmap-backup | zero-skip for standalone FULL | Non-zero chunk written in FULL via libnbd | tests/modules/backup/test_bitmap.py | test_libnbd_full_zero_skip_non_zero | bitmap-unit |
| nbd-bitmap-backup | zero-skip for standalone FULL | Zero-skip never applied to incrementals | tests/modules/backup/test_bitmap_incremental.py | test_incremental_zero_skip_false | bitmap-unit |
| nbd-bitmap-backup | qemu-nbd compress driver for write-side compression | Compress driver enabled for libnbd FULL | tests/modules/backup/test_bitmap.py | test_libnbd_full_compress_driver_enabled | bitmap-unit |
| nbd-bitmap-backup | qemu-nbd compress driver for write-side compression | No compress driver when compress=False | tests/modules/backup/test_bitmap.py | test_libnbd_full_no_compress_driver_when_compress_false | bitmap-unit |
| backup-provider | Full transfer engine parameters for backup providers | create_full_backup with default engine | tests/interfaces/test_backup_provider.py | test_backup_provider_create_full_backup_accepts_full_transfer_engine | contracts |
| backup-provider | Full transfer engine parameters for backup providers | create_full_backup with default engine | tests/interfaces/test_backup_provider.py | test_backup_provider_create_full_backup_accepts_convert_parallel | contracts |
| backup-provider | Full transfer engine parameters for backup providers | create_full_backup with default engine | tests/interfaces/test_backup_provider.py | test_backup_provider_create_full_backup_accepts_convert_out_of_order | contracts |
| backup-provider | Full transfer engine parameters for backup providers | transfer_missing with default engine | tests/interfaces/test_backup_provider.py | test_backup_provider_transfer_missing_accepts_full_transfer_engine | contracts |
| backup-provider | Full transfer engine parameters for backup providers | transfer_missing with default engine | tests/interfaces/test_backup_provider.py | test_backup_provider_transfer_missing_accepts_convert_parallel | contracts |
| backup-provider | Full transfer engine parameters for backup providers | transfer_missing with default engine | tests/interfaces/test_backup_provider.py | test_backup_provider_transfer_missing_accepts_convert_out_of_order | contracts |
| backup-provider | Full transfer engine parameters for backup providers | create_full_backup with default engine | tests/mocks/test_mock_validity.py | test_mock_backup_provider_accepts_new_kwargs | mocks-unit |
| backup-provider | Full transfer engine parameters for backup providers | create_full_backup with default engine | tests/mocks/test_mock_validity.py | test_mock_bitmap_backup_provider_accepts_new_kwargs | mocks-unit |
| backup-provider | Full transfer engine parameters for backup providers | create_full_backup with default engine | tests/mocks/test_mock_validity.py | test_mock_factory_backup_provider_returns_correct_interface | mocks-unit |
| backup-provider | Full transfer engine parameters for backup providers | create_full_backup with default engine | tests/core/test_pipeline.py | test_core_passes_full_transfer_engine_to_create_full_backup | core-unit |
| backup-provider | Full transfer engine parameters for backup providers | create_full_backup with default engine | tests/core/test_pipeline.py | test_core_passes_convert_parallel_to_create_full_backup | core-unit |
| backup-provider | Full transfer engine parameters for backup providers | create_full_backup with default engine | tests/core/test_pipeline.py | test_core_passes_convert_out_of_order_to_create_full_backup | core-unit |
| backup-provider | Full transfer engine parameters for backup providers | transfer_missing with default engine | tests/core/test_pipeline.py | test_core_passes_full_transfer_engine_to_transfer_missing | core-unit |
| backup-provider | Full transfer engine parameters for backup providers | transfer_missing with default engine | tests/core/test_pipeline.py | test_core_passes_convert_parallel_to_transfer_missing | core-unit |
| backup-provider | Full transfer engine parameters for backup providers | transfer_missing with default engine | tests/core/test_pipeline.py | test_core_passes_convert_out_of_order_to_transfer_missing | core-unit |
| config-model | full_transfer_engine field in GlobalConfig | GlobalConfig default full_transfer_engine is qemu-img-convert | tests/config/test_fixtures.py | test_make_global_config_defaults_full_transfer_engine | conftest |
| config-model | full_transfer_engine field in TargetConfig | TargetConfig default full_transfer_engine is qemu-img-convert | tests/config/test_fixtures.py | test_make_target_defaults_full_transfer_engine | conftest |
| config-model | convert_parallel field in GlobalConfig | GlobalConfig default convert_parallel is 4 | tests/config/test_fixtures.py | test_make_global_config_defaults_convert_parallel | conftest |
| config-model | convert_out_of_order field in GlobalConfig | GlobalConfig default convert_out_of_order is true | tests/config/test_fixtures.py | test_make_global_config_defaults_convert_out_of_order | conftest |
| config-model | full_transfer_engine field in TargetConfig | TargetConfig full_transfer_engine inherits from global | tests/config/test_fixtures.py | test_engine_config_toml_parses_correctly | config-fixtures |
| config-model | convert_parallel field in TargetConfig | TargetConfig convert_parallel inherits from global | tests/config/test_fixtures.py | test_engine_config_toml_parses_correctly | config-fixtures |
| config-model | convert_out_of_order field in TargetConfig | TargetConfig convert_out_of_order inherits from global | tests/config/test_fixtures.py | test_engine_config_toml_parses_correctly | config-fixtures |
| qemu-img-convert-full-backup | qemu-img convert as FULL backup transfer engine | Running VM FULL with zstd compression and default flags | tests/integration/test_full_backup.py | test_full_backup_qemu_img_convert_engine_default | integration |
| qemu-img-convert-full-backup | qemu-img convert as FULL backup transfer engine | Running VM FULL with custom parallel count | tests/integration/test_full_backup.py | test_full_backup_libnbd_engine | integration |
| config-model | full_transfer_engine field in TargetConfig | TargetConfig full_transfer_engine overrides global | tests/integration/test_full_backup.py | test_full_backup_custom_convert_parallel_and_out_of_order | integration |

## Delegation Groups

### Group: config-unit

**Scope:** `tests/config/test_model.py`, `tests/config/test_parser.py`, `tests/config/test_resolver.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/config/test_model.py | 8 (GlobalConfig/TargetConfig field immutability, defaults) | NEW |
| tests/config/test_parser.py | 5 (full_transfer_engine validation, convert_parallel validation) | NEW |
| tests/config/test_resolver.py | 6 (inheritance: full_transfer_engine, convert_parallel, convert_out_of_order) | NEW |

### Group: bitmap-unit

**Scope:** `tests/modules/backup/test_bitmap.py`, `tests/modules/backup/test_bitmap_incremental.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/backup/test_bitmap.py | 30 (engine selection, libnbd FULL, qemu-img flags, zero-skip, compress driver) | NEW + MODIFY |
| tests/modules/backup/test_bitmap_incremental.py | 3 (incremental unaffected, zero_skip false) | NEW + MODIFY |

### Group: mocks-unit

**Scope:** `tests/mocks/mock_modules.py`, `tests/mocks/test_mock_validity.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/mocks/mock_modules.py | 2 (MockBackupProvider, MockBitmapBackupProvider signature updates) | MODIFY |
| tests/mocks/test_mock_validity.py | 3 (mock accepts new kwargs, factory returns correct interface) | NEW |

### Group: contracts

**Scope:** `tests/interfaces/test_backup_provider.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/interfaces/test_backup_provider.py | 6 (IBackupProvider new parameters on create_full_backup and transfer_missing) | MODIFY |

### Group: core-unit

**Scope:** `tests/core/test_pipeline.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_pipeline.py | 6 (Core passes new fields from TargetConfig to provider methods) | NEW |

### Group: conftest

**Scope:** `tests/conftest.py`, `tests/config/test_fixtures.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/conftest.py | 3 (make_global_config, make_target updated with new defaults) | MODIFY |
| tests/config/test_fixtures.py | 4 (conftest fixtures for new fields) | NEW |

### Group: config-fixtures

**Scope:** `tests/fixtures/configs/`, `tests/config/test_fixtures.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/fixtures/configs/engine_config.toml | 3 (new TOML fixture with all engine fields) | NEW |
| tests/config/test_fixtures.py | 1 (parse engine_config.toml end-to-end) | NEW |

### Group: integration

**Scope:** `tests/integration/test_full_backup.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/integration/test_full_backup.py | 3 (qemu-img convert engine, libnbd engine, custom flags) | MODIFY |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| tests/mocks/mock_modules.py | ADD `full_transfer_engine="qemu-img-convert"`, `convert_parallel=4`, `convert_out_of_order=True` kwargs to `MockBackupProvider.create_full_backup()` and `MockBackupProvider.transfer_missing()` | Interface breakage — D1 adds new parameters with defaults to `IBackupProvider`; all mocks must accept them to pass contract tests. |
| tests/mocks/mock_modules.py | ADD `full_transfer_engine="qemu-img-convert"`, `convert_parallel=4`, `convert_out_of_order=True` kwargs to `MockBitmapBackupProvider.create_full_backup()` and `MockBitmapBackupProvider.transfer_missing()` | Same as above — mock must match updated `IBackupProvider` signature. |
| tests/conftest.py | ADD `full_transfer_engine="qemu-img-convert"`, `convert_parallel=4`, `convert_out_of_order=True` to `make_global_config()` factory fixture and `make_target()` factory fixture | New config fields need fixture coverage so tests can construct valid configs. |
| tests/modules/backup/test_bitmap.py | ADD `test_first_full_transfer_via_libnbd_engine` (mock-based, verifies `_full_transfer_via_libnbd()` is called when `full_transfer_engine="libnbd"`); ADD `test_create_full_backup_libnbd_with_zstd_compression` (verifies `qemu-img create -o compression_type=zstd`, `_start_write_server(compress=True)`, `_transfer(zero_skip=True)`); ADD `test_create_full_backup_libnbd_no_compression`; ADD `test_create_full_backup_custom_convert_parallel`; ADD `test_create_full_backup_convert_out_of_order_disabled`; ADD `test_create_full_backup_defaults_to_qemu_img_convert`; ADD `test_create_full_backup_libnbd_engine_selected`; ADD `test_transfer_missing_defaults_to_qemu_img_convert`; ADD `test_transfer_missing_libnbd_full_engine`; ADD `test_full_pull_lifecycle_branches_on_engine`; ADD `test_libnbd_full_zero_skip_all_zero`; ADD `test_libnbd_full_zero_skip_non_zero`; ADD `test_libnbd_full_compress_driver_enabled`; ADD `test_libnbd_full_no_compress_driver_when_compress_false`; ADD `test_atomic_full_export_libnbd_with_checkpoint`; ADD `test_qemu_img_convert_uses_stall_detection`; ADD `test_libnbd_full_uses_stall_detection`; ADD `test_stall_timeout_disabled_falls_back_to_fixed_timeout`; ADD `test_create_full_backup_stopped_vm_custom_flags`; ADD `test_create_full_backup_stopped_vm_direct_convert` | New functionality: engine selection branch, libnbd FULL path, configurable convert flags, stall detection plumbing. |
| tests/modules/backup/test_bitmap_incremental.py | ADD `test_incremental_unaffected_by_full_transfer_engine` (verifies incremental transfers ignore `full_transfer_engine` and always use pread/pwrite); ADD `test_incremental_zero_skip_false` | Spec mandates incrementals are always libnbd regardless of engine setting, and zero_skip=False for incrementals. |
| tests/interfaces/test_backup_provider.py | ADD `test_backup_provider_create_full_backup_accepts_full_transfer_engine`; ADD `test_backup_provider_create_full_backup_accepts_convert_parallel`; ADD `test_backup_provider_create_full_backup_accepts_convert_out_of_order`; ADD `test_backup_provider_transfer_missing_accepts_full_transfer_engine`; ADD `test_backup_provider_transfer_missing_accepts_convert_parallel`; ADD `test_backup_provider_transfer_missing_accepts_convert_out_of_order` | Contract tests must verify new parameters exist on interface methods with correct defaults. Parametrize over all concrete implementations. |
| tests/core/test_pipeline.py | ADD `test_core_passes_full_transfer_engine_to_create_full_backup`; ADD `test_core_passes_convert_parallel_to_create_full_backup`; ADD `test_core_passes_convert_out_of_order_to_create_full_backup`; ADD `test_core_passes_full_transfer_engine_to_transfer_missing`; ADD `test_core_passes_convert_parallel_to_transfer_missing`; ADD `test_core_passes_convert_out_of_order_to_transfer_missing` | Core must read new fields from `TargetConfig` and pass them explicitly to provider methods (DI pattern per AGENTS.md). |
| tests/config/test_model.py | ADD `test_global_config_full_transfer_engine_default_is_qemu_img_convert`; ADD `test_global_config_full_transfer_engine_is_immutable`; ADD `test_global_config_full_transfer_engine_set_to_libnbd`; ADD `test_global_config_convert_parallel_default_is_4`; ADD `test_global_config_convert_parallel_is_immutable`; ADD `test_global_config_convert_out_of_order_default_is_true`; ADD `test_global_config_convert_out_of_order_is_immutable` | New frozen dataclass fields must be verified for correct defaults and immutability. |
| tests/config/test_parser.py | ADD `test_valid_full_transfer_engine_accepted`; ADD `test_invalid_full_transfer_engine_raises_config_error`; ADD `test_valid_convert_parallel_accepted`; ADD `test_convert_parallel_below_range_raises_config_error`; ADD `test_convert_parallel_above_range_raises_config_error` | Config validation for new fields must be tested: accepted values, rejected values with clear error messages. |
| tests/config/test_resolver.py | ADD `test_target_full_transfer_engine_inherits_from_global`; ADD `test_target_full_transfer_engine_overrides_global`; ADD `test_target_full_transfer_engine_default_is_qemu_img_convert`; ADD `test_target_convert_parallel_inherits_from_global`; ADD `test_target_convert_parallel_overrides_global`; ADD `test_target_convert_out_of_order_inherits_from_global`; ADD `test_target_convert_out_of_order_overrides_global` | Inheritance resolution for new fields (global → target) must be verified end-to-end through ConfigFacade. |
| tests/config/test_fixtures.py | ADD `test_make_global_config_defaults_full_transfer_engine`; ADD `test_make_target_defaults_full_transfer_engine`; ADD `test_make_global_config_defaults_convert_parallel`; ADD `test_make_global_config_defaults_convert_out_of_order`; ADD `test_engine_config_toml_parses_correctly` | Conftest fixture defaults for new fields must be tested; new TOML fixture must parse correctly through ConfigFacade. |
| tests/fixtures/configs/engine_config.toml | CREATE new TOML fixture with `full_transfer_engine = "libnbd"` at global level, overridden to `"qemu-img-convert"` at one target, `convert_parallel = 2`, `convert_out_of_order = false` | Integration-test fixture that exercises inheritance cascade for all three new fields. |
| tests/integration/test_full_backup.py | ADD `test_full_backup_qemu_img_convert_engine_default` (running VM, engine param explicitly `"qemu-img-convert"`, verify `qemu-img convert` used); ADD `test_full_backup_libnbd_engine` (running VM, engine param `"libnbd"`, verify libnbd pread/pwrite used, no qemu-img convert, checkpoint atomic); ADD `test_full_backup_custom_convert_parallel_and_out_of_order` (running VM, engine `"qemu-img-convert"`, `convert_parallel=2`, `convert_out_of_order=False`, verify flags in command) | Real VM integration tests for both engines and custom flags, per design.md risk mitigation (reviving dead code, performance regression). |

### No Deletions Required

A grep of the entire `tests/` directory confirmed there are **zero references** to `rsync`, `FileCopy`, `file_copy`, `rate_limit`, `nbd_full_export`, or `copy_base`. These deprecated concepts were already fully removed from the test suite in prior changes.

The `deprecated_fields.toml` fixture and its tests (`test_deprecated_fields_toml_parses_with_logging_warnings`, `test_deprecated_fields_toml_full_every_ignored_in_behavior`, `test_full_every_deprecation_warning`, `test_full_compress_mapped_to_compress_with_warning`) are **deliberately kept** — they validate backward compatibility for users upgrading from older config formats. These tests are NOT related to the rsync/FileCopy removal and should remain.

## Risks & Edge Cases

- **[Risk: Reviving dead code `_transfer(zero_skip=True)`]** → The `zero_skip=True` branch in `_transfer()` has not been exercised since v0.3.0. **Mitigation tests:** `test_libnbd_full_zero_skip_all_zero` (verify all-zero chunk skipped, counter incremented), `test_libnbd_full_zero_skip_non_zero` (verify non-zero chunk written), `test_create_full_backup_libnbd_with_zstd_compression` (end-to-end libnbd FULL with compression), `test_create_full_backup_libnbd_no_compression` (end-to-end libnbd FULL without compression).

- **[Risk: Virtual size discovery for libnbd FULL]** → The qcow2 must be created before `_start_write_server()` but after `backup-begin`. **Mitigation tests:** `test_atomic_full_export_libnbd_with_checkpoint` verifies the correct ordering: `backup-begin` → `INbdClient.get_size()` → `qemu-img create` → `_start_write_server()` → `_transfer()`.

- **[Risk: Performance regression for libnbd FULL]** → Python pread/pwrite ~570x slower than qemu-img convert. **Mitigation tests:** `test_full_backup_libnbd_engine` (integration test) measures real-world transfer time; log-level test verifies WARNING is emitted when `full_transfer_engine="libnbd"` is selected.

- **[Trade-off: Interface breakage]** → Adding parameters to `IBackupProvider` methods breaks all implementations. **Mitigation tests:** All contract tests parametrize over `(BitmapBackupProvider, MockBackupProvider, MockBitmapBackupProvider)` and verify new parameters have defaults matching current behavior. Mock validity tests confirm `MockBackupProvider` and `MockBitmapBackupProvider` accept new kwargs without error. Core pass-through tests confirm Core correctly passes new `TargetConfig` fields to provider methods.

- **[Edge: libnbd engine ignores convert_parallel and convert_out_of_order]** → When `full_transfer_engine="libnbd"`, the `convert_parallel` and `convert_out_of_order` parameters are consumed as kwargs but silently ignored. **Mitigation test:** `test_create_full_backup_libnbd_engine_selected` — call with `convert_parallel=8, convert_out_of_order=False, full_transfer_engine="libnbd"` and verify no `qemu-img convert` command is constructed (the flags are not relevant for libnbd).

- **[Edge: Engine value default chain]** → If neither global nor target config specifies `full_transfer_engine`, the default `"qemu-img-convert"` must flow all the way from `GlobalConfig` → `TargetConfig` → method parameter default → `_full_pull_lifecycle()` branch. **Mitigation tests:** `test_global_config_full_transfer_engine_default_is_qemu_img_convert`, `test_target_full_transfer_engine_default_is_qemu_img_convert`, `test_create_full_backup_defaults_to_qemu_img_convert`, `test_transfer_missing_defaults_to_qemu_img_convert` form a chain verifying the default propagates correctly at every level.

- **[Edge: convert_parallel boundaries 1-8]** → Both 0 and 9 must raise `ConfigError`. Value 8 must be accepted. **Mitigation tests:** `test_convert_parallel_below_range_raises_config_error`, `test_convert_parallel_above_range_raises_config_error`, `test_valid_convert_parallel_accepted`.
