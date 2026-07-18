# QA Strategy & Test Plan

## Coverage Map

### backup-verification spec

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| Metadata verification | verify_backup checks format & virtual-size, NOT actual-size | Metadata verification passes | `tests/modules/backup/test_verification.py` | `test_metadata_verification_passes` (MODIFY) | verification-unit |
| Metadata verification | verify_backup returns error on wrong format | Metadata verification fails — wrong format | `tests/modules/backup/test_verification.py` | `test_metadata_verification_wrong_format` | verification-unit |
| Metadata verification | verify_backup returns error on virtual-size mismatch | Metadata verification fails — virtual-size mismatch | `tests/modules/backup/test_verification.py` | `test_metadata_verification_size_mismatch` | verification-unit |
| Metadata verification | actual-size difference does NOT cause failure (removed check) | Metadata verification passes despite actual-size difference | `tests/modules/backup/test_verification.py` | `test_metadata_passes_despite_actual_size_difference` (NEW) | verification-unit |
| Metadata verification | Source-side qemu-img info uses --force-share | Source-side info uses --force-share on active layer | `tests/modules/backup/test_verification.py` | `test_source_side_info_uses_force_share_on_active_layer` | verification-unit |
| Full verification | qemu-img compare uses --force-share, timeout=7200 | Full verification passes (stopped VM or frozen snapshot) | `tests/modules/backup/test_verification.py` | `test_full_verification_passes` (MODIFY) | verification-unit |
| Full verification | Non-zero exit → BackupResult(success=False) | Full verification detects corruption | `tests/modules/backup/test_verification.py` | `test_full_verification_detects_corruption` (MODIFY) | verification-unit |
| Full verification | WARNING logged for live source; --force-share used | Full verification on live source logs warning | `tests/modules/backup/test_verification.py` | `test_full_verification_live_source_logs_warning` (MODIFY) | verification-unit |
| Full verification | Lock conflict on live source → specific error | Full verification lock conflict on live source | `tests/modules/backup/test_verification.py` | `test_full_verification_live_source_lock_conflict` (MODIFY) | verification-unit |
| No verification | verify=off skips all qemu-img commands | No verification when verify=off | `tests/modules/backup/test_verification.py` | `test_no_verification_when_verify_off` | verification-unit |
| Timeout | qemu-img compare timeout is 7200s | Full verification timeout is 2 hours | `tests/modules/backup/test_verification.py` | `test_risk_full_verification_timeout_7200s` (MODIFY) | verification-unit |
| Live source full verify | --force-share on compare returns specific lock error | Full verification on live source lock conflict | `tests/modules/backup/test_verification.py` | `test_full_verify_live_source_lock_error_message` (NEW) | verification-unit |

### backup-hash-verification spec

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| Hash verification | SHA-256 match → None (success) | Hash match passes verification | `tests/modules/backup/test_verification.py` | `test_hash_verification_match_passes` | verification-unit |
| Hash verification | SHA-256 mismatch → error string | Hash mismatch fails verification | `tests/modules/backup/test_verification.py` | `test_hash_verification_mismatch_fails` | verification-unit |
| Hash verification | expected_hash=None → skip, return None | Hash verification skipped when no expected hash | `tests/modules/backup/test_verification.py` | `test_hash_verification_skipped_when_no_expected_hash` | verification-unit |
| Hash default | verify defaults to "hash" for file-copy mode | Hash is default for file-copy mode | `tests/config/test_resolver.py` | `test_facade_resolves_hash_default_for_file_copy_mode` (NEW) | config-model-unit |
| Metadata default for bitmap | verify defaults to "metadata" for bitmap mode | Metadata is default for bitmap mode | `tests/config/test_resolver.py` | `test_facade_resolves_metadata_default_for_bitmap_mode` (NEW) | config-model-unit |
| Explicit override | Explicit verify overrides mode-dependent default | Explicit verify overrides mode-dependent default | `tests/config/test_resolver.py` | `test_facade_explicit_verify_overrides_mode_default` (NEW) | config-model-unit |
| Bitmap+hash warning | ConfigFacade logs WARNING and auto-downgrades verify to "metadata" | Bitmap mode with verify="hash" warns and downgrades | `tests/config/test_resolver.py` | `test_facade_bitmap_mode_hash_warns_and_downgrades` (NEW) | config-model-unit |

