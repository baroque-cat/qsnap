# Test Plan: unify-nbd-transfer

## Section 1: Coverage Map

Every `#### Scenario:` from every delta spec mapped to a test case.

### Spec: `backup-verification` (MODIFIED)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| TargetConfig verify field | Default verification is metadata | WHEN verify is not set THEN defaults to "metadata" | `tests/config/test_model.py` | `test_target_config_verify_default_metadata` | config-model |
| TargetConfig verify field | Explicit compare verification | WHEN verify="compare" THEN TargetConfig.verify is "compare" | `tests/config/test_model.py` | `test_target_config_verify_explicit_compare` | config-model |
| TargetConfig verify field | Deprecated hash treated as compare | WHEN verify="hash" THEN WARNING logged AND treated as "compare" | `tests/config/test_facade.py` | `test_verify_hash_deprecated_warning_and_maps_to_compare` | config-model |
| TargetConfig verify field | Deprecated full treated as compare | WHEN verify="full" THEN WARNING logged AND treated as "compare" | `tests/config/test_facade.py` | `test_verify_full_deprecated_warning_and_maps_to_compare` | config-model |
| TargetConfig verify field | Invalid verify value raises ConfigError | WHEN verify="invalid" THEN ConfigError raised | `tests/config/test_facade.py` | `test_verify_invalid_value_raises_config_error` | config-model |

### Spec: `state-management` (MODIFIED)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| State serialization format | New state file has no content_hash | WHEN snapshot recorded THEN JSON does NOT contain content_hash key | `tests/state/test_manager.py` | `test_new_state_file_excludes_content_hash` | state-management |
| State serialization format | Old state file with content_hash loads fine | WHEN old state file with content_hash loaded THEN no error AND content_hash silently ignored | `tests/state/test_manager.py` | `test_old_state_content_hash_ignored_on_load` | state-management |

### Spec: `shell-abstraction` (MODIFIED)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| run_with_stall_detection | Stall detection kills stalled process | WHEN output_file shows no growth THEN process killed | `tests/utils/test_shell.py` | `test_stall_detection_kills_stalled_process` | nbd-utils |
| run_with_stall_detection | Data flows to completion | WHEN output_file grows steadily THEN runs to completion | `tests/utils/test_shell.py` | `test_stall_detection_completes_with_steady_growth` | nbd-utils |

### Spec: `nbd-bitmap-backup` (MODIFIED + ADDED)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| NBD pull-model via virsh backup-begin | First backup — full pull via NBD with atomic checkpoint | No prior checkpoint → full NBD export with base:allocation only, zero_skip=True, no qemu-img convert | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_first_backup_unified_engine_full` | bitmap-backup |
| NBD pull-model via virsh backup-begin | Incremental backup — dirty blocks via NBD checkpoint | Prior checkpoint → incremental with dirty bitmap, zero_skip=False, no --incremental CLI flag | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_incremental_unified_engine` | bitmap-backup |
| NBD pull-model via virsh backup-begin | Socket cleanup on success | Transfer success → socket removed, qemu-nbd killed, checkpoint preserved | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_socket_cleanup_on_success` | bitmap-backup |
| NBD pull-model via virsh backup-begin | Socket cleanup on failure | Transfer fails → socket still removed in finally, qemu-nbd killed, prior checkpoint preserved | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_socket_cleanup_on_failure` | bitmap-backup |
| create_full_backup via unified NBD engine | Bitmap FULL with zstd compression | compress=True + compression_type=zstd → qemu-img create -o compression_type=zstd, qemu-nbd compress driver, no qemu-img convert | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_zstd_compression_unified` | bitmap-backup |
| create_full_backup via unified NBD engine | Bitmap FULL without compression | compress=False → qemu-img create (plain), qemu-nbd --format=qcow2, no compress driver | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_no_compression_unified` | bitmap-backup |
| create_full_backup via unified NBD engine | Bitmap FULL leaves atomic checkpoint baseline | Running VM → virsh backup-begin with checkpoint XML 3rd arg → checkpoint exists on success | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_atomic_checkpoint_baseline` | bitmap-backup |
| flush() before closing write-side (ADDED) | flush called after successful transfer | pread/pwrite loop completes → can_flush() called → if True: flush() → disconnect() → qemu-nbd terminated | `tests/modules/backup/test_bitmap.py` | `test_transfer_flush_called_before_disconnect` | bitmap-backup |
| flush() before closing write-side (ADDED) | flush skipped when unsupported | can_flush() returns False → flush() not called → disconnect() proceeds | `tests/modules/backup/test_bitmap.py` | `test_transfer_flush_skipped_when_unsupported` | bitmap-backup |
| connect-retry in LibnbdClient (ADDED) | NBD server not ready on first attempt | backup-begin called but server not listening → connect retries up to 20x with 1s sleep, fresh handle each time | `tests/utils/test_nbd_client.py` | `test_connect_retry_server_not_ready_immediately` | nbd-utils |
| connect-retry in LibnbdClient (ADDED) | NBD server never starts | Server never available after 20 retries → NbdResult(success=False) with timeout message | `tests/utils/test_nbd_client.py` | `test_connect_retry_exhausted_returns_failure` | nbd-utils |
| zero-skip for standalone FULL (ADDED) | All-zero chunk skipped in FULL | zero_skip=True + pread chunk is all zeros → no pwrite, chunk counter incremented | `tests/modules/backup/test_bitmap.py` | `test_zero_skip_all_zero_chunk_no_pwrite` | bitmap-backup |
| zero-skip for standalone FULL (ADDED) | Non-zero chunk written in FULL | zero_skip=True + pread chunk has data → pwrite called | `tests/modules/backup/test_bitmap.py` | `test_zero_skip_nonzero_chunk_pwrite_called` | bitmap-backup |
| zero-skip for standalone FULL (ADDED) | Zero-skip never applied to incrementals | zero_skip=False → all dirty extents written regardless of content | `tests/modules/backup/test_bitmap.py` | `test_zero_skip_not_applied_to_incrementals` | bitmap-backup |
| qemu-nbd compress driver (ADDED) | Compress driver enabled for compressed FULL | compress=True, compression_type="zstd" → --image-opts driver=compress,... | `tests/modules/backup/test_bitmap.py` | `test_compress_driver_qemu_nbd_image_opts` | bitmap-backup |
| qemu-nbd compress driver (ADDED) | No compress driver when compress=False | compress=False → --format=qcow2 (no compress driver) | `tests/modules/backup/test_bitmap.py` | `test_no_compress_driver_when_compress_false` | bitmap-backup |

