# QA Strategy & Test Plan

## Coverage Map

### Spec: nbd-bitmap-backup

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | First backup — full pull via NBD | `tests/modules/backup/test_bitmap.py` | `test_first_backup_full_nbd_no_prior_checkpoint` | bitmap-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | Incremental backup — dirty blocks via NBD checkpoint | `tests/modules/backup/test_bitmap.py` | `test_incremental_backup_dirty_blocks_via_nbd` | bitmap-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | Socket cleanup on success | `tests/modules/backup/test_bitmap.py` | `test_socket_cleanup_on_success` | bitmap-unit |
| nbd-bitmap-backup | NBD pull-model backup via virsh backup-begin | Socket cleanup on failure | `tests/modules/backup/test_bitmap.py` | `test_socket_cleanup_on_failure` | bitmap-unit |
| nbd-bitmap-backup | Checkpoint-only creation when FULL exists and no prior checkpoint | Checkpoint created without transfer when FULL exists | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_checkpoint_only_when_full_exists` | bitmap-unit |
| nbd-bitmap-backup | Checkpoint-only creation when FULL exists and no prior checkpoint | Full NBD export when no FULL and no checkpoint | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_skips_checkpoint_when_state_is_none` | bitmap-unit |
| nbd-bitmap-backup | Checkpoint-only creation when FULL exists and no prior checkpoint | Checkpoint-only path does not trigger when checkpoint exists | `tests/modules/backup/test_bitmap.py` | `test_incremental_backup_dirty_blocks_via_nbd` | bitmap-unit |
| nbd-bitmap-backup | Checkpoint-only creation when FULL exists and no prior checkpoint | Checkpoint-only path skips snapshots already on target | `tests/modules/backup/test_bitmap.py` | `test_transfer_missing_skips_existing_snapshot_before_checkpoint_check` | bitmap-unit |

### Spec: shared-utilities

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| shared-utilities | Shared NBD utility functions in qsnap.utils | Core imports NBD utilities from utils | `tests/utils/test_nbd.py` | `test_core_imports_nbd_from_utils` | nbd-utils-unit |
| shared-utilities | Shared NBD utility functions in qsnap.utils | FileCopyBackupProvider imports NBD utilities from utils | `tests/utils/test_nbd.py` | `test_file_copy_provider_imports_nbd_from_utils` | nbd-utils-unit |
| shared-utilities | Shared NBD utility functions in qsnap.utils | BitmapBackupProvider imports write_backup_xml from utils | `tests/utils/test_nbd.py` | `test_bitmap_provider_imports_write_backup_xml_from_utils` | nbd-utils-unit |
| shared-utilities | Shared NBD utility functions in qsnap.utils | write_backup_xml with incremental parameter | `tests/utils/test_nbd.py` | `test_write_backup_xml_with_incremental` | nbd-utils-unit |
| shared-utilities | Shared NBD utility functions in qsnap.utils | write_backup_xml without incremental parameter | `tests/utils/test_nbd.py` | `test_write_backup_xml_without_incremental` | nbd-utils-unit |

