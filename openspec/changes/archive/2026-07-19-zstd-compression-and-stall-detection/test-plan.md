# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| **shell-abstraction** | IShell.run_with_stall_detection method | Process completes normally | `tests/utils/test_shell.py` | `test_run_with_stall_detection_completes_normally` | shell-unit |
| **shell-abstraction** | IShell.run_with_stall_detection method | Stall detected — process killed | `tests/utils/test_shell.py` | `test_run_with_stall_detection_kills_stalled_process` | shell-unit |
| **shell-abstraction** | IShell.run_with_stall_detection method | Data flowing slowly — not killed | `tests/utils/test_shell.py` | `test_run_with_stall_detection_slow_but_progressing` | shell-unit |
| **shell-abstraction** | IShell.run_with_stall_detection method | No output_file — no stall detection | `tests/utils/test_shell.py` | `test_run_with_stall_detection_no_output_file` | shell-unit |
| **shell-abstraction** | IShell.run_with_stall_detection method | Process exits non-zero | `tests/utils/test_shell.py` | `test_run_with_stall_detection_nonzero_exit` | shell-unit |
| **shell-abstraction** | IShell.run_with_stall_detection method | check=True suppresses error logging | `tests/utils/test_shell.py` | `test_run_with_stall_detection_check_mode_suppresses_error` | shell-unit |
| **shell-abstraction** | SubprocessShell implements run_with_stall_detection | Kills hung process on stall | `tests/utils/test_shell.py` | `test_subprocess_shell_stall_kills` | shell-unit |
| **shell-abstraction** | SubprocessShell implements run_with_stall_detection | Allows growing file | `tests/utils/test_shell.py` | `test_subprocess_shell_stall_allows_growth` | shell-unit |
| **shell-abstraction** | Structured logging | No speed/progress logged during stall detection | `tests/utils/test_shell.py` | `test_stall_detection_logs_no_speed` | shell-unit |
| **shell-abstraction** | IShell ABC has run_with_stall_detection | IShell has abstract method | `tests/interfaces/test_shell.py` | `test_ishell_has_run_with_stall_detection` | shell-contract |
| **shell-abstraction** | SubprocessShell is an IShell instance | instance check passes with new method | `tests/interfaces/test_shell.py` | `test_ishell_is_abstract` | shell-contract |
| **shell-abstraction** | MockShell implements run_with_stall_detection | Returns predefined ShellResult | `tests/mocks/__init__.py` | `test_mock_shell_run_with_stall_detection` | mock |
| **stall-detection** | SubprocessShell stall detection | Kills hung process (sleep 3600 + no output file) | `tests/utils/test_shell.py` | `test_subprocess_shell_stall_kills` | shell-unit |
| **stall-detection** | SubprocessShell stall detection | Allows growing file | `tests/utils/test_shell.py` | `test_subprocess_shell_stall_allows_growth` | shell-unit |
| **config-model** | compression_type in GlobalConfig | Default is "zstd" | `tests/config/test_model.py` | `test_global_config_compression_type_default` | config-model |
| **config-model** | compression_type in GlobalConfig | Immutable | `tests/config/test_model.py` | `test_global_config_compression_type_immutable` | config-model |
| **config-model** | compression_type in GlobalConfig | Set to "zlib" | `tests/config/test_model.py` | `test_global_config_compression_type_zlib` | config-model |
| **config-model** | backup_stall_timeout in GlobalConfig | Default is "30m" | `tests/config/test_model.py` | `test_global_config_backup_stall_timeout_default` | config-model |
| **config-model** | backup_stall_timeout in GlobalConfig | Immutable | `tests/config/test_model.py` | `test_global_config_backup_stall_timeout_immutable` | config-model |
| **config-model** | compression_type in TargetConfig | Inherits from global | `tests/config/test_model.py` | `test_target_config_compression_type_inherits` | config-model |
| **config-model** | compression_type in TargetConfig | Overrides global | `tests/config/test_model.py` | `test_target_config_compression_type_overrides` | config-model |
| **config-model** | backup_stall_timeout in TargetConfig | Inherits from global | `tests/config/test_model.py` | `test_target_config_backup_stall_timeout_inherits` | config-model |
| **config-model** | backup_stall_timeout in TargetConfig | Overrides global | `tests/config/test_model.py` | `test_target_config_backup_stall_timeout_overrides` | config-model |
| **config-parsing** | Parse compression_type from TOML | Global parsed from TOML | `tests/config/test_facade.py` | `test_global_compression_type_parsed` | config-facade |
| **config-parsing** | Parse compression_type from TOML | Target overrides global | `tests/config/test_facade.py` | `test_target_compression_type_overrides_global` | config-facade |
| **config-parsing** | Parse compression_type from TOML | Target inherits global | `tests/config/test_facade.py` | `test_target_compression_type_inherits` | config-facade |
| **config-parsing** | Parse compression_type from TOML | Invalid value raises ConfigError | `tests/config/test_facade.py` | `test_invalid_compression_type_raises_config_error` | config-facade |
| **config-parsing** | Parse compression_type from TOML | Absent defaults to "zstd" | `tests/config/test_facade.py` | `test_compression_type_absent_defaults_to_zstd` | config-facade |
| **config-parsing** | Parse backup_stall_timeout from TOML | Global parsed from TOML | `tests/config/test_facade.py` | `test_global_backup_stall_timeout_parsed` | config-facade |
| **config-parsing** | Parse backup_stall_timeout from TOML | Target overrides global | `tests/config/test_facade.py` | `test_target_stall_timeout_overrides_global` | config-facade |
| **config-parsing** | Parse backup_stall_timeout from TOML | Target inherits global | `tests/config/test_facade.py` | `test_target_stall_timeout_inherits` | config-facade |
| **config-parsing** | Parse backup_stall_timeout from TOML | Invalid value raises ConfigError | `tests/config/test_facade.py` | `test_invalid_stall_timeout_raises_config_error` | config-facade |
| **config-parsing** | Parse backup_stall_timeout from TOML | Absent defaults to "30m" | `tests/config/test_facade.py` | `test_stall_timeout_absent_defaults_to_30m` | config-facade |
| **config-parsing** | Parse backup_stall_timeout from TOML | "0s" disables stall detection | `tests/config/test_facade.py` | `test_stall_timeout_zero_disables` | config-facade |
| **backup-provider** | compression_type in create_full_backup | zstd compression (stopped VM) | `tests/modules/backup/test_copy.py` | `test_create_full_backup_compressed_zstd_stopped_vm` | backup-copy-unit |
| **backup-provider** | compression_type in create_full_backup | zlib compression (stopped VM) | `tests/modules/backup/test_copy.py` | `test_create_full_backup_compressed_zlib_stopped_vm` | backup-copy-unit |
| **backup-provider** | compression_type in create_full_backup | compression disabled | `tests/modules/backup/test_copy.py` | `test_create_full_backup_uncompressed_stopped_vm` (MODIFY) | backup-copy-unit |
| **backup-provider** | Stall detection for data transfer | rsync uses stall detection | `tests/modules/backup/test_copy.py` | `test_rsync_uses_stall_detection` | backup-copy-unit |
| **backup-provider** | Stall detection for data transfer | NBD convert uses stall detection | `tests/modules/backup/test_copy.py` | `test_nbd_full_uses_stall_detection` | backup-copy-unit |
| **backup-provider** | Stall detection for data transfer | Stall timeout disabled falls back | `tests/modules/backup/test_copy.py` | `test_stall_timeout_zero_falls_back` | backup-copy-unit |
| **backup-provider** | rsync with zstd compression | rsync --compress-choice=zstd | `tests/modules/backup/test_copy.py` | `test_rsync_with_zstd_compression` | backup-copy-unit |
| **backup-provider** | rsync with zlib compression | rsync --compress only (default) | `tests/modules/backup/test_copy.py` | `test_rsync_with_zlib_compression` | backup-copy-unit |
| **backup-provider** | rsync with zstd + rate limit | rsync --bwlimit --compress-choice=zstd | `tests/modules/backup/test_copy.py` | `test_rsync_zstd_with_rate_limit` | backup-copy-unit |
| **backup-provider** | rsync without compression | No --compress or --compress-choice | `tests/modules/backup/test_copy.py` | `test_rsync_no_compression` | backup-copy-unit |
| **backup-provider** | NBD full backup with zstd | qemu-img convert -c -o compression_type=zstd nbd:... | `tests/modules/backup/test_copy.py` | `test_nbd_full_zstd_compression` | backup-copy-unit |
| **backup-provider** | NBD full backup with zlib | qemu-img convert -c nbd:... | `tests/modules/backup/test_copy.py` | `test_nbd_full_zlib_compression` | backup-copy-unit |
| **backup-provider** | Full backup uses stall detection | run_with_stall_detection called | `tests/modules/backup/test_copy.py` | `test_full_backup_uses_stall_detection` | backup-copy-unit |
| **nbd-bitmap-backup** | transfer_missing with zstd | qemu-img convert -c -o compression_type=zstd via NBD | `tests/modules/backup/test_bitmap.py` | `test_bitmap_transfer_with_zstd_compression` | backup-bitmap-unit |
| **nbd-bitmap-backup** | transfer_missing with zlib | qemu-img convert -c via NBD (default zlib) | `tests/modules/backup/test_bitmap.py` | `test_bitmap_transfer_with_zlib_compression` | backup-bitmap-unit |
| **nbd-bitmap-backup** | transfer_missing uses stall detection | run_with_stall_detection called | `tests/modules/backup/test_bitmap.py` | `test_bitmap_transfer_uses_stall_detection` | backup-bitmap-unit |
| **nbd-bitmap-backup** | Bitmap FULL with zstd | nbd_full_export called with compression_type="zstd" | `tests/modules/backup/test_bitmap.py` | `test_bitmap_full_zstd_compression` | backup-bitmap-unit |
| **nbd-bitmap-backup** | Bitmap FULL with zlib | nbd_full_export called with compression_type="zlib" | `tests/modules/backup/test_bitmap.py` | `test_bitmap_full_zlib_compression` | backup-bitmap-unit |
| **live-vm-full-backup** | NBD full export with zstd | qemu-img convert -c -o compression_type=zstd nbd:... | `tests/modules/backup/test_bitmap.py` | `test_bitmap_full_zstd_compression` | backup-bitmap-unit |
| **live-vm-full-backup** | NBD full export with zlib | qemu-img convert -c nbd:... | `tests/modules/backup/test_bitmap.py` | `test_bitmap_full_zlib_compression` | backup-bitmap-unit |
| **live-vm-full-backup** | NBD full export uses stall detection | run_with_stall_detection called | `tests/modules/backup/test_bitmap.py` | `test_nbd_full_export_uses_stall_detection` | backup-bitmap-unit |
| **live-vm-full-backup** | NBD FULL creates tmp then renames | .tmp file used as output_file for stall detection | `tests/modules/backup/test_bitmap.py` | `test_nbd_full_tmp_rename` (MODIFY existing) | backup-bitmap-unit |
| **live-vm-full-backup** | NBD FULL failure leaves no final file | .tmp removed on failure | `tests/modules/backup/test_copy.py` | `test_nbd_full_failure_leaves_no_final_file` (MODIFY) | backup-copy-unit |
| **size-estimation** | _log_size_estimate() removed | No size projections logged | `tests/core/test_engine.py` | (DELETE: all size estimation tests) | core-unit |
| **size-estimation** | schedule_summary simplified | No projected fields | `tests/core/test_schedule_summary.py` | (MODIFY: all tests) | core-summary |
| **size-estimation** | estimate() simplified | No projected fields | `tests/core/test_engine.py` | (DELETE: test_estimate_method_*) | core-unit |
| **systemd-integration** | qsnap.service has TimeoutStartSec=0 | Unit file contains TimeoutStartSec=0 | `tests/systemd/test_units.py` | `test_qsnap_service_has_timeout_start_sec_zero` | systemd-unit |
| **systemd-integration** | qsnap.service is Type=oneshot | Unit file contains Type=oneshot | `tests/systemd/test_units.py` | `test_qsnap_service_is_oneshot` | systemd-unit |
| — | **Integration: real qemu-img zstd** | Real qemu-img convert with -c -o compression_type=zstd | `tests/integration/test_zstd_backup.py` | `test_qemu_img_convert_zstd_produces_valid_qcow2` | integration |
| — | **Integration: zstd vs zlib speed** | Compare zstd and zlib compression speeds | `tests/integration/test_zstd_backup.py` | `test_zstd_faster_than_zlib` | integration |
| — | **Integration: stall detection with real process** | Kill a stalled convert via stall detection | `tests/integration/test_stall_detection.py` | `test_stall_detection_kills_hung_convert` | integration |
| — | **Integration: slow-but-progressing allowed** | Slow write does not trigger stall | `tests/integration/test_stall_detection.py` | `test_slow_progress_not_killed` | integration |
| — | **Integration: rsync with zstd** | rsync --compress --compress-choice=zstd on real files | `tests/integration/test_zstd_backup.py` | `test_rsync_zstd_transfer` | integration |
| — | **Integration: large disk + zstd** | Model bug report: 500MB+ test disk, zstd compression, stall detection | `tests/integration/test_zstd_backup.py` | `test_large_disk_zstd_no_stall` | integration |