### nbd-bitmap-backup spec

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| Checkpoint-only creation | Creates checkpoint without data transfer when FULL exists | Checkpoint created without transfer when FULL exists | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_checkpoint_only_when_full_exists` (NEW) | bitmap-unit |
| Checkpoint-only creation | Preserves existing full NBD export when no FULL | Full NBD export when no FULL and no checkpoint | `tests/modules/backup/test_bitmap.py` | `test_first_backup_full_nbd_no_prior_checkpoint` | bitmap-unit |
| Checkpoint-only creation | Incremental path used when checkpoint exists | Checkpoint-only path does not trigger when checkpoint exists | `tests/modules/backup/test_bitmap.py` | `test_incremental_backup_dirty_blocks_via_nbd` | bitmap-unit |
| Checkpoint-only creation | Already-existing snapshots skipped before checkpoint logic | Checkpoint-only path skips snapshots already on target | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_skips_existing_snapshot_before_checkpoint_check` (NEW) | bitmap-unit |
| State manager integration | FULL existence check uses state manager | Checkpoint creation checks state for FULLs | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_skips_checkpoint_when_state_is_none` (NEW) | bitmap-unit |
| NBD incremental compression | `-c` flag passed to qemu-img convert when target.compress=True | Incremental NBD transfer with compression (-c flag) | `tests/modules/backup/test_bitmap.py` | `test_bitmap_incremental_nbd_with_compression` (NEW) | bitmap-compression-unit |
| NBD incremental no compression | No `-c` flag when target.compress=False | Incremental NBD transfer without compression | `tests/modules/backup/test_bitmap.py` | `test_bitmap_incremental_nbd_without_compression` (NEW) | bitmap-compression-unit |
| Compression + metadata verify | qemu-img info reports same format/virtual-size for compressed files | Compression does not affect metadata verification | `tests/modules/backup/test_bitmap.py` | `test_bitmap_compress_metadata_verification_passes` (NEW) | bitmap-compression-unit |
| Compression + full verify | qemu-img compare decompresses clusters transparently | Compression does not affect full verification | `tests/modules/backup/test_bitmap.py` | `test_bitmap_compress_full_verification_passes` (NEW) | bitmap-compression-unit |

### backup-provider spec (failed file deletion)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| Failed file deletion | rm -f executed after verify_backup failure | Failed backup file deleted immediately after verification failure | `tests/modules/backup/test_copy.py` | `test_transfer_verify_failure_deletes_file` (REPLACE old) | copy-provider-unit |
| Failed file deletion | File gone before retention runs | Failed backup file not found by retention cleanup | `tests/modules/backup/test_copy.py` | `test_failed_backup_deletion_before_retention_cleanup` (NEW) | copy-provider-unit |
| Failed file deletion | rsync failure → partial file deleted | rsync failure does not leave partial file | `tests/modules/backup/test_copy.py` | `test_transfer_rsync_fails_disk_full` (MODIFY) | copy-provider-unit |
| Failed file deletion | qemu-img convert NBD failure → partial file deleted | Bitmap NBD convert failure does not leave partial file | `tests/modules/backup/test_bitmap.py` | `test_transfer_failure_deletes_partial_file` (NEW) | bitmap-unit |
| Failed file deletion | rm -f on verification failure in bitmap | Bitmap verification failure deletes target file | `tests/modules/backup/test_bitmap.py` | `test_bitmap_verify_failure_deletes_file` (NEW) | bitmap-unit |
| rsync compression | rsync adds `--compress` when target.compress=True | rsync with --compress flag | `tests/modules/backup/test_copy.py` | `test_rsync_with_compress_flag` (NEW) | copy-compression-unit |
| rsync compression + rate limit | `--bwlimit` and `--compress` coexist in rsync command | rsync with --compress and --bwlimit | `tests/modules/backup/test_copy.py` | `test_rsync_compress_with_rate_limit` (NEW) | copy-compression-unit |
| rsync without compression | No `--compress` when target.compress=False | rsync without --compress | `tests/modules/backup/test_copy.py` | `test_rsync_without_compress` (NEW) | copy-compression-unit |
| Compression + hash verify | SHA-256 matches after rsync --compress (byte-identical files) | --compress does not affect hash verification | `tests/modules/backup/test_copy.py` | `test_rsync_compress_hash_verification_passes` (NEW) | copy-compression-unit |

### config-model spec

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| TargetConfig defaults | Dataclass-level verify default is "metadata" | TargetConfig with incremental enabled | `tests/config/test_model.py` | `test_target_config_default_verify_metadata` | config-model-unit |
| ConfigFacade resolution | Hash default for file-copy at facade level | ConfigFacade resolves hash default for file-copy mode | `tests/config/test_resolver.py` | `test_facade_resolves_hash_default_for_file_copy_mode` (NEW) | config-model-unit |
| ConfigFacade resolution | Metadata default for bitmap at facade level | ConfigFacade resolves metadata default for bitmap mode | `tests/config/test_resolver.py` | `test_facade_resolves_metadata_default_for_bitmap_mode` (NEW) | config-model-unit |
| ConfigFacade resolution | Explicit verify overrides mode-dependent default | Explicit verify overrides mode-dependent default | `tests/config/test_resolver.py` | `test_facade_explicit_verify_overrides_mode_default` (NEW) | config-model-unit |
| ConfigFacade resolution | verify="full" works for both modes | Explicit verify="full" works for both modes | `tests/config/test_resolver.py` | `test_facade_verify_full_works_for_both_modes` (NEW) | config-model-unit |
| Bitmap+hash warning+downgrade | WARNING logged; verify downgraded to "metadata" | Bitmap mode with verify="hash" triggers warning and downgrade | `tests/config/test_resolver.py` | `test_facade_bitmap_mode_hash_warns_and_downgrades` (NEW) | config-model-unit |
| Default incremental_mode | `incremental_mode` defaults to `"bitmap"` at dataclass level | TargetConfig default incremental_mode is "bitmap" | `tests/config/test_model.py` | `test_target_config_default_incremental_mode_is_bitmap` (MODIFY existing) | config-model-unit |
| Explicit file-copy override | `incremental_mode="file-copy"` explicitly set overrides default | Explicit incremental_mode="file-copy" overrides bitmap default | `tests/config/test_model.py` | `test_target_config_explicit_file_copy_overrides_default` (NEW) | config-model-unit |
| Factory bitmap fallback | Factory returns FileCopyBackupProvider when libvirt < 6.0, logs WARNING, does NOT mutate TargetConfig | Factory falls back to file-copy when libvirt too old | `tests/factory/test_default.py` | `test_factory_bitmap_mode_old_libvirt_falls_back` (EXISTING) | config-fallback-unit |
| Factory fallback logging | WARNING logged on fallback; TargetConfig.incremental_mode unchanged | Factory fallback logs WARNING with "falling back" | `tests/factory/test_default.py` | `test_factory_bitmap_fallback_logs_warning` (EXISTING) | config-fallback-unit |

### backup-retry spec

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| Retry logic | Exponential backoff on transient errors | Transient error retried successfully | `tests/utils/test_retry.py` | `test_is_retryable_connection_refused` et al. | retry-unit |
| Retry logic | Hash mismatch is retryable | Hash mismatch retried | `tests/utils/test_retry.py` | `test_is_retryable_hash_mismatch` (NEW) | retry-unit |
| Retry logic | Last error returned after max retries | All retries exhausted | `tests/utils/test_retry.py` | `test_is_retryable_exhausted_returns_last_error` (NEW) | retry-unit |
| Retry logic | Non-retryable error → immediate failure | Non-retryable error fails immediately | `tests/utils/test_retry.py` | `test_is_retryable_no_space_left_on_device` | retry-unit |
| Retry logic | Format verification error NOT retryable | Format verification error not retried | `tests/utils/test_retry.py` | `test_is_retryable_format_verification_error` (NEW) | retry-unit |
| Retry logic | backup_retry_max=0 → no retry loop | Retry disabled when backup_retry_max = 0 | `tests/utils/test_retry.py` | `test_retry_disabled_when_max_is_zero` (NEW) | retry-unit |
| Retry logic | Hash mismatch retried (Core-level integration) | Hash mismatch triggers retry in transfer | `tests/core/test_pipeline.py` | `test_transfer_retries_on_hash_mismatch` (NEW) | core-unit |
| Retry logic | Format error NOT retried (Core-level) | Format error halts immediately | `tests/core/test_pipeline.py` | `test_transfer_does_not_retry_format_error` (NEW) | core-unit |

### Integration tests (real virsh/qemu-img)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| Metadata verification | Real qemu-img info with --force-share on a qcow2 file | Metadata verification with real qemu-img info | `tests/integration/test_verification.py` (NEW) | `test_int_metadata_verification_real_qcow2` | backup-integration |
| Hash verification | Real SHA-256 hash computed and verified | Hash verification with real qemu-img and SHA-256 | `tests/integration/test_verification.py` (NEW) | `test_int_hash_verification_real_qcow2` | backup-integration |
| Full verification | Real qemu-img compare with --force-share | Full verification passes with real compare | `tests/integration/test_verification.py` (NEW) | `test_int_full_verification_real_compare` | backup-integration |
| Full verification | Simulate race condition (write after transfer) | Full verification detects race-condition corruption | `tests/integration/test_verification.py` (NEW) | `test_int_full_verify_detects_race_condition_corruption` | backup-integration |
| Checkpoint creation | Real virsh checkpoint-create-as on test VM | Checkpoint created without NBD transfer | `tests/integration/test_bitmap_integration.py` (NEW) | `test_int_checkpoint_only_creation` | backup-integration |
| Failed file deletion | File deleted after verification failure | Real rm -f after simulated verify failure | `tests/integration/test_verification.py` (NEW) | `test_int_failed_file_deleted_after_verify_failure` | backup-integration |
| Retry with backoff | Hash mismatch triggers retry with delay | Retry loop with exponential backoff | `tests/integration/test_retry_integration.py` (NEW) | `test_int_retry_on_hash_mismatch` | backup-integration |
| NBD incremental compression | Real qemu-img convert -c produces compressed qcow2, verify passes | NBD incremental with compression flag (-c) | `tests/integration/test_bitmap_integration.py` (NEW) | `test_int_nbd_incremental_with_compression` | backup-compression-integration |
| Bitmap+hash warning | ConfigFacade logs WARNING and auto-downgrades verify to metadata | Bitmap mode with verify="hash" triggers warning and downgrade | `tests/integration/test_config_integration.py` (NEW) | `test_int_bitmap_hash_warns_and_downgrades` | backup-integration |

## Delegation Groups

### Group: verification-unit
**Scope:** `tests/modules/backup/test_verification.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_verification.py` | Metadata verification (remove actual-size check), full verification (add --force-share), hash verification, new actual-size-pass test, new live-source lock error test | MODIFY (7 tests changed, 2 tests added) |

### Group: full-verification-unit
**Scope:** `tests/modules/backup/test_full_verification.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_full_verification.py` | No changes needed — this module tests `verify_full_backup()` (standalone FULL backup verification), not `verify_backup()`. The `--force-share` change is only for `verify_backup()` full mode. | NONE (0 changes) |

### Group: copy-provider-unit
**Scope:** `tests/modules/backup/test_copy.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_copy.py` | Failed file deletion after verification (rm -f assertion), failed file deletion after rsync (rm -f assertion), verify default change awareness | MODIFY (2 tests changed, 1 test replaced, 1 test added) |

### Group: bitmap-unit
**Scope:** `tests/modules/backup/test_bitmap.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | Checkpoint-only creation when FULL exists, skip existing snapshots before checkpoint, failed file deletion, verify failure deletion | MODIFY (1 test changed, 4 tests added) |

