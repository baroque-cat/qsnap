# QA Strategy & Test Plan

> Removal-first change: the center of gravity is DELETING and MODIFYING existing tests, not writing new ones. Every scenario of all 16 spec deltas is mapped below, including REMOVED requirements (mapped to test deletions). New tests are justified only for behavior newly introduced by the deltas (deprecation warnings, factory hard gates, stopped-VM FULL failure, unconditional libnbd check).

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| **rate-limit** | REMOVED — GlobalConfig rate_limit field | *(no scenario — field removal)* | `tests/config/test_model.py` | `test_global_config_rate_limit_defaults_no`, `test_global_config_rate_limit_frozen`, `test_global_config_has_rate_limit_field`, `test_make_global_config_accepts_rate_limit_kwarg` | `config-suite` |
| **rate-limit** | REMOVED — TargetConfig rate_limit field | *(no scenario — field removal)* | `tests/config/test_model.py` | `test_target_config_rate_limit_frozen`, `test_target_config_has_rate_limit_field`, `test_make_target_accepts_rate_limit_kwarg` | `config-suite` |
| **rate-limit** | REMOVED — GlobalConfig immutability includes rate_limit | *(no scenario — moot)* | `tests/config/test_model.py` | `test_global_config_frozen_rate_limit` (line 427–438) | `config-suite` |
| **rate-limit** | REMOVED — TargetConfig immutability includes rate_limit | *(no scenario — moot)* | `tests/config/test_model.py` | `test_target_config_rate_limit_frozen` (line 440–444) | `config-suite` |
| **rate-limit** | REMOVED — Rsync used for transfers when rate_limit set | *(no scenario — mechanism removed)* | `tests/modules/backup/test_copy.py` | ~80 tests (entire file) | `backup-modules` |
| **rate-limit** | REMOVED — Rsync `--partial` resume | *(no scenario — mechanism removed)* | `tests/modules/backup/test_copy.py` | *(subset of file deletion)* | `backup-modules` |
| **rate-limit** | REMOVED — Fallback to `cp` when rsync unavailable | *(no scenario — mechanism removed)* | `tests/modules/backup/test_copy.py` | *(subset of file deletion)* | `backup-modules` |
| **rate-limit** | REMOVED — Transfer logging for rate-limited transfers | *(no scenario — mechanism removed)* | `tests/modules/backup/test_copy.py` | *(subset of file deletion)* | `backup-modules` |
| **rate-limit** | REMOVED — Anomalous throughput warning | *(no scenario — mechanism removed)* | `tests/core/test_validation.py` | `test_rsync_check_always_runs_regardless_of_rate_limit` | `core-suite` |
| **rate-limit** | REMOVED — Rate limit parsed with binary suffixes | *(no scenario — helpers removed)* | `tests/utils/test_parsing.py` | `test_parse_rate_limit_*` (10 tests: 500k, 100m, no, zero, empty, 1g, lowercase, invalid, no-suffix, invalid-suffix) | `config-suite` |
| **rate-limit** | REMOVED — Rsync `--bwlimit` receives KiB/s | *(no scenario — helper removed)* | `tests/utils/test_parsing.py` | `test_rate_limit_to_kib_*` (3 tests: 100m, no, 500k) | `config-suite` |
| **rate-limit** | REMOVED — FULL/bitmap backup unaffected by rate_limit | *(no scenario — moot)* | `tests/modules/backup/test_bitmap.py` | `test_bitmap_backup_ignores_rate_limit` (line 521) | `backup-modules` |
| **rate-limit** | REMOVED — Pre-flight rsync availability check | *(no scenario — check removed)* | `tests/core/test_validation.py` | `test_rsync_available_validation_passes`, `test_rsync_unavailable_pipeline_aborts` | `core-suite` |
| **rate-limit** | REMOVED — Fixture TOML files | *(no scenario — fixtures deleted)* | `tests/fixtures/configs/` | `rate_limit_global.toml`, `rate_limit_target_override.toml`, `rate_limit_invalid.toml` | `config-suite` |
| **rate-limit** | REMOVED — ConfigFacade rate_limit tests | *(no scenario — parsing removed)* | `tests/config/test_facade.py` | `test_global_rate_limit_parsed`, `test_invalid_rate_limit_raises_config_error`, `test_target_overrides_global_rate_limit`, `test_target_inherits_global_rate_limit` | `config-suite` |
| **env-validation** | REMOVED — Pre-flight rsync availability check | *(no scenario — check removed)* | `tests/conftest.py` | `shell.expect("which rsync")` line (line 89–97) | `core-suite` |
| **env-validation** | REMOVED — Rsync check in mock_shell | *(no scenario)* | `tests/config/test_fixtures.py` | `test_mock_shell_knows_rsync` | `config-suite` |
| **env-validation** | REMOVED — Rsync in mock_shell test | *(no scenario)* | `tests/mocks/test_mock_shell.py` | Lines 70, 85, 89 (rsync command references) | `factory-interfaces-mocks` |
| **env-validation** | REMOVED — Rsync in cli test_commands | *(no scenario)* | `tests/cli/test_commands.py` | Line 467 (`error="rsync failed..."`) | `core-suite` |
| **env-validation** | MODIFIED — libnbd availability check for all targets (unconditional) | libnbd installed — validation passes | `tests/integration/test_env_validation.py` | `test_validate_environment_with_bitmap_target_passes` (MODIFY — rename, remove file-copy context) | `integration-suite` |
| **env-validation** | MODIFIED — libnbd availability check for all targets (unconditional) | libnbd missing — hard failure | `tests/integration/test_env_validation.py` | NEW: `test_libnbd_missing_hard_failure` | `integration-suite` |
| **env-validation** | MODIFIED — libnbd availability check for all targets (unconditional) | Dry-run downgrades the failure to a warning | `tests/core/test_validation.py` | NEW: `test_dry_run_downgrades_libnbd_missing_to_warning` | `core-suite` |
| **env-validation** | REMOVED — File-copy skips libnbd check scenario | *(no scenario — unconditional now)* | `tests/core/test_bitmap_dependency.py` | `test_validate_environment_file_copy_skips_libnbd_check` | `core-suite` |
| **env-validation** | REMOVED — "no bitmap targets skips libnbd" scenario | *(no scenario — unconditional now)* | `tests/integration/test_env_validation.py` | `test_no_bitmap_targets_skips_libnbd_check` | `integration-suite` |
| **module-factory** | REMOVED — DefaultFactory gates BitmapBackupProvider on libvirt version | *(no scenario — fallback deleted)* | `tests/factory/test_default.py` | `test_factory_bitmap_mode_old_libvirt_falls_back`, `test_factory_non_bitmap_mode_no_version_check`, `test_factory_bitmap_fallback_logs_warning`, `test_factory_libvirt_7_1_falls_back`, `test_factory_bitmap_mode_without_libnbd_with_old_libvirt_returns_fallback` | `factory-interfaces-mocks` |
| **module-factory** | ADDED — DefaultFactory returns BitmapBackupProvider with hard dependency gates | Sufficient platform returns BitmapBackupProvider | `tests/factory/test_default.py` | NEW: `test_factory_always_returns_bitmap_backup_provider` | `factory-interfaces-mocks` |
| **module-factory** | ADDED — DefaultFactory returns BitmapBackupProvider with hard dependency gates | Old libvirt is a hard error | `tests/factory/test_default.py` | NEW: `test_factory_old_libvirt_raises_runtime_error` | `factory-interfaces-mocks` |
| **module-factory** | ADDED — DefaultFactory returns BitmapBackupProvider with hard dependency gates | Missing libnbd is a hard error | `tests/factory/test_default.py` | MODIFY: `test_factory_bitmap_mode_without_libnbd_raises_actionable_error` (remove old-libvirt gate, always test non-fallback path) | `factory-interfaces-mocks` |
| **module-factory** | MODIFIED — Factory passes IStateManager to BitmapBackupProvider | Factory constructs BitmapBackupProvider with state | `tests/factory/test_default.py` | `test_factory_passes_state_to_bitmap_provider` (MODIFY — remove bitmap-vs-file-copy distinction) | `factory-interfaces-mocks` |
| **config-model** | REMOVED — TargetConfig incremental_mode field | *(no scenario — field removed)* | `tests/config/test_model.py` | `test_target_config_default_incremental_mode_is_bitmap`, `test_target_config_explicit_incremental_mode_file_copy`, `test_target_config_explicit_incremental_mode_bitmap` (3 tests) | `config-suite` |
| **config-model** | REMOVED — TargetConfig rate_limit field | *(no scenario — field removed)* | `tests/config/test_model.py` | `test_target_config_rate_limit_defaults_no` (line 163–164), plus others above | `config-suite` |
| **config-model** | REMOVED — TargetConfig copy_base field | *(no scenario — field removed)* | `tests/config/test_model.py` | `test_target_config_copy_base_default_false`, `test_target_config_copy_base_explicit_true` (line 377–386) | `config-suite` |
| **config-model** | MODIFIED — TargetConfig verify field (default "metadata", no mode dependency) | Default verification is metadata | `tests/config/test_model.py` | MODIFY: `test_target_config_verify_default` — ensure default is "metadata" | `config-suite` |
| **config-model** | MODIFIED — TargetConfig verify field | Explicit full / hash verification | `tests/config/test_model.py` | KEEP: existing verify tests, verify no mode-dependent default | `config-suite` |
| **config-model** | MODIFIED — TargetConfig verify field | Invalid verify value raises ConfigError | `tests/config/test_model.py` | KEEP: existing invalid verify test | `config-suite` |
| **config-parsing** | MODIFIED — Removed fields trigger deprecation warnings | `incremental_mode`, `rate_limit`, `copy_base` in TOML log WARNING and are ignored | `tests/config/test_facade.py` | NEW: `test_removed_fields_trigger_deprecation_warnings` | `config-suite` |
| **config-parsing** | MODIFIED — ConfigFacade parses fault-tolerance fields | Global/target safety fields parsed | `tests/config/test_facade.py` | KEEP: existing safety field tests | `config-suite` |
| **config-parsing** | MODIFIED — ConfigFacade updates example config | Example config parseable | — | — (docs-only requirement, no test changes) | — |
| **backup-verification** | REMOVED — Metadata verification after transfer (file-copy path) | *(no scenario — path removed)* | `tests/modules/backup/test_verification.py` | All 18 tests (entire file) | `backup-modules` |
| **backup-verification** | REMOVED — Full verification via `qemu-img compare` (file-copy path) | *(no scenario — path removed)* | `tests/integration/test_verification.py` | All 5 tests (entire file) | `integration-suite` |
| **backup-verification** | REMOVED — Hash verification tier `verify="hash"` via SHA-256 | *(no scenario — helper removed)* | `tests/modules/backup/test_verification.py` | `test_hash_verification_match_passes`, `test_hash_verification_mismatch_fails`, `test_hash_verification_skipped_when_no_expected_hash`, `test_file_sha256_computes_hash` (within deleted file) | `backup-modules` |
| **backup-verification** | REMOVED — verify_backup live-source lock conflict | *(no scenario)* | `tests/modules/backup/test_verification.py` | `test_full_verification_live_source_logs_warning`, `test_full_verification_live_source_lock_conflict`, `test_full_verify_live_source_lock_error_message` | `backup-modules` |
| **backup-verification** | MODIFIED — TargetConfig verify field | Default verification is metadata | `tests/modules/backup/test_bitmap.py` | KEEP: existing verify tests unchanged | `backup-modules` |
| **backup-hash-verification** | REMOVED — verify_backup supports `verify="hash"` mode | *(no scenario — helper removed)* | `tests/modules/backup/test_verification.py` | *(same file as above — deleted)* | `backup-modules` |
| **backup-hash-verification** | REMOVED — verify_backup import in test_utils | *(no scenario)* | `tests/utils/test_verification.py` | `test_verify_full_backup_imported_from_utils` (MODIFY — drop `verify_backup` from imports) | `backup-modules` |
| **backup-hash-verification** | REMOVED — verify_backup usage in retry integration | *(no scenario)* | `tests/integration/test_retry_integration.py` | Lines 124–162 (MODIFY — replace verify_backup with verify_bitmap_incremental) | `integration-suite` |
| **periodic-full-backup** | REMOVED — FileCopyBackupProvider creates full backups via `qemu-img convert` | *(no scenario — path removed)* | `tests/integration/test_nbd_full_backup.py` | All 7 tests using `FileCopyBackupProvider` (MODIFY — rewrite onto `BitmapBackupProvider`) | `integration-suite` |
| **periodic-full-backup** | REMOVED — Incremental backups rebase to the FULL anchor | *(no scenario — rebase removed)* | `tests/modules/backup/test_copy.py` | *(subset of file deletion)* | `backup-modules` |
| **periodic-full-backup** | MODIFIED — Core triggers full backup before incremental transfer | First backup to target creates FULL (via NBD) | `tests/core/test_pipeline.py` | `test_full_creation_works_for_file_copy_and_bitmap` (MODIFY — bitmap-only, rename) | `core-suite` |
| **periodic-full-backup** | MODIFIED — Core triggers full backup before incremental transfer | New weekly period triggers FULL | `tests/core/test_full_anchor.py` | KEEP: existing bucket-driven FULL tests | `core-suite` |
| **periodic-full-backup** | MODIFIED — Core triggers full backup before incremental transfer | FULL creation works for backup targets (via BitmapBackupProvider) | `tests/core/test_pipeline.py` | MODIFY: existing FULL creation tests — ensure they use BitmapBackupProvider | `core-suite` |
| **periodic-full-backup** | MODIFIED — Core triggers full backup before incremental transfer | Dry-run logs FULL-would-be-created | `tests/core/test_pipeline.py` | KEEP: existing dry-run FULL tests | `core-suite` |
| **backup-provider** | REMOVED — Transfer missing snapshots to backup target (rsync + rebase + copy_base) | *(no scenario — mechanism removed)* | `tests/modules/backup/test_copy.py` | *(subset of file deletion)* | `backup-modules` |
| **backup-provider** | REMOVED — Rebase error handling in FileCopyBackupProvider | *(no scenario — mechanism removed)* | `tests/modules/backup/test_copy.py` | *(subset of file deletion)* | `backup-modules` |
| **backup-provider** | REMOVED — FileCopyBackupProvider.create_full_backup standalone qcow2 | *(no scenario — mechanism removed)* | `tests/modules/backup/test_copy.py` | *(subset of file deletion)* | `backup-modules` |
| **backup-provider** | REMOVED — Snapshot file existence guard before rsync | *(no scenario)* | `tests/modules/backup/test_copy.py` | *(subset of file deletion)* | `backup-modules` |
| **backup-provider** | REMOVED — Compression for rsync incremental transfers | *(no scenario)* | `tests/modules/backup/test_copy.py` + `tests/integration/test_zstd_backup.py` | *(subset of file deletion)* + `test_rsync_zstd_transfer` | `backup-modules` + `integration-suite` |
| **backup-provider** | REMOVED — FileCopyBackupProvider rsync failure logging | *(no scenario)* | `tests/modules/backup/test_copy.py` | *(subset of file deletion)* | `backup-modules` |
| **backup-provider** | REMOVED — FileCopyBackupProvider verify_backup failure logging | *(no scenario)* | `tests/modules/backup/test_copy.py` | *(subset of file deletion)* | `backup-modules` |
| **backup-provider** | REMOVED — Libvirt version check in BitmapBackupProvider (WARNING+fallback) | *(no scenario)* | `tests/factory/test_default.py` | *(same tests as module-factory REMOVED)* | `factory-interfaces-mocks` |
| **backup-provider** | REMOVED — Factory selects BitmapBackupProvider for bitmap mode | *(no scenario — single provider now)* | `tests/factory/test_default.py` | `test_factory_selects_file_copy_provider_for_default_mode`, `test_factory_selects_bitmap_provider_for_bitmap_mode` | `factory-interfaces-mocks` |
| **backup-provider** | MODIFIED — Backup verification step (via verify_bitmap_incremental) | Metadata verification passes | `tests/modules/backup/test_bitmap.py` | KEEP: existing verify_bitmap_incremental tests | `backup-modules` |
| **backup-provider** | MODIFIED — Backup verification step | Verification failure produces error | `tests/modules/backup/test_bitmap.py` | KEEP: existing verify_bitmap_incremental error tests | `backup-modules` |
| **backup-provider** | MODIFIED — transfer_missing SHALL NOT create FULL backups | transfer_missing never creates a FULL | `tests/modules/backup/test_bitmap.py` | KEEP: existing transfer_missing tests, verify no FULL creation | `backup-modules` |
| **backup-provider** | MODIFIED — Immediate deletion of failed backup files after verification failure | Failed backup file deleted immediately | `tests/modules/backup/test_bitmap.py` | KEEP: existing failed-file-deletion tests | `backup-modules` |
| **backup-provider** | MODIFIED — Stall detection for data transfer commands (rsync removed) | NBD convert uses stall detection; stall timeout disabled falls back | `tests/modules/backup/test_bitmap.py` | KEEP: existing stall-detection tests | `backup-modules` |
| **backup-provider** | MODIFIED — Backup providers remain retry-unaware | Provider returns error, Core handles retry | `tests/modules/backup/test_bitmap.py` | KEEP: existing retry-unaware tests | `backup-modules` |
| **backup-provider** | MODIFIED — BitmapBackupProvider accepts IStateManager | Constructor accepts IStateManager / works without | `tests/modules/backup/test_bitmap.py` | KEEP: existing state-aware tests | `backup-modules` |
| **backup-provider** | MODIFIED — BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL with zstd/zlib/compress disabled | `tests/modules/backup/test_bitmap.py` | KEEP: existing BitmapBackupProvider FULL tests | `backup-modules` |
| **backup-provider** | MODIFIED — transfer_missing signature (no rate_limit) | *(signature change — contract enforcement)* | `tests/interfaces/test_backup_provider.py` | MODIFY: contract test parametrization + calls (drop rate_limit) | `factory-interfaces-mocks` |
| **backup-provider** | MODIFIED — transfer_missing signature (no rate_limit) | *(mock signature update)* | `tests/mocks/mock_modules.py` | MODIFY: drop `rate_limit` from `transfer_missing` signatures | `factory-interfaces-mocks` |
| **backup-provider** | MODIFIED — transfer_missing signature (no rate_limit) | *(mock factory update)* | `tests/mocks/mock_factory.py` | MODIFY: single-provider return, no incremental_mode branch | `factory-interfaces-mocks` |
| **live-vm-full-backup** | REMOVED — VM running-state detection for FULL backup method selection | *(no scenario — single path now)* | `tests/integration/test_nbd_full_backup.py` | *(same as periodic-full-backup — rewrite)* | `integration-suite` |
| **live-vm-full-backup** | REMOVED — Libvirt version check for NBD FULL path (libvirt < 6.0 fallback) | *(no scenario — hard error now)* | `tests/factory/test_default.py` | *(same as module-factory old-libvirt tests)* | `factory-interfaces-mocks` |
| **live-vm-full-backup** | ADDED — FULL backup requires a running VM | Running VM triggers NBD-based FULL backup | `tests/integration/test_nbd_full_backup.py` | MODIFY: existing running-VM FULL test → rewrite onto BitmapBackupProvider | `integration-suite` |
| **live-vm-full-backup** | ADDED — FULL backup requires a running VM | Stopped VM fails with a BackupResult error | `tests/modules/backup/test_bitmap.py` | NEW: `test_create_full_backup_stopped_vm_returns_error` | `backup-modules` |
| **live-vm-full-backup** | ADDED — FULL backup requires a running VM | Dotted VM name passed untruncated | `tests/integration/test_nbd_full_backup.py` | KEEP: existing dotted-name test → rewrite | `integration-suite` |
| **live-vm-full-backup** | ADDED — FULL backup requires a running VM | Core passes vm_config.name to create_full_backup | `tests/core/test_pipeline.py` | MODIFY: existing FULL creation pipeline test | `core-suite` |
| **live-vm-full-backup** | MODIFIED — NBD full-export helper for FULL backups | NBD full export with zstd/zlib/no compression, stall detection, standalone qcow2, socket cleanup on success/failure | `tests/utils/test_nbd.py` | KEEP: existing nbd_full_export tests (helper survives) | `config-suite` |
| **live-vm-full-backup** | MODIFIED — Atomic FULL file creation via NBD | NBD FULL creates `.tmp` then renames; failure leaves no final file | `tests/modules/backup/test_bitmap.py` | KEEP: existing atomic-create tests | `backup-modules` |
| **pre-flight-cleanup** | MODIFIED — Truncated qcow2 detection on backup targets (rewording only) | Truncated qcow2 on target is deleted; valid qcow2 is kept | `tests/core/test_validation.py` | KEEP: existing cleanup tests (no behavior change) | `core-suite` |
| **stall-detection** | MODIFIED — In-process stall watchdog (rsync removed from commands covered) | Watchdog aborts stalled copy loop; disabled at zero timeout; subprocess transfers unchanged | `tests/integration/test_stall_detection.py`, `tests/integration/test_stall_inprocess.py` | KEEP: all existing stall tests | `integration-suite` |
| **restore-command** | MODIFIED — Restore command copies backup chain (scenario renamed) | Restore a backup chain with FULL anchor; restore nonexistent backup; target directory does not exist | `tests/e2e/test_restore.py`, `tests/cli/test_commands.py` | KEEP: existing restore tests (scenario rename only) | `core-suite` |
| **nbd-bitmap-backup** | MODIFIED — Incremental verification includes backing-file check and dirty-size barrier | Delta proportional/non-proportional passes/fails barrier; wrong backing file fails; Core records incremental→FULL dependency | `tests/modules/backup/test_bitmap_incremental.py`, `tests/utils/test_verification_bitmap.py` | KEEP: all existing bitmap-verification tests | `backup-modules` |
| **nbd-dirty-block-transfer** | MODIFIED — Incremental output is a backing-chained COW delta (rewording) | `qemu-img info` shows backing chain; restore resolves bitmap chains unchanged | `tests/modules/backup/test_bitmap_incremental.py` | KEEP: all existing delta-verification tests | `backup-modules` |
| **parsing-utils** | MODIFIED — Modules use shared parsers (purpose reworded) | ExternalSnapshotProvider uses shared parser | `tests/modules/snapshot/test_external.py` | KEEP: existing shared-parser tests | `backup-modules` |
| *(cross-cutting)* | Core pipeline rsync/rate_limit references | *(no scenario — plumbing removed)* | `tests/core/test_pipeline.py` | MODIFY: any rate_limit references in pipeline tests | `core-suite` |
| *(cross-cutting)* | Core engine rate_limit references | *(no scenario — plumbing removed)* | `tests/core/test_engine.py` | MODIFY: any rate_limit references in engine tests | `core-suite` |
| *(cross-cutting)* | Core bitmap_dependency rate_limit | *(no scenario)* | `tests/core/test_bitmap_dependency.py` | MODIFY: remove `rate_limit="no"` from test calls (lines 37, 194) | `core-suite` |
| *(cross-cutting)* | verify_backup import in integration/retry | *(no scenario — helper removed)* | `tests/integration/test_retry_integration.py` | MODIFY: replace verify_backup usage with verify_bitmap_incremental | `integration-suite` |
| *(cross-cutting)* | FileCopyBackupProvider references in stale-state recovery | *(no scenario — provider removed)* | `tests/integration/test_stale_state_recovery.py` | MODIFY: rewrite `FileCopyBackupProvider` → `BitmapBackupProvider` (2 tests) | `integration-suite` |
| *(cross-cutting)* | incremental_mode="file-copy" in blockcommit/preserve integration | *(no scenario — field removed)* | `tests/integration/test_blockcommit_defer.py`, `tests/integration/test_preserve_all.py` | MODIFY: change `"file-copy"` → `"bitmap"` in target configs | `integration-suite` |
| *(cross-cutting)* | FileCopyBackupProvider import in test_nbd | *(no scenario — provider removed)* | `tests/utils/test_nbd.py` | `test_file_copy_provider_imports_nbd_from_utils` | `config-suite` |
| *(cross-cutting)* | MockShell rsync test references | *(no scenario — rsync removed)* | `tests/mocks/test_mock_shell.py` | MODIFY: remove rsync command references (lines 70, 85, 89) | `factory-interfaces-mocks` |
| *(cross-cutting)* | Shell interface rsync docstring | *(no scenario — rsync removed)* | `tests/interfaces/test_shell.py` | MODIFY: remove rsync from docstring (line 41) | `factory-interfaces-mocks` |
| *(cross-cutting)* | CLI test_commands rsync error string | *(no scenario — rsync removed)* | `tests/cli/test_commands.py` | MODIFY: line 467 `"rsync failed..."` → generic error | `core-suite` |
| *(cross-cutting)* | `make_target` fixture defaults | *(no scenario — fixtures updated)* | `tests/conftest.py` | MODIFY: remove `incremental_mode`/`rate_limit`/`copy_base` from `make_target` signature | `core-suite` |
| *(cross-cutting)* | `make_global_config` fixture | *(no scenario)* | `tests/conftest.py` | MODIFY: remove `rate_limit` from `make_global_config` signature | `core-suite` |
| *(cross-cutting)* | TOML fixtures with removed fields | *(no scenario — fixtures updated)* | `tests/fixtures/configs/safety_fields.toml`, `full_backup.toml`, `bucket_driven.toml`, `verify_full_both.toml`, `verify_mode_defaults.toml` | MODIFY: remove `rate_limit`/`incremental_mode`/`copy_base` fields; change `"file-copy"` → `"bitmap"` | `config-suite` |