## Delegation Groups

### Group: shell-unit
**Scope:** `tests/utils/test_shell.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_shell.py` | `test_run_with_stall_detection_completes_normally` — command completes before first poll; returns ShellResult(success=True) | NEW |
| `tests/utils/test_shell.py` | `test_run_with_stall_detection_kills_stalled_process` — output file never grows, process killed after stall_timeout | NEW |
| `tests/utils/test_shell.py` | `test_run_with_stall_detection_slow_but_progressing` — output file grows slowly (1KB per cycle), not killed | NEW |
| `tests/utils/test_shell.py` | `test_run_with_stall_detection_no_output_file` — output_file=None behaves like run() with infinite timeout | NEW |
| `tests/utils/test_shell.py` | `test_run_with_stall_detection_nonzero_exit` — process exits with returncode=1, returns ShellResult(success=False) | NEW |
| `tests/utils/test_shell.py` | `test_run_with_stall_detection_check_mode_suppresses_error` — check=True logs at DEBUG on failure | NEW |
| `tests/utils/test_shell.py` | `test_subprocess_shell_stall_kills` — SubprocessShell kills `sleep 3600` after stall_timeout=60 | NEW |
| `tests/utils/test_shell.py` | `test_subprocess_shell_stall_allows_growth` — SubprocessShell allows growing file (background writer) | NEW |
| `tests/utils/test_shell.py` | `test_stall_detection_logs_no_speed` — no speed/progress logged during stall detection polling | NEW |

