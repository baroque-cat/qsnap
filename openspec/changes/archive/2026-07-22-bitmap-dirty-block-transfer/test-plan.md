# QA Strategy & Test Plan

## Coverage Map

### nbd-dirty-block-transfer (new capability — 7 requirements, 17 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| nbd-dirty-block-transfer | INbdClient abstraction | Connection failure returns result object | `tests/utils/test_nbd_client.py` | `test_connect_refused_returns_error_result` | nbd-client-unit |
| nbd-dirty-block-transfer | INbdClient abstraction | Read error normalized for retry | `tests/utils/test_nbd_client.py` | `test_pread_eof_normalized_for_retry` | nbd-client-unit |
| nbd-dirty-block-transfer | LibnbdClient production impl | Lazy import keeps qsnap runnable without libnbd | `tests/utils/test_nbd_client.py` | `test_lazy_import_success_without_package` | nbd-client-unit |
| nbd-dirty-block-transfer | LibnbdClient production impl | Missing package yields actionable error | `tests/utils/test_nbd_client.py` | `test_missing_package_returns_actionable_error` | nbd-client-unit |
| nbd-dirty-block-transfer | LibnbdClient production impl | Large read is chunked | `tests/utils/test_nbd_client.py` | `test_large_read_chunked_to_max_request_size` | nbd-client-unit |
| nbd-dirty-block-transfer | Pure extent-processing functions | Consecutive same-kind extents are unified | `tests/utils/test_extents.py` | `test_unify_adjacent_dirty_extents` | extents-unit |
| nbd-dirty-block-transfer | Pure extent-processing functions | Dirty-but-unallocated regions are filtered | `tests/utils/test_extents.py` | `test_overlap_filters_unallocated_regions` | extents-unit |
| nbd-dirty-block-transfer | Dirty-block copy loop | Incremental copies only dirty blocks | `tests/modules/backup/test_bitmap_incremental.py` | `test_copy_loop_reads_only_dirty_extents` | bitmap-provider-unit |
| nbd-dirty-block-transfer | Dirty-block copy loop | First incremental chains to the FULL | `tests/modules/backup/test_bitmap_incremental.py` | `test_first_incremental_backing_is_full` | bitmap-provider-unit |
| nbd-dirty-block-transfer | Dirty-block copy loop | Previous backup vanished — retryable failure | `tests/modules/backup/test_bitmap_incremental.py` | `test_previous_backup_vanished_retryable_failure` | bitmap-provider-unit |
| nbd-dirty-block-transfer | Write-side lifecycle crash-safe | Failure mid-copy cleans everything | `tests/modules/backup/test_bitmap_incremental.py` | `test_mid_copy_failure_cleans_temp_qemu_nbd_and_socket` | bitmap-provider-unit |
| nbd-dirty-block-transfer | Write-side lifecycle crash-safe | Successful transfer leaves no artifacts | `tests/modules/backup/test_bitmap_incremental.py` | `test_successful_transfer_no_tmp_or_socket_remain` | bitmap-provider-unit |
| nbd-dirty-block-transfer | In-process stall watchdog | Stall aborts the transfer | `tests/modules/backup/test_bitmap_incremental.py` | `test_stall_watchdog_aborts_with_correct_error_string` | bitmap-provider-unit |
| nbd-dirty-block-transfer | In-process stall watchdog | Slow but progressing transfer completes | `tests/modules/backup/test_bitmap_incremental.py` | `test_slow_progressing_loop_not_killed` | bitmap-provider-unit |
| nbd-dirty-block-transfer | Incremental output backing-chained COW delta | qemu-img info shows the backing chain | `tests/modules/backup/test_bitmap_incremental.py` | `test_qemu_img_info_shows_backing_filename` | bitmap-provider-unit |
| nbd-dirty-block-transfer | Incremental output backing-chained COW delta | Restore resolves bitmap chains unchanged | `tests/modules/backup/test_bitmap_incremental.py` | `test_restore_chain_resolved_without_bitmap_specific_logic` | bitmap-provider-unit |
| design-risk | INbdClient contract | All implementors pass contract test | `tests/interfaces/test_nbd.py` | `test_nbd_client_contract_parametrized` | nbd-client-unit |
| design-risk | MockNbdClient correctness | Mock passes isinstance(INbdClient) | `tests/interfaces/test_nbd.py` | `test_mock_nbd_client_is_inbdclient` | nbd-client-unit |