## Delegation Groups

### Group 1: `backup-modules`

**Scope:** `tests/modules/backup/*`, `tests/utils/test_verification.py`, `tests/utils/test_verification_bitmap.py`, `tests/utils/test_hash.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_copy.py` (5783 lines, 80 tests) | 80 tests | DELETE (entire file) |
| `tests/modules/backup/test_verification.py` (542 lines, 18 tests) | 18 tests | DELETE (entire file — all test `verify_backup`) |
| `tests/modules/backup/test_bitmap.py` (2395 lines) | ~3 tests | MODIFY — remove `rate_limit` param from `test_bitmap_backup_ignores_rate_limit` (delete test or repurpose), remove `incremental_mode` from fixture calls; NEW: `test_create_full_backup_stopped_vm_returns_error` |
| `tests/modules/backup/test_bitmap_incremental.py` (1527 lines) | ~1 test | MODIFY — adjust `incremental_mode="bitmap"` references (keep, but verify they still compile after config changes) |
| `tests/modules/backup/test_full_verification.py` (281 lines) | 0 | KEEP — no changes (verify_full_backup survives) |
| `tests/utils/test_verification.py` (2 lines) | 1 test | MODIFY — remove `verify_backup` from imports, keep `verify_full_backup` only |
| `tests/utils/test_verification_bitmap.py` (21 tests) | 0 | KEEP — protective skeleton (verify_bitmap_incremental) |
| `tests/utils/test_hash.py` | 0 | KEEP — file_sha256 survives independently |

