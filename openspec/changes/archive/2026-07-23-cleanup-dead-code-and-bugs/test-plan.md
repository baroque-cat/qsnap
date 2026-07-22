# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-full-verification | M2 structural verification of FULL (qemu-img check) | M2 passes when all fields are zero | `tests/modules/backup/test_full_verification.py` | `test_verify_full_backup_check_passes_all_fields_zero` | verification-unit |
| backup-full-verification | M2 structural verification of FULL (qemu-img check) | M2 fails on non-zero corruptions | `tests/modules/backup/test_full_verification.py` | `test_verify_full_backup_check_corruptions_detected` | verification-unit |
| backup-full-verification | M2 structural verification of FULL (qemu-img check) | M2 fails on non-zero errors | `tests/modules/backup/test_full_verification.py` | `test_verify_full_backup_check_errors_detected` (MODIFY) | verification-unit |
| backup-full-verification | M2 structural verification of FULL (qemu-img check) | M2 fails on non-zero leaks | `tests/modules/backup/test_full_verification.py` | `test_verify_full_backup_check_leaks_detected` | verification-unit |
| backup-verification | TargetConfig verify field | Default verification is metadata | `tests/config/test_model.py` | `test_target_config_verify_default_metadata` (MODIFY) | config-unit |
| backup-verification | TargetConfig verify field | Explicit compare verification | `tests/config/test_model.py` | `test_target_config_verify_compare` (existing) | config-unit |
| backup-verification | TargetConfig verify field | Explicit check verification | `tests/config/test_facade.py` | `test_facade_target_verify_check_allowed` | config-unit |
| backup-verification | TargetConfig verify field | Deprecated hash treated as compare | `tests/config/test_facade.py` | `test_facade_deprecated_verify_hash` (existing) | config-unit |
| backup-verification | TargetConfig verify field | Deprecated full treated as compare | `tests/config/test_facade.py` | `test_facade_deprecated_verify_full` (existing) | config-unit |
| backup-verification | TargetConfig verify field | Invalid verify value raises ConfigError | `tests/config/test_facade.py` | `test_facade_invalid_verify_raises` (existing) | config-unit |
| backup-hash-verification | SnapshotResult carries content_hash (REMOVED) | — | `tests/models/test_results.py` | `test_snapshot_result_content_hash_absent` (existing — keep) | state-interfaces-unit |
| backup-hash-verification | SnapshotInfo stores content_hash in persistent state (REMOVED) | — | `tests/state/test_manager.py` | `test_new_state_file_excludes_content_hash` (existing — keep) | state-interfaces-unit |
| backup-hash-verification | Shared hash utility in qsnap.utils (REMOVED) | — | `tests/models/test_results.py` | `test_snapshot_info_content_hash_absent` (existing — keep) | state-interfaces-unit |
| backup-provider | Transfer missing snapshots via dirty bitmap extraction | First backup — full NBD export (no prior checkpoint) | `tests/modules/backup/test_bitmap.py` | `test_no_checkpoints_triggers_full_export` (existing) | backup-unit |
| backup-provider | Transfer missing snapshots via dirty bitmap extraction | Incremental backup — dirty blocks via NBD checkpoint | `tests/modules/backup/test_bitmap.py` | `test_checkpoint_found_triggers_incremental` (existing) | backup-unit |
| backup-provider | Transfer missing snapshots via dirty bitmap extraction | Scaffolding dedup — both FULL paths use shared helper | `tests/modules/backup/test_bitmap.py` | `test_full_pull_lifecycle_shared_by_both_paths` | backup-unit |
| config-model | GlobalConfig full_verify_before_rebase field (REMOVED) | full_verify_before_rebase not in GlobalConfig | `tests/config/test_model.py` | `test_global_config_no_full_verify_before_rebase` | config-unit |
| config-model | GlobalConfig full_verify_before_rebase field (REMOVED) | TOML with full_verify_before_rebase is silently ignored | `tests/config/test_facade.py` | `test_facade_unknown_key_full_verify_before_rebase_ignored` | config-unit |
| config-model | VMConfig snapshot_create validation | Valid snapshot_create value | `tests/config/test_facade.py` | `test_facade_valid_snapshot_create_onchange` | config-unit |
| config-model | VMConfig snapshot_create validation | Invalid snapshot_create value raises ConfigError | `tests/config/test_facade.py` | `test_facade_invalid_snapshot_create_raises_config_error` | config-unit |
| config-model | VMConfig snapshot_create validation | Default snapshot_create is always | `tests/config/test_model.py` | `test_vm_config_snapshot_create_default` (MODIFY) | config-unit |
| config-model | VMConfig blockcommit_deep_verify and snapshot_deep_verify fields | VMConfig has blockcommit_deep_verify only | `tests/config/test_model.py` | `test_vm_config_deep_verify_blockcommit_only` | config-unit |
| config-model | VMConfig blockcommit_deep_verify and snapshot_deep_verify fields | TOML with snapshot_deep_verify is silently ignored | `tests/config/test_facade.py` | `test_facade_unknown_key_snapshot_deep_verify_ignored` | config-unit |
| core-orchestrator | Pipeline step order | Orphan checkpoint detection uses factory | `tests/core/test_pipeline.py` | `test_detect_orphan_checkpoints_uses_factory` | core-unit |
| core-orchestrator | Pipeline step order | domblklist failure returns empty list | `tests/core/test_pipeline.py` | `test_resolve_disks_returns_empty_on_failure` | core-unit |
| core-orchestrator | Pipeline step order | Pipeline with always mode | `tests/core/test_pipeline.py` | `test_pipeline_always_mode_skips_detection` (existing) | core-unit |
| deep-verification-circuit | VMConfig blockcommit_deep_verify and snapshot_deep_verify fields | Deep verify defaults to off | `tests/config/test_model.py` | `test_vm_config_deep_verify_blockcommit_only` (same as above) | config-unit |
| deep-verification-circuit | VMConfig blockcommit_deep_verify and snapshot_deep_verify fields | Deep verify enabled for critical VM | `tests/core/test_lifecycle_fork.py` | `test_deep_verify_enabled_for_critical_vm` (existing) | core-unit |
| deep-verification-circuit | BlockCommitManager deep_verify flag | deep_verify passes after deferred commit | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_deep_verify_passes` (MODIFY) | lifecycle-unit |
| deep-verification-circuit | BlockCommitManager deep_verify flag | deep_verify fails on corruptions | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_deep_verify_fails_corruptions` (MODIFY) | lifecycle-unit |
| deep-verification-circuit | BlockCommitManager deep_verify flag | deep_verify fails on errors | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_deep_verify_fails_errors` | lifecycle-unit |
| deep-verification-circuit | BlockCommitManager deep_verify flag | deep_verify fails on leaks | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_deep_verify_fails_leaks` | lifecycle-unit |
| deep-verification-circuit | BlockCommitManager deep_verify flag | deep_verify passes after deferred commit (qemu-img commit) | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_qemu_img_commit_deep_verify` (MODIFY) | lifecycle-unit |
| deep-verification-circuit | BlockCommitManager deep_verify flag | deep_verify fails on errors (qemu-img commit) | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_qemu_img_commit_deep_verify_fails_errors` | lifecycle-unit |
| deep-verification-circuit | BlockCommitManager deep_verify flag | deep_verify fails on leaks (qemu-img commit) | `tests/modules/lifecycle/test_qemu_img_commit.py` | `test_qemu_img_commit_deep_verify_fails_leaks` | lifecycle-unit |
| env-validation | Pre-flight environment validation before pipeline | Compress driver available — validation passes | `tests/core/test_validation.py` | `test_validate_compress_driver_available` (UNSKIP) | core-unit |
| env-validation | Pre-flight environment validation before pipeline | Compress driver missing — hard failure | `tests/core/test_validation.py` | `test_validate_compress_driver_missing_fails_hard` (UNSKIP) | core-unit |
| env-validation | Pre-flight environment validation before pipeline | Compress driver missing in dry-run — warning | `tests/core/test_validation.py` | `test_validate_compress_driver_missing_dry_run_warning` | core-unit |
| env-validation | Pre-flight environment validation before pipeline | All validations pass | `tests/integration/test_env_validation.py` | `test_validate_environment_passes_with_libnbd` (existing) | integration-real |
| env-validation | Pre-flight environment validation before pipeline | snapshot_dir does not exist | `tests/core/test_validation.py` | `test_validate_snapshot_dir_missing` (existing) | core-unit |
| env-validation | Pre-flight environment validation before pipeline | libnbd missing — hard failure | `tests/integration/test_env_validation.py` | `test_libnbd_missing_hard_failure` (existing) | integration-real |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | First backup — full pull via NBD with atomic checkpoint | `tests/modules/backup/test_bitmap.py` | `test_atomic_full_export_passes_checkpoint_xml` (existing) | backup-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | Incremental backup — dirty blocks via NBD checkpoint | `tests/modules/backup/test_bitmap.py` | `test_atomic_incremental_passes_checkpoint_xml_and_incremental` (existing) | backup-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | _start_write_server does not accept compression_type | `tests/modules/backup/test_bitmap.py` | `test_start_write_server_signature_no_compression_type` | backup-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | Scaffolding dedup — shared _full_pull_lifecycle helper | `tests/modules/backup/test_bitmap.py` | `test_full_pull_lifecycle_shared_by_both_paths` (same) | backup-unit |
| shared-utilities | Shared hash utility in qsnap.utils (REMOVED) | — | — | (no test needed — covered by hash-verification removal) | — |
| shared-utilities | Shared NBD utility functions in qsnap.utils | Core imports NBD utilities from utils | `tests/utils/test_nbd.py` | `test_nbd_utilities_importable` (existing) | utils-unit |
| shared-utilities | Shared NBD utility functions in qsnap.utils | BitmapBackupProvider imports write_backup_xml from utils | `tests/modules/backup/test_bitmap.py` | `test_bitmap_uses_shared_nbd_utils` (existing) | backup-unit |
| shared-utilities | Shared verification functions in qsnap.utils | Core imports verify_full_backup from utils | `tests/utils/test_verification.py` | `test_verify_full_backup_imported_from_utils` (existing) | utils-unit |
| shared-utilities | Shared verification functions in qsnap.utils | is_retryable does not match hash mismatch | `tests/utils/test_retry.py` | `test_is_retryable_hash_mismatch` (MODIFY) | retry-utils-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | First backup to target creates FULL | `tests/core/test_full_verification_pipeline.py` | `test_first_backup_creates_full` (existing) | core-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | New weekly period triggers FULL (all-buckets mode) | `tests/modules/retention/test_bucket_full_strategy.py` | `test_bucket_full_strategy_weekly` (existing) | retention-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | F-anchor on weekly only triggers FULL at week boundaries | `tests/modules/retention/test_bucket_full_strategy.py` | `test_f_anchor_weekly_only` (existing) | retention-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | Bucket strategy obtained via factory | `tests/core/test_full_verification_pipeline.py` | `test_bucket_strategy_via_factory` | core-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | Dry-run logs FULL-would-be-created without executing | `tests/core/test_full_verification_pipeline.py` | `test_dry_run_logs_full_would_be_created` (existing) | core-unit |