### Spec: `config-model` (MODIFIED)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| TargetConfig verify field | Default verification is metadata | verify not set → "metadata" | `tests/config/test_model.py` | `test_target_verify_default_metadata` | config-model |
| TargetConfig verify field | Explicit compare verification | verify="compare" → "compare" | `tests/config/test_model.py` | `test_target_verify_explicit_compare` | config-model |
| TargetConfig verify field | Deprecated hash treated as compare | verify="hash" → WARNING + "compare" | `tests/config/test_facade.py` | `test_verify_hash_deprecated_maps_to_compare` | config-model |
| TargetConfig verify field | Invalid verify value raises ConfigError | verify="invalid" → ConfigError | `tests/config/test_facade.py` | `test_verify_invalid_raises_config_error` | config-model |
| GlobalConfig full_verify_after_create | Default is check | not set → "check" | `tests/config/test_model.py` | `test_full_verify_after_create_defaults_check` | config-model |
| GlobalConfig full_verify_after_create | Deprecated hash treated as compare | full_verify_after_create="hash" → WARNING + "compare" | `tests/config/test_facade.py` | `test_full_verify_after_create_hash_deprecated_maps_to_compare` | config-model |

### Spec: `backup-hash-verification` (REMOVED)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| file_sha256 utility | REMOVED — entire hash.py deleted | N/A (no new test needed; delete `tests/utils/test_hash.py` if it exists) | — | — | — |

### Spec: `snapshot-provider` (MODIFIED)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| ExternalSnapshotProvider.create() | Successful snapshot creation | virsh succeeds → SnapshotResult with no content_hash field | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_success` (MODIFY: remove content_hash assertion) | snapshot-provider |
| ExternalSnapshotProvider.create() | Snapshot creation fails | virsh fails → SnapshotResult(success=False) with no content_hash | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_failure_no_content_hash` (RENAME from old test) | snapshot-provider |

### Spec: `nbd-dirty-block-transfer` (MODIFIED)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| Incremental output is backing-chained COW delta | qemu-img info shows backing chain (incremental) | Incremental completes → backing file: `<previous>` in qemu-img info | `tests/modules/backup/test_bitmap.py` | `test_incremental_output_has_backing_chain` | bitmap-backup |
| Incremental output is backing-chained COW delta | qemu-img info shows no backing file (FULL) | FULL completes → backing file: none | `tests/modules/backup/test_bitmap.py` | `test_full_output_no_backing_file` | bitmap-backup |
| Incremental output is backing-chained COW delta | Restore resolves bitmap chains unchanged | FULL + incremental chain → standard qcow2 backing-chain resolution works | `tests/integration/test_bitmap_integration.py` | `test_bitmap_chain_restore_resolves_correctly` | integration-nbd-unified |

### Spec: `result-types` (REMOVED)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| SnapshotResult content_hash field | REMOVED | `content_hash` field gone from SnapshotResult | `tests/models/test_results.py` | DELETE `test_snapshot_result_content_hash_defaults_none`, DELETE `test_snapshot_result_content_hash_set` | models-results |
| SnapshotInfo content_hash field | REMOVED | `content_hash` field gone from SnapshotInfo | `tests/models/test_results.py` | DELETE `test_snapshot_info_content_hash_defaults_none` | models-results |

### Spec: `backup-provider` (MODIFIED)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| Transfer missing via dirty bitmap | First backup — full NBD export (no prior checkpoint) | No prior checkpoint → unified engine: meta_contexts=["base:allocation"], zero_skip=True, standalone qcow2 | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_first_backup_full_export` | bitmap-backup |
| Transfer missing via dirty bitmap | Incremental backup — dirty blocks only | Prior checkpoint → unified engine: dirty+allocated, zero_skip=False, delta proportional to writes | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_incremental_dirty_blocks_only` | bitmap-backup |
| Transfer missing via dirty bitmap | Checkpoint rotation after successful transfer | Transfer succeeds + verify passes → successor checkpoint exists, older qsnap checkpoints deleted, exactly 1 remains | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_checkpoint_rotation_on_success` | bitmap-backup |
| Transfer missing via dirty bitmap | Transfer failure preserves prior checkpoint | Transfer fails → prior checkpoint NOT deleted, successor deleted best-effort, BackupResult(success=False), cleanups run | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_failure_preserves_prior_checkpoint` | bitmap-backup |
| Backup verification step | Metadata verification passes | verify="metadata" + structural checks pass → backup marked success | `tests/modules/backup/test_bitmap.py` | `test_verify_metadata_passes_backup_success` | bitmap-backup |
| Backup verification step | Compare verification passes | verify="compare" + qemu-img compare succeeds → backup marked success | `tests/modules/backup/test_bitmap.py` | `test_verify_compare_passes_backup_success` | bitmap-backup |
| Backup verification step | Verification failure produces error | Verification detects mismatch → BackupResult(success=False, error="verification failed: ...") | `tests/modules/backup/test_bitmap.py` | `test_verify_failure_produces_error_result` | bitmap-backup |
| Backup verification step | Deprecated verify values treated as compare | verify="hash" or "full" → WARNING + compare behavior | `tests/modules/backup/test_bitmap.py` | `test_verify_deprecated_hash_full_treated_as_compare` | bitmap-backup |

### Spec: `live-vm-full-backup` (REMOVED + MODIFIED)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| NBD full-export helper | REMOVED — `nbd_full_export()` deleted | N/A (all tests referencing `nbd_full_export` deleted or rewritten) | `tests/modules/backup/test_bitmap.py`, `tests/utils/test_nbd.py` | Multiple DELETEs (see Section 3) | bitmap-backup, nbd-utils |
| FULL backup requires running VM | Running VM triggers NBD-based FULL backup | VM running → unified NBD engine, no qemu-img convert | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_running_vm_unified_engine` | bitmap-backup |
| FULL backup requires running VM | Stopped VM fails with BackupResult error | VM stopped → virsh backup-begin fails → BackupResult(success=False), no fallback | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_stopped_vm_returns_error` | bitmap-backup |
| FULL backup requires running VM | Dotted VM name passed untruncated | vm_name="3.Projects_opencode" → virsh backup-begin --domain 3.Projects_opencode | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_dotted_vm_name_passed_untruncated` | bitmap-backup |
| FULL backup requires running VM | Core passes vm_config.name to create_full_backup | _should_create_bucket_full returns (True, level) → create_full_backup(vm_config.name, ...) called | `tests/core/test_pipeline.py` | `test_core_passes_vm_name_to_create_full_backup` | core-pipeline |
| Atomic FULL file creation via NBD | NBD FULL creates tmp then renames | Unified engine succeeds → data in .tmp → renamed to final name → BackupResult(path=<final>) | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_atomic_rename_tmp_to_final` | bitmap-backup |
| Atomic FULL file creation via NBD | NBD FULL failure leaves no final file | Unified engine fails → .tmp removed, no .qcow2 final file, BackupResult(success=False) | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_failure_removes_tmp` | bitmap-backup |