### Group: shell-contract
**Scope:** `tests/interfaces/test_shell.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_shell.py` | `test_ishell_has_run_with_stall_detection` — assert `run_with_stall_detection` is in IShell.__abstractmethods__ | NEW |
| `tests/interfaces/test_shell.py` | `test_ishell_is_abstract` — existing test; assert SubprocessShell passes `isinstance(sub, IShell)` still works | MODIFY |

### Group: mock-shell
**Scope:** `tests/mocks/mock_shell.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/mocks/mock_shell.py` | Add `run_with_stall_detection()` method to MockShell with expectation-matching | MODIFY |
| `tests/mocks/__init__.py` | (if mock contract test exists) `test_mock_shell_implements_full_interface` — verify MockShell has both `run` and `run_with_stall_detection` | NEW |

### Group: mock-modules
**Scope:** `tests/mocks/mock_modules.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/mocks/mock_modules.py` | `MockBackupProvider.create_full_backup()` — add `compression_type="zstd"` parameter | MODIFY |
| `tests/mocks/mock_modules.py` | `MockBitmapBackupProvider.create_full_backup()` — add `compression_type="zstd"` parameter | MODIFY |
| `tests/mocks/mock_modules.py` | `MockBackupProvider.transfer_missing()` — add `compression_type="zstd"` parameter (if method signature changes) | MODIFY |
| `tests/mocks/mock_modules.py` | `MockBitmapBackupProvider.transfer_missing()` — add `compression_type="zstd"` parameter (if method signature changes) | MODIFY |