---

## Delegation Groups

### Group: verification-unit

**Scope:** `tests/utils/test_verification.py`, `tests/modules/backup/test_full_verification.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_full_verification.py` | 4 scenarios (M2 pass all-zero, corruptions, errors, leaks) | MODIFY (add corruptions check to existing check tests; add new corruptions/leaks test) |
| `tests/utils/test_verification.py` | 0 new scenarios (existing tests cover shared utils import) | MODIFY (update `_VALID_CHECK` to include `corruptions`) |

### Group: lifecycle-unit

**Scope:** `tests/modules/lifecycle/test_blockcommit.py`, `tests/modules/lifecycle/test_qemu_img_commit.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/lifecycle/test_blockcommit.py` | 4 scenarios (deep_verify passes, fails corruptions, fails errors, fails leaks) | MODIFY (add `errors`/`leaks` to existing JSON; add new `test_blockcommit_deep_verify_fails_errors`, `test_blockcommit_deep_verify_fails_leaks`) |
| `tests/modules/lifecycle/test_qemu_img_commit.py` | 3 scenarios (deep_verify combined: pass, fail errors, fail leaks) | MODIFY (add `errors`/`leaks` params to parametrize; rename/restructure) |

### Group: config-unit

**Scope:** `tests/config/test_model.py`, `tests/config/test_facade.py`, `tests/config/test_parser.py`, `tests/config/test_fixtures.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | 5 scenarios (verify default, full_verify_before_rebase removal, snapshot_create default, blockcommit_deep_verify only, snapshot_deep_verify removal) | MODIFY (remove `full_verify_before_rebase` and `snapshot_deep_verify` assertions; add new REMOVED/only tests; add snapshot_create test) |
| `tests/config/test_facade.py` | 8 scenarios (verify="check" allowed, full_verify_before_rebase ignored, snapshot_create valid/invalid/default, snapshot_deep_verify ignored) | MODIFY (remove `full_verify_before_rebase` tests; add new REMOVED/snapshot_create/verify="check" tests) |
| `tests/config/test_parser.py` | 0 new scenarios | NO CHANGE |
| `tests/config/test_fixtures.py` | 0 new scenarios | MODIFY (remove `snapshot_deep_verify` assertions) |

### Group: core-unit

**Scope:** `tests/core/test_validation.py`, `tests/core/test_pipeline.py`, `tests/core/test_full_verification_pipeline.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_validation.py` | 3 scenarios (compress driver available, missing hard-fail, missing dry-run warning) | MODIFY (UNSKIP 2 tests; add dry-run warning test) |
| `tests/core/test_pipeline.py` | 3 scenarios (orphan checkpoint factory, empty disk list, always-mode) | MODIFY (rename hash mismatch test; add factory-routing test; add empty-disk-list test) |
| `tests/core/test_full_verification_pipeline.py` | 3 scenarios (first backup Creates FULL, bucket strategy via factory, dry-run log) | MODIFY (rename hash mismatch test; add factory-strategy test) |

### Group: retry-utils-unit

**Scope:** `tests/utils/test_retry.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_retry.py` | 1 scenario (is_retryable does not match hash mismatch) | MODIFY (change test_is_retryable_hash_mismatch to assert False) |

### Group: backup-unit

**Scope:** `tests/modules/backup/test_bitmap.py`, `tests/modules/backup/test_bitmap_incremental.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | 4 scenarios (full export, incremental, no compression_type sig, shared helper) | MODIFY (add `_start_write_server` signature test and `_full_pull_lifecycle` shared-call test) |
| `tests/modules/backup/test_bitmap_incremental.py` | 0 new scenarios | NO CHANGE |