### Spec: `backup-full-verification` (MODIFIED)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| M3 — Content comparison tier | M3 triggered by compare mode | verify_mode="compare" + M1+M2 pass → qemu-img compare -q --force-share | `tests/modules/backup/test_full_verification.py` | `test_verify_full_backup_compare_match` (RENAME from hash_match) | bitmap-backup |
| M3 — Content comparison tier | Deprecated hash triggers compare | verify_mode="hash" → WARNING + M3 triggered (same as "compare") | `tests/modules/backup/test_full_verification.py` | `test_verify_full_backup_hash_deprecated_triggers_compare` | bitmap-backup |

---

## Section 2: Delegation Groups

Groups are non-overlapping by test file. Each test FILE belongs to EXACTLY ONE group for parallel @Mr.Tester execution.

### Group: `nbd-interface-contract`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_nbd.py` | Contract test parametrized over INbdClient impls — add `flush()`, `can_flush()` to method existence check | MODIFY |
| `tests/interfaces/test_backup_provider.py` | Contract test — remove `full_verify_before_rebase` from sig check | MODIFY |
| `tests/interfaces/test_snapshot_provider.py` | Contract test — remove `content_hash` from result shape assertion | MODIFY |

### Group: `config-model`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | TargetConfig.verify field values ("off"/"metadata"/"compare"), deprecated "hash"/"full" removed; GlobalConfig.full_verify_after_create values ("off"/"metadata"/"check"/"compare"), deprecated "hash" removed; GlobalConfig.full_verify_before_rebase retains "off"/"metadata" unchanged | MODIFY |
| `tests/config/test_facade.py` | Deprecation WARNINGs for "hash"/"full" verify values treated as "compare"; deprecation WARNING for "hash" in full_verify_after_create; invalid verify values raise ConfigError; compress-driver pre-flight check fixture | MODIFY |

### Group: `state-management`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/state/test_manager.py` | DELETE content_hash persistence tests; ADD test that new state files exclude content_hash; ADD test that old state files with content_hash load fine (field ignored) | MODIFY |
| `tests/mocks/test_mock_state.py` | DELETE `test_inmemory_state_manager_content_hash_persists` | MODIFY |

### Group: `models-results`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/models/test_results.py` | DELETE `test_snapshot_result_content_hash_defaults_none`, DELETE `test_snapshot_result_content_hash_set`, DELETE `test_snapshot_info_content_hash_defaults_none`; ADD tests that SnapshotResult/SnapshotInfo do NOT have content_hash field (AttributeError) | MODIFY |

### Group: `mocks`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/mocks/mock_modules.py` | Remove `content_hash="a"*64` from MockSnapshotProvider.create(); remove `full_verify_before_rebase` from MockBackupProvider/MockBitmapBackupProvider transfer_missing() signatures | MODIFY |
| `tests/mocks/mock_nbd.py` | Add `flush()`, `can_flush()`, `flush_count`, `connect_attempts` to MockNbdClient | MODIFY |
| `tests/mocks/test_mock_factory.py` | DELETE `test_mock_snapshot_provider_returns_content_hash` | MODIFY |