### Group: config-model
**Scope:** `tests/config/test_model.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | `test_global_config_compression_type_default` — GlobalConfig().compression_type == "zstd" | NEW |
| `tests/config/test_model.py` | `test_global_config_compression_type_immutable` — FrozenInstanceError on mutation | NEW |
| `tests/config/test_model.py` | `test_global_config_compression_type_zlib` — GlobalConfig(compression_type="zlib") | NEW |
| `tests/config/test_model.py` | `test_global_config_backup_stall_timeout_default` — GlobalConfig().backup_stall_timeout == "30m" | NEW |
| `tests/config/test_model.py` | `test_global_config_backup_stall_timeout_immutable` — FrozenInstanceError on mutation | NEW |
| `tests/config/test_model.py` | `test_target_config_compression_type_inherits` — default "zstd" on TargetConfig | NEW |
| `tests/config/test_model.py` | `test_target_config_compression_type_overrides` — explicit "zlib" overrides default | NEW |
| `tests/config/test_model.py` | `test_target_config_backup_stall_timeout_inherits` — default "30m" on TargetConfig | NEW |
| `tests/config/test_model.py` | `test_target_config_backup_stall_timeout_overrides` — explicit "1h" overrides default | NEW |
| `tests/config/test_model.py` | `test_global_config_defaults` — add assertions for compression_type="zstd", backup_stall_timeout="30m" | MODIFY |
| `tests/config/test_model.py` | `test_global_config_immutable` — add assertion for mutation on compression_type, backup_stall_timeout | MODIFY |

### Group: config-facade
**Scope:** `tests/config/test_facade.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_facade.py` | `test_global_compression_type_parsed` — parse "zlib" from TOML | NEW |
| `tests/config/test_facade.py` | `test_target_compression_type_overrides_global` — target overrides global compression_type | NEW |
| `tests/config/test_facade.py` | `test_target_compression_type_inherits` — target inherits global compression_type | NEW |
| `tests/config/test_facade.py` | `test_invalid_compression_type_raises_config_error` — "lz4" raises ConfigError | NEW |
| `tests/config/test_facade.py` | `test_compression_type_absent_defaults_to_zstd` — absent field gets "zstd" | NEW |
| `tests/config/test_facade.py` | `test_global_backup_stall_timeout_parsed` — parse "1h" from TOML | NEW |
| `tests/config/test_facade.py` | `test_target_stall_timeout_overrides_global` — target overrides global stall timeout | NEW |
| `tests/config/test_facade.py` | `test_target_stall_timeout_inherits` — target inherits global stall timeout | NEW |
| `tests/config/test_facade.py` | `test_invalid_stall_timeout_raises_config_error` — "abc" raises ConfigError | NEW |
| `tests/config/test_facade.py` | `test_stall_timeout_absent_defaults_to_30m` — absent field gets "30m" | NEW |
| `tests/config/test_facade.py` | `test_stall_timeout_zero_disables` — "0s" disables stall detection | NEW |

### Group: config-fixtures
**Scope:** `tests/fixtures/configs/`, `tests/conftest.py`, `tests/mocks/mock_config.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/conftest.py` | `make_global_config` — add `compression_type="zstd"`, `backup_stall_timeout="30m"` kwargs | MODIFY |
| `tests/conftest.py` | `make_target` — add `compression_type="zstd"`, `backup_stall_timeout="30m"` kwargs | MODIFY |
| `tests/fixtures/configs/` (optional) | New fixture: `zstd_config.toml` with explicit compression_type fields | NEW |

### Group: backup-copy-unit
**Scope:** `tests/modules/backup/test_copy.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_copy.py` | `test_create_full_backup_compressed_zstd_stopped_vm` — assert `-c` AND `-o compression_type=zstd` in command | NEW |
| `tests/modules/backup/test_copy.py` | `test_create_full_backup_compressed_zlib_stopped_vm` — assert `-c` present, NO `-o compression_type=` in command | NEW |
| `tests/modules/backup/test_copy.py` | `test_rsync_uses_stall_detection` — assert `run_with_stall_detection` called for rsync | NEW |
| `tests/modules/backup/test_copy.py` | `test_nbd_full_uses_stall_detection` — assert `run_with_stall_detection` called for NBD convert | NEW |
| `tests/modules/backup/test_copy.py` | `test_stall_timeout_zero_falls_back` — when backup_stall_timeout="0s", `run()` used with timeout=3600 | NEW |
| `tests/modules/backup/test_copy.py` | `test_rsync_with_zstd_compression` — assert `--compress-choice=zstd` in rsync when compression_type="zstd" | NEW |
| `tests/modules/backup/test_copy.py` | `test_rsync_with_zlib_compression` — assert `--compress` present, NO `--compress-choice=` when compression_type="zlib" | NEW |
| `tests/modules/backup/test_copy.py` | `test_rsync_zstd_with_rate_limit` — assert both `--bwlimit` and `--compress-choice=zstd` | NEW |
| `tests/modules/backup/test_copy.py` | `test_rsync_no_compression` — assert no `--compress` or `--compress-choice` when compress=False | NEW |
| `tests/modules/backup/test_copy.py` | `test_nbd_full_zstd_compression` — NBD convert with `-c -o compression_type=zstd` | NEW |
| `tests/modules/backup/test_copy.py` | `test_nbd_full_zlib_compression` — NBD convert with `-c` only (no -o flag) | NEW |
| `tests/modules/backup/test_copy.py` | `test_full_backup_uses_stall_detection` — create_full_backup passes output_file and stall_timeout to run_with_stall_detection | NEW |
| `tests/modules/backup/test_copy.py` | `test_create_full_backup_compressed_stopped_vm` — MODIFY to also assert `-o compression_type=zstd` (since the provider now passes compression_type to Shell) | MODIFY |
| `tests/modules/backup/test_copy.py` | `test_nbd_full_backup_with_compression_succeeds` — MODIFY to assert `-o compression_type=zstd` is present | MODIFY |
| `tests/modules/backup/test_copy.py` | `test_transfer_missing_new_snapshot_rsync_empty_target` — MODIFY to verify `--compress-choice=zstd` in rsync command (since target.compress defaults to True and compression_type defaults to "zstd") | MODIFY |
| `tests/modules/backup/test_copy.py` | `test_transfer_incremental_rebase_backing_path` — MODIFY rsync `--compress` assertion to also check `--compress-choice=zstd` | MODIFY |
| `tests/modules/backup/test_copy.py` | `test_transfer_non_incremental_no_rebase` — MODIFY rsync `--compress` assertion | MODIFY |
| `tests/modules/backup/test_copy.py` | `test_transfer_missing_metadata_verification_default` — MODIFY rsync `--compress` assertion | MODIFY |
| `tests/modules/backup/test_copy.py` | `test_transfer_missing_full_verification` — MODIFY rsync `--compress` assertion | MODIFY |
| `tests/modules/backup/test_copy.py` | `test_transfer_missing_no_verification_when_off` — MODIFY rsync `--compress` assertion | MODIFY |
| `tests/modules/backup/test_copy.py` | `test_transfer_with_rate_limit_uses_rsync` — MODIFY to verify `--compress-choice=zstd` | MODIFY |
| `tests/modules/backup/test_copy.py` | All other tests that verify rsync `--compress` without `--compress-choice=` — UPDATE to expect `--compress-choice=zstd` when compression_type="zstd" (default) | MODIFY |

### Group: backup-bitmap-unit
**Scope:** `tests/modules/backup/test_bitmap.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_transfer_with_zstd_compression` — qemu-img convert includes -c -o compression_type=zstd | NEW |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_transfer_with_zlib_compression` — qemu-img convert includes -c only (no -o) | NEW |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_transfer_uses_stall_detection` — run_with_stall_detection called for NBD convert | NEW |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_full_zstd_compression` — create_full_backup passes compression_type="zstd" to nbd_full_export | NEW |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_full_zlib_compression` — create_full_backup passes compression_type="zlib" to nbd_full_export | NEW |
| `tests/modules/backup/test_bitmap.py` | `test_nbd_full_export_uses_stall_detection` — nbd_full_export uses run_with_stall_detection | NEW |
| `tests/modules/backup/test_bitmap.py` | `test_first_backup_full_nbd_no_prior_checkpoint` — MODIFY to verify `-c -o compression_type=zstd` in convert command (since compress=True default) | MODIFY |
| `tests/modules/backup/test_bitmap.py` | `test_incremental_backup_dirty_blocks_via_nbd` — MODIFY to verify `-c -o compression_type=zstd` | MODIFY |
| `tests/modules/backup/test_bitmap.py` | `test_checkpoint_cleanup_after_successful_transfer` — MODIFY to verify `-c -o compression_type=zstd` | MODIFY |
| `tests/modules/backup/test_bitmap.py` | `test_transfer_failure_preserves_checkpoint` — MODIFY to verify `-c -o compression_type=zstd` | MODIFY |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_create_full_backup_with_compression_succeeds` — MODIFY to verify BOTH `-c` AND `-o compression_type=zstd` | MODIFY |
| `tests/modules/backup/test_bitmap.py` | All other tests asserting `-c` in qemu-img convert — MODIFY to also assert `-o compression_type=zstd` | MODIFY |