### Group: cli-systemd-unit

**Scope:** `tests/cli/test_commands.py`, `tests/systemd/test_units.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/cli/test_commands.py` | 0 new scenarios | MODIFY (remove `snapshot_deep_verify` references from 2 list-config tests) |
| `tests/systemd/test_units.py` | 0 new scenarios | MODIFY (remove `snapshot_deep_verify` assertion from example-config test) |

### Group: state-interfaces-unit

**Scope:** `tests/models/test_results.py`, `tests/state/test_manager.py`, `tests/interfaces/test_snapshot_provider.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/models/test_results.py` | 2 scenarios (SnapshotResult content_hash absent, SnapshotInfo content_hash absent) | NO CHANGE (existing tests verify removal — keep as regression guards) |
| `tests/state/test_manager.py` | 2 scenarios (new state excludes content_hash, old content_hash ignored) | NO CHANGE (existing tests verify removal — keep as regression guards) |
| `tests/interfaces/test_snapshot_provider.py` | 1 scenario (create returns no content_hash) | NO CHANGE (existing test verifies removal — keep as regression guard) |

### Group: utils-unit

**Scope:** `tests/utils/test_verification.py`, `tests/utils/test_nbd.py`, `tests/utils/test_verification_bitmap.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_verification.py` | 1 scenario (Core imports verify_full_backup from utils) | NO CHANGE (existing test) |
| `tests/utils/test_nbd.py` | 0 new scenarios | NO CHANGE |
| `tests/utils/test_verification_bitmap.py` | 0 new scenarios (check-tier test already exists) | NO CHANGE |