### nbd-bitmap-backup (delta spec — 5 requirements, 13 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| nbd-bitmap-backup | NBD pull-model backup (MODIFIED) | First backup — full pull via NBD with atomic checkpoint | `tests/modules/backup/test_bitmap.py` | `test_no_checkpoints_triggers_full_export` | bitmap-provider-unit |
| nbd-bitmap-backup | NBD pull-model backup (MODIFIED) | Incremental backup — dirty blocks via NBD checkpoint | `tests/modules/backup/test_bitmap_incremental.py` | `test_incremental_uses_inbd_client_copy_loop` | bitmap-provider-unit |
| nbd-bitmap-backup | NBD pull-model backup (MODIFIED) | Socket cleanup on success | `tests/modules/backup/test_bitmap.py` | `test_socket_cleanup_on_success` | bitmap-provider-unit |
| nbd-bitmap-backup | NBD pull-model backup (MODIFIED) | Socket cleanup on failure | `tests/modules/backup/test_bitmap.py` | `test_socket_cleanup_on_failure` | bitmap-provider-unit |
| nbd-bitmap-backup | Checkpoint rotation (MODIFIED) | Successful incremental rotates checkpoints | `tests/modules/backup/test_bitmap.py` | `test_atomic_rotation_deletes_older_after_success` | bitmap-provider-unit |
| nbd-bitmap-backup | Checkpoint rotation (MODIFIED) | Export failure preserves prior, removes successor | `tests/modules/backup/test_bitmap.py` | `test_transfer_failure_preserves_checkpoint` | bitmap-provider-unit |
| nbd-bitmap-backup | Checkpoint rotation (MODIFIED) | checkpoint-delete failure is non-fatal | `tests/modules/backup/test_bitmap.py` | `test_checkpoint_delete_failure_non_fatal` | bitmap-provider-unit |
| nbd-bitmap-backup | First incremental after FULL (MODIFIED) | Writes during FULL export appear in the first incremental | `tests/integration/test_bitmap_atomic.py` | `test_int_writes_during_full_appear_in_incremental` | integration-bitmap |
| nbd-bitmap-backup | First incremental after FULL (MODIFIED) | No writes since FULL — minimal incremental | `tests/integration/test_bitmap_atomic.py` | `test_int_no_writes_minimal_incremental` | integration-bitmap |
| nbd-bitmap-backup | Verification (ADDED) | Delta proportional to dirtied data passes | `tests/utils/test_verification_bitmap.py` | `test_delta_within_barrier_passes` | verification-unit |
| nbd-bitmap-backup | Verification (ADDED) | Full-size incremental fails the barrier | `tests/utils/test_verification_bitmap.py` | `test_full_size_incremental_fails_barrier` | verification-unit |
| nbd-bitmap-backup | Verification (ADDED) | Wrong backing file fails verification | `tests/utils/test_verification_bitmap.py` | `test_wrong_backing_file_fails_verification` | verification-unit |
| nbd-bitmap-backup | Core records dependency (ADDED) | Bitmap incremental registered as dependent | `tests/core/test_bitmap_dependency.py` | `test_bitmap_incremental_registers_dependency` | core-wiring-unit |
| nbd-bitmap-backup | Core records dependency (ADDED) | Failed transfer records nothing | `tests/core/test_bitmap_dependency.py` | `test_failed_transfer_records_no_dependency` | core-wiring-unit |

### stall-detection (delta spec — 1 requirement, 3 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| stall-detection | In-process stall watchdog (ADDED) | Watchdog aborts stalled copy loop | `tests/integration/test_stall_inprocess.py` | `test_int_watchdog_aborts_stalled_loop` | integration-bitmap |
| stall-detection | In-process stall watchdog (ADDED) | Watchdog disabled at zero timeout | `tests/modules/backup/test_bitmap_incremental.py` | `test_zero_stall_timeout_disables_watchdog` | bitmap-provider-unit |
| stall-detection | In-process stall watchdog (ADDED) | Subprocess transfers unchanged | `tests/integration/test_stall_detection.py` | `test_stall_detection_kills_hung_convert` | integration-bitmap |