### Group: `snapshot-provider`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/snapshot/test_external.py` | DELETE `test_create_snapshot_returns_content_hash`, DELETE `test_create_snapshot_failure_content_hash_none`; MODIFY `test_create_snapshot_success` to remove content_hash assertion | MODIFY |

### Group: `bitmap-backup`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | ~25 tests DELETE or rewrite: remove ALL `qemu-img convert` expectations, remove ALL `nbd_full_export` spy/assertions, replace with unified `pread`/`pwrite` engine expectations; ADD flush/can_flush tests, zero-skip tests, compress-driver tests, atomic-rename tests; ADD deprecated-verify-value-as-compare tests | MODIFY (heavy) |
| `tests/modules/backup/test_bitmap_incremental.py` | Remove ALL `qemu-img convert` assertions ("No qemu-img convert on the incremental path" → keep but update message); verify mode references updated | MODIFY |
| `tests/modules/backup/test_full_verification.py` | RENAME tests: `test_verify_full_backup_hash_match` → `test_verify_full_backup_compare_match`, `test_verify_full_backup_hash_mismatch` → `test_verify_full_backup_compare_mismatch`, `test_verify_full_backup_hash_none_skips_m3` → `test_verify_full_backup_compare_none_skips_m3`; ADD deprecated-hash-WARNING test; update verify_mode strings in test calls | MODIFY |

### Group: `core-pipeline`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | MODIFY tests referencing `nbd_full_export` or `qemu-img convert` in backup path; ADD `test_core_passes_vm_name_to_create_full_backup`; KEEP all non-backup pipeline tests | MODIFY |
| `tests/core/test_full_verification_pipeline.py` | MODIFY all `content_hash="..."` → remove field; MODIFY `full_verify_before_rebase` threading tests to DELETE or re-scope (parameter removed from provider); MODIFY M3 tests: "hash"→"compare"; MODIFY verify mode references in Core-level M1/M2/M3 tests | MODIFY |
| `tests/core/test_validation.py` | ADD env validation test: trial `qemu-nbd --image-opts driver=compress` → hard-fail with actionable message when missing | MODIFY |
| `tests/core/test_bitmap_dependency.py` | MODIFY tests referencing `full_verify_before_rebase` → remove parameter | MODIFY |

### Group: `nbd-utils`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_nbd.py` | DELETE all `nbd_full_export` tests (class `TestNbdFullExportCheckpointKwarg`, class `TestCoreImportsNbdFromUtils` assertions for `nbd_full_export`); DELETE `test_nbd_public_functions_importable` assertion for `nbd_full_export`; KEEP `write_backup_xml`, `write_checkpoint_xml`, `is_vm_running`, `is_libvirt_new_enough` tests | MODIFY |
| `tests/utils/test_nbd_client.py` | ADD connect-retry tests (20 attempts, fresh handle, sleep); ADD flush/can_flush tests; MODIFY existing tests (connect-refused normalization, EOF normalization, chunked I/O, block-status extent parsing) to account for retry loop | MODIFY |
| `tests/utils/test_verification.py` | UPDATE import (verify_full_backup still importable); ADD deprecated-verify-value test | MODIFY |
| `tests/utils/test_verification_bitmap.py` | KEEP — barrier tests for incremental verification unchanged (no qemu-img convert in path) | KEEP |

### Group: `integration-nbd-unified`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_nbd_full_backup.py` | REWRITE: remove ALL `nbd_full_export()` references; use BitmapBackupProvider.create_full_backup() with unified engine; verify no qemu-img convert in log/history; verify backing-chain via qemu-img info; verify checkpoint creation; verify atomic rename | MODIFY |
| `tests/integration/test_bitmap_atomic.py` | KEEP — atomic checkpoint semantics unchanged (backup-begin + checkpoint XML still used) | KEEP |
| `tests/integration/test_bitmap_dirty_transfer.py` | KEEP — dirty block transfer semantics unchanged (pread/pwrite already used for incrementals) | KEEP |
| `tests/integration/test_bitmap_integration.py` | MODIFY — verify unified engine used for both FULL and incremental; verify no qemu-img convert | MODIFY |
| `tests/integration/test_verification_bitmap.py` | MODIFY — verify mode strings updated ("hash"/"full" → "compare") | MODIFY |
| `tests/integration/test_stall_detection.py` | KEEP — stall detection for long-running commands still relevant | KEEP |
| `tests/integration/test_stall_inprocess.py` | KEEP — in-process stall watchdog still used for pread/pwrite loop | KEEP |

### Group: `integration-new`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_unified_engine.py` | NEW — integration: create disposable VM, run FULL backup via unified engine, verify output is standalone qcow2 with no backing; run incremental, verify delta has backing chain; restore chain and boot restored VM; verify no qemu-img convert in shell history | NEW |
| `tests/integration/test_flush_connect.py` | NEW — integration: verify flush() called before qemu-nbd teardown; verify can_flush() check; verify connect-retry with real nbd (may need controllable nbd server start delay) | NEW |
| `tests/integration/test_compress_driver.py` | NEW — integration: create FULL with compress=True+zstd, verify compress driver used in qemu-nbd command line; verify output qcow2 has compression_type=zstd; verify data integrity | NEW |
| `tests/integration/test_zero_skip.py` | NEW — integration: create FULL with known zero regions; verify zero regions NOT written as qcow2 clusters (smaller actual-size than with zero-skip disabled) | NEW |