### Group: retention-unit

**Scope:** `tests/modules/retention/test_bucket_full_strategy.py`, `tests/modules/retention/test_time_based.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/retention/test_bucket_full_strategy.py` | 2 scenarios (weekly bucket, F-anchor) | NO CHANGE (existing tests) |
| `tests/modules/retention/test_time_based.py` | 0 new scenarios | NO CHANGE |

### Group: integration-real

**Scope:** `tests/integration/test_compress_driver.py`, `tests/integration/test_env_validation.py`, `tests/integration/test_retry_integration.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_compress_driver.py` | 0 new scenarios | NO CHANGE (existing tests cover COMPRESS driver end-to-end) |
| `tests/integration/test_env_validation.py` | 2 scenarios (validation passes, libnbd missing) | NO CHANGE (existing tests) |
| `tests/integration/test_retry_integration.py` | 0 new scenarios | MODIFY (update hash mismatch references to content comparison mismatch) |

### Group: deprecated-fixtures

**Scope:** `tests/fixtures/configs/safety_fields.toml`, `tests/conftest.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/fixtures/configs/safety_fields.toml` | 0 new scenarios | MODIFY (remove `snapshot_deep_verify` lines) |
| `tests/conftest.py` | 0 new scenarios | MODIFY (remove `full_verify_before_rebase` from `make_global_config` fixture) |