### env-validation (delta spec — 1 requirement, 3 scenarios)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| env-validation | libnbd availability check (ADDED) | Bitmap mode with libnbd installed | `tests/integration/test_env_validation.py` | `test_bitmap_mode_with_libnbd_installed_passes` | integration-bitmap |
| env-validation | libnbd availability check (ADDED) | Bitmap mode without libnbd — hard failure | `tests/modules/backup/test_bitmap_incremental.py` | `test_missing_libnbd_fails_factory_construction` | bitmap-provider-unit |
| env-validation | libnbd availability check (ADDED) | No bitmap targets — check skipped | `tests/integration/test_env_validation.py` | `test_no_bitmap_targets_skips_libnbd_check` | integration-bitmap |

### design-risk (additional from design.md Risks R1-R7, T1)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| design-risk | D3/R2: Previous backup race | Existence check before create prevents race | `tests/modules/backup/test_bitmap_incremental.py` | `test_previous_existence_rechecked_before_create` | bitmap-provider-unit |
| design-risk | R3: qemu-nbd process leak | No orphaned qemu-nbd after any outcome | `tests/integration/test_bitmap_dirty_transfer.py` | `test_int_no_qemu_nbd_orphan_after_failure` | integration-bitmap |
| design-risk | R4: libnbd missing | Factory hard-fails for bitmap mode without libnbd | `tests/factory/test_default.py` | `test_factory_bitmap_mode_without_libnbd_raises` | core-wiring-unit |
| design-risk | R5: Regression barrier false positives | Slack absorbs qcow2 metadata | `tests/utils/test_verification_bitmap.py` | `test_barrier_slack_absorbs_qcow2_metadata` | verification-unit |
| design-risk | R6: Live-VM drift | qemu-img compare with --force-share logs warning | `tests/utils/test_verification_bitmap.py` | `test_hash_tier_logs_live_source_warning` | verification-unit |
| design-risk | R7: libvirt < 7.2 gate | Factory gates unchanged post-refactor | `tests/factory/test_default.py` | `test_factory_libvirt_7_1_falls_back` | core-wiring-unit |
| design-risk | T1: Incrementals lose compression | Delta is uncompressed, compress field ignored for bitmap | `tests/modules/backup/test_bitmap_incremental.py` | `test_bitmap_incremental_ignores_compress_setting` | bitmap-provider-unit |

---

## Delegation Groups

### extents-unit

**Scope:** Pure functions in `qsnap/utils/extents.py` — `unify_extents`, `overlap_with_allocation`. No I/O, no mocks. Deterministic.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_extents.py` | unify_extents: merge adjacent same-kind extents, no-ops for single extent, empty input; overlap_with_allocation: filters unallocated sub-ranges, preserves fully-allocated dirty, handles zero-overlap, handles partial overlap on boundaries | NEW |

### nbd-client-unit

**Scope:** `INbdClient` ABC in `qsnap/interfaces/nbd.py`, `LibnbdClient` in `qsnap/utils/nbd_client.py`, `MockNbdClient` in `tests/mocks/mock_nbd.py`. Contract + unit tests.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_nbd.py` | Contract: mock and libnbd implementors pass isinstance; all methods return correct types; MockNbdClient is instantiable and configurable | NEW |
| `tests/utils/test_nbd_client.py` | Lazy import success; missing package actionable error; connection refused returns error result; pread EOF normalized; large read chunked to max_request_size; block_status returns extent list; pwrite succeeds; disconnect safe on unconnected | NEW |
| `tests/mocks/mock_nbd.py` | MockNbdClient implementing INbdClient; configurable block_status/pread/pwrite responses; tracks call history | NEW |

### bitmap-provider-unit