### Spec: backup-provider

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-provider | Transfer missing snapshots to backup target | New snapshot copied to empty target via rsync | `tests/modules/backup/test_copy.py` | `test_transfer_missing_new_snapshot_rsync_empty_target` | file-copy-unit |
| backup-provider | Transfer missing snapshots to backup target | Transfer with rate limit uses rsync --bwlimit | `tests/modules/backup/test_copy.py` | `test_transfer_with_rate_limit` (NEW) | file-copy-unit |
| backup-provider | Transfer missing snapshots to backup target | Snapshot already exists on target — skipped | `tests/modules/backup/test_copy.py` | `test_transfer_missing_existing_snapshot_skipped` | file-copy-unit |
| backup-provider | Transfer missing snapshots to backup target | Incremental backup — rebase backing path with -B flag | `tests/modules/backup/test_copy.py` | `test_transfer_incremental_rebase_backing_path` | file-copy-unit |
| backup-provider | Transfer missing snapshots to backup target | Rebase to FULL anchor when present | `tests/modules/backup/test_copy.py` | `test_transfer_rebase_to_full_anchor` (NEW) | file-copy-unit |
| backup-provider | Transfer missing snapshots to backup target | No FULL anchor preserves existing behavior | `tests/modules/backup/test_copy.py` | `test_transfer_incremental_rebase_backing_path` | file-copy-unit |
| backup-provider | Transfer missing snapshots to backup target | Non-incremental backup — no rebase | `tests/modules/backup/test_copy.py` | `test_transfer_non_incremental_no_rebase` | file-copy-unit |
| backup-provider | Transfer missing snapshots to backup target | rsync unavailable — transfer fails | `tests/modules/backup/test_copy.py` | `test_rsync_unavailable_transfer_fails_no_cp_fallback` | file-copy-unit |
| backup-provider | Transfer missing snapshots to backup target | Copy fails — disk full or permission error | `tests/modules/backup/test_copy.py` | `test_transfer_rsync_fails_disk_full` | file-copy-unit |
| backup-provider | Transfer missing snapshots to backup target | copy_base=false prevents base.qcow2 duplication | `tests/modules/backup/test_copy.py` | `test_transfer_missing_new_snapshot_rsync_empty_target` | file-copy-unit |
| backup-provider | Transfer missing snapshots to backup target | copy_base=true allows legacy base copy | `tests/modules/backup/test_copy.py` | `test_transfer_missing_new_snapshot_rsync_empty_target` | file-copy-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL with zstd compression | `tests/modules/backup/test_bitmap.py` | `test_bitmap_create_full_backup_with_compression_succeeds` | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL with zlib compression | `tests/modules/backup/test_bitmap.py` | `test_bitmap_full_backup_zlib_compression` (NEW) | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL no longer raises NotImplementedError | `tests/modules/backup/test_bitmap.py` | `test_bitmap_full_backup_does_not_raise_not_implemented` | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL does not create checkpoint | `tests/modules/backup/test_bitmap.py` | `test_bitmap_full_backup_no_checkpoint` | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL does not self-record in state | `tests/modules/backup/test_bitmap.py` | `test_bitmap_full_backup_does_not_self_record` (NEW) | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bucket-driven FULL works for bitmap targets | `tests/modules/backup/test_bitmap.py` | `test_bitmap_bucket_driven_full_no_longer_crashes` | bitmap-unit |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL with dotted VM name | `tests/modules/backup/test_bitmap.py` | `test_bitmap_create_full_backup_dotted_vm_name` | bitmap-unit |

### Spec: backup-verification

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-verification | Hash verification tier (verify="hash") | Bitmap mode with verify=hash auto-downgrades | `tests/config/test_facade.py` | `test_bitmap_verify_hash_auto_downgrades` (NEW) | config-unit |
| backup-verification | Hash verification tier (verify="hash") | Bitmap mode with verify=full auto-downgrades | `tests/config/test_facade.py` | `test_bitmap_verify_full_auto_downgrades` (NEW) | config-unit |
| backup-verification | Hash verification tier (verify="hash") | Bitmap mode with verify=metadata (default) works correctly | `tests/config/test_facade.py` | `test_bitmap_verify_metadata_no_warning` (NEW) | config-unit |
| backup-verification | Hash verification tier (verify="hash") | File-copy mode retains verify=full | `tests/config/test_facade.py` | `test_filecopy_verify_full_no_downgrade` (NEW) | config-unit |
| backup-verification | Hash verification tier (verify="hash") | File-copy mode retains verify=hash | `tests/config/test_facade.py` | `test_filecopy_verify_hash_no_downgrade` (NEW) | config-unit |