### Group: `fixtures-config`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/fixtures/configs/full_backup.toml` | ADD fixture with `verify = "compare"` and `full_verify_after_create = "compare"` | MODIFY |
| `tests/fixtures/configs/deprecated_fields.toml` | ADD `verify = "hash"` to exercise deprecation WARNING path | MODIFY |

---

## Section 3: Test Modifications

### Deletions (entire tests to be removed)

| # | File | Test Name | Reason |
|---|---|---|---|
| 1 | `tests/modules/backup/test_bitmap.py` | All tests with `mock_shell.expect("qemu-img convert")` in the `create_full_backup` path (~25 tests: lines ~169, 428, 454, 531, 589, 597, 629, 662, 697, 735, 756, 793, 824, 870, 931, 970, 983, 1058, 1097, 1124, 1217, 1281, 1314, 1538, 1591, 1642, 1693, 1765, 2127) | `qemu-img convert` no longer in data path. Replaced by unified `pread`/`pwrite` engine. |
| 2 | `tests/modules/backup/test_bitmap.py` | All tests patching/spying `nbd_full_export` (~10 tests: `test_create_full_backup_passes_checkpoint_name_to_nbd_full_export`, `test_create_full_backup_passes_compression_type_zstd`, `test_create_full_backup_passes_compression_type_zlib`, `test_nbd_full_export_uses_stall_detection`, `test_create_full_backup_tmp_and_atomic_rename`, `test_create_full_backup_stopped_vm_no_fallback_on_snapshot_file` — lines 882, 1538, 1583, 1633, 1685, 2117, etc.) | `nbd_full_export()` deleted entirely (design D2). |
| 3 | `tests/utils/test_nbd.py` | `TestNbdFullExportCheckpointKwarg` class and all its methods (line ~302+); `TestCoreImportsNbdFromUtils.test_core_imports_nbd_from_utils` assertions for `nbd_full_export` | `nbd_full_export()` deleted. `write_backup_xml`, `write_checkpoint_xml`, `is_vm_running`, `is_libvirt_new_enough` survive. |
| 4 | `tests/utils/test_nbd.py` | `test_nbd_public_functions_importable` — `assert callable(nbd_full_export)` assertion | `nbd_full_export()` deleted. |
| 5 | `tests/models/test_results.py` | `test_snapshot_result_content_hash_defaults_none` (line 185) | `content_hash` field removed from `SnapshotResult` (spec: result-types). |
| 6 | `tests/models/test_results.py` | `test_snapshot_result_content_hash_set` (line 197) | `content_hash` field removed from `SnapshotResult`. |
| 7 | `tests/models/test_results.py` | `test_snapshot_info_content_hash_defaults_none` (line 210) | `content_hash` field removed from `SnapshotInfo`. |
| 8 | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_returns_content_hash` (line 990) | `content_hash` computation removed from `ExternalSnapshotProvider.create()`. |
| 9 | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_failure_content_hash_none` (line 1030) | `content_hash` field no longer exists on `SnapshotResult`. Rename to generic failure-no-content_hash test. |
| 10 | `tests/state/test_manager.py` | `test_record_snapshot_with_content_hash_restored` (line 338) | `content_hash` no longer serialized. Replace with test that new state excludes field. |
| 11 | `tests/state/test_manager.py` | `test_snapshot_content_hash_persists_across_runs` (line 355) | `content_hash` no longer serialized. |
| 12 | `tests/mocks/test_mock_state.py` | `test_inmemory_state_manager_content_hash_persists` (line 107) | `content_hash` no longer in state. |
| 13 | `tests/mocks/test_mock_factory.py` | `test_mock_snapshot_provider_returns_content_hash` (line 148) | `content_hash` removed from SnapshotResult. |
| 14 | `tests/interfaces/test_snapshot_provider.py` | `test_snapshot_provider_create_returns_content_hash` (line 123) | `content_hash` removed from SnapshotResult. |
| 15 | `tests/modules/backup/test_full_verification.py` | Tests with `verify_mode="hash"` calls — `test_verify_full_backup_hash_match` (line 189), `test_verify_full_backup_hash_mismatch` (line 213), `test_verify_full_backup_hash_none_skips_m3` (line 239) | `"hash"` verify mode replaced by `"compare"`. Rename tests, keep logic (both use `qemu-img compare`). |
| 16 | `tests/core/test_full_verification_pipeline.py` | `full_verify_before_rebase` threading tests: `test_core_passes_full_verify_before_rebase_metadata_to_transfer_missing` (line 1405), `test_core_passes_full_verify_before_rebase_off_to_transfer_missing` (line 1441), `test_core_passes_full_verify_before_rebase_check_to_transfer_missing` (line 1477), `test_core_threads_full_verify_before_rebase_from_global_config_to_transfer_missing` (line 1515) | `full_verify_before_rebase` removed from `transfer_missing()` signature (design D4). Core-level full_verify_before_rebase survives. |
| 17 | `tests/core/test_bitmap_dependency.py` | `full_verify_before_rebase="metadata"` in kwargs — lines 147, 197, 219, 282 | Parameter removed from `transfer_missing()`; delete these calls. |
| 18 | `tests/interfaces/test_backup_provider.py` | `test_backup_provider_transfer_missing_signature_has_full_verify_before_rebase` or any test checking `full_verify_before_rebase` in kwargs | Parameter removed from `IBackupProvider.transfer_missing()`. |
| 19 | `tests/integration/test_nbd_full_backup.py` | Tests referencing `nbd_full_export()` directly (line 232 comment, line 255 assertion, line 422 comment) | `nbd_full_export()` deleted. |
| 20 | `tests/integration/test_verification_bitmap.py` | Tests using `"hash"` verify mode | `"hash"` → `"compare"`. |
| 21 | `tests/core/test_fork.py` | `qemu-img convert` references in fork path — NOT in scope for this change (fork uses direct qemu-img convert, not NBD) | KEEP — fork path unchanged by this change. |