**Scope:** `BitmapBackupProvider` incremental transfer path — copy loop, qemu-nbd write side, stall watchdog, verification integration. Uses MockShell + MockNbdClient.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap_incremental.py` | Copy loop reads only dirty extents from MockNbdClient; first incremental qemu-img create chains to FULL backing; previous backup vanished returns retryable error; qemu-nbd cleanup on failure; qemu-img info shows backing filename; stall watchdog aborts with exact error string; slow-progress loop not killed; zero stall_timeout disables watchdog; bitmap incremental ignores compress; missing libnbd fails factory construction; full-size verify failure triggers cleanup | NEW |
| `tests/modules/backup/test_bitmap.py` | MODIFY: `test_transfer_failure_preserves_checkpoint`, `test_transfer_failure_deletes_partial_file`, `test_bitmap_verify_failure_deletes_file`, `test_atomic_incremental_passes_checkpoint_xml_and_incremental`, `test_atomic_rotation_deletes_older_after_success`, `test_checkpoint_delete_failure_non_fatal`, `test_transfer_missing_collision_successor_differs_from_prior`, `test_checkpoint_cleanup_after_successful_transfer`, 3 constructor tests (add `nbd=MockNbdClient()`). DELETE: 9 obsolete tests (see Test Modifications). | MIXED |

### verification-unit

**Scope:** `verify_bitmap_incremental()` in `qsnap/utils/verification.py`. Tested with MockShell for unit, real qcow2 for integration.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_verification_bitmap.py` | Metadata: format/qcow2, virtual-size match, backing-filename match passes; wrong backing-filename fails; wrong format fails; size-mismatch fails; Regression barrier: actual-size ≤ dirty_bytes×2+64MiB passes; actual-size > barrier fails; barrier slack absorbs metadata; hash-tier: compare passes/fails; full-tier: compare passes/fails; live-source WARNING on hash/full tiers | NEW |
| `tests/integration/test_verification_bitmap.py` | Real qcow2: create FULL+delta chain; verify_bitmap_incremental metadata passes; hash tier with known content; full tier compare across chain; barrier breach detected on oversized delta | NEW |

### core-wiring-unit

**Scope:** `DefaultFactory` wiring of `LibnbdClient` → `BitmapBackupProvider`; `Core` recording `record_incremental_dependency` for bitmap mode.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/factory/test_default.py` | MODIFY: `test_factory_selects_bitmap_provider_for_bitmap_mode`, `test_factory_bitmap_mode_new_libvirt_returns_bitmap`, `test_factory_passes_state_to_bitmap_provider` (add nbd assertion); NEW: `test_factory_bitmap_mode_without_libnbd_raises_actionable_error`, `test_factory_libvirt_7_1_falls_back` | MODIFY + NEW |
| `tests/core/test_bitmap_dependency.py` | Core calls record_incremental_dependency after verified bitmap transfer; failed transfer records nothing; dependency visible in check --state | NEW |

### integration-bitmap

**Scope:** Real libvirt/QEMU integration tests. Use `test_vm` fixture from `conftest.py`. Marked `@pytest.mark.integration`. Skip when libnbd not importable.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_bitmap_dirty_transfer.py` | Full pipeline: boot VM, dd 10 MiB inside guest, run FULL via create_full_backup, run bitmap incremental via transfer_missing, assert delta actual-size bounded by dirty_bytes×2+slack; verify backing-filename chains to FULL; verify qemu-nbd process not orphaned after success; verify no .tmp/write-socket remain | NEW |
| `tests/integration/test_stall_inprocess.py` | Simulated stalled NBD read + in-process watchdog kills loop with correct error string | NEW |
| `tests/integration/test_env_validation.py` | Bitmap mode with libnbd installed → validation passes; no bitmap targets → check skipped | NEW |
| `tests/integration/test_bitmap_atomic.py` | MODIFY: `test_int_writes_during_full_appear_in_incremental` (dirty-barrier assertion), `test_int_export_failure_deletes_successor_preserves_prior` (new failure injection) | MODIFY |
| `tests/integration/test_bitmap_integration.py` | MODIFY: `test_int_nbd_incremental_with_compression` (clarify FULL vs incremental), `test_int_incremental_is_smaller_than_full` (strengthen to dirty-barrier) | MODIFY |
| `tests/integration/test_verification.py` | KEEP (file-copy verify_backup; unchanged) | KEEP |

### test-removal

**Scope:** DELETE-only. No new code. Tests rendered obsolete by D6 (compression on incrementals removed) or D7 (qemu-img convert replaced for incrementals).

| Test File | Test Name | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | `test_incremental_backup_dirty_blocks_via_nbd` | DELETE |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_incremental_dirty_blocks_via_nbd` | DELETE |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_incremental_nbd_with_compression` | DELETE |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_incremental_nbd_without_compression` | DELETE |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_compress_metadata_verification_passes` | DELETE |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_compress_full_verification_passes` | DELETE |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_transfer_with_zstd_compression` | DELETE |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_transfer_with_zlib_compression` | DELETE |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_transfer_uses_stall_detection` | DELETE |