### Group: core-summary
**Scope:** `tests/core/test_schedule_summary.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_schedule_summary.py` | `test_schedule_summary_empty_state_produces_simulation` — remove assertions for Projected fields, keep Base image actual-size | MODIFY |
| `tests/core/test_schedule_summary.py` | `test_schedule_summary_shows_snapshot_and_backup_breakdown` — remove assertions for Projected fields | MODIFY |
| `tests/core/test_schedule_summary.py` | `test_schedule_summary_includes_base_image_size` — test remains, but remove Projected assertions if present | MODIFY |
| `tests/core/test_schedule_summary.py` | `test_schedule_summary_includes_avg_incremental_size` — check if avg incremental size is still logged (factual data) or removed | MODIFY |

### Group: core-unit
**Scope:** `tests/core/test_engine.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_engine.py` | All size estimation tests (lines ~832-1297) | DELETE |
| `tests/core/test_engine.py` | `test_action_appended_on_full_backup` — remove mock_shell.expect("qemu-img info") and mock_shell.expect("du") for size estimation; keep only mocks needed for FULL backup itself | MODIFY |
| `tests/core/test_engine.py` | `test_action_appended_on_backup_delete` — remove size estimation mocks | MODIFY |
| `tests/core/test_engine.py` | `test_backup_failed_warning_with_transfer_failures` — remove size estimation mocks | MODIFY |
| `tests/core/test_engine.py` | `test_no_backup_failed_warning_when_all_succeed` — remove size estimation mocks | MODIFY |
| `tests/core/test_engine.py` | `test_backup_transfer_info_log` — remove size estimation mocks | MODIFY |
| `tests/core/test_engine.py` | `test_full_backup_create_info_log` — remove size estimation mocks (keep qemu-img info for FULL backup itself) | MODIFY |
| `tests/core/test_engine.py` | `test_backup_delete_info_log` — remove size estimation mocks | MODIFY |
| `tests/core/test_engine.py` | `test_ghost_retention_info_log` — remove size estimation mocks | MODIFY |
| `tests/core/test_engine.py` | `test_no_actions_in_dry_run_mutations` — remove size estimation mocks | MODIFY |