---

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/modules/backup/test_full_verification.py` | In `_VALID_CHECK`, add `"corruptions": 0`. Add new test `test_verify_full_backup_check_corruptions_detected` (assert corruptions=3 fails). Add new test `test_verify_full_backup_check_leaks_detected` (assert leaks=5 fails). Update `test_verify_full_backup_check_errors_detected` to also verify corruptions/leaks=0 pattern. | Spec D3: M2 must check corruptions in addition to errors+leaks. |
| `tests/modules/backup/test_full_verification.py` | Add `test_verify_full_backup_check_passes_all_fields_zero` — verify `qemu-img check` output with `{"errors":0, "leaks":0, "corruptions":0}` passes. | Spec scenario: M2 passes when all fields are zero. |
| `tests/modules/lifecycle/test_blockcommit.py` | In `test_blockcommit_deep_verify_passes`, update JSON from `{"corruptions": 0}` to `{"corruptions":0, "errors":0, "leaks":0}`. In `test_blockcommit_deep_verify_fails_corruptions`, update JSON to `{"corruptions":5, "errors":0, "leaks":0}`. Add `test_blockcommit_deep_verify_fails_errors` — JSON `{"corruptions":0, "errors":2, "leaks":0}` → `result.success is False` + "2 errors" in error. Add `test_blockcommit_deep_verify_fails_leaks` — JSON `{"corruptions":0, "errors":0, "leaks":3}` → `result.success is False` + "3 leaks" in error. | Spec D3: lifecycle deep_verify must check errors+leaks in addition to corruptions. |
| `tests/modules/lifecycle/test_blockcommit.py` | In `test_blockcommit_no_force_share`, update expected JSON to `{"corruptions":0, "errors":0, "leaks":0}`. | Consistent with new three-field check. |
| `tests/modules/lifecycle/test_qemu_img_commit.py` | In `test_qemu_img_commit_deep_verify`, extend parametrize with: `(json.dumps({"corruptions":0, "errors":2, "leaks":0}), False, "deep verify: 2 errors in base image")` and `(json.dumps({"corruptions":0, "errors":0, "leaks":4}), False, "deep verify: 4 leaks in base image")`. Update existing `corruptions` case to include `"errors":0, "leaks":0`. | Spec D3: QemuImgCommitManager deep_verify must check errors+leaks. |
| `tests/config/test_model.py` | Delete `test_global_config_full_verify_before_rebase_default` (line 556) and `test_global_config_full_verify_before_rebase_off` (line 561). Replace with `test_global_config_no_full_verify_before_rebase` that asserts `AttributeError` on `config.full_verify_before_rebase`. | Spec D1: `full_verify_before_rebase` removed from `GlobalConfig`. |
| `tests/config/test_model.py` | In `test_vm_config_deep_verify_defaults_false` (line 461), remove assertion `assert vm.snapshot_deep_verify is False`. In `test_vm_config_required_fields` (line 91), remove assertion `assert vm.snapshot_deep_verify is False`. Add `test_vm_config_deep_verify_blockcommit_only` that constructs VMConfig and asserts `blockcommit_deep_verify` exists and `snapshot_deep_verify` raises `AttributeError`. | Spec D2: `snapshot_deep_verify` removed from `VMConfig`. |
| `tests/config/test_model.py` | In `test_global_config_required_fields_defaults` (line ~86), remove assertion `assert cfg.full_verify_before_rebase == "metadata"`. | `full_verify_before_rebase` removed from GlobalConfig. |
| `tests/config/test_facade.py` | Delete `test_facade_parses_full_verify_before_rebase_off` (line 488) and `test_facade_invalid_full_verify_before_rebase_raises_config_error` (line 556). Add `test_facade_unknown_key_full_verify_before_rebase_ignored`: TOML with `full_verify_before_rebase = "metadata"` does NOT raise ConfigError and field is not on GlobalConfig. | Spec D1: field removed, silently ignored. |
| `tests/config/test_facade.py` | In `test_facade_parses_vm_deep_verify_fields` (line 214), remove `snapshot_deep_verify = true` from TOML and remove assertion `assert vm.snapshot_deep_verify is True`. Add `test_facade_unknown_key_snapshot_deep_verify_ignored`: TOML with `snapshot_deep_verify = true` → no ConfigError, field not on VMConfig. | Spec D2: `snapshot_deep_verify` silently ignored. |
| `tests/config/test_facade.py` | Add `test_facade_target_verify_check_allowed`: TOML with `verify = "check"` → `target.verify == "check"` (no ConfigError). | Spec D4: `"check"` added to allowed TargetConfig.verify values. |
| `tests/config/test_facade.py` | Add `test_facade_valid_snapshot_create_onchange`: TOML with `snapshot_create = "onchange"` → `VMConfig.snapshot_create == "onchange"`. Add `test_facade_invalid_snapshot_create_raises_config_error`: TOML with `snapshot_create = "on-changed"` → `ConfigError` listing valid values. | Spec D9: `snapshot_create` validation added. |
| `tests/config/test_parser.py` | In `test_config_with_snapshot_create` (if exists), add parametrization for valid/invalid values. If no such test exists, add to `test_facade.py` instead. | Spec D9: `snapshot_create` validation. |
| `tests/config/test_fixtures.py` | In `test_safety_fields_toml_all_fields` (line ~240), remove assertions `assert critical.snapshot_deep_verify is True` and `assert standard.snapshot_deep_verify is False`. | `snapshot_deep_verify` removed from model. |
| `tests/core/test_validation.py` | UNSKIP `test_validate_compress_driver_available` (line 965) and `test_validate_compress_driver_missing_fails_hard` (line 1002) — remove `@pytest.mark.skip` decorators. | Spec D10: compress driver validation implemented. |
| `tests/core/test_validation.py` | Add `test_validate_compress_driver_missing_dry_run_warning`: compress driver missing + dry_run=True → WARNING logged, `CheckResult` returned (not RuntimeError). | Spec scenario: compress driver missing in dry-run → warning. |
| `tests/core/test_pipeline.py` | Rename `test_transfer_retries_on_hash_mismatch` (line 2177) to `test_transfer_retries_on_content_comparison_mismatch`. Change error string from `"verification failed: hash mismatch"` to `"verification failed: content comparison mismatch"`. Update docstring. | Spec D8 retry: `"hash mismatch"` pattern removed. |
| `tests/core/test_pipeline.py` | Add `test_detect_orphan_checkpoints_uses_factory`: mock factory's `create_backup_provider` → verify `_detect_orphan_checkpoints` calls `self._factory.create_backup_provider(vm_config, target)` and does NOT directly import `BitmapBackupProvider`. | Spec D5: factory violation fix. |
| `tests/core/test_pipeline.py` | Add `test_resolve_disks_returns_empty_on_failure`: mock `virsh domblklist` returning failure → `_resolve_disks()` returns empty list, WARNING logged, snapshot skipped. | Spec D6: disk="vda" fallback fix. |
| `tests/core/test_full_verification_pipeline.py` | Rename `test_full_verify_hash_mismatch_fails` (line 597) to `test_full_verify_content_comparison_mismatch_fails`. Change mocked return value to `"verification failed: content comparison mismatch"`. Update docstring. | Spec D8 retry: `"hash mismatch"` pattern removed. |
| `tests/core/test_full_verification_pipeline.py` | Add `test_bucket_strategy_via_factory`: verify `Core._backup_target()` calls `self._factory.create_bucket_full_strategy()` and does NOT have a `_should_create_bucket_full()` private method. | Spec D12: periodic-full-backup sync — factory-delegated strategy. |
| `tests/utils/test_retry.py` | Change `test_is_retryable_hash_mismatch` (line 57): assert `is_retryable("verification failed: hash mismatch")` returns `False`. Update docstring. | Spec D8 retry: `"hash mismatch"` pattern removed from `is_retryable()`. |
| `tests/integration/test_retry_integration.py` | Line 47: change `assert is_retryable("verification failed: hash mismatch")` to `assert not is_retryable("verification failed: hash mismatch")`. Line 48: change to `assert not is_retryable("VERIFICATION FAILED: HASH MISMATCH")`. Lines 136-137: update comment to reflect that hash mismatch is NO LONGER retryable. | Spec D8 retry: `"hash mismatch"` pattern removed. |
| `tests/cli/test_commands.py` | In `test_list_config_shows_off_for_default_deep_verify` (line 641): remove `snapshot_deep_verify=False` from VMConfig constructor. Update docstring. Remove assertion `assert "SNAPSHOT_DEEP_VERIFY" in captured.out`. In `test_list_config_shows_on_for_enabled_deep_verify` (line 678): remove `snapshot_deep_verify=True` from VMConfig constructor. Remove `SNAPSHOT_DEEP_VERIFY` assertion. | Spec D2: `snapshot_deep_verify` removed from CLI status output. |
| `tests/systemd/test_units.py` | In `test_example_config_documents_all_safety_fields` (line 70): remove assertion `assert "snapshot_deep_verify" in content`. Update docstring. | Spec D2: `snapshot_deep_verify` removed. |
| `tests/fixtures/configs/safety_fields.toml` | Remove `snapshot_deep_verify = true` (line 30) from `critical-vm` section and `snapshot_deep_verify = false` (line 45) from `standard-vm` section. | `snapshot_deep_verify` removed from model. |
| `tests/conftest.py` | In `make_global_config` fixture (line ~227): remove `full_verify_before_rebase` parameter and its passing to `GlobalConfig(...)`. | Spec D1: `full_verify_before_rebase` removed. |
| `tests/modules/backup/test_bitmap.py` | Add `test_start_write_server_signature_no_compression_type`: inspect `_start_write_server` signature, assert `compression_type` not in parameters. | Spec D8: `compression_type` parameter removed. |
| `tests/modules/backup/test_bitmap.py` | Add `test_full_pull_lifecycle_shared_by_both_paths`: mock the `_full_pull_lifecycle` helper and verify both `transfer_missing()` full-pull path and `create_full_backup()` call it. | Spec D7: scaffolding dedup helper. |

---

## Test Deletions (Old rsync/Dead Code Tests)

| File | References | Reason |
|---|---|---|
| `tests/config/test_model.py::test_global_config_full_verify_before_rebase_default` (line 556) | `GlobalConfig().full_verify_before_rebase` | Field removed per spec D1. Replaced with `test_global_config_no_full_verify_before_rebase`. |
| `tests/config/test_model.py::test_global_config_full_verify_before_rebase_off` (line 561) | `GlobalConfig(full_verify_before_rebase="off")` | Field removed per spec D1. Replaced with `test_global_config_no_full_verify_before_rebase`. |
| `tests/config/test_facade.py::test_facade_parses_full_verify_before_rebase_off` (line 488) | `facade.get_global().full_verify_before_rebase` | Field removed. Replaced with `test_facade_unknown_key_full_verify_before_rebase_ignored`. |
| `tests/config/test_facade.py::test_facade_invalid_full_verify_before_rebase_raises_config_error` (line 556) | `Invalid full_verify_before_rebase` ConfigError | Validation removed. Replaced with silent-ignore test. |
| (None for rsync — `tests/config/test_facade.py` lines 989-1021 reference `rate_limit`, `copy_base`, `incremental_mode` but these are **deprecation-warning tests** from the prior rsync removal change. They are NOT dead code — they verify backward-compatibility. **KEEP.**) | — | — |
| (None for rsync — `tests/fixtures/configs/deprecated_fields.toml` is a fixture for deprecation tests. **KEEP.**) | — | — |
| (None for rsync — `tests/config/test_parser.py` line 11 comment references rsync. Comment-only reference. Minor edit to remove stale comment.) | — | — |
| (None for rsync — `tests/config/test_resolver.py` line 14 comment. Comment-only reference. Minor edit.) | — | — |
| (None for rsync — `tests/mocks/mock_factory.py` line 33, 52 comments. Comment-only references. Minor edit.) | — | — |
| (None for rsync — `tests/mocks/test_mock_factory.py` line 72 comment. Comment-only reference. Minor edit.) | — | — |
| (None for rsync — `tests/integration/test_env_validation.py` lines 65, 127 comments reference `incremental_mode`. Comment-only references. Minor edit.) | — | — |

---

## Risks & Edge Cases

- **[Risk] Removing `full_verify_before_rebase` breaks configs that set it** → Mitigation: The field was never consumed. New test `test_facade_unknown_key_full_verify_before_rebase_ignored` verifies that TOML configs with the field parse without error and the value is not stored.

- **[Risk] Adding `"check"` to allowed verify modes changes behavior** → New test `test_facade_target_verify_check_allowed` verifies that `verify = "check"` in TOML produces `TargetConfig.verify == "check"`. Existing test `test_check_tier_does_not_run_compare` in `tests/utils/test_verification_bitmap.py` already verifies the runtime behavior of `"check"` mode.

- **[Risk] Factory-routed orphan detection changes call path** → New test `test_detect_orphan_checkpoints_uses_factory` mocks the factory's `create_backup_provider` and asserts it is called with `(vm_config, target)`. Verifies no direct `BitmapBackupProvider` import.

- **[Risk] Removing `disk="vda"` fallback may break VMs where domblklist fails** → New test `test_resolve_disks_returns_empty_on_failure` verifies empty list return + WARNING log + snapshot skip. Integration test could also verify real `virsh domblklist` failure on a stopped VM.

- **[Risk] Scaffolding dedup introduces a large helper with many parameters** → New test `test_full_pull_lifecycle_shared_by_both_paths` verifies the helper is called from both `transfer_missing()` and `create_full_backup()` paths. Parameter count is validated by the `_start_write_server` signature test (assert no `compression_type` — one less param).

- **[Risk] Compress driver validation may break on old `qemu-nbd` versions** → Unskipped tests `test_validate_compress_driver_available` and `test_validate_compress_driver_missing_fails_hard` use mocked shell. New `test_validate_compress_driver_missing_dry_run_warning` verifies non-fatal WARNING in dry-run mode. Integration tests in `tests/integration/test_compress_driver.py` verify real compress driver availability.

- **[Edge Case] `qemu-img check` returns only `corruptions` field** (no `errors`/`leaks` keys) → All three fields should use `.get("errors", 0)` / `.get("leaks", 0)` / `.get("corruptions", 0)` with default 0. Test by providing JSON with only one of the three keys and verifying the other two default to 0.

- **[Edge Case] `qemu-img check` returns non-JSON stdout** → Both verification.py M2 and lifecycle deep_verify should handle JSON parse failure gracefully. Existing tests cover `ShellResult(success=False)` for failed check commands. Add test for `ShellResult(success=True, stdout="not json")` → parse failure returns error.

- **[Edge Case] `snapshot_create = "ALWAYS"` (uppercase)** → The validation set should use lowercase exact match. New `test_facade_invalid_snapshot_create_raises_config_error` should include a case-sensitive mismatch variant.