### Group: config-model-unit
**Scope:** `tests/config/test_model.py`, `tests/config/test_resolver.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | TargetConfig dataclass-level defaults (no change needed — dataclass default stays "metadata"); NEW: default incremental_mode is "bitmap", explicit file-copy override | MODIFY (1 test changed to flip default from "file-copy" to "bitmap"), ADD (1 test for explicit override) |
| `tests/config/test_resolver.py` | ConfigFacade mode-dependent verify default resolution (hash for file-copy, metadata for bitmap, explicit override), bitmap+hash warning+downgrade | ADD (5 tests) |

### Group: config-fallback-unit
**Scope:** `tests/factory/test_default.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/factory/test_default.py` | Factory bitmap fallback when libvirt < 6.0 (tests already exist — verified coverage), WARNING logged | VERIFY (2 EXISTING tests — ensure they still pass with default incremental_mode="bitmap") |

### Group: copy-compression-unit
**Scope:** `tests/modules/backup/test_copy.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_copy.py` | rsync --compress flag (when compress=True), rsync --compress + --bwlimit, rsync without --compress (compress=False), compression does not affect hash verification | ADD (4 tests) |

### Group: bitmap-compression-unit
**Scope:** `tests/modules/backup/test_bitmap.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | NBD incremental with -c flag (compress=True), NBD incremental without -c (compress=False), compression and metadata verification, compression and full verification | ADD (4 tests) |