### Group: systemd-unit
**Scope:** `tests/systemd/test_units.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/systemd/test_units.py` | `test_qsnap_service_has_timeout_start_sec_zero` — assert `TimeoutStartSec=0` in generated service unit | NEW |
| `tests/systemd/test_units.py` | `test_qsnap_service_is_oneshot` — assert `Type=oneshot` in generated service unit (may already exist) | NEW or MODIFY |

### Group: integration-zstd
**Scope:** `tests/integration/test_zstd_backup.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_zstd_backup.py` | `test_qemu_img_convert_zstd_produces_valid_qcow2` — create test disk, convert with -c -o compression_type=zstd, verify with qemu-img info/check | NEW |
| `tests/integration/test_zstd_backup.py` | `test_zstd_faster_than_zlib` — create 500MB disk with non-zero data, time both zstd and zlib conversions, assert zstd is faster | NEW |
| `tests/integration/test_zstd_backup.py` | `test_rsync_zstd_transfer` — create file, rsync with --compress --compress-choice=zstd, verify integrity | NEW |
| `tests/integration/test_zstd_backup.py` | `test_large_disk_zstd_no_stall` — create 1G+ test disk, run qemu-img convert with stall detection, verify completes without false positive stall | NEW |

### Group: integration-stall
**Scope:** `tests/integration/test_stall_detection.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_stall_detection.py` | `test_stall_detection_kills_hung_convert` — start qemu-img convert on a very large data source, simulate stall by making output file immutable, verify process is killed after stall_timeout | NEW |
| `tests/integration/test_stall_detection.py` | `test_slow_progress_not_killed` — write data very slowly to output file during convert, verify stall detection does not kill | NEW |

---

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/conftest.py` | `make_global_config()` — add `compression_type="zstd"`, `backup_stall_timeout="30m"` kwargs | New config fields added to GlobalConfig |
| `tests/conftest.py` | `make_target()` — add `compression_type="zstd"`, `backup_stall_timeout="30m"` kwargs | New config fields added to TargetConfig |
| `tests/mocks/mock_shell.py` | Add `run_with_stall_detection()` method matching IShell ABC signature | New abstract method on IShell; all mocks must implement |
| `tests/mocks/mock_modules.py` | `MockBackupProvider.create_full_backup()` — add `compression_type="zstd"` parameter | New parameter on IBackupProvider.create_full_backup |
| `tests/mocks/mock_modules.py` | `MockBitmapBackupProvider.create_full_backup()` — add `compression_type="zstd"` parameter | New parameter on IBackupProvider.create_full_backup |
| `tests/config/test_model.py` | `test_global_config_defaults` — add assertions for `compression_type="zstd"`, `backup_stall_timeout="30m"` | New config fields with defaults |
| `tests/config/test_model.py` | `test_global_config_immutable` — add mutation assertions for new fields | Verify immutability of new fields |
| `tests/modules/backup/test_copy.py` | `test_create_full_backup_compressed_stopped_vm` — add assertion for `-o compression_type=` in command (currently only asserts `-c`) | zstd is now the default; command must include `-o compression_type=zstd` |
| `tests/modules/backup/test_copy.py` | All rsync `--compress` tests — add assertion for `--compress-choice=zstd` when compression_type="zstd" (default) | rsync now uses zstd by default |
| `tests/modules/backup/test_bitmap.py` | All `-c` assertion tests — add assertion for `-o compression_type=zstd` (since default is now zstd, not zlib) | qemu-img convert now uses `-c -o compression_type=zstd` by default |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_create_full_backup_with_compression_succeeds` — assert both `-c` AND `-o compression_type=zstd` | zstd is now the default compression |
| `tests/core/test_schedule_summary.py` | Remove assertions for "Projected FULLs:", "Projected incrementals:", "Projected total size:" | `_log_size_estimate()` removed |
| `tests/core/test_engine.py` | Remove `qemu-img info` and `du` expectations for size estimation from tests that only have them as side effects | `_log_size_estimate()` removed from pipeline |
| `tests/interfaces/test_shell.py` | Add `test_ishell_has_run_with_stall_detection` | New abstract method on IShell |
| `tests/interfaces/test_backup_provider.py` | Add parametrized contract test for compression_type parameter on create_full_backup and transfer_missing (if contract tests exist for IBackupProvider) | New parameter on IBackupProvider methods |

---

## Outdated Tests to Delete or Rewrite