---

## Test Modifications

### DELETEs (obsolete by design — D6 compression removal, D7 convert→copy-loop)

| File | Change | Reason |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | DELETE `test_incremental_backup_dirty_blocks_via_nbd` | Asserts `qemu-img convert` in incremental path; superseded by `test_incremental_uses_inbd_client_copy_loop`. (D7) |
| `tests/modules/backup/test_bitmap.py` | DELETE `test_bitmap_incremental_dirty_blocks_via_nbd` | Same: asserts convert command, compression flags, domjobabort ordering. (D7) |
| `tests/modules/backup/test_bitmap.py` | DELETE `test_bitmap_incremental_nbd_with_compression` | Compression on incrementals removed. (D6) |
| `tests/modules/backup/test_bitmap.py` | DELETE `test_bitmap_incremental_nbd_without_compression` | Incremental path no longer uses convert at all. (D7) |
| `tests/modules/backup/test_bitmap.py` | DELETE `test_bitmap_compress_metadata_verification_passes` | Compression + old verification path removed. (D6, D5) |
| `tests/modules/backup/test_bitmap.py` | DELETE `test_bitmap_compress_full_verification_passes` | Compression on incrementals removed. (D6) |
| `tests/modules/backup/test_bitmap.py` | DELETE `test_bitmap_transfer_with_zstd_compression` | `compression_type` kwarg on `transfer_missing` removed for bitmap mode; compression is FULL-only via `create_full_backup`. (D6) |
| `tests/modules/backup/test_bitmap.py` | DELETE `test_bitmap_transfer_with_zlib_compression` | Same rationale. (D6) |
| `tests/modules/backup/test_bitmap.py` | DELETE `test_bitmap_transfer_uses_stall_detection` | Shell-level stall detection replaced by in-process watchdog for the incremental path. FULL-path stall test `test_nbd_full_export_uses_stall_detection` is KEPT. (D4) |

### MODIFYs (same scenario, new mechanics)