### Spec: orphan-checkpoint-detection

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| orphan-checkpoint-detection | Orphaned checkpoint detection in check_state | Orphaned checkpoint from removed target | `tests/core/test_state_check.py` | `test_check_state_orphaned_checkpoint_removed_target` (NEW) | core-unit |
| orphan-checkpoint-detection | Orphaned checkpoint detection in check_state | Orphaned checkpoint from changed target path | `tests/core/test_state_check.py` | `test_check_state_orphaned_checkpoint_changed_path` (NEW) | core-unit |
| orphan-checkpoint-detection | Orphaned checkpoint detection in check_state | No orphaned checkpoints when all targets match | `tests/core/test_state_check.py` | `test_check_state_no_orphans_all_match` (NEW) | core-unit |
| orphan-checkpoint-detection | Orphaned checkpoint detection in check_state | Checkpoint-list command failure is non-fatal | `tests/core/test_state_check.py` | `test_check_state_checkpoint_list_failure_non_fatal` (NEW) | core-unit |
| orphan-checkpoint-detection | Orphaned checkpoint detection in check_state | Non-qsnap checkpoints are ignored | `tests/core/test_state_check.py` | `test_check_state_non_qsnap_checkpoints_ignored` (NEW) | core-unit |
| orphan-checkpoint-detection | StateCheckResult includes orphan_checkpoints field | StateCheckResult with orphaned checkpoints | `tests/core/test_state_check.py` | `test_check_state_result_includes_orphans` (NEW) | core-unit |
| orphan-checkpoint-detection | StateCheckResult includes orphan_checkpoints field | StateCheckResult with no orphaned checkpoints | `tests/core/test_state_check.py` | `test_check_state_result_empty_orphans` (NEW) | core-unit |
| orphan-checkpoint-detection | Deduplication of FULL backup state entries on load | Duplicate FULL entries deduplicated on load | `tests/state/test_manager.py` | `test_deduplicate_duplicate_full_entries` (NEW) | state-unit |
| orphan-checkpoint-detection | Deduplication of FULL backup state entries on load | No duplicates — no deduplication | `tests/state/test_manager.py` | `test_deduplicate_no_duplicates_noop` (NEW) | state-unit |
| orphan-checkpoint-detection | Deduplication of FULL backup state entries on load | Deduplication is idempotent | `tests/state/test_manager.py` | `test_deduplicate_is_idempotent` (NEW) | state-unit |

### Risks & Edge Cases (Design.md)

| Risk / Edge Case | Test File | Test Name | Group |
|---|---|---|---|
| Users with orphaned checkpoints from broken `--incremental` runs | `tests/core/test_state_check.py` | `test_check_state_orphaned_checkpoint_removed_target` | core-unit |
| `verify="full"` auto-downgrade may surprise users | `tests/config/test_facade.py` | `test_bitmap_verify_full_auto_downgrades` | config-unit |
| Double-record deduplication migration may lose data | `tests/state/test_manager.py` | `test_deduplicate_duplicate_full_entries` | state-unit |
| `<incremental>` XML element may not be supported on old libvirt (already handled by existing version check) | `tests/utils/test_nbd.py` | `test_write_backup_xml_with_incremental` | nbd-utils-unit |
| Orphan detection adds a `virsh checkpoint-list` call per VM (acceptable, offline diagnostic) | `tests/core/test_state_check.py` | `test_check_state_checkpoint_list_failure_non_fatal` | core-unit |
| Duplicate `_write_backup_xml` method in bitmap.py removed; import from qsnap.utils.nbd | `tests/utils/test_nbd.py` | `test_bitmap_provider_imports_write_backup_xml_from_utils` | nbd-utils-unit |
| Rotation safety: old FULL not deleted when incrementals still depend on it | `tests/integration/test_bitmap_integration.py` | `test_int_ghost_retention_full_not_deleted_with_dependents` (NEW) | bitmap-integration |

### Integration Tests on Real virsh/qemu

| Purpose | Test File | Test Name | Group |
|---|---|---|---|
| virsh backup-begin accepts XML with `<incremental>` element | `tests/integration/test_bitmap_integration.py` | `test_int_backup_begin_accepts_incremental_xml` (NEW) | bitmap-integration |
| FULL→incremental flow: first run FULL, second run incremental | `tests/integration/test_bitmap_integration.py` | `test_int_full_to_incremental_flow` (NEW) | bitmap-integration |
| Dirty-block-only export: incremental file is smaller than FULL | `tests/integration/test_bitmap_integration.py` | `test_int_incremental_is_smaller_than_full` (NEW) | bitmap-integration |
| Rotation safety: old FULL not deleted when incrementals depend on it | `tests/integration/test_bitmap_integration.py` | `test_int_ghost_retention_full_not_deleted_with_dependents` (NEW) | bitmap-integration |
| Orphaned checkpoint detection: create checkpoint, change target path, run check_state | `tests/integration/test_bitmap_integration.py` | `test_int_orphaned_checkpoint_detected_by_check_state` (NEW) | bitmap-integration |
| qemu-img rebase -B qcow2 on real files | `tests/integration/test_nbd_full_backup.py` | `test_qemu_img_rebase_minus_B_qcow2_integration` (NEW — replaces `test_qemu_img_rebase_minus_F_qcow2_integration`) | bitmap-integration |

---

## Delegation Groups