| File | What's Outdated | Action (DELETE/MODIFY) | Reason |
|---|---|---|---|
| `tests/core/test_engine.py` | `test_size_estimation_logged_during_normal_run` (lines ~835-880) — asserts "Size estimate" in log, `base=1048576` | DELETE | `_log_size_estimate()` and `_log_size_estimate()` signal are removed (design D5 removed). |
| `tests/core/test_engine.py` | `test_size_estimation_logged_during_dry_run` (lines ~883-946) — asserts "Size estimate" and "[dry-run] FULL backup would be created" with `bucket=weekly` | DELETE | `_log_size_estimate()` removed; dry-run FULL indicator should be tested elsewhere (non-size-estimation path). |
| `tests/core/test_engine.py` | `test_size_estimation_no_state_history` (lines ~949-991) — asserts `avg_inc=0`, `base=2097152` in size estimation log | DELETE | No more `_log_size_estimate()`. |
| `tests/core/test_engine.py` | `test_estimate_method_for_specific_vm` (lines ~994-1032) — asserts "Projected FULLs:", "Projected total size:", "Current target size:" | DELETE | `Core.estimate()` simplified — no projections. |
| `tests/core/test_engine.py` | `test_estimate_method_for_all_vms` (lines ~1035-1072) — asserts "Projected FULLs:", "Current target size:" | DELETE | `Core.estimate()` simplified — no projections. |
| `tests/core/test_engine.py` | `test_compressed_full_projection_30_percent` (lines ~1075-1114) — asserts `full(compressed=True)=300000` (base_size × 0.3) | DELETE | 0.3 compression factor formula removed. |
| `tests/core/test_engine.py` | `test_uncompressed_full_projection_100_percent` (lines ~1117-1155) — asserts `full(compressed=False)=1000000` | DELETE | No more projections. |
| `tests/core/test_engine.py` | `test_incremental_size_rolling_average_from_state` (lines ~1158-1230) — asserts `avg_inc=200000` in size estimation log | DELETE | No more `_log_size_estimate()`. |
| `tests/core/test_engine.py` | `test_size_estimation_uses_force_share_on_base_image` (lines ~1236-1296) — asserts `--force-share` in qemu-img info call from `_log_size_estimate()` | DELETE | `_log_size_estimate()` removed. |
| `tests/core/test_schedule_summary.py` | `test_schedule_summary_empty_state_produces_simulation` — asserts "Projected FULLs:", "Projected incrementals:", "Projected total size:" | MODIFY | Remove projection assertions; keep factual assertions (Base image actual-size, avg incremental size if still tracked). |
| `tests/core/test_schedule_summary.py` | `test_schedule_summary_shows_snapshot_and_backup_breakdown` — asserts "Projected FULLs:", "Projected incrementals:", "Projected total size:" | MODIFY | Remove projection assertions; keep snapshot/backup breakdown assertions. |
| `tests/core/test_engine.py` | `test_action_appended_on_full_backup` — has `mock_shell.expect("qemu-img info")` and `mock_shell.expect("du")` as side effects for size estimation | MODIFY | Remove size estimation mocks; keep qemu-img info mock for the FULL backup path itself if needed. |
| `tests/core/test_engine.py` | `test_action_appended_on_backup_delete` — has `mock_shell.expect("qemu-img info")` and `mock_shell.expect("du")` for size estimation | MODIFY | Remove size estimation mocks. |
| `tests/core/test_engine.py` | `test_backup_failed_warning_with_transfer_failures` — has qemu-img info + du mocks for size estimation side effects | MODIFY | Remove size estimation mocks. |
| `tests/core/test_engine.py` | `test_no_backup_failed_warning_when_all_succeed` — has qemu-img info + du mocks | MODIFY | Remove size estimation mocks. |
| `tests/core/test_engine.py` | `test_backup_transfer_info_log` — has qemu-img info + du mocks | MODIFY | Remove size estimation mocks. |
| `tests/core/test_engine.py` | `test_full_backup_create_info_log` — has qemu-img info + du mocks | MODIFY | Remove size estimation mocks (keep qemu-img info for FULL backup). |
| `tests/core/test_engine.py` | `test_backup_delete_info_log` — has qemu-img info + du mocks | MODIFY | Remove size estimation mocks. |
| `tests/core/test_engine.py` | `test_ghost_retention_info_log` — has qemu-img info + du mocks | MODIFY | Remove size estimation mocks. |
| `tests/core/test_engine.py` | `test_no_actions_in_dry_run_mutations` — has qemu-img info + du mocks | MODIFY | Remove size estimation mocks (dry-run no longer calls _log_size_estimate). |
| `tests/modules/backup/test_copy.py` | `test_create_full_backup_compressed_stopped_vm` — asserts `-c` in qemu-img convert but does NOT assert `-o compression_type=zstd` | MODIFY | Default compression_type is now "zstd"; command must include `-o compression_type=zstd`. Keep test but update assertions. |
| `tests/modules/backup/test_copy.py` | `test_nbd_full_backup_with_compression_succeeds` — asserts `-c` present but does NOT assert `-o compression_type=zstd` | MODIFY | Default compression_type is now "zstd"; command must include `-o compression_type=zstd`. |
| `tests/modules/backup/test_copy.py` | All rsync `--compress` assertion tests (11+ tests) — assert `--compress` but not `--compress-choice=zstd` | MODIFY | Default compression_type="zstd" → rsync uses `--compress-choice=zstd`. Update each assertion from `assert "--compress" in rsync_cmds[0]` to also check for `--compress-choice=zstd`. Affected: `test_transfer_missing_new_snapshot_rsync_empty_target`, `test_transfer_incremental_rebase_backing_path`, `test_transfer_non_incremental_no_rebase`, `test_transfer_rsync_fails_disk_full`, `test_rsync_unavailable_transfer_fails_no_cp_fallback`, `test_transfer_missing_metadata_verification_default`, `test_transfer_missing_full_verification`, `test_transfer_missing_no_verification_when_off`, `test_transfer_with_rate_limit_uses_rsync`, `test_pre_transfer_info_log`, `test_transfer_rebase_failure_returns_backup_result_failure`, `test_transfer_verify_failure_deletes_file_and_logs_warning`, `test_transfer_json_decode_failure_logs_warning`, `test_transfer_missing_rebases_to_full_anchor`, `test_transfer_missing_no_full_anchor_uses_source_backing`. | |
| `tests/modules/backup/test_bitmap.py` | All tests asserting `-c` in qemu-img convert (6+ tests) — assert `-c` but not `-o compression_type=zstd` | MODIFY | Default compression_type="zstd" → qemu-img convert command must include `-c -o compression_type=zstd`. Affected: `test_first_backup_full_nbd_no_prior_checkpoint`, `test_incremental_backup_dirty_blocks_via_nbd`, `test_checkpoint_cleanup_after_successful_transfer`, `test_transfer_failure_preserves_checkpoint`, `test_bitmap_backup_ignores_rate_limit`, `test_bitmap_create_full_backup_with_compression_succeeds`. | |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_create_full_backup_with_compression_succeeds` — asserts `-c` present on NBD convert. Should also verify `-c -o compression_type=zstd` and that the `nbd_full_export` receives `compression_type="zstd"`. | MODIFY | Default is now "zstd"; need to verify full convert command string. |
| `tests/interfaces/test_shell.py` | Only tests `IShell.run()`; no test for `run_with_stall_detection()` | MODIFY | New abstract method added to IShell; contract test must verify it exists. |
| `tests/mocks/mock_shell.py` | Only implements `run()`; no `run_with_stall_detection()` method | MODIFY | IShell ABC now requires `run_with_stall_detection()`; MockShell must implement it or tests will fail on construction. |

---

## Risks & Edge Cases

- **[Risk] MockShell: expectation API mismatch** → `run_with_stall_detection()` has `output_file: Path | None, stall_timeout: int` parameters that `run()` doesn't have. The existing `expect()` pattern relies on matching the command string via regex; `run_with_stall_detection()` may need the same pattern-matching mechanism. If so, the implementation is straightforward (match on cmd, return predefined result). If the expectation API needs to be extended (e.g., `expect_stall(pattern, output_file, timeout)`), then the MockShell interface change is more complex.
- **[Risk] Tests that mock `shell.run()` (via `patch.object(mock_shell, "run", ...)`) will not intercept `run_with_stall_detection()` calls** → In all backup provider unit tests, if the production code switches from `shell.run()` to `shell.run_with_stall_detection()`, existing `patch.object(mock_shell, "run", wraps=mock_shell.run)` spies will no longer capture the command execution. Tests need to spy on `run_with_stall_detection` instead. This affects ~20 tests across `test_copy.py` and `test_bitmap.py`.
- **[Risk] rsync `--compress-choice=zstd` requires rsync ≥ 3.2.0** → Integration tests must check rsync version before testing with `--compress-choice=zstd`. Unit tests are unaffected (MockShell).
- **[Risk] qemu-img `-o compression_type=zstd` requires qemu-img ≥ 5.2** → Integration tests must check qemu-img version. Unit tests are unaffected. The system's QEMU 11.0.2 supports this.
- **[Risk] Stall detection polling interval (60s)** → Unit tests for stall detection are inherently slow (need to wait for stall_timeout). For the `test_subprocess_shell_stall_kills` test, use a short stall_timeout (e.g., 5s for testing). Production default is 1800s (30 min).
- **[Risk] `_log_size_estimate()` removal affects `_execute_pipeline()` call sites** → If Core's `_execute_pipeline()` calls `_log_size_estimate(vm, target)` as a step, and that step is removed, any test that spied on `_execute_pipeline()` or verified step ordering may break. Check `tests/core/test_pipeline.py`.
- **[Risk] `IShell.run()` timeout parameter may be removed for data-transfer calls** → With stall detection replacing fixed timeouts, the `timeout` parameter on `run()` becomes unused for data-transfer commands. However, `run()` still needs timeout for short commands. The `run_with_stall_detection()` does not take a `timeout` parameter (stall_timeout replaces it). Tests that assert on the `timeout` value passed to `run()` for backup commands may need updating.
- **[Risk] `qsnap estimate` CLI tests may assert on projected sizes** → If `tests/cli/` has tests for the `estimate` subcommand that verify projected sizes in output, those assertions must be removed or updated to reflect the simplified (factual-only) output.
- **[Edge] `compression_type` is ignored when `compress=False`** → Tests should verify that when `compress=False` and `compression_type="zstd"`, NO `-c` or `-o compression_type=` flag appears in qemu-img convert, and no `--compress` or `--compress-choice=` appears in rsync.
- **[Edge] `backup_stall_timeout="0s"` disables stall detection and falls back to fixed timeout** → Tests should verify that when `backup_stall_timeout="0s"`, `run()` with `timeout=3600` is called, not `run_with_stall_detection()`.