### Group 2: `core-suite`

**Scope:** `tests/core/*`, `tests/conftest.py`, `tests/cli/test_commands.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/conftest.py` (318 lines) | ~5 fixtures | MODIFY — remove `shell.expect("which rsync")` (lines 89–97); remove `incremental_mode`, `rate_limit`, `copy_base` from `_make` fixture (lines 192–206); remove `rate_limit` from `make_global_config` fixture (lines 229, 257) |
| `tests/core/test_validation.py` (1000 lines) | 3 tests DELETE + 1 NEW | DELETE: `test_rsync_available_validation_passes`, `test_rsync_unavailable_pipeline_aborts`, `test_rsync_check_always_runs_regardless_of_rate_limit`; NEW: `test_dry_run_downgrades_libnbd_missing_to_warning` |
| `tests/core/test_pipeline.py` (4136 lines) | 1 test | MODIFY: `test_full_creation_works_for_file_copy_and_bitmap` → rename to `test_full_creation_works_for_bitmap`, remove file-copy half, ensure bitmap-only path |
| `tests/core/test_bitmap_dependency.py` (410 lines) | 1 test DELETE + 2 MODIFY | DELETE: `test_validate_environment_file_copy_skips_libnbd_check`; MODIFY: remove `rate_limit="no"` from `make_target` calls (lines 37, 194) |
| `tests/core/test_engine.py` (1706 lines) | 0 | KEEP — protective skeleton (verify no rate_limit references after sweep) |
| `tests/core/test_full_verification_pipeline.py` (1549 lines) | 0 | KEEP — protective skeleton |
| `tests/core/test_full_anchor.py` | 0 | KEEP — protective skeleton |
| `tests/core/test_deferred.py` | 0 | KEEP — protective skeleton |
| `tests/core/test_state_check.py` | 0 | KEEP — protective skeleton |
| `tests/core/test_preserve.py` | 0 | KEEP — protective skeleton |
| `tests/core/test_list_commands.py` | 0 | KEEP — protective skeleton |
| `tests/core/test_schedule_summary.py` | 0 | KEEP — protective skeleton |
| `tests/core/test_fork.py` | 0 | KEEP — protective skeleton |
| `tests/core/test_lifecycle_fork.py` | 0 | KEEP — protective skeleton |
| `tests/cli/test_commands.py` | 1 line | MODIFY — line 467: change `"rsync failed..."` to generic backup error string |