| File | Change | Reason |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | `test_transfer_failure_preserves_checkpoint`: replace convert-failure mock with MockNbdClient pread failure; remove convert/compression assertions; keep checkpoint-rotation assertions | D7, D6 |
| `tests/modules/backup/test_bitmap.py` | `test_checkpoint_cleanup_after_successful_transfer`: remove convert assertions; add MockNbdClient expectations; keep rotation assertions | D7 |
| `tests/modules/backup/test_bitmap.py` | `test_transfer_failure_deletes_partial_file`: replace convert failure with copy-loop failure; verify .tmp removal and successor checkpoint best-effort delete | D7 |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_verify_failure_deletes_file`: replace old verify failure with `verify_bitmap_incremental` failure; keep cleanup assertions | D5 |
| `tests/modules/backup/test_bitmap.py` | `test_atomic_incremental_passes_checkpoint_xml_and_incremental`: remove convert assertions; add MockNbdClient expectations; keep backup-begin XML assertions | D7 |
| `tests/modules/backup/test_bitmap.py` | `test_atomic_rotation_deletes_older_after_success`: remove convert assertions; add MockNbdClient expectations | D7 |
| `tests/modules/backup/test_bitmap.py` | `test_checkpoint_delete_failure_non_fatal`: remove convert assertions; keep non-fatal warning assertion | D7 |
| `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_collision_successor_differs_from_prior`: replace convert assertions with MockNbdClient expectations; keep collision-bump assertions | D7 |
| `tests/modules/backup/test_bitmap.py` | 3 constructor tests (`test_constructor_accepts_ishell_and_implements_abc`, `test_constructor_accepts_state_manager`, `test_constructor_works_without_state_manager`): add `nbd=MockNbdClient()` | D1 |
| `tests/factory/test_default.py` | 3 bitmap factory tests: add assertion that provider's nbd client is injected | D1 |
| `tests/interfaces/test_backup_provider.py` | 5 parametrized contract tests: add `nbd=MockNbdClient()` to BitmapBackupProvider init_kwargs | D1 |
| `tests/integration/test_bitmap_atomic.py` | `test_int_writes_during_full_appear_in_incremental`: replace `incr_size > 0` with dirty-barrier assertion | D5 |
| `tests/integration/test_bitmap_atomic.py` | `test_int_export_failure_deletes_successor_preserves_prior`: new failure injection (kill qemu-nbd mid-transfer instead of blocking convert) | D2 |
| `tests/integration/test_bitmap_integration.py` | `test_int_nbd_incremental_with_compression`: remove incremental compression assertions; assert compress ignored on incremental | D6 |
| `tests/integration/test_bitmap_integration.py` | `test_int_incremental_is_smaller_than_full`: strengthen to dirty-barrier assertion | D5 |

### KEEPs (untouched by the change)

All FULL-path tests in `test_bitmap.py` (`test_no_checkpoints_triggers_full_export`, socket cleanup tests, domjobabort tests, all 8 `create_full_backup` tests, `test_atomic_full_export_passes_checkpoint_xml`, `test_backup_begin_failure_preserves_prior_checkpoint`, `test_nbd_full_export_uses_stall_detection`). All checkpoint-management tests (list/parse/newest-wins/collision-bump). All file-copy provider and non-bitmap factory tests. `tests/utils/test_nbd.py` (439 lines — FULL-export only). `tests/modules/backup/test_verification.py` (verify_backup file-copy path). `tests/integration/test_verification.py`. `tests/integration/test_nbd_full_backup.py`, `test_stall_detection.py`, `test_stale_state_recovery.py`. `tests/mocks/mock_modules.py`, `tests/mocks/mock_factory.py` (MockBitmapBackupProvider implements IBackupProvider directly; no constructor change needed).

---

## Risks & Edge Cases

- **[T1]** Incrementals lose compression → `test_bitmap_incremental_ignores_compress_setting` (`tests/modules/backup/test_bitmap_incremental.py`)
- **[R1]** Holes vs backing corruption → `test_overlap_filters_unallocated_regions` (`tests/utils/test_extents.py`)
- **[R2]** Previous backup disappears between list and create → `test_previous_backup_vanished_retryable_failure` (`tests/modules/backup/test_bitmap_incremental.py`)
- **[R3]** qemu-nbd process leak → `test_int_no_qemu_nbd_orphan_after_failure` (`tests/integration/test_bitmap_dirty_transfer.py`)
- **[R4]** python3-libnbd missing → `test_factory_bitmap_mode_without_libnbd_raises_actionable_error` (`tests/factory/test_default.py`); integration tests skip via `pytest.skip("python3-libnbd not installed")` guard
- **[R5]** Regression barrier false positives → `test_barrier_slack_absorbs_qcow2_metadata` (`tests/utils/test_verification_bitmap.py`)
- **[R6]** Live-VM content drift → `test_hash_tier_logs_live_source_warning` (`tests/utils/test_verification_bitmap.py`)
- **[R7]** libvirt < 7.2 gate → `test_factory_libvirt_7_1_falls_back` (`tests/factory/test_default.py`)
- **[Edge]** NbdResult normalized error strings for retry → `test_pread_eof_normalized_for_retry` (`tests/utils/test_nbd_client.py`)
- **[Edge]** qemu-nbd socket collision / artifact residue → `test_successful_transfer_no_tmp_or_socket_remain` (`tests/modules/backup/test_bitmap_incremental.py`)
- **[Edge]** stall_timeout=0 disables watchdog → `test_zero_stall_timeout_disables_watchdog` (`tests/modules/backup/test_bitmap_incremental.py`)
- **[Edge]** Restore chain resolution unchanged → `test_restore_chain_resolved_without_bitmap_specific_logic` (`tests/modules/backup/test_bitmap_incremental.py`)

---

## Environment Notes

- **libnbd availability on this host:** `python3 -c "import nbd"` succeeds — `python3-libnbd` is installed. Integration tests can run for real here.
- **Skip strategy:** every integration test in the `integration-bitmap` group that requires the copy loop guards with `try: import nbd except ImportError: pytest.skip(...)` — same pattern as existing libvirt-version guards. Unit tests never import real `nbd` (they inject `MockNbdClient`) and run everywhere.
- **Real-environment scenarios:** integration tests boot a disposable VM (existing `test_vm` fixture, 256M disk), `dd` a known payload inside the guest, run FULL + incremental, and assert the delta's `actual-size` is bounded by the dirty-barrier — this test would have caught the original full-copy bug.
