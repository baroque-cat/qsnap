# QA Strategy & Test Plan

## Coverage Map

Every `#### Scenario:` from both delta spec files is mapped to at least one test. Spec capability names use the folder names from `openspec/changes/atomic-backup-checkpoints/specs/`.

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | First backup — full pull via NBD with atomic checkpoint | tests/modules/backup/test_bitmap.py | test_atomic_full_export_passes_checkpoint_xml | bitmap-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | Incremental backup — dirty blocks via NBD checkpoint | tests/modules/backup/test_bitmap.py | test_atomic_incremental_passes_checkpoint_xml_and_incremental | bitmap-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | Socket cleanup on success | tests/modules/backup/test_bitmap.py | test_socket_cleanup_on_success (MODIFY: add checkpoint assertions) | bitmap-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | Socket cleanup on failure | tests/modules/backup/test_bitmap.py | test_socket_cleanup_on_failure (MODIFY: add successor-checkpoint-delete assertion) | bitmap-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via NBD full export | Bitmap FULL with zstd compression | tests/modules/backup/test_bitmap.py | test_bitmap_create_full_backup_with_compression_succeeds (MODIFY: add checkpoint XML 3rd-arg assertion) | bitmap-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via NBD full export | Bitmap FULL with zlib compression | tests/modules/backup/test_bitmap.py | test_bitmap_full_zlib_compression (MODIFY: add checkpoint XML 3rd-arg assertion) | bitmap-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via NBD full export | Bitmap FULL socket cleanup | tests/modules/backup/test_bitmap.py | test_bitmap_full_socket_cleanup (MODIFY: add checkpoint assertions) | bitmap-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via NBD full export | Bitmap FULL leaves an atomic checkpoint baseline | tests/modules/backup/test_bitmap.py | test_bitmap_full_backup_creates_atomic_checkpoint | bitmap-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via NBD full export | Bucket-driven FULL no longer crashes bitmap targets | tests/modules/backup/test_bitmap.py | test_bitmap_bucket_driven_full_no_longer_crashes (MODIFY: add checkpoint assertions) | bitmap-unit |
| nbd-bitmap-backup | Libvirt version check for NBD API | Libvirt too old | tests/factory/test_default.py | test_factory_bitmap_mode_old_libvirt_falls_back (MODIFY: update comments to 7.2) | factory-unit |
| nbd-bitmap-backup | Libvirt version check for NBD API | Libvirt sufficient | tests/factory/test_default.py | test_factory_bitmap_mode_new_libvirt_returns_bitmap (MODIFY: update comments) | factory-unit |
| nbd-bitmap-backup | Libvirt version check for NBD API | BitmapBackupProvider constructor is version-check-free | tests/modules/backup/test_bitmap.py | test_bitmap_constructor_no_version_check (KEEP) | bitmap-unit |
| nbd-bitmap-backup | Atomic checkpoint creation on every bitmap backup-begin | Checkpoint XML passed on FULL export | tests/modules/backup/test_bitmap.py | test_atomic_full_export_passes_checkpoint_xml | bitmap-unit |
| nbd-bitmap-backup | Atomic checkpoint creation on every bitmap backup-begin | Checkpoint XML passed on incremental export | tests/modules/backup/test_bitmap.py | test_atomic_incremental_passes_checkpoint_xml_and_incremental | bitmap-unit |
| nbd-bitmap-backup | Atomic checkpoint creation on every bitmap backup-begin | backup-begin failure leaves prior checkpoint intact | tests/modules/backup/test_bitmap.py | test_backup_begin_failure_preserves_prior_checkpoint (NEW) | bitmap-unit |
| nbd-bitmap-backup | Prior checkpoint discovery is newest-wins | Multiple checkpoints — newest selected | tests/modules/backup/test_bitmap.py | test_prior_discovery_newest_wins | bitmap-unit |
| nbd-bitmap-backup | Prior checkpoint discovery is newest-wins | Legacy checkpoint name recognized | tests/modules/backup/test_bitmap.py | test_prior_discovery_legacy_name_parsed | bitmap-unit |
| nbd-bitmap-backup | Prior checkpoint discovery is newest-wins | No checkpoints — full export | tests/modules/backup/test_bitmap.py | test_no_checkpoints_triggers_full_export (MODIFY from test_first_backup_full_nbd_no_prior_checkpoint) | bitmap-unit |
| nbd-bitmap-backup | Checkpoint rotation deletes superseded checkpoints only after successor success | Successful incremental rotates checkpoints | tests/modules/backup/test_bitmap.py | test_atomic_rotation_deletes_older_after_success | bitmap-unit |
| nbd-bitmap-backup | Checkpoint rotation deletes superseded checkpoints only after successor success | Export failure preserves prior, removes successor | tests/modules/backup/test_bitmap.py | test_transfer_failure_preserves_checkpoint (MODIFY: add successor-delete assertion) | bitmap-unit |
| nbd-bitmap-backup | Checkpoint rotation deletes superseded checkpoints only after successor success | checkpoint-delete failure is non-fatal | tests/modules/backup/test_bitmap.py | test_checkpoint_delete_failure_non_fatal | bitmap-unit |
| nbd-bitmap-backup | First incremental after FULL transfers dirty blocks since FULL start | Writes during FULL export appear in the first incremental | tests/integration/test_bitmap_atomic.py | test_int_writes_during_full_appear_in_incremental | bitmap-integration |
| nbd-bitmap-backup | First incremental after FULL transfers dirty blocks since FULL start | No writes since FULL — minimal incremental | tests/integration/test_bitmap_atomic.py | test_int_no_writes_minimal_incremental | bitmap-integration |
| backup-provider | Transfer missing snapshots via dirty bitmap extraction | First backup — full NBD export (no prior checkpoint) | tests/modules/backup/test_bitmap.py | test_no_checkpoints_triggers_full_export | bitmap-unit |
| backup-provider | Transfer missing snapshots via dirty bitmap extraction | Incremental backup — dirty blocks only | tests/modules/backup/test_bitmap.py | test_atomic_incremental_passes_checkpoint_xml_and_incremental | bitmap-unit |
| backup-provider | Transfer missing snapshots via dirty bitmap extraction | Checkpoint rotation after successful transfer | tests/modules/backup/test_bitmap.py | test_atomic_rotation_deletes_older_after_success | bitmap-unit |
| backup-provider | Transfer missing snapshots via dirty bitmap extraction | Transfer failure preserves prior checkpoint | tests/modules/backup/test_bitmap.py | test_transfer_failure_preserves_checkpoint (MODIFY) | bitmap-unit |
| backup-provider | NBD pull-model backup via virsh backup-begin | First backup — full pull via NBD | tests/modules/backup/test_bitmap.py | test_atomic_full_export_passes_checkpoint_xml | bitmap-unit |
| backup-provider | NBD pull-model backup via virsh backup-begin | Incremental backup — dirty blocks via NBD | tests/modules/backup/test_bitmap.py | test_atomic_incremental_passes_checkpoint_xml_and_incremental | bitmap-unit |
| backup-provider | Libvirt version check in BitmapBackupProvider | Libvirt too old — factory fallback | tests/factory/test_default.py | test_factory_bitmap_mode_old_libvirt_falls_back (MODIFY) | factory-unit |
| backup-provider | Libvirt version check in BitmapBackupProvider | Libvirt sufficient — BitmapBackupProvider constructed | tests/factory/test_default.py | test_factory_bitmap_mode_new_libvirt_returns_bitmap (MODIFY) | factory-unit |
| backup-provider | Libvirt version check in BitmapBackupProvider | BitmapBackupProvider constructor does not check version | tests/modules/backup/test_bitmap.py | test_bitmap_constructor_no_version_check (KEEP) | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL with zstd compression | tests/modules/backup/test_bitmap.py | test_bitmap_create_full_backup_with_compression_succeeds (MODIFY) | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL with zlib compression | tests/modules/backup/test_bitmap.py | test_bitmap_full_zlib_compression (MODIFY) | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL no longer raises NotImplementedError | tests/modules/backup/test_bitmap.py | test_bitmap_full_backup_does_not_raise_not_implemented (KEEP) | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL creates checkpoint atomically | tests/modules/backup/test_bitmap.py | test_bitmap_full_backup_creates_atomic_checkpoint | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL does not self-record in state | tests/modules/backup/test_bitmap.py | test_create_full_backup_does_not_self_record (KEEP) | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bucket-driven FULL works for bitmap targets | tests/modules/backup/test_bitmap.py | test_bitmap_bucket_driven_full_no_longer_crashes (MODIFY) | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL with dotted VM name | tests/modules/backup/test_bitmap.py | test_bitmap_create_full_backup_dotted_vm_name (KEEP) | bitmap-unit |
| backup-provider | BitmapBackupProvider accepts IStateManager | Constructor accepts IStateManager | tests/modules/backup/test_bitmap.py | test_constructor_accepts_state_manager (KEEP) | bitmap-unit |
| backup-provider | BitmapBackupProvider accepts IStateManager | Constructor works without IStateManager | tests/modules/backup/test_bitmap.py | test_constructor_works_without_state_manager (KEEP) | bitmap-unit |
| backup-provider | BitmapBackupProvider accepts IStateManager | create_full_backup does not self-record in state | tests/modules/backup/test_bitmap.py | test_create_full_backup_does_not_self_record (KEEP) | bitmap-unit |
| backup-provider | BitmapBackupProvider accepts IStateManager | create_full_backup skips state recording when state is None | tests/modules/backup/test_bitmap.py | test_create_full_backup_skips_state_when_none (KEEP) | bitmap-unit |
| nbd-bitmap-backup | Atomic checkpoint creation on every bitmap backup-begin | Checkpoint XML passed on FULL export | tests/utils/test_nbd.py | TestWriteCheckpointXml::test_generates_valid_xml | nbd-utils-unit |
| nbd-bitmap-backup | Atomic checkpoint creation on every bitmap backup-begin | Checkpoint XML passed on FULL export | tests/utils/test_nbd.py | TestWriteCheckpointXml::test_file_removable | nbd-utils-unit |
| nbd-bitmap-backup | Atomic checkpoint creation on every bitmap backup-begin | Checkpoint XML passed on FULL export | tests/utils/test_nbd.py | TestNbdFullExportCheckpoint::test_passes_checkpoint_xml_when_provided | nbd-utils-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | First backup — full pull via NBD with atomic checkpoint | tests/utils/test_nbd.py | TestNbdFullExportCheckpoint::test_no_checkpoint_when_none (file-copy backward compat: checkpoint_name=None) | nbd-utils-unit |
| nbd-bitmap-backup | Libvirt version check for NBD API | Libvirt too old | tests/utils/test_nbd.py | TestIsLibvirtNewEnough::test_boundary_7_2 (parametrized: 7.1.0→False, 6.5.0→False, 7.2.0→True, 9.0.0→True) | nbd-utils-unit |
| nbd-bitmap-backup | Libvirt version check for NBD API | Libvirt sufficient | tests/utils/test_nbd.py | TestIsLibvirtNewEnough::test_accepts_min_major_override | nbd-utils-unit |
| nbd-bitmap-backup | Atomic checkpoint creation on every bitmap backup-begin | Checkpoint XML passed on incremental export | tests/modules/backup/test_bitmap.py | test_new_checkpoint_name_bumps_on_collision (uniqueness clause) | bitmap-unit |
| nbd-bitmap-backup | Atomic checkpoint creation on every bitmap backup-begin | Checkpoint XML passed on incremental export | tests/modules/backup/test_bitmap.py | test_transfer_missing_collision_successor_differs_from_prior (uniqueness clause) | bitmap-unit |
| nbd-bitmap-backup | Prior checkpoint discovery is newest-wins | Multiple checkpoints — newest selected | tests/modules/backup/test_bitmap.py | test_newest_checkpoint_unchanged_contract | bitmap-unit |