### Group: retry-unit
**Scope:** `tests/utils/test_retry.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_retry.py` | Hash mismatch retryable, format error NOT retryable, retry disabled when max=0, exhaustion returns last error | MODIFY (0 changes), ADD (4 tests) |

### Group: core-unit
**Scope:** `tests/core/test_pipeline.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | Retry on hash mismatch in transfer pipeline, immediate halt on format error | ADD (2 tests) |

### Group: backup-integration
**Scope:** `tests/integration/` (new files)
| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_verification.py` | Real qcow2 metadata verification, hash verification, full verification, race condition simulation, failed file deletion | NEW FILE (5 tests) |
| `tests/integration/test_bitmap_integration.py` | Real virsh checkpoint creation without NBD transfer, NBD incremental with compression (-c flag) | NEW FILE (2 tests) |
| `tests/integration/test_retry_integration.py` | Retry loop with exponential backoff on hash mismatch | NEW FILE (1 test) |
| `tests/integration/test_config_integration.py` | Bitmap+hash warning and auto-downgrade via ConfigFacade | NEW FILE (1 test) |

### Group: mocks-and-fixtures
**Scope:** `tests/mocks/mock_shell.py`, `tests/mocks/mock_state.py`, `tests/conftest.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/mocks/mock_shell.py` | No changes — MockShell already supports regex-based expectations and `expect_first` | NONE (0 changes) |
| `tests/mocks/mock_state.py` | No changes — InMemoryStateManager already has `get_full_backups()` for checkpoint-only logic | NONE (0 changes) |
| `tests/conftest.py` | `make_target` fixture already accepts `verify` kwarg; no changes needed for test infrastructure | NONE (0 changes) |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/modules/backup/test_verification.py` | `test_full_verification_passes`: Change assertion from `"--force-share" not in compare_cmd` to `"--force-share" in compare_cmd` (add --force-share) | Per design D5: --force-share is ADDED to qemu-img compare to avoid lock errors on live sources |
| `tests/modules/backup/test_verification.py` | `test_full_verification_detects_corruption`: Change assertion from `"--force-share" not in compare_cmd` to `"--force-share" in compare_cmd` | Same as above |
| `tests/modules/backup/test_verification.py` | `test_risk_full_verification_timeout_7200s`: Change assertion from `"--force-share" not in compare_cmd` to `"--force-share" in compare_cmd` | Same as above |
| `tests/modules/backup/test_verification.py` | `test_full_verification_live_source_logs_warning`: Change docstring and assertions to reflect --force-share is now used (was "executed without --force-share"); warning message may also change per updated spec | Per design D5: compare now uses --force-share, warning text updated |
| `tests/modules/backup/test_verification.py` | `test_full_verification_live_source_lock_conflict`: Update to reflect that --force-share is now used AND that a lock conflict still produces a specific error message recommending verify=metadata | The --force-share may prevent lock errors, but if it still happens, error message should match new spec |
| `tests/modules/backup/test_verification.py` | `test_metadata_verification_passes`: Update docstring to remove reference to "actual-size within 10% tolerance" | actual-size check is removed per design D1 |
| `tests/modules/backup/test_copy.py` | `test_transfer_verify_failure_logs_warning`: Add assertion that `rm -f <target_file>` is called before returning BackupResult(success=False). Rename to `test_transfer_verify_failure_deletes_file_and_logs_warning` | Per spec: failed backup files must be deleted immediately |
| `tests/modules/backup/test_copy.py` | `test_transfer_rsync_fails_disk_full`: Add assertion that `rm -f <target_file>` is called before returning BackupResult(success=False) | Per spec: rsync failure must delete partial file |
| `tests/modules/backup/test_copy.py` | `test_rsync_unavailable_transfer_fails_no_cp_fallback`: Add assertion that `rm -f <target_file>` is called for partial file cleanup | Per spec: rsync failure must delete partial file |

## Outdated Tests to Remove or Update

| File | Test Function | Issue | Action |
|---|---|---|---|
| `tests/modules/backup/test_verification.py` | `test_full_verification_passes` | Asserts `--force-share` is NOT in qemu-img compare command. Design D5 now ADDS --force-share to compare. | MODIFY: Flip assertion from `not in` to `in` |
| `tests/modules/backup/test_verification.py` | `test_full_verification_detects_corruption` | Same as above — asserts `--force-share` NOT in compare command. | MODIFY: Flip assertion from `not in` to `in` |
| `tests/modules/backup/test_verification.py` | `test_risk_full_verification_timeout_7200s` | Same as above — asserts `--force-share` NOT in compare command. | MODIFY: Flip assertion from `not in` to `in` |
| `tests/modules/backup/test_verification.py` | `test_full_verification_live_source_logs_warning` | Docstring says "executed without --force-share because it is a data-copying operation" — this is the OLD design. Design D5 reverses this: --force-share IS used with a WARNING. | MODIFY: Update docstring, change warning assertion to reflect new message, verify --force-share IS in compare command |
| `tests/modules/backup/test_verification.py` | `test_full_verification_live_source_lock_conflict` | Test sets up lock conflict error on qemu-img compare. With --force-share, the lock conflict scenario changes (--force-share should prevent most lock errors). The error message format should match new spec: `"verification failed: lock conflict — use verify=metadata for live sources"`. | MODIFY: Update error assertion to match new spec message; verify --force-share is present in compare args |
| `tests/modules/backup/test_verification.py` | `test_metadata_verification_passes` | Docstring references "actual-size within 10% tolerance" — this check is being removed. Test data still passes because virtual-size and format match, but docstring is misleading. | MODIFY: Update docstring to reflect that actual-size is no longer checked |
| `tests/modules/backup/test_verification.py` | `test_risk_full_verification_not_default` | Asserts `target.verify == "metadata"` at dataclass level. Dataclass-level default IS still "metadata", but ConfigFacade resolves to "hash" for file-copy mode. Test is at model level and is still technically correct, but its semantic context ("the default is metadata") is outdated. | MODIFY: Add a clarifying comment that the dataclass-level default is "metadata" but ConfigFacade resolves mode-dependent defaults |
| `tests/modules/backup/test_copy.py` | `test_transfer_verify_failure_logs_warning` | This test mocks `verify_backup` to return an error and checks WARNING is logged + BackupResult(success=False). The new spec adds `rm -f` BEFORE returning failure. Test does NOT assert `rm -f` is called. | REPLACE with `test_transfer_verify_failure_deletes_file_and_logs_warning`: Add `rm -f` expectation to mock_shell, assert `rm -f` is called, assert file deletion before BackupResult |
| `tests/modules/backup/test_copy.py` | `test_transfer_rsync_fails_disk_full` | Rsync failure returns error string. New spec requires `rm -f` of partial file before returning failure. Test does NOT assert `rm -f`. | MODIFY: Add `rm -f` assertion; verify `rm -f` is called with the target file path |
| `tests/modules/backup/test_copy.py` | `test_rsync_unavailable_transfer_fails_no_cp_fallback` | Same as above — rsync failure should delete partial file. | MODIFY: Add `rm -f` assertion |
| `tests/modules/backup/test_copy.py` | `test_transfer_missing_metadata_verification_default` | Uses `make_target` without explicit `verify`, relying on default. With the new mode-dependent default, file-copy mode without explicit verify should resolve to "hash" at the facade level. However this test creates TargetConfig directly, so dataclass default "metadata" still applies. | MODIFY: Add comment clarifying that at dataclass level default is "metadata", but ConfigFacade would resolve to "hash" for file-copy mode |
| `tests/config/test_model.py` | `test_target_config_default_verify_metadata` | Asserts dataclass-level default is "metadata". This is technically still correct (the dataclass field default stays "metadata"). But the semantic meaning has changed: for file-copy targets, the effective default is now "hash" (resolved by ConfigFacade). | MODIFY: Add clarifying comment about mode-dependent resolution in ConfigFacade |
| `tests/utils/test_retry.py` | None directly outdated, but missing coverage for hash mismatch retryability | `is_retryable` does not currently check for "verification failed: hash mismatch" pattern. The new spec adds this to retryable patterns. | ADD: `test_is_retryable_hash_mismatch` |
| `tests/modules/backup/test_bitmap.py` | `test_first_backup_full_nbd_no_prior_checkpoint` | Two issues: (a) test has no state manager set up, so `self._state` is None — the new checkpoint-only path falls through to existing full NBD export behavior (still valid). (b) `qemu-img convert` does NOT include `-c` flag — when `target.compress=True` (default), NBD transfers should include `-c`. | (a) NONE — test is still valid for the "no state, no FULL" case. (b) MODIFY: Add assertion that `-c` is in qemu-img convert command when compress=True (default) |
| `tests/modules/backup/test_bitmap.py` | `test_transfer_failure_preserves_checkpoint` | Test asserts checkpoint is preserved on qemu-img convert failure. Per new specs: (a) the partial target file must ALSO be deleted (`rm -f`), (b) the `qemu-img convert` command should include `-c` when compress=True (default). | MODIFY: Add `rm -f` expectation and assertion for partial file deletion; add `-c` assertion in qemu-img convert command |
| `tests/config/test_model.py` | `test_target_config_default_incremental_mode_is_file_copy` | Asserts `incremental_mode == "file-copy"` as the default. The spec now defaults `incremental_mode` to `"bitmap"`. | MODIFY: Rename to `test_target_config_default_incremental_mode_is_bitmap` and assert `incremental_mode == "bitmap"` |
| `tests/modules/backup/test_copy.py` | `test_transfer_missing_new_snapshot_rsync_empty_target` | Rsync command does NOT include `--compress`. When `target.compress=True` (default), rsync should include `--compress`. | MODIFY: Add assertion that `--compress` is in rsync command when compress=True (default); verify `--compress` appears before `--partial` |
| `tests/modules/backup/test_copy.py` | `test_transfer_incremental_rebase_backing_path` | Same as above — rsync without `--compress`. | MODIFY: Add `--compress` assertion in rsync command |
| `tests/modules/backup/test_copy.py` | `test_transfer_non_incremental_no_rebase` | Same as above — rsync without `--compress`. | MODIFY: Add `--compress` assertion in rsync command |
| `tests/modules/backup/test_copy.py` | `test_transfer_missing_full_verification` | Same as above — rsync without `--compress`. | MODIFY: Add `--compress` assertion in rsync command |
| `tests/modules/backup/test_copy.py` | `test_transfer_missing_metadata_verification_default` | Same as above — rsync without `--compress`. | MODIFY: Add `--compress` assertion in rsync command |
| `tests/modules/backup/test_copy.py` | `test_transfer_missing_no_verification_when_off` | Same as above — rsync without `--compress`. | MODIFY: Add `--compress` assertion in rsync command |
| `tests/modules/backup/test_bitmap.py` | `test_incremental_backup_dirty_blocks_via_nbd` | `qemu-img convert` does NOT include `-c` flag. When `target.compress=True` (default), NBD incremental transfers should include `-c`. | MODIFY: Add assertion that `-c` is in qemu-img convert command when compress=True (default) |
| `tests/modules/backup/test_bitmap.py` | `test_checkpoint_cleanup_after_successful_transfer` | Same as above — qemu-img convert without `-c`. | MODIFY: Add `-c` assertion in qemu-img convert command |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_incremental_dirty_blocks_via_nbd` | Same as above — qemu-img convert without `-c`. | MODIFY: Add `-c` assertion in qemu-img convert command |
| `tests/modules/backup/test_bitmap.py` | `test_domjobabort_called_after_successful_transfer` | Same as above — qemu-img convert without `-c`. | MODIFY: Add `-c` assertion in qemu-img convert command |