### Group 3: `config-suite`

**Scope:** `tests/config/*`, `tests/fixtures/configs/*`, `tests/utils/test_parsing.py`, `tests/utils/test_nbd.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` (803 lines) | ~10 tests | DELETE: `test_global_config_rate_limit_defaults_no`, `test_global_config_rate_limit_frozen`, `test_global_config_has_rate_limit_field`, `test_make_global_config_accepts_rate_limit_kwarg`, `test_target_config_rate_limit_defaults_no`, `test_target_config_rate_limit_frozen`, `test_target_config_has_rate_limit_field`, `test_make_target_accepts_rate_limit_kwarg`, `test_target_config_default_incremental_mode_is_bitmap`, `test_target_config_explicit_incremental_mode_file_copy`, `test_target_config_explicit_incremental_mode_bitmap`, `test_target_config_copy_base_default_false`, `test_target_config_copy_base_explicit_true`; MODIFY: adjust any test that references removed fields in assertions |
| `tests/config/test_facade.py` (1029 lines) | ~8 tests DELETE + 1 NEW | DELETE: `test_global_rate_limit_parsed`, `test_invalid_rate_limit_raises_config_error`, `test_target_overrides_global_rate_limit`, `test_target_inherits_global_rate_limit`, `test_facade_parses_target_copy_base`; MODIFY: remove `copy_base`/`incremental_mode`/`rate_limit` assertions from remaining tests; NEW: `test_removed_fields_trigger_deprecation_warnings` |
| `tests/config/test_resolver.py` (138 lines) | 1 test | MODIFY: remove `incremental_mode` resolution tests (lines 89–137 — `file-copy`/`bitmap` mode switching); keep pure inheritance tests unrelated to backup mode |
| `tests/config/test_parser.py` (164 lines) | 1 test | MODIFY: `test_parse_target_compress_and_copy_base` → remove `copy_base` assertions; keep `compress` assertions |
| `tests/config/test_fixtures.py` (446 lines) | 1 test DELETE + ~5 MODIFY | DELETE: `test_mock_shell_knows_rsync`; MODIFY: remove `copy_base` assertions from `test_make_target_defaults_compress_true_copy_base_false`, `test_make_target_compress_false_copy_base_true`, `test_bucket_driven_parses`, `test_full_backup_toml_parses_compress_and_copy_base`, `test_deprecated_fields_toml_does_not_affect_compress_copy_base` |
| `tests/fixtures/configs/rate_limit_global.toml` | — | DELETE |
| `tests/fixtures/configs/rate_limit_target_override.toml` | — | DELETE |
| `tests/fixtures/configs/rate_limit_invalid.toml` | — | DELETE |
| `tests/fixtures/configs/safety_fields.toml` | — | MODIFY — remove `rate_limit`, `incremental_mode`, `copy_base` lines |
| `tests/fixtures/configs/full_backup.toml` | — | MODIFY — remove `incremental_mode`, `copy_base` lines |
| `tests/fixtures/configs/bucket_driven.toml` | — | MODIFY — remove `copy_base` lines |
| `tests/fixtures/configs/verify_full_both.toml` | — | MODIFY — change `incremental_mode = "file-copy"` → `"bitmap"` |
| `tests/fixtures/configs/verify_mode_defaults.toml` | — | MODIFY — change `incremental_mode = "file-copy"` → `"bitmap"` on file-copy targets; keep bitmap target |
| `tests/fixtures/configs/deprecated_fields.toml` | — | MODIFY — add `incremental_mode = "file-copy"`, `rate_limit = "100M"`, `copy_base = true` to exercise deprecation WARNING path |
| `tests/utils/test_parsing.py` (245 lines) | ~13 tests | DELETE: `test_parse_rate_limit_*` (10 tests), `test_rate_limit_to_kib_*` (3 tests); KEEP: non-rate-limit parsing tests |
| `tests/utils/test_nbd.py` (439 lines) | 1 test | DELETE: `test_file_copy_provider_imports_nbd_from_utils`; KEEP: all `nbd_full_export` tests |