### Modifications (existing tests to update, not delete)

| # | File | Change Description | Reason |
|---|---|---|---|
| 1 | `tests/modules/backup/test_bitmap.py` | All remaining tests (after deletions): replace `mock_shell.expect("qemu-img convert")` patterns with unified engine expectations: `MockNbdClient` connect + `_setup_incr_expectations`-style setup | FULL path now uses `pread`/`pwrite`, not `qemu-img convert`. |
| 2 | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_first_backup_*` tests — update to use unified engine, not `nbd_full_export` or `qemu-img convert` | Design D1: unified engine. |
| 3 | `tests/modules/backup/test_bitmap.py` | Tests that assert "No qemu-img convert on incremental path" (lines ~293, 1904, 2397) — KEEP these assertions but update context (they now apply to ALL paths, not just incremental) | No `qemu-img convert` anywhere. |
| 4 | `tests/modules/backup/test_bitmap_incremental.py` | Tests asserting "No qemu-img convert on incremental path" (lines ~255, 975, 1029) — KEEP, remove "incremental" qualifier | No `qemu-img convert` anywhere. |
| 5 | `tests/modules/backup/test_full_verification.py` | `test_verify_full_backup_hash_match` → rename to `test_verify_full_backup_compare_match`; all "hash"→"compare" in verify_mode strings; ADD deprecated-WARNING check for "hash" | Spec D5: "hash"/"full" → "compare". |
| 6 | `tests/mocks/mock_modules.py` | `MockSnapshotProvider.create()` — remove `content_hash="a"*64` from SnapshotResult construction | `content_hash` removed from SnapshotResult. |
| 7 | `tests/mocks/mock_modules.py` | `MockBackupProvider.transfer_missing()` — remove `full_verify_before_rebase` parameter | Design D4: dead plumbing removed. |
| 8 | `tests/mocks/mock_modules.py` | `MockBitmapBackupProvider.transfer_missing()` — remove `full_verify_before_rebase` parameter | Design D4. |
| 9 | `tests/mocks/mock_nbd.py` | `MockNbdClient` — add methods: `can_flush() -> bool`, `flush() -> NbdResult`, `flush_count`; add `connect_retries`/`connect_attempt` tracking | Design D7: flush; Design D8: connect-retry. |
| 10 | `tests/interfaces/test_nbd.py` | Add `"flush"`, `"can_flush"` to method-existence loop (line 35-43); add `can_flush()` return-type check; add `flush()` safe-to-call-without-connect test | Design D7: new INbdClient methods. |
| 11 | `tests/interfaces/test_backup_provider.py` | Update `transfer_missing()` call in `test_backup_provider_transfer_missing_signature_has_full_verify_before_rebase` — remove `full_verify_before_rebase` kwarg | Design D4. |
| 12 | `tests/interfaces/test_snapshot_provider.py` | Remove `test_snapshot_provider_create_returns_content_hash` or rewrite to verify content_hash is absent | Field removed from SnapshotResult. |
| 13 | `tests/config/test_model.py` | Update TargetConfig.verify allowed-values assertions: remove "hash"/"full", add "compare"; update GlobalConfig.full_verify_after_create: remove "hash", add "compare" | Spec D5: unified to "compare". |
| 14 | `tests/config/test_facade.py` | ADD deprecation WARNING tests: verify="hash" → WARNING + mapped to "compare"; verify="full" → WARNING + mapped to "compare"; full_verify_after_create="hash" → WARNING + mapped to "compare"; ADD compress driver pre-flight check test | Spec D5 + D6. |
| 15 | `tests/config/test_model.py` | `test_global_config_defaults` — `full_verify_after_create` default remains "check" (unchanged, but verify) | Spec D5. |
| 16 | `tests/config/test_facade.py` | `test_facade_parses_full_verify_before_rebase_off` (line 472) — `full_verify_before_rebase` allows "off"/"metadata" only, unchanged; verify "hash" still raises ConfigError | Spec: full_verify_before_rebase unchanged. |
| 17 | `tests/state/test_manager.py` | `test_new_state_file_excludes_content_hash` — NEW: create SnapshotInfo (no content_hash field), record it, read JSON file, assert "content_hash" key not present | Spec: state serialization excludes content_hash. |
| 18 | `tests/state/test_manager.py` | `test_old_state_content_hash_ignored_on_load` — NEW: manually write JSON with "content_hash" key, load via JsonStateManager, assert no error, snapshot loaded without content_hash | Spec: old state files load fine. |
| 19 | `tests/core/test_full_verification_pipeline.py` | `_record_snap` helper (line 33) — remove `content_hash="a"*64`; all SnapshotInfo fixtures — remove `content_hash` field | Field removed. |
| 20 | `tests/core/test_full_verification_pipeline.py` | `test_m3_hash_content_comparison` (line ~105) and related — update verify_mode from "hash"→"compare"; add deprecated WARNING test | Spec D5. |
| 21 | `tests/core/test_validation.py` | ADD `test_validate_compress_driver_available` — mock qemu-nbd --image-opts compress driver test; ADD `test_validate_compress_driver_missing_fails_hard` | Risk: compress-driver unavailable on some QEMU builds. |
| 22 | `tests/utils/test_nbd_client.py` | ADD `test_connect_retry_20_attempts_fresh_handle`; ADD `test_connect_retry_exhausted_returns_failure`; ADD `test_can_flush_delegates_to_nbd`; ADD `test_flush_delegates_to_nbd`; ADD `test_flush_safe_when_can_flush_false` | Design D7 + D8. |
| 23 | `tests/integration/test_nbd_full_backup.py` | REWRITE all tests: replace `nbd_full_export()` path with `BitmapBackupProvider.create_full_backup()` unified engine; assert no `qemu-img convert` in shell history | Design D1 + D2. |
| 24 | `tests/integration/test_bitmap_integration.py` | Update commentary: "qemu-img convert" references → "unified NBD engine"; update shell history assertions | Design D1. |

### New Tests (to be authored)

| # | File | Test Name | What It Covers |
|---|---|---|---|
| 1 | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_unified_engine_no_qemu_img_convert` | FULL backup uses pread/pwrite, not qemu-img convert |
| 2 | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_unified_engine_meta_contexts` | FULL engine connects with only ["base:allocation"] |
| 3 | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_unified_engine_zero_skip` | zero_skip=True passed to unified engine for FULL |
| 4 | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_incremental_unified_engine_no_convert` | Incremental uses pread/pwrite, no qemu-img convert |
| 5 | `tests/modules/backup/test_bitmap.py` | `test_transfer_flush_called_before_disconnect` | dst.flush() called after transfer, before disconnect |
| 6 | `tests/modules/backup/test_bitmap.py` | `test_transfer_flush_skipped_when_unsupported` | can_flush()=False → flush() skipped |
| 7 | `tests/modules/backup/test_bitmap.py` | `test_zero_skip_all_zero_chunk_no_pwrite` | All-zero pread → no pwrite |
| 8 | `tests/modules/backup/test_bitmap.py` | `test_zero_skip_nonzero_chunk_pwrite_called` | Non-zero pread → pwrite called |
| 9 | `tests/modules/backup/test_bitmap.py` | `test_zero_skip_not_applied_to_incrementals` | zero_skip=False → all extents written |
| 10 | `tests/modules/backup/test_bitmap.py` | `test_compress_driver_qemu_nbd_image_opts` | compress=True → qemu-nbd started with --image-opts driver=compress |
| 11 | `tests/modules/backup/test_bitmap.py` | `test_no_compress_driver_when_compress_false` | compress=False → --format=qcow2 |
| 12 | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_atomic_rename_tmp_to_final` | .tmp → final rename on success |
| 13 | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_failure_removes_tmp` | .tmp removed on failure, no final file |
| 14 | `tests/modules/backup/test_bitmap.py` | `test_verify_deprecated_hash_full_treated_as_compare` | verify="hash"/"full" → WARNING + compare behavior |
| 15 | `tests/utils/test_nbd_client.py` | `test_connect_retry_20_attempts_fresh_handle` | LibnbdClient.connect retries 20x with fresh nbd.NBD() |
| 16 | `tests/utils/test_nbd_client.py` | `test_connect_retry_exhausted_returns_failure` | After 20 failures → NbdResult(success=False) |
| 17 | `tests/utils/test_nbd_client.py` | `test_can_flush_delegates_to_nbd` | can_flush() calls nbd.can_flush() |
| 18 | `tests/utils/test_nbd_client.py` | `test_flush_delegates_to_nbd` | flush() calls nbd.flush() |
| 19 | `tests/integration/test_unified_engine.py` | `test_full_backup_via_unified_engine` | Real VM: create_full_backup → verify standalone qcow2 |
| 20 | `tests/integration/test_unified_engine.py` | `test_incremental_via_unified_engine` | Real VM: write data, incremental backup → verify chain |
| 21 | `tests/integration/test_unified_engine.py` | `test_no_qemu_img_convert_in_shell_history` | Assert no qemu-img convert anywhere in backup path |
| 22 | `tests/integration/test_flush_connect.py` | `test_flush_called_before_teardown` | Verify flush happens before qemu-nbd teardown |
| 23 | `tests/integration/test_compress_driver.py` | `test_compress_zstd_full_backup` | compress=True+zstd → compressed qcow2 output |
| 24 | `tests/integration/test_zero_skip.py` | `test_zero_skip_reduces_actual_size` | Zero regions produce sparser qcow2 with zero_skip=True |
| 25 | `tests/config/test_facade.py` | `test_verify_hash_deprecated_warning` | verify="hash" → deprecation WARNING logged |
| 26 | `tests/config/test_facade.py` | `test_verify_full_deprecated_warning` | verify="full" → deprecation WARNING logged |
| 27 | `tests/config/test_facade.py` | `test_full_verify_after_create_hash_deprecated_warning` | full_verify_after_create="hash" → WARNING |
| 28 | `tests/state/test_manager.py` | `test_new_state_file_excludes_content_hash` | JSON written without content_hash key |
| 29 | `tests/state/test_manager.py` | `test_old_state_content_hash_ignored_on_load` | Old state with content_hash loads silently |
| 30 | `tests/core/test_validation.py` | `test_validate_compress_driver_available` | Compression pre-flight check passes |
| 31 | `tests/core/test_validation.py` | `test_validate_compress_driver_missing_fails_hard` | Missing compress driver → actionable error |

---

## Section 4: Risks & Edge Cases

From `design.md` Risks table, plus additional edge cases discovered during analysis.

| # | Risk | Severity | Edge Cases Needing Test Coverage | Test Location |
|---|---|---|---|---|
| R1 | **FULL speed: single-threaded pread/pwrite slower than qemu-img convert** | MEDIUM | Acceptance: benchmark FULL 40–100 GB to measure throughput; assert within acceptable range; future aio_pread/pwrite pipelining (deferred) | `tests/integration/test_unified_engine.py` — `test_full_backup_throughput_acceptable` |
| R2 | **25+ tests expect qemu-img convert in shell history** | HIGH | Covered by Section 3 deletions — all qemu-img convert expectations removed or replaced with pread/pwrite assertions | `tests/modules/backup/test_bitmap.py` (DELETEs) |
| R3 | **Compress-driver unavailable on specific QEMU build** | LOW | Env validation: trial `qemu-nbd --image-opts driver=compress` → hard-fail with actionable message; unit test: MockShell returns failure → Core reports actionable error; integration: skip if QEMU < 4.1 | `tests/core/test_validation.py` — `test_validate_compress_driver_*` |
| R4 | **Zero-skip masks real data** | LOW | Unit: all-zero chunk → no pwrite; mixed (zero+non-zero) → pwrite with full data; integration: known-zero regions produce sparser qcow2; never applied to incrementals (backing-chained corruption) | `tests/modules/backup/test_bitmap.py` — `test_zero_skip_*`; `tests/integration/test_zero_skip.py` |
| R5 | **Forgotten flush → tail data loss** | MEDIUM | Unit: flush() in ABC, dst.flush() called before dst.disconnect(), qemu-nbd terminated only after flush; can_flush()=False → safe skip; integration: kill qemu-nbd without flush → expect data integrity failure vs flush-then-kill → success | `tests/modules/backup/test_bitmap.py` — `test_transfer_flush_*`; `tests/integration/test_flush_connect.py` |
| R6 | **content_hash removal breaks state file schema** | LOW | Unit: new state files omit content_hash; old state files with content_hash load silently; JsonStateManager uses `if "content_hash" in d` guard | `tests/state/test_manager.py` — `test_new_state_file_excludes_content_hash`, `test_old_state_content_hash_ignored_on_load` |
| R7 | **Restore path depends on nbd_full_export()** | UNKNOWN | Pre-implementation verification: grep Core/fork for `nbd_full_export` callers; if found, update restore path; add dedicated restore test verifying unified engine path | `tests/core/test_pipeline.py`, `tests/e2e/test_restore.py` |
| R8 | **"hash"/"full" config values in user configs** | LOW | Deprecation WARNING + treat as "compare"; unit: ConfigFacade logs WARNING; integration: use fixture with verify="hash" → backup succeeds with "compare" behavior | `tests/config/test_facade.py` — `test_verify_*_deprecated_warning` |
| R9 | **Connect-retry race with backup-begin async NBD server start** | MEDIUM | Unit: 20 retries, 1s sleep, fresh handle per attempt; exhaustion → NbdResult failure; integration: start backup-begin, connect simultaneously → retries succeed | `tests/utils/test_nbd_client.py` — `test_connect_retry_*`; `tests/integration/test_flush_connect.py` |
| R10 | **MockShell/IShell stall detection: pread/pwrite loop uses in-process stall watchdog, not run_with_stall_detection** | LOW | Unit: in-process stall watchdog still exercises stall detection; verify run_with_stall_detection survives for future subprocess-based paths | `tests/integration/test_stall_inprocess.py` (KEEP); `tests/utils/test_shell.py` (KEEP) |
| R11 | **Compress-driver for incrementals not in scope — accidental application** | LOW | Unit: assert compress=False passed when incremental path is taken; integration: verify --format=qcow2 (no compress driver) for incremental backup | `tests/modules/backup/test_bitmap.py` — `test_no_compress_driver_when_compress_false` |
| R12 | **Atomic rename: .tmp exists from prior failed run** | LOW | Edge: previous crashed run left .tmp file; create_full_backup should delete stale .tmp before starting; rename should overwrite if collision | `tests/modules/backup/test_bitmap.py` — `test_create_full_backup_cleans_stale_tmp` |
| R13 | **Dotted VM name with dots in name (e.g., "3.Projects_opencode")** | LOW | Already tested in existing test; ensure create_full_backup(vm_name=str, ...) receives full untruncated name | `tests/modules/backup/test_bitmap.py` — `test_create_full_backup_dotted_vm_name_passed_untruncated` (MODIFY, not DELETE) |
| R14 | **Meta-context negotiation: base:allocation + dirty-bitmap for incremental** | MEDIUM | Unit: incremental connects with exactly ["base:allocation", "qemu:dirty-bitmap:backup-<disk>"]; FULL connects with exactly ["base:allocation"] only; no excess contexts | `tests/modules/backup/test_bitmap.py` — `test_transfer_missing_meta_contexts_full`, `test_transfer_missing_meta_contexts_incremental` |
| R15 | **Block status extent filtering: allocated-only for FULL vs dirty∩allocated for incremental** | MEDIUM | Unit: FULL filters to allocated-only extents; incremental intersects allocated + dirty extents; gap/hole extents skipped in both cases | `tests/modules/backup/test_bitmap.py` — extent filtering tests |