## Risks & Edge Cases

- **[Risk] Removing actual-size check weakens metadata verification** → Test `test_metadata_passes_despite_actual_size_difference` (NEW) directly proves that wildly divergent actual-size values pass verification, documenting the intentional relaxation. Integration test confirms format+virtual-size catches the critical corruptions.

- **[Risk] --force-share on qemu-img compare may produce false mismatches for live sources** → Test `test_full_verification_live_source_logs_warning` (MODIFIED) verifies the WARNING is logged. Integration test `test_int_full_verify_detects_race_condition_corruption` simulates the race by writing to a file between rsync and compare, then verifies the error message recommends using verify=metadata.

- **[Risk] Hash default for file-copy may surprise users with large incrementals** → Config resolver tests `test_facade_resolves_hash_default_for_file_copy_mode` and `test_facade_explicit_verify_overrides_mode_default` verify the resolution is correct and overridable.

- **[Risk] Checkpoint-only creation path may fail if state is stale (FULL recorded but file deleted)** → Integration test `test_int_checkpoint_only_creation` verifies the checkpoint is created with real virsh. Unit test `test_transfer_missing_checkpoint_only_when_full_exists` verifies the state check and conditional behavior.

- **[Risk] Hash mismatch retried but transfer degradation could loop indefinitely** → Test `test_is_retryable_hash_mismatch` (NEW) verifies hash mismatch enters the retryable pattern. Test `test_retry_disabled_when_max_is_zero` verifies that setting `backup_retry_max=0` disables retry entirely, giving operators an escape hatch.