### Group: bitmap-unit
**Scope:** `tests/modules/backup/test_bitmap.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | 14 scenarios (8 existing, 2 new, 4 modified) | MODIFY + NEW |

**Modifications required:**
- 3 tests that assert `--incremental` CLI flag in backup-begin MUST be changed to assert `<incremental>` is present in the backup XML content
- 1 test that asserts `record_full_backup()` is called from `create_full_backup()` MUST be changed to assert it is NOT called
- All existing tests using `--incremental` assertion on backup-begin commands: `test_incremental_backup_dirty_blocks_via_nbd`, `test_checkpoint_cleanup_after_successful_transfer`, `test_bitmap_incremental_dirty_blocks_via_nbd`

**New tests:**
- `test_bitmap_full_backup_zlib_compression` — verifies `nbd_full_export` is called with `compression_type="zlib"`
- `test_bitmap_full_backup_does_not_self_record` — verifies `self._state.record_full_backup()` is NOT called by `create_full_backup()`

### Group: file-copy-unit
**Scope:** `tests/modules/backup/test_copy.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_copy.py` | 11 scenarios (9 existing, 2 new) | MODIFY + NEW |

**Modifications required:**
- `test_transfer_incremental_rebase_backing_path` — assert `-B qcow2` instead of `-F qcow2` in the rebase command (line 345)

**New tests:**
- `test_transfer_with_rate_limit` — verify `rsync --bwlimit` is used when `rate_limit` is set
- `test_transfer_rebase_to_full_anchor` — verify rebase targets the FULL anchor file when present

### Group: core-unit
**Scope:** `tests/core/test_state_check.py`, `tests/core/test_pipeline.py`, `tests/core/test_full_verification_pipeline.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_state_check.py` | 7 scenarios | NEW |
| `tests/core/test_pipeline.py` | 0 scenarios (no changes needed in this file for this change) | NONE |
| `tests/core/test_full_verification_pipeline.py` | 0 scenarios (no changes needed in this file for this change) | NONE |

**New tests in `test_state_check.py`:**
- `test_check_state_orphaned_checkpoint_removed_target`
- `test_check_state_orphaned_checkpoint_changed_path`
- `test_check_state_no_orphans_all_match`
- `test_check_state_checkpoint_list_failure_non_fatal`
- `test_check_state_non_qsnap_checkpoints_ignored`
- `test_check_state_result_includes_orphans`
- `test_check_state_result_empty_orphans`

### Group: nbd-utils-unit
**Scope:** `tests/utils/test_nbd.py` (NEW file)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_nbd.py` | 5 scenarios | NEW |

**New tests:**
- `test_core_imports_nbd_from_utils`
- `test_file_copy_provider_imports_nbd_from_utils`
- `test_bitmap_provider_imports_write_backup_xml_from_utils`
- `test_write_backup_xml_with_incremental`
- `test_write_backup_xml_without_incremental`

### Group: config-unit
**Scope:** `tests/config/test_facade.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_facade.py` | 5 scenarios | NEW |

**New tests:**
- `test_bitmap_verify_hash_auto_downgrades`
- `test_bitmap_verify_full_auto_downgrades`
- `test_bitmap_verify_metadata_no_warning`
- `test_filecopy_verify_full_no_downgrade`
- `test_filecopy_verify_hash_no_downgrade`

### Group: state-unit
**Scope:** `tests/state/test_manager.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/state/test_manager.py` | 3 scenarios | NEW |

**New tests:**
- `test_deduplicate_duplicate_full_entries`
- `test_deduplicate_no_duplicates_noop`
- `test_deduplicate_is_idempotent`

### Group: bitmap-integration
**Scope:** `tests/integration/test_bitmap_integration.py`, `tests/integration/test_nbd_full_backup.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_bitmap_integration.py` | 5 scenarios | NEW |
| `tests/integration/test_nbd_full_backup.py` | 1 scenario | MODIFY |

**Modifications in `test_nbd_full_backup.py`:**
- `test_qemu_img_rebase_minus_F_qcow2_integration` — rename to `test_qemu_img_rebase_minus_B_qcow2_integration`, replace `-F` with `-B` in the rebase command

**New tests in `test_bitmap_integration.py`:**
- `test_int_backup_begin_accepts_incremental_xml`
- `test_int_full_to_incremental_flow`
- `test_int_incremental_is_smaller_than_full`
- `test_int_ghost_retention_full_not_deleted_with_dependents`
- `test_int_orphaned_checkpoint_detected_by_check_state`