## Delegation Groups

### Group: bitmap-unit

**Scope:** Unit tests for `BitmapBackupProvider` — atomic checkpoint creation, prior discovery (newest-wins), rotation (create-then-delete-superseded), removal of checkpoint-only path, updated transfer pipeline. All via `MockShell`.

| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/backup/test_bitmap.py | 28 (mix of new, modified, kept, and removed) | MODIFY |

### Group: nbd-utils-unit

**Scope:** Unit tests for new `write_checkpoint_xml()`, updated `is_libvirt_new_enough()` threshold (7.2), updated `nbd_full_export()` with `<incremental>` XML element and optional third-arg checkpoint. All via `MockShell`.

| Test File | Scenarios | Action |
|---|---|---|
| tests/utils/test_nbd.py | 5 | MODIFY |

### Group: factory-unit

**Scope:** Unit tests for `DefaultFactory.create_backup_provider()` — libvirt version gate raised to 7.2. Boundary tests for versions between 6.0 and 7.1.

| Test File | Scenarios | Action |
|---|---|---|
| tests/factory/test_default.py | 6 | MODIFY |

### Group: contract-bitmap

**Scope:** Contract tests — verify `BitmapBackupProvider` still satisfies `IBackupProvider` after signature/behavior changes. Includes `MockBitmapBackupProvider`.