- **[Risk] Failed backup file deletion may fail itself (disk full, permissions)** → The `rm -f` is called with timeout=10. The provider already returns `BackupResult(success=False)` for the prior failure. A failed `rm -f` is best-effort; the test `test_failed_backup_deletion_before_retention_cleanup` (NEW) verifies the happy path, and the timeout on `rm` ensures it won't hang.

- **[Risk] Two integration test files need test VM fixtures** → New integration `conftest.py` additions: `tests/integration/test_verification.py` uses existing `test_vm` fixture patterns; `tests/integration/test_bitmap_integration.py` extends with checkpoint-aware fixture; `tests/integration/test_retry_integration.py` may share `test_vm` fixture.

- **[Risk] Changes touch 6 existing test files across 5 module domains** → Each delegation group is independent (no cross-group dependencies). Parallel execution via `pytest -n auto` per group is safe. Integration tests are separated from unit tests via `@pytest.mark.integration` marker.

- **[Risk] Default incremental_mode changes from "file-copy" to "bitmap"** → Test `test_target_config_default_incremental_mode_is_bitmap` (renamed from `test_target_config_default_incremental_mode_is_file_copy`) verifies the new default. Existing factory tests `test_factory_bitmap_mode_old_libvirt_falls_back` and `test_factory_bitmap_mode_new_libvirt_returns_bitmap` already cover the factory behavior for bitmap mode. Users without libvirt 6.0+ on older deployments will get the file-copy fallback via the factory.

- **[Risk] rsync --compress may significantly increase CPU usage on source host** → Test `test_rsync_compress_with_rate_limit` (NEW) verifies that --compress and --bwlimit coexist, allowing operators to throttle bandwidth while compressing. Test `test_rsync_without_compress` (NEW) verifies that setting `compress=False` disables --compress, giving operators the ability to trade bandwidth for CPU.

- **[Risk] NBD incremental -c flag produces different file structure than uncompressed** → Tests `test_bitmap_compress_metadata_verification_passes` and `test_bitmap_compress_full_verification_passes` verify that metadata (format, virtual-size) is identical regardless of compression, and that `qemu-img compare` handles compressed files transparently.

- **[Risk] Bitmap+hash warning+downgrade may silently change user configuration** → Unit test `test_facade_bitmap_mode_hash_warns_and_downgrades` (NEW) verifies both the WARNING log message and the auto-downgrade to `"metadata"`. Integration test `test_int_bitmap_hash_warns_and_downgrades` (NEW) verifies the full ConfigFacade path with real config TOML.