---

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` — `test_incremental_backup_dirty_blocks_via_nbd` (line 197) | Replace assertion `assert "--incremental" in backup_cmds[0]` with assertion that the backup XML written by `write_backup_xml` contains `<incremental>prior_checkpoint</incremental>`. The `--incremental` CLI flag does not exist in virsh; the checkpoint must be passed via XML. | Spec nbd-bitmap-backup, Scenario "Incremental backup — dirty blocks via NBD checkpoint"; Design D1 |
| `tests/modules/backup/test_bitmap.py` — `test_checkpoint_cleanup_after_successful_transfer` (line 277) | Replace assertion `assert "--incremental" in backup_cmds[0]` with assertion that the generated backup XML contains `<incremental>`. Also verify no `--incremental` flag is present in the virsh command. | Spec nbd-bitmap-backup, Scenario "Incremental backup — dirty blocks via NBD checkpoint"; Design D1 |
| `tests/modules/backup/test_bitmap.py` — `test_bitmap_incremental_dirty_blocks_via_nbd` (line 1638) | Replace assertion `assert "--incremental" in backup_cmds[0]` (line 1707) with assertion that the generated backup XML contains `<incremental>` with the prior checkpoint name. Also verify no `--incremental` CLI flag is present. | Spec nbd-bitmap-backup, Scenario "Incremental backup — dirty blocks via NBD checkpoint"; Design D1 |
| `tests/modules/backup/test_bitmap.py` — `test_create_full_backup_records_in_state` (line 2011) | Replace assertion that `mock_state.record_full_backup()` is called exactly once with assertion that `mock_state.record_full_backup()` is NOT called — state recording is Core's responsibility. | Spec backup-provider, Scenario "Bitmap FULL does not self-record in state"; Design D4 |
| `tests/modules/backup/test_copy.py` — `test_transfer_incremental_rebase_backing_path` (line 268) | Replace assertion `assert "-F qcow2" in rebase_cmd` with `assert "-B qcow2" in rebase_cmd`. The `-F` flag was renamed to `-B` in QEMU 11.0 for `qemu-img rebase`. | Spec backup-provider, Scenario "Incremental backup — rebase backing path with -B flag"; Design D3 |
| `tests/integration/test_nbd_full_backup.py` — `test_qemu_img_rebase_minus_F_qcow2_integration` (line 580) | Rename to `test_qemu_img_rebase_minus_B_qcow2_integration`. Replace `-F` with `-B` in the `qemu-img rebase` shell command (line 643). Update all references from `-F` to `-B` in docstrings and comments. | Spec backup-provider, Scenario "Incremental backup — rebase backing path with -B flag"; Design D3 |

---

## Risks & Edge Cases

- **[Risk: Orphaned checkpoints from broken `--incremental` runs]** Users who ran qsnap before the fix will have orphaned `qsnap-*` checkpoints in libvirt from the first successful (full) run. The new `check_state()` detection will report them. → Test: `test_check_state_orphaned_checkpoint_removed_target` (core-unit) and `test_int_orphaned_checkpoint_detected_by_check_state` (bitmap-integration).
- **[Risk: `verify="full"` auto-downgrade may surprise users]** Users who explicitly configured `verify="full"` for bitmap targets will now get a WARNING and downgrade to `"metadata"`. → Test: `test_bitmap_verify_full_auto_downgrades` (config-unit). Also verify the WARNING message text matches the spec.
- **[Risk: Double-record deduplication migration may lose data]** Deduplication by `(name, target_path)` tuple could theoretically remove a legitimate duplicate-accidental case. → Test: `test_deduplicate_duplicate_full_entries` verifies dedup behavior; `test_deduplicate_is_idempotent` verifies safety on repeated runs.
- **[Risk: `<incremental>` XML element on old libvirt]** Already handled — `is_libvirt_new_enough()` checks libvirt ≥ 6.0, which is when `backup-begin` and the `<incremental>` element were introduced. No additional version check needed. → Test: `test_write_backup_xml_with_incremental` verifies XML structure regardless of libvirt version.
- **[Trade-off: Orphan detection adds a `virsh checkpoint-list` call per VM]** Acceptable — `check_state()` is offline. → Test: `test_check_state_checkpoint_list_failure_non_fatal` verifies the call is non-fatal when it fails.
- **[Edge case: Incremental backup at same time as FULL creation]** When the bucket strategy creates a FULL in the same run, the checkpoint-only path must not try to duplicate the transfer. → Test: `test_transfer_missing_checkpoint_only_when_full_exists` (already exists in bitmap-unit).
- **[Edge case: Dotted VM names in NBD export]** The `<incremental>` element uses the checkpoint name derived from `_target_hash`, not the VM name, so dotted names do not affect the incremental XML path. → Existing test: `test_bitmap_create_full_backup_dotted_vm_name` (bitmap-unit). No additional test needed but existing test must still pass.

---

## Integration Tests on Real virsh/qemu

All integration tests below are marked `@pytest.mark.integration` and require a running libvirt daemon. They use the existing `test_vm` fixture from `tests/integration/conftest.py` (256M qcow2 disposable VM).

### 1. `test_int_backup_begin_accepts_incremental_xml`
**Purpose:** Verify that `virsh backup-begin` accepts a backup XML with an `<incremental>` element on libvirt 12.5.0 / QEMU 11.0.2.

**Steps:**
1. Start the test VM.
2. Create a checkpoint on the VM via `virsh checkpoint-create-as --domain <vm> --name qsnap-test-cp`.
3. Call `write_backup_xml(socket_path, incremental="qsnap-test-cp")` to generate XML with `<incremental>`.
4. Call `virsh backup-begin --domain <vm> backup.xml` (without `--incremental` flag).
5. Assert the command succeeds (exit code 0).
6. Cleanup: `virsh checkpoint-delete --metadata qsnap-test-cp`.

### 2. `test_int_full_to_incremental_flow`
**Purpose:** Execute FULL→incremental flow end-to-end: first run creates FULL (no `<incremental>`), second run creates incremental (with `<incremental>`) pointing to the first checkpoint.

**Steps:**
1. Start the test VM.
2. **First run:** call `transfer_missing()` with `state=None` (no FULLs in state) → full NBD export, creates checkpoint `qsnap-{hash}-{snap}`.
3. Write some data to the VM disk to dirty blocks: `virsh qemu-monitor-command --domain <vm> --hmp 'qemu-io vda "write 0 1M"'`.
4. **Second run:** call `transfer_missing()` with a new snapshot name. The prior checkpoint exists → incremental NBD export via `<incremental>` in XML.
5. Assert both runs succeeded.
6. Assert the second backup file is a valid qcow2 and references the checkpoint correctly.
7. Assert the second backup used `<incremental>` in the XML (verify via command logs or side-effect).

### 3. `test_int_incremental_is_smaller_than_full`
**Purpose:** Verify that an incremental NBD export (dirty blocks only) produces a smaller file than the FULL export.

**Steps:**
1. Start the test VM.
2. **First run (full):** `transfer_missing()` without prior checkpoint → FULL NBD export.
3. Note the file size of the full backup via `stat`.
4. Write a small amount of data to dirty only a few blocks.
5. **Second run (incremental):** `transfer_missing()` with prior checkpoint → incremental NBD export via `<incremental>`.
6. Assert the incremental backup file size is smaller than the full backup file size.
7. Assert the incremental backup is still a valid, readable qcow2 via `qemu-img info`.

### 4. `test_int_ghost_retention_full_not_deleted_with_dependents`
**Purpose:** Verify rotation safety: when retention policy marks an old FULL for deletion but incremental backups still reference it (ghost retention), the FULL is NOT deleted.

**Steps:**
1. Create a FULL backup on the target.
2. Create an incremental backup that depends on that FULL (via rebase).
3. Record the incremental dependency via `IStateManager.record_incremental_dependency()`.
4. Configure a retention policy that would delete the old FULL.
5. Call `_cleanup_backups()`.
6. Assert the old FULL file still exists on disk (ghost retention preserved it).
7. Assert the incremental files still exist.

### 5. `test_int_orphaned_checkpoint_detected_by_check_state`
**Purpose:** Verify that `Core.check_state()` detects orphaned checkpoints on a live libvirt instance.

**Steps:**
1. Start the test VM.
2. Create a qsnap-named checkpoint with a target hash that matches NO configured target (e.g., `qsnap-deadbeef-snap1`).
3. Call `core.check_state()`.
4. Assert `StateCheckResult.orphan_checkpoints` contains `"qsnap-deadbeef-snap1"`.
5. Assert a WARNING was logged about the orphaned checkpoint.
6. Cleanup: delete the orphaned checkpoint.