| Test File | Scenarios | Action |
|---|---|---|
| tests/interfaces/test_backup_provider.py | 2 | KEEP (verify no regressions) |

### Group: bitmap-integration

**Scope:** Integration tests on real libvirt/QEMU. Includes: atomic FULL→incremental gap-elimination proof (R1/R2), crash simulation (R3 checkpoint self-healing), legacy checkpoint migration, checkpoint rotation to exactly-one, `write_checkpoint_xml` + third-arg validation with real `virsh backup-begin`.

| Test File | Scenarios | Action |
|---|---|---|
| tests/integration/test_bitmap_integration.py | 5 | MODIFY |
| tests/integration/test_bitmap_atomic.py | 7 | NEW |
| tests/integration/test_nbd_full_backup.py | 3 | MODIFY (skip-message updates) |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| tests/modules/backup/test_bitmap.py: `test_transfer_missing_checkpoint_only_when_full_exists` | **REMOVE** | Encodes D4 checkpoint-only path: FULL exists in state + no prior checkpoint → skip transfer, checkpoint-create-as only. This entire branch is removed per D4. |
| tests/modules/backup/test_bitmap.py: `test_transfer_missing_skips_checkpoint_when_state_is_none` | **REMOVE** | Tests D4.3 fall-through: when state=None, the checkpoint-only guard is skipped and full NBD export runs. With D4 removed, this branch no longer exists — no-state always means full export naturally, no special guard to test. |
| tests/modules/backup/test_bitmap.py: `test_transfer_missing_skips_existing_snapshot_before_checkpoint_check` | **REMOVE** | Tests D4.4: existing-names short-circuit prevents checkpoint-only path from triggering. The existing-names skip itself is unchanged, but the assertion specifically validates that the checkpoint-only path was NOT triggered — with D4 removed, that assertion has no meaning. A separate SKIP-existing test already covers the short-circuit elsewhere or can be recreated. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_full_backup_no_checkpoint` | **REMOVE** | Asserts `create_full_backup()` creates and deletes ZERO checkpoints. Now it MUST create one atomically (D1). The assertion is inverted; a replacement test (`test_bitmap_full_backup_creates_atomic_checkpoint`) covers the new behavior. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_create_full_backup_nbd_succeeds` | **MODIFY** | Lines 796-799 assert no `checkpoint-create-as` and no `checkpoint-delete` calls. Change to: assert `backup-begin` receives a third positional arg (checkpoint XML path); assert NO standalone `checkpoint-create-as` (atomic creation replaces it). Remove `cp_create_cmds == 0` and `cp_delete_cmds == 0` assertions. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_create_full_backup_with_compression_succeeds` | **MODIFY** | Lines 894-897 assert no checkpoint commands in create_full_backup. Change to: assert `backup-begin` receives checkpoint XML third arg. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_full_socket_cleanup` | **MODIFY** | Similarly asserts no checkpoint commands (`cp_create_cmds == 0` on success path). Change to verify checkpoint XML passed. Failure-path already correct (no checkpoint touched on failure, which matches "successor deleted best-effort" on failure). |
| tests/modules/backup/test_bitmap.py: `test_bitmap_first_full_pull_via_nbd` | **MODIFY** | Line 1638: `assert not any("checkpoint" in cmd for cmd in all_cmds)`. Change to: assert `backup-begin` command line contains a `.xml` path as third positional arg (checkpoint XML), and assert no `checkpoint-create-as`. |
| tests/modules/backup/test_bitmap.py: `test_first_backup_full_nbd_no_prior_checkpoint` | **MODIFY** | Expects `checkpoint-create-as` mock (post-hoc checkpoint). Change to: expect `write_checkpoint_xml` to be called (or verify backup-begin receives checkpoint XML third arg); remove `checkpoint-create-as` mock expectation; on success-path, verify post-success rotation deletes older checkpoints if any. |
| tests/modules/backup/test_bitmap.py: `test_incremental_backup_dirty_blocks_via_nbd` | **MODIFY** | Expects both `checkpoint-delete` AND `checkpoint-create-as` as separate commands. Change to: only `checkpoint-delete` after success (rotation of older checkpoints); NO `checkpoint-create-as` (atomic via backup-begin). Verify `write_checkpoint_xml` + `write_backup_xml` both called; verify backup-begin receives three positional args. |
| tests/modules/backup/test_bitmap.py: `test_checkpoint_cleanup_after_successful_transfer` | **MODIFY** | Same as above: remove `checkpoint-create-as` mock expectation; verify checkpoint-delete is called after success with older checkpoint names; verify atomic creation via backup-begin third arg. Update docstring from "delete-prior-then-create-new" to "create-atomically-then-delete-superseded" (D3). |
| tests/modules/backup/test_bitmap.py: `test_transfer_failure_preserves_checkpoint` | **MODIFY** | Currently asserts `checkpoint-delete` NOT called and `checkpoint-create-as` NOT called. Change to: assert prior checkpoint preserved (checkpoint-delete NOT called for the prior); assert the successor checkpoint created by the failed run is deleted best-effort (NEW: add `mock_shell.expect("checkpoint-delete")` for the successor checkpoint name). |
| tests/modules/backup/test_bitmap.py: `test_domjobabort_called_after_successful_transfer` | **MODIFY** | Expects `checkpoint-create-as` mock. Remove it; add assertion that backup-begin receives checkpoint XML. |
| tests/modules/backup/test_bitmap.py: `test_domjobabort_failure_is_non_fatal` | **MODIFY** | Same: remove `checkpoint-create-as` mock, add atomic checkpoint assertion. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_incremental_nbd_with_compression` | **MODIFY** | Expects `checkpoint-delete` + `checkpoint-create-as`. Change to atomic model: remove `checkpoint-create-as`, keep `checkpoint-delete` for rotation. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_incremental_nbd_without_compression` | **MODIFY** | Same as above. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_compress_metadata_verification_passes` | **MODIFY** | Same: remove `checkpoint-create-as`, keep `checkpoint-delete`. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_compress_full_verification_passes` | **MODIFY** | Same. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_transfer_with_zstd_compression` | **MODIFY** | Same. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_transfer_with_zlib_compression` | **MODIFY** | Same. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_transfer_uses_stall_detection` | **MODIFY** | Same. |
| tests/modules/backup/test_bitmap.py: `test_transfer_failure_deletes_partial_file` | **MODIFY** | Does not mock checkpoint-create-as (only checkpoint-delete). Verify successor checkpoint is deleted best-effort on failure path. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_verify_failure_deletes_file` | **MODIFY** | Same as partial-file deletion case — add successor-checkpoint-delete assertion. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_rate_limit` (the `test_bitmap_backup_ignores_rate_limit`) | **MODIFY** | Expects `checkpoint-create-as`. Remove it. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_domjobabort_called_after_failed_transfer` (`test_domjobabort_called_after_failed_transfer`) | **MODIFY** | Mock expectations include `checkpoint-create-as`. Remove it if transfer_missing no longer calls it. Add successor-checkpoint-delete mock on failure path. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_nbd_job_terminated_after_transfer` | **KEEP** | Tests `create_full_backup()` domjobabort behavior. Does not currently assert on checkpoint commands (it mocks `backup-begin` with simple substring match). Verify the mock still matches after checkpoint XML is added. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_socket_cleanup_after_job_abort` | **KEEP** | Similar. Verify mock expectations still work after checkpoint XML change. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_create_full_backup_returns_standalone_qcow2` | **KEEP** | Does not check checkpoint commands directly. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_create_full_backup_dotted_vm_name` | **KEEP** | Verifies VM name propagation to `nbd_full_export`. Does not check checkpoint commands. |
| tests/modules/backup/test_bitmap.py: `test_create_full_backup_does_not_self_record` | **KEEP** | Verifies provider does NOT call `state.record_full_backup()`. Unchanged behavior. |
| tests/modules/backup/test_bitmap.py: `test_create_full_backup_skips_state_when_none` | **KEEP** | Unchanged. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_full_zstd_compression` | **MODIFY** | Verify `checkpoint_name` is passed to `nbd_full_export` via spy. |
| tests/modules/backup/test_bitmap.py: `test_bitmap_full_zlib_compression` | **MODIFY** | Same. |
| tests/modules/backup/test_bitmap.py: `test_nbd_full_export_uses_stall_detection` | **MODIFY** | Verify `nbd_full_export` receives `checkpoint_name`. |
| tests/modules/backup/test_bitmap.py: `test_nbd_full_tmp_rename` | **MODIFY** | Similar; `nbd_full_export` spy should confirm `checkpoint_name` was passed. |
| tests/factory/test_default.py: `test_factory_selects_bitmap_provider_for_bitmap_mode` | **MODIFY** | Docstring says "libvirt version >= 6.0". Update to ">= 7.2". The test uses virsh 8.2.0 which passes both gates — behavior unchanged. |
| tests/factory/test_default.py: `test_factory_bitmap_mode_old_libvirt_falls_back` | **MODIFY** | Docstring says "libvirt < 6.0". Update to "< 7.2". Test uses virsh 4.5.0 — still old, behavior unchanged. |
| tests/factory/test_default.py: `test_factory_bitmap_mode_new_libvirt_returns_bitmap` | **MODIFY** | Docstring says ">= 6.0". Update to ">= 7.2". Patches `is_libvirt_new_enough` to return True — behavior unchanged. |
| tests/factory/test_default.py: `test_factory_bitmap_fallback_logs_warning` | **MODIFY** | Uses virsh 5.9.0 which is < 6.0 but was already below old threshold. With new 7.2 gate, still old — behavior unchanged. Update docstring from "libvirt 5.9.0" narrative to mention 7.2 boundary. |
| tests/factory/test_default.py: (NEW) `test_factory_libvirt_7_1_falls_back` | **NEW** | Simulate `virsh --version` returning 7.1.0 — verify factory falls back to `FileCopyBackupProvider`. Boundary test proving that 6.x and 7.0/7.1 are no longer sufficient. |
| tests/factory/test_default.py: (NEW) `test_factory_libvirt_7_2_returns_bitmap` | **NEW** | Simulate `virsh --version` returning 7.2.0 — verify factory returns `BitmapBackupProvider`. Exact boundary. |
| tests/utils/test_nbd.py: (NEW) `TestWriteCheckpointXml::test_generates_valid_xml` | **NEW** | Verify `write_checkpoint_xml("qsnap-h-20260721T010000")` writes `<domaincheckpoint><name>qsnap-h-20260721T010000</name></domaincheckpoint>` to a temp file and returns the path. (Implemented with class-based grouping; name synced to reality.) |
| tests/utils/test_nbd.py: (NEW) `TestWriteCheckpointXml::test_file_removable` | **NEW** | Verify the returned path exists and can be unlinked. |
| tests/utils/test_nbd.py: (NEW) `TestIsLibvirtNewEnough::test_boundary_7_2` | **NEW** | Parametrized: `virsh --version` returning 7.1.0 → False; 6.5.0 → False; 7.2.0 → True; 9.0.0 → True. |
| tests/utils/test_nbd.py: (NEW) `TestIsLibvirtNewEnough::test_accepts_min_major_override` | **NEW** | Verify `is_libvirt_new_enough(shell, min_major=8)` returns False for 7.2. |
| tests/utils/test_nbd.py: (NEW) `TestNbdFullExportCheckpoint::test_passes_checkpoint_xml_when_provided` | **NEW** | Use MockShell; call `nbd_full_export(shell, "vm", target, checkpoint_name="qsnap-h-ts")`; verify `backup-begin` command-line contains both backup.xml and checkpoint.xml as positional args. |
| tests/utils/test_nbd.py: (NEW) `TestNbdFullExportCheckpoint::test_no_checkpoint_when_none` | **NEW** | Call `nbd_full_export(shell, "vm", target, checkpoint_name=None)`; verify `backup-begin` command-line has exactly two positional args (backup XML only). Backward compat for FileCopyBackupProvider. |
| tests/utils/test_nbd.py: (NEW) `TestIsLibvirtNewEnough::test_unparseable_version_returns_false`, `TestIsLibvirtNewEnough::test_command_failure_returns_false` | **NEW** | Extra error-path coverage beyond the planned scenarios: unparseable `virsh --version` output and command failure both return False. |
| tests/integration/test_bitmap_integration.py: `test_int_checkpoint_only_creation` | **REMOVE** | Integration test encoding D4 checkpoint-only path. FULL exists in state → no NBD transfer, only checkpoint-create-as. D4 is removed; this integration behavior is no longer valid. |
| tests/integration/test_bitmap_integration.py: `test_int_nbd_incremental_with_compression` | **MODIFY** | Skip message says "libvirt < 6.0". Update to "libvirt < 7.2". Test also uses `transfer_missing` → verify it still passes after atomic checkpoint changes (transfer_missing behavior changed). |
| tests/integration/test_bitmap_integration.py: `test_int_backup_begin_accepts_incremental_xml` | **MODIFY** | Skip message update 6.0 → 7.2. Test calls `backup-begin` with two positional args — ADD a third-arg assertion (checkpoint XML path), or update to pass the checkpoint XML. |
| tests/integration/test_bitmap_integration.py: `test_int_full_to_incremental_flow` | **MODIFY** | Skip message update 6.0 → 7.2. Verifies FULL→incremental with `transfer_missing`. After D1, both calls should pass atomic checkpoints. Add assertions that checkpoints exist after each run. |
| tests/integration/test_bitmap_integration.py: `test_int_incremental_is_smaller_than_full` | **MODIFY** | Skip message update 6.0 → 7.2. Add checkpoint assertions. |
| tests/integration/test_nbd_full_backup.py: multiple tests | **MODIFY** | Skip messages at lines 75, 340, 459 say "libvirt < 6.0". Update to "libvirt < 7.2". |
| tests/integration/test_bitmap_atomic.py: (NEW) `test_int_writes_during_full_appear_in_incremental` | **NEW** | **R1 gap-elimination proof.** Start VM → write data via QEMU monitor → atomic FULL (create_full_backup) → write more data → atomic incremental (transfer_missing) → verify incremental file contains the guest writes from during the FULL. |
| tests/integration/test_bitmap_atomic.py: (NEW) `test_int_crash_between_export_and_cleanup_self_heals` | **NEW** | **R3 crash simulation.** Start VM → run FULL → kill qsnap process between export success and checkpoint cleanup → next run discovers multiple checkpoints → newest-wins picks the correct one → old ones deleted. Verify no data loss. |
| tests/integration/test_bitmap_atomic.py: (NEW) `test_int_legacy_checkpoint_migrated_seamlessly` | **NEW** | Pre-create a legacy-format checkpoint `qsnap-{target_hash}-{snapshot_name}` via `virsh checkpoint-create-as` → run atomic FULL → verify legacy checkpoint is discovered as prior and the first incremental works → new-format checkpoint created atomically → legacy deleted after success. |
| tests/integration/test_bitmap_atomic.py: (NEW) `test_int_exactly_one_checkpoint_after_success` | **NEW** | Run FULL + incremental → verify exactly one qsnap-prefixed checkpoint exists (`virsh checkpoint-list --name`). |
| tests/integration/test_bitmap_atomic.py: (NEW) `test_int_export_failure_deletes_successor_preserves_prior` | **NEW** | Start VM → run incremental → inject failure (kill qemu-img or timeout) → verify the prior checkpoint still exists → verify the successor checkpoint was deleted (best-effort). |
| tests/integration/test_bitmap_atomic.py: (NEW) `test_int_backup_begin_three_args_creates_checkpoint` | **NEW** | Call `virsh backup-begin --domain VM backup.xml checkpoint.xml` with a real checkpoint XML file → verify checkpoint exists via `virsh checkpoint-list`. Confirms the libvirt API accepts the third arg on this environment. |
| tests/integration/test_bitmap_atomic.py: (NEW) `test_int_write_checkpoint_xml_roundtrips` | **NEW** | Generate checkpoint XML via `write_checkpoint_xml()` → pass to `virsh backup-begin` → verify checkpoint name visible in `virsh checkpoint-list --name`. |

## Risks & Edge Cases

- **[libvirt < 7.2 deployments silently flip to file-copy mode after upgrade]** → Covered by `test_factory_libvirt_7_1_falls_back` (factory-unit) and `TestIsLibvirtNewEnough::test_boundary_7_2` (nbd-utils-unit). Factory WARNING log verified by existing `test_factory_bitmap_fallback_logs_warning` (factory-unit, MODIFY to update comments).

- **[First incremental after a FULL is large (all writes during the FULL)]** → Covered by `test_int_writes_during_full_appear_in_incremental` (bitmap-integration). Unit-level covered by spec scenario: "Writes during FULL export appear in the first incremental" in the coverage map (bitmap-unit).

- **[Checkpoint XML third arg unsupported on exotic/older virsh builds]** → Gated by D6 (libvirt ≥ 7.2). Covered by `test_factory_libvirt_7_1_falls_back` and the real-environment integration test `test_int_backup_begin_three_args_creates_checkpoint` which proves the API works on this machine's libvirt 12.5.0.

- **[Legacy checkpoint names mixed with new names during ordering]** → Covered by `test_prior_discovery_legacy_name_parsed` (bitmap-unit, unit test parsing `qsnap-h-3.Projects_opencode.20260721T0018_vda`) and `test_int_legacy_checkpoint_migrated_seamlessly` (bitmap-integration).

- **[Stale checkpoints accumulate if cleanup keeps failing]** → Covered by `test_checkpoint_delete_failure_non_fatal` (bitmap-unit) and `test_int_crash_between_export_and_cleanup_self_heals` (bitmap-integration). The WARNING log and retry-on-next-success are verified.

- **[libvirt creates checkpoint even when qemu-img convert later fails]** → Covered by `test_backup_begin_failure_preserves_prior_checkpoint` (bitmap-unit, backup-begin itself fails) and `test_transfer_failure_preserves_checkpoint` (bitmap-unit, MODIFY: add successor-checkpoint-delete assertion) and `test_int_export_failure_deletes_successor_preserves_prior` (bitmap-integration).