### Group 4: `factory-interfaces-mocks`

**Scope:** `tests/factory/*`, `tests/interfaces/*`, `tests/mocks/*`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/factory/test_default.py` (612 lines) | ~8 test changes | DELETE: `test_factory_selects_file_copy_provider_for_default_mode`, `test_factory_non_bitmap_mode_no_version_check`, `test_factory_bitmap_mode_old_libvirt_falls_back`, `test_factory_bitmap_fallback_logs_warning`, `test_factory_libvirt_7_1_falls_back`, `test_factory_bitmap_mode_without_libnbd_with_old_libvirt_returns_fallback`; MODIFY: `test_factory_selects_bitmap_provider_for_bitmap_mode` → rename `test_factory_always_returns_bitmap_backup_provider`, remove mode-select logic; MODIFY: `test_factory_bitmap_mode_new_libvirt_returns_bitmap` → simplify; MODIFY: `test_factory_passes_state_to_bitmap_provider` → remove mode-select distinction; MODIFY: `test_factory_bitmap_mode_without_libnbd_raises_actionable_error` → remove old-libvirt gate; MODIFY: `test_factory_libvirt_7_2_returns_bitmap` → simplify; NEW: `test_factory_old_libvirt_raises_runtime_error` |
| `tests/interfaces/test_backup_provider.py` (311 lines) | ~10 contract test changes | MODIFY: remove `FileCopyBackupProvider` from all parametrizations (lines 60, 66, 94, 99, 114, 119, 191, 196, 250, 255); DELETE: `test_file_copy_backup_provider_is_ibackup_provider`, `test_file_copy_backup_provider_no_core_inheritance`, `test_file_copy_backup_provider_requires_shell`; MODIFY: drop `rate_limit` from all `transfer_missing` contract calls |
| `tests/interfaces/test_nbd.py` | 0 | KEEP — protective skeleton |
| `tests/interfaces/test_bucket_full_strategy.py` | 0 | KEEP — protective skeleton |
| `tests/mocks/mock_factory.py` (62 lines) | 1 mock | MODIFY: remove `self._backup_provider` (MockBackupProvider for file-copy); `create_backup_provider()` always returns `self._bitmap_backup_provider` (no incremental_mode branch) |
| `tests/mocks/mock_modules.py` (263 lines) | 2 signatures | MODIFY: drop `rate_limit: str = "no"` from `MockBackupProvider.transfer_missing()` and `MockBitmapBackupProvider.transfer_missing()` signatures |
| `tests/mocks/test_mock_factory.py` | ~2 tests | MODIFY: adjust tests for single-provider behavior (no mode-select) |
| `tests/mocks/test_mock_shell.py` | 1 test | MODIFY: remove rsync command references (lines 70, 85, 89) |
| `tests/mocks/test_mock_config.py` | 0 | KEEP — no changes |
| `tests/mocks/test_mock_state.py` | 0 | KEEP — no changes |
| `tests/interfaces/test_shell.py` | 1 line | MODIFY: remove rsync from docstring (line 41) |

### Group 5: `integration-suite`

**Scope:** `tests/integration/*`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_zstd_backup.py` (428 lines) | 1 test DELETE | DELETE: `test_rsync_zstd_transfer` + `_get_rsync_version` helper; KEEP: 3 remaining zstd compression tests |
| `tests/integration/test_env_validation.py` (171 lines) | 1 test DELETE + 1 NEW | DELETE: `test_no_bitmap_targets_skips_libnbd_check`; MODIFY: `test_validate_environment_with_bitmap_target_passes` → rename, remove file-copy context; NEW: `test_libnbd_missing_hard_failure` |
| `tests/integration/test_nbd_full_backup.py` (685 lines) | 7 tests | MODIFY: rewrite all 7 tests — replace `FileCopyBackupProvider` with `BitmapBackupProvider`; adjust assertions to bitmap behavior |
| `tests/integration/test_verification.py` | 5 tests | DELETE (entire file — all tests use `verify_backup`) |
| `tests/integration/test_stale_state_recovery.py` (201 lines) | 2 tests | MODIFY: rewrite `test_stale_state_snapshot_removed_when_file_missing` + `test_stale_state_crash_recovery_simulated` — replace `FileCopyBackupProvider` with `BitmapBackupProvider` |
| `tests/integration/test_retry_integration.py` | ~1 test | MODIFY: replace `verify_backup` calls (lines 124–162) with `verify_bitmap_incremental` or `verify_full_backup` equivalent |
| `tests/integration/test_blockcommit_defer.py` | 1 line | MODIFY: change `incremental_mode="file-copy"` (line 59) → `"bitmap"` or remove |
| `tests/integration/test_preserve_all.py` | 1 line | MODIFY: change `incremental_mode="file-copy"` (line 104) → `"bitmap"` or remove |
| `tests/integration/test_config_integration.py` | 0 | KEEP — already uses `"bitmap"` mode |
| `tests/integration/test_bitmap_atomic.py` | 0 | KEEP — protective skeleton |
| `tests/integration/test_bitmap_dirty_transfer.py` | 0 | KEEP — protective skeleton |
| `tests/integration/test_bitmap_integration.py` | 0 | KEEP — protective skeleton |
| `tests/integration/test_stall_detection.py` | 0 | KEEP — protective skeleton |
| `tests/integration/test_stall_inprocess.py` | 0 | KEEP — protective skeleton |
| `tests/integration/test_verification_bitmap.py` | 0 | KEEP — protective skeleton |
| `tests/integration/conftest.py` | 0 | KEEP — no changes |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/modules/backup/test_copy.py` | DELETE entire file (5783 lines, 80 tests) | `FileCopyBackupProvider` deleted (design D1); all rsync/copy tests invalid |
| `tests/modules/backup/test_verification.py` | DELETE entire file (542 lines, 18 tests) | `verify_backup()` deleted (design D6); all tests exercise the removed helper |
| `tests/modules/backup/test_bitmap.py` | DELETE `test_bitmap_backup_ignores_rate_limit` (line 521); remove `rate_limit`/`incremental_mode` from 2 calls (lines 1398, 1485) | `rate_limit` parameter removed from `transfer_missing` signature (design D4); `incremental_mode` removed from `TargetConfig` |
| `tests/modules/backup/test_bitmap.py` | NEW: `test_create_full_backup_stopped_vm_returns_error` | `live-vm-full-backup` ADDED: stopped-VM FULL returns `BackupResult(success=False)` with no direct-convert fallback |
| `tests/modules/backup/test_bitmap_incremental.py` | Remove `incremental_mode="bitmap"` from call (line 1370) | `incremental_mode` removed from `TargetConfig` |
| `tests/utils/test_verification.py` | Remove `verify_backup` from import line; keep `verify_full_backup` | `verify_backup()` deleted (design D6); `verify_full_backup()` survives |
| `tests/conftest.py` | Remove `shell.expect("which rsync")` (lines 89–97) | rsync env check deleted (design D5); mock_shell no longer needs rsync expectation |
| `tests/conftest.py` | Remove `incremental_mode`, `rate_limit`, `copy_base` params from `_make` (lines 192–206) and `make_global_config` (line 229, 257) | Fields removed from `TargetConfig`/`GlobalConfig` (config-model REMOVED) |
| `tests/core/test_validation.py` | DELETE: `test_rsync_available_validation_passes` (line 147), `test_rsync_unavailable_pipeline_aborts` (line 181), `test_rsync_check_always_runs_regardless_of_rate_limit` (line 215) | rsync env check removed unconditionally (design D5) |
| `tests/core/test_validation.py` | NEW: `test_dry_run_downgrades_libnbd_missing_to_warning` | `env-validation` MODIFIED: libnbd check unconditional; dry-run downgrades hard failure to WARNING |
| `tests/core/test_pipeline.py` | MODIFY: `test_full_creation_works_for_file_copy_and_bitmap` (line 2800) → rename, remove file-copy half, test bitmap-only | `periodic-full-backup` MODIFIED: single provider creates FULLs via NBD |
| `tests/core/test_bitmap_dependency.py` | DELETE: `test_validate_environment_file_copy_skips_libnbd_check` (line 378) | No file-copy mode exists to skip the check (design D5) |
| `tests/core/test_bitmap_dependency.py` | Remove `rate_limit="no"` from `make_target` (line 37) and `transfer_missing` call (line 194) | `rate_limit` removed from config and interface (design D4) |
| `tests/cli/test_commands.py` | Change line 467: `"rsync failed: permission denied"` → generic error string | rsync-specific test fixture data; generic backup error |
| `tests/config/test_model.py` | DELETE: `test_global_config_rate_limit_*` (3 tests), `test_target_config_rate_limit_*` (3 tests), `test_global_config_has_rate_limit_field`, `test_target_config_has_rate_limit_field`, `test_make_global_config_accepts_rate_limit_kwarg`, `test_make_target_accepts_rate_limit_kwarg` | `rate_limit` field removed from `GlobalConfig` and `TargetConfig` |
| `tests/config/test_model.py` | DELETE: `test_target_config_default_incremental_mode_is_bitmap`, `test_target_config_explicit_incremental_mode_file_copy`, `test_target_config_explicit_incremental_mode_bitmap` | `incremental_mode` field removed from `TargetConfig` |
| `tests/config/test_model.py` | DELETE: `test_target_config_copy_base_default_false`, `test_target_config_copy_base_explicit_true` | `copy_base` field removed from `TargetConfig` |
| `tests/config/test_facade.py` | DELETE: `test_global_rate_limit_parsed`, `test_invalid_rate_limit_raises_config_error`, `test_target_overrides_global_rate_limit`, `test_target_inherits_global_rate_limit`, `test_facade_parses_target_copy_base` | Corresponding fields and parsing removed |
| `tests/config/test_facade.py` | Remove `copy_base`/`incremental_mode`/`rate_limit` assertions from remaining parse/inheritance tests | Fields no longer produced by `ConfigFacade` |
| `tests/config/test_facade.py` | NEW: `test_removed_fields_trigger_deprecation_warnings` | `config-parsing` MODIFIED: `incremental_mode`, `rate_limit`, `copy_base` produce WARNING + ignore (design D3) |
| `tests/config/test_resolver.py` | Remove `incremental_mode` resolution tests (lines 89–137 — `file-copy`/`bitmap` mode switching) | No mode to resolve |
| `tests/config/test_parser.py` | Remove `copy_base` assertions from `test_parse_target_compress_and_copy_base` | `copy_base` parsing removed |
| `tests/config/test_fixtures.py` | DELETE: `test_mock_shell_knows_rsync` | rsync check removed from mock_shell |
| `tests/config/test_fixtures.py` | Remove `copy_base` assertions from `test_make_target_defaults_compress_true_copy_base_false`, `test_make_target_compress_false_copy_base_true`, `test_bucket_driven_parses`, `test_full_backup_toml_parses_compress_and_copy_base`, `test_deprecated_fields_toml_does_not_affect_compress_copy_base`; rename test functions that include `copy_base` in name | `copy_base` field removed |
| `tests/fixtures/configs/rate_limit_global.toml` | DELETE | Fixtures for removed field |
| `tests/fixtures/configs/rate_limit_target_override.toml` | DELETE | Fixtures for removed field |
| `tests/fixtures/configs/rate_limit_invalid.toml` | DELETE | Fixtures for removed field |
| `tests/fixtures/configs/safety_fields.toml` | Remove `rate_limit`, `incremental_mode`, `copy_base` lines | Fields removed from config surface |
| `tests/fixtures/configs/full_backup.toml` | Remove `incremental_mode`, `copy_base` lines | Fields removed |
| `tests/fixtures/configs/bucket_driven.toml` | Remove `copy_base` lines | Field removed |
| `tests/fixtures/configs/verify_full_both.toml` | Change `incremental_mode = "file-copy"` → `"bitmap"` | No file-copy mode |
| `tests/fixtures/configs/verify_mode_defaults.toml` | Change `incremental_mode = "file-copy"` → `"bitmap"` on 2 targets | No file-copy mode |
| `tests/fixtures/configs/deprecated_fields.toml` | Add `incremental_mode = "file-copy"`, `rate_limit = "100M"`, `copy_base = true` for deprecation WARNING exercise | `config-parsing` MODIFIED scenario: removed fields trigger deprecation warnings (design D3) |
| `tests/utils/test_parsing.py` | DELETE: `test_parse_rate_limit_*` (10 tests), `test_rate_limit_to_kib_*` (3 tests) | `parse_rate_limit()` and `rate_limit_to_kib()` deleted |
| `tests/utils/test_nbd.py` | DELETE: `test_file_copy_provider_imports_nbd_from_utils` | `FileCopyBackupProvider` deleted; import-check test moot |
| `tests/factory/test_default.py` | DELETE: `test_factory_selects_file_copy_provider_for_default_mode`, `test_factory_non_bitmap_mode_no_version_check`, `test_factory_bitmap_mode_old_libvirt_falls_back`, `test_factory_bitmap_fallback_logs_warning`, `test_factory_libvirt_7_1_falls_back`, `test_factory_bitmap_mode_without_libnbd_with_old_libvirt_returns_fallback` | FileCopy fallback branches deleted (design D2); libvirt < 7.2 is now hard error |
| `tests/factory/test_default.py` | MODIFY: `test_factory_selects_bitmap_provider_for_bitmap_mode` → `test_factory_always_returns_bitmap_backup_provider` (remove mode check) | Single provider, no `incremental_mode` field |
| `tests/factory/test_default.py` | NEW: `test_factory_old_libvirt_raises_runtime_error` | `module-factory` ADDED: libvirt < 7.2 → `RuntimeError` (design D2) |
| `tests/factory/test_default.py` | MODIFY: `test_factory_bitmap_mode_without_libnbd_raises_actionable_error` — remove old-libvirt gate, always test the non-fallback path | `module-factory` ADDED: missing libnbd → `RuntimeError` always |
| `tests/interfaces/test_backup_provider.py` | DELETE: `test_file_copy_backup_provider_is_ibackup_provider`, `test_file_copy_backup_provider_no_core_inheritance`, `test_file_copy_backup_provider_requires_shell` | `FileCopyBackupProvider` deleted (design D1) |
| `tests/interfaces/test_backup_provider.py` | Remove `FileCopyBackupProvider` from all `@pytest.mark.parametrize` (lines 60, 66, 94, 99, 114, 119, 191, 196, 250, 255); change `ids` to drop `"file_copy"` | No `FileCopyBackupProvider` to parametrize over |
| `tests/interfaces/test_backup_provider.py` | Drop `rate_limit` parameter from all `transfer_missing` contract calls | `rate_limit` removed from `IBackupProvider.transfer_missing` signature (design D4) |
| `tests/mocks/mock_factory.py` | Remove `self._backup_provider` (MockBackupProvider); `create_backup_provider()` always returns `self._bitmap_backup_provider` | Single provider; no `incremental_mode` branch |
| `tests/mocks/mock_modules.py` | Drop `rate_limit: str = "no"` from `MockBackupProvider.transfer_missing()` and `MockBitmapBackupProvider.transfer_missing()` | Interface signature change (design D4) |
| `tests/mocks/test_mock_factory.py` | Adjust tests for single-provider behavior | Factory no longer mode-switches |
| `tests/mocks/test_mock_shell.py` | Remove rsync command references (lines 70, 85, 89) | `rsync` no longer required or mocked |
| `tests/interfaces/test_shell.py` | Remove `rsync` from docstring (line 41) | `rsync` no longer referenced in shell abstraction |
| `tests/integration/test_zstd_backup.py` | DELETE: `test_rsync_zstd_transfer` + `_get_rsync_version` helper | rsync transfer mechanism removed |
| `tests/integration/test_env_validation.py` | DELETE: `test_no_bitmap_targets_skips_libnbd_check` | No file-copy targets exist to skip the check |
| `tests/integration/test_env_validation.py` | MODIFY: `test_validate_environment_with_bitmap_target_passes` → rename, remove file-copy context from docstring/comments | Libnbd check is now unconditional |
| `tests/integration/test_env_validation.py` | NEW: `test_libnbd_missing_hard_failure` | `env-validation` MODIFIED: unconditional libnbd check means missing libnbd always fails |
| `tests/integration/test_nbd_full_backup.py` | Rewrite all 7 tests: replace `from qsnap.modules.backup.file_copy import FileCopyBackupProvider` → `from qsnap.modules.backup.bitmap import BitmapBackupProvider`; replace constructor calls accordingly | `FileCopyBackupProvider` deleted; NBD FULL tests must use the surviving provider (design D1) |
| `tests/integration/test_verification.py` | DELETE entire file (5 tests) | All tests exercise `verify_backup()`, which is deleted (design D6) |
| `tests/integration/test_stale_state_recovery.py` | Rewrite 2 tests: replace `FileCopyBackupProvider` → `BitmapBackupProvider` | `FileCopyBackupProvider` deleted; stale-state recovery must use surviving provider |
| `tests/integration/test_retry_integration.py` | Replace `verify_backup()` calls (lines 124–162) with `verify_bitmap_incremental()`-based approach for hash mismatch simulation | `verify_backup()` deleted (design D6); retry classification must use surviving verification helpers |
| `tests/integration/test_blockcommit_defer.py` | Change `incremental_mode="file-copy"` (line 59) → `incremental_mode="bitmap"` | Field removed; keep integration test meaningful with single mode |
| `tests/integration/test_preserve_all.py` | Change `incremental_mode="file-copy"` (line 104) → `incremental_mode="bitmap"` | Field removed; keep integration test meaningful with single mode |

## Risks & Edge Cases

### R1 — Stray callers of deleted helpers

**Risk:** `verify_backup()`, `parse_rate_limit()`, and `rate_limit_to_kib()` may have importers or callers outside the specifically identified test files.

**Mitigation (rg-verification step):** Before deleting, run in `qsnap/` and `tests/`:
- `rg "verify_backup\|from.*verification import.*verify_backup"` → confirm all hits are in files already identified for deletion/modification
- `rg "parse_rate_limit\|rate_limit_to_kib"` → confirm only hits are in `qsnap/utils/parsing.py` and `tests/utils/test_parsing.py`
- `rg "from qsnap.modules.backup.file_copy import\|FileCopyBackupProvider"` → confirm all hits in tests are in files marked DELETE/MODIFY

**Test coverage:** No new test needed — `ruff` + `pyright --strict` act as the compile-time net.

### R2 — Offline-VM FULL backup failure path

**Risk:** After removal, `BitmapBackupProvider.create_full_backup()` is the sole FULL path. Stopped VMs cause `virsh backup-begin` to fail, and no direct-convert fallback exists (accepted BREAKING).

**Test coverage:**
- NEW test in `tests/modules/backup/test_bitmap.py`: `test_create_full_backup_stopped_vm_returns_error` — mock `nbd_full_export` to raise the virsh "domain not running" error, verify `BackupResult(success=False, error=...)` is returned, and verify no `qemu-img convert` fallback is attempted.
- Existing NBD FULL tests in `tests/integration/test_nbd_full_backup.py` (rewritten onto `BitmapBackupProvider`) verify the happy path with a running VM.

### R3 — Deprecation WARNINGs do not break config loading

**Risk:** User TOMLs containing `incremental_mode`, `rate_limit`, or `copy_base` must still load and produce valid config dataclasses (design D3). A WARNING is logged; the fields are ignored.

**Test coverage:**
- NEW test in `tests/config/test_facade.py`: `test_removed_fields_trigger_deprecation_warnings` — parse a TOML with all three removed fields, assert WARNING logs appear (via `caplog`), assert the resulting `TargetConfig`/`GlobalConfig` have valid defaults for surviving fields, and assert no `ConfigError` is raised.
- MODIFY `tests/fixtures/configs/deprecated_fields.toml` to include `incremental_mode = "file-copy"`, `rate_limit = "100M"`, `copy_base = true` for the deprecation exercise.

### R4 — Factory hard errors on insufficient platform

**Risk:** `DefaultFactory.create_backup_provider()` must raise `RuntimeError` for libvirt < 7.2 and missing `python3-libnbd`, with actionable messages. No fallback provider exists (design D2).

**Test coverage:**
- NEW: `test_factory_old_libvirt_raises_runtime_error` (mock `is_libvirt_new_enough` → `False`, assert `RuntimeError` with "libvirt" and "7.2" in message, assert no provider returned).
- MODIFY: `test_factory_bitmap_mode_without_libnbd_raises_actionable_error` — remove old-libvirt guard, mock `is_libnbd_available` → `False`, assert `RuntimeError` naming `python3-libnbd`.

### R5 — Protective-skeleton behaviors must stay green

**Risk:** ~85 test deletions may incidentally break behaviors that survive. The following behaviors are explicitly preserved and must have green tests after the change:

| Behavior | Verified By |
|---|---|
| Bitmap checkpoint atomicity (checkpoint created atomically with `backup-begin`) | `tests/modules/backup/test_bitmap.py`, `tests/integration/test_bitmap_atomic.py` |
| Stall error string parity (`"Stall detected: no progress for {N}s"`) | `tests/integration/test_stall_inprocess.py`, `tests/modules/backup/test_bitmap.py` |
| `.tmp` → final atomic rename for FULL backups | `tests/modules/backup/test_bitmap.py`, `tests/modules/backup/test_bitmap_incremental.py` |
| M1/M2/M3 FULL verification tiers | `tests/modules/backup/test_full_verification.py`, `tests/core/test_full_verification_pipeline.py` |
| `verify_bitmap_incremental()` barrier + chain checks | `tests/utils/test_verification_bitmap.py` (21 tests) |
| `nbd_full_export()` helper (survives, used by BitmapBackupProvider and Core restore) | `tests/utils/test_nbd.py` (all nbd_full_export tests KEEP) |
| Restore of historical file-copy chains (design D9) | `tests/e2e/test_restore.py`, `tests/cli/test_commands.py` (restore scenarios KEEP) |
| Retention cascade-deletion and `check` | `tests/modules/retention/test_time_based.py`, `tests/core/test_state_check.py` |
| Blockcommit/lifecycle | `tests/modules/lifecycle/test_blockcommit.py` |
| Deferred snapshot thresholds | `tests/core/test_deferred.py` |
| State backup count | `tests/state/test_manager.py` |

**Mitigation:** Run `pytest tests/modules/backup/test_bitmap.py tests/modules/backup/test_bitmap_incremental.py tests/modules/backup/test_full_verification.py tests/utils/test_verification_bitmap.py tests/utils/test_nbd.py tests/core/test_full_verification_pipeline.py` after deletions to confirm all protective-skeleton tests pass.

### R6 — Transfer signature change breaks all callers

**Risk:** Removing `rate_limit` from `IBackupProvider.transfer_missing()` affects every implementation, mock, and contract test.

**Test coverage:**
- `tests/interfaces/test_backup_provider.py` — contract tests enforce the new signature for all parametrized implementations.
- `tests/mocks/mock_modules.py` — mocks updated to match new signature.
- `tests/core/test_bitmap_dependency.py` — caller tests updated to pass correct kwargs.
- `tests/modules/backup/test_bitmap.py` — provider tests updated.

**Mitigation:** `pyright --strict` catches type mismatches on `transfer_missing()` calls.

### R7 — TOML fixture field removal breaks existing parser tests

**Risk:** Fixtures like `safety_fields.toml`, `full_backup.toml`, `bucket_driven.toml`, `verify_full_both.toml`, and `verify_mode_defaults.toml` contain `incremental_mode`, `rate_limit`, or `copy_base`. If these are removed from the model but left in fixtures, parser tests will fail.

**Mitigation:** Every fixture with removed fields is updated in the `config-suite` group. `verify_full_both.toml` and `verify_mode_defaults.toml` change `"file-copy"` to `"bitmap"`; `safety_fields.toml` drops all three fields; `full_backup.toml` and `bucket_driven.toml` drop `copy_base`/`incremental_mode`.

### R8 — Post-change `rg` verification

**Risk:** Stray references to `rsync`, `file-copy`, `FileCopy`, `rate_limit`, `parse_rate_limit`, `copy_base`, `incremental_mode` anywhere in `qsnap/` or `tests/` after the change.

**Verification command (post-change gate):**
```bash
# Must return zero hits in qsnap/ (except TOML deprecation warnings):
rg -i "rsync" qsnap/ && echo "FAIL: rsync still referenced in qsnap/" || echo "OK"
# Must return zero hits everywhere:
rg "FileCopyBackupProvider\|file_copy\|from.*file_copy" qsnap/ tests/ && echo "FAIL: FileCopy still referenced" || echo "OK"
# Must return zero hits (except in deprecated_fields.toml):
rg "rate_limit\b" qsnap/ && echo "FAIL: rate_limit still in production code" || echo "OK"
# Must return zero hits (except in deprecated_fields.toml):
rg "copy_base\b" qsnap/ && echo "FAIL: copy_base still in production code" || echo "OK"
# Must return zero hits (except in deprecated_fields.toml):
rg "incremental_mode\b" qsnap/ && echo "FAIL: incremental_mode still in production code" || echo "OK"
```

### R9 — Integration test environment validation now unconditional

**Risk:** `test_env_validation.py` test `test_no_bitmap_targets_skips_libnbd_check` was predicated on having only file-copy targets. After removal, libnbd is checked unconditionally. The test must be deleted (as listed) and replaced with a test verifying the new unconditional behavior.

**Test coverage:** NEW `test_libnbd_missing_hard_failure` replaces the deleted test. It creates a minimal config (no `incremental_mode` field needed), forces libnbd import failure, and verifies the pipeline aborts with a `RuntimeError` naming `python3-libnbd`.
