# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| cascade-deletion | Core prevents deletion of FULLs with active dependents | FULL kept due to active dependent | tests/core/test_pipeline.py | test_full_kept_due_to_active_dependent | cascade-unit |
| cascade-deletion | Core prevents deletion of FULLs with active dependents | FULL deleted when no active dependents | tests/core/test_pipeline.py | test_full_deleted_when_no_active_dependents | cascade-unit |
| cascade-deletion | Core prevents deletion of FULLs with active dependents | Incremental kept due to active dependent in keep-set | tests/core/test_pipeline.py | test_incremental_kept_due_to_active_dependent | cascade-unit |
| cascade-deletion | Core prevents deletion of FULLs with active dependents | Incremental deleted when no active dependents | tests/core/test_pipeline.py | test_incremental_deleted_when_no_active_dependents | cascade-unit |
| cascade-deletion | Cascade deletion of orphaned incrementals | Orphaned incrementals cascade-deleted after FULL deletion | tests/core/test_pipeline.py | test_orphaned_incrementals_cascade_deleted | cascade-unit |
| cascade-deletion | Cascade deletion of orphaned incrementals | Kept incremental rebased to new anchor | tests/core/test_pipeline.py | test_kept_incremental_rebased_to_new_anchor | cascade-unit |
| cascade-deletion | Cascade deletion of orphaned incrementals | Orphaned incrementals cascade-deleted after incremental deletion | tests/core/test_pipeline.py | test_orphaned_incrementals_cascade_deleted_after_inc_deletion | cascade-unit |
| cascade-deletion | State cleanup when incremental backup is deleted | Dependency record cleaned on retention-driven incremental deletion | tests/core/test_pipeline.py | test_dependency_cleaned_on_retention_driven_inc_deletion | cascade-unit |
| cascade-deletion | Reverse backing-chain dependency map | Reverse dependency map built correctly | tests/core/test_pipeline.py | test_reverse_dependency_map_built_correctly | cascade-unit |
| cascade-deletion | Reverse backing-chain dependency map | Broken qemu-img info skipped | tests/core/test_pipeline.py | test_broken_qemu_img_info_skipped_in_reverse_map | cascade-unit |
| state-management | IStateManager tracks incremental-to-FULL dependencies | Dependency recorded after rebase | tests/state/test_manager.py | test_dependency_recorded_after_rebase | state-unit |
| state-management | IStateManager tracks incremental-to-FULL dependencies | Multiple incrementals depend on same FULL | tests/state/test_manager.py | test_multiple_incrementals_depend_on_same_full | state-unit |
| state-management | IStateManager tracks incremental-to-FULL dependencies | Lookup with stem key finds dependencies stored with stem | tests/state/test_manager.py | test_get_incremental_deps_with_stem_key | state-unit |
| state-management | IStateManager tracks incremental-to-FULL dependencies | Lookup with extended key finds dependencies stored with stem | tests/state/test_manager.py | test_get_incremental_deps_with_qcow2_key_finds_stem_stored | state-unit |
| state-management | Legacy dependency key migration on load | Legacy .qcow2 keys migrated to stem on load | tests/state/test_manager.py | test_legacy_qcow2_keys_migrated_to_stem_on_load | state-unit |
| state-management | Legacy dependency key migration on load | Already-migrated file loaded unchanged | tests/state/test_manager.py | test_already_migrated_deps_file_loaded_unchanged | state-unit |
| state-management | Legacy dependency key migration on load | Mixed keys migrated correctly | tests/state/test_manager.py | test_mixed_keys_migrated_correctly | state-unit |
| nbd-dirty-block-transfer | Dirty-block copy loop replaces qemu-img convert for incrementals | Incremental copies only dirty blocks | tests/modules/backup/test_bitmap.py | test_checkpoint_cleanup_after_successful_transfer | bitmap-unit |
| nbd-dirty-block-transfer | Dirty-block copy loop replaces qemu-img convert for incrementals | First incremental chains to the FULL | tests/modules/backup/test_bitmap.py | test_atomic_incremental_passes_checkpoint_xml_and_incremental | bitmap-unit |
| nbd-dirty-block-transfer | Dirty-block copy loop replaces qemu-img convert for incrementals | Previous backup vanished — retryable failure | tests/modules/backup/test_bitmap.py | test_previous_backup_vanished_retryable_failure | bitmap-unit |
| nbd-dirty-block-transfer | Dirty-block copy loop replaces qemu-img convert for incrementals | Broken-chain newest backup skipped — walk to valid previous | tests/modules/backup/test_bitmap.py | test_broken_chain_newest_backup_skipped_walk_to_valid | bitmap-unit |
| nbd-dirty-block-transfer | Dirty-block copy loop replaces qemu-img convert for incrementals | All non-FULL backups broken — fall back to FULL | tests/modules/backup/test_bitmap.py | test_all_non_full_broken_fall_back_to_full | bitmap-unit |
| nbd-dirty-block-transfer | Dirty-block copy loop replaces qemu-img convert for incrementals | No valid backup found — error with guidance | tests/modules/backup/test_bitmap.py | test_no_valid_backup_found_error_with_guidance | bitmap-unit |
| nbd-dirty-block-transfer | Backing-chain validation method for backup files | Valid backing chain returns True | tests/modules/backup/test_bitmap.py | test_validate_backing_chain_valid_returns_true | bitmap-unit |
| nbd-dirty-block-transfer | Backing-chain validation method for backup files | Broken backing chain returns False | tests/modules/backup/test_bitmap.py | test_validate_backing_chain_broken_returns_false | bitmap-unit |
| nbd-dirty-block-transfer | Backing-chain validation method for backup files | Standalone FULL returns True | tests/modules/backup/test_bitmap.py | test_validate_backing_chain_standalone_full_returns_true | bitmap-unit |
| state-consistency-check | Broken backing chain detection in check --state | Broken backing chain detected | tests/core/test_state_check.py | test_check_state_broken_backing_chain_detected | state-check-unit |
| state-consistency-check | Broken backing chain detection in check --state | All backing chains intact — clean state | tests/core/test_state_check.py | test_check_state_all_backing_chains_intact | state-check-unit |
| state-consistency-check | Broken backing chain detection in check --state | FULL backups skipped in chain validation | tests/core/test_state_check.py | test_check_state_full_backups_skipped_in_chain_validation | state-check-unit |
| state-reconciliation | Reconcile removes orphan files on target | Reconcile skips non-qsnap files on target | tests/core/test_reconcile.py | test_reconcile_skips_non_qsnap_files_on_target | reconcile-unit |
| state-reconciliation | Reconcile removes orphan files on target | Reconcile removes orphan snapshot files | tests/core/test_reconcile.py | test_reconcile_removes_orphan_snapshot_files | reconcile-unit |
| state-reconciliation | Reconcile removes orphan files on target | Reconcile orphan file cleanup is non-fatal | tests/core/test_reconcile.py | test_reconcile_orphan_file_cleanup_non_fatal | reconcile-unit |
| state-reconciliation | Reconcile removes orphan files on target | Reconcile cleans dependency records on orphan deletion | tests/core/test_reconcile.py | test_reconcile_cleans_dependency_records_on_orphan_deletion | reconcile-unit |
| state-reconciliation | Broken backing chain detection in reconcile | Reconcile detects broken chain before orphan classification | tests/core/test_reconcile.py | test_reconcile_detects_broken_chain_before_orphan | reconcile-unit |
| state-reconciliation | Broken backing chain detection in reconcile | Reconcile with intact chains — no broken_chains | tests/core/test_reconcile.py | test_reconcile_intact_chains_no_broken_chains | reconcile-unit |
| state-reconciliation | Broken backing chain detection in reconcile | Reconcile dry-run reports broken chains without deletion | tests/core/test_reconcile.py | test_reconcile_dry_run_reports_broken_chains_no_deletion | reconcile-unit |
| integration | Full pipeline broken-chain recovery | Create FULL+incr chain → delete intermediate → run qsnap → verify skips broken and chains to valid | tests/integration/test_broken_chain.py | test_broken_chain_recovery_skips_and_chains_to_valid | integration |
| integration | Ghost retention for incrementals in real pipeline | Create scenario where retention would delete incr that another incr chains to → verify ghost retention | tests/integration/test_broken_chain.py | test_ghost_retention_incrementals_real_pipeline | integration |
| integration | check --state detects broken chains | Create broken chain → run qsnap check --state → verify broken_chains status | tests/integration/test_broken_chain.py | test_check_state_detects_broken_chains | integration |
| integration | reconcile detects and cleans broken chains | Create broken chain → run qsnap reconcile → verify broken file detected and deleted | tests/integration/test_broken_chain.py | test_reconcile_detects_and_cleans_broken_chains | integration |

## Delegation Groups

### Group: state-unit
**Scope:** tests/state/test_manager.py

| Test File | Scenarios | Action |
|---|---|---|
| tests/state/test_manager.py | 7 scenarios (dependency key normalization, legacy migration) | MODIFY — add 4 new tests for key normalization and migration; existing dep tests remain |

### Group: cascade-unit
**Scope:** tests/core/test_pipeline.py

| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_pipeline.py | 10 scenarios (ghost retention for incrementals, cascade-delete after incr, reverse dep map, state cleanup) | MODIFY — add 6 new tests; 4 existing tests already cover FULL ghost retention + cascade after FULL deletion |

### Group: bitmap-unit
**Scope:** tests/modules/backup/test_bitmap.py

| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/backup/test_bitmap.py | 9 scenarios (backing-chain validation, broken-chain walk, retryable failure, no-valid-backup error) | MODIFY — add 9 new tests; existing tests cover incremental transfer + checkpoint management |

### Group: state-check-unit
**Scope:** tests/core/test_state_check.py

| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_state_check.py | 3 scenarios (broken chain detection, intact chains, FULL skip) | MODIFY — add 3 new tests |

### Group: reconcile-unit
**Scope:** tests/core/test_reconcile.py

| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_reconcile.py | 7 scenarios (orphan files, broken chains in reconcile, dry-run) | NEW — create file with 7 tests |

### Group: integration
**Scope:** tests/integration/test_broken_chain.py

| Test File | Scenarios | Action |
|---|---|---|
| tests/integration/test_broken_chain.py | 4 scenarios (broken-chain recovery, ghost retention, check --state, reconcile) | NEW — create file with 4 tests, all marked `@pytest.mark.integration` |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| tests/state/test_manager.py | Add `test_get_incremental_deps_with_stem_key` | State-management spec: "Lookup with stem key finds dependencies stored with stem" — verifies key normalization at lookup (design D3). |
| tests/state/test_manager.py | Add `test_get_incremental_deps_with_qcow2_key_finds_stem_stored` | State-management spec: "Lookup with extended key finds dependencies stored with stem" — verifies extended-form keys normalize to stem before lookup. |
| tests/state/test_manager.py | Add `test_legacy_qcow2_keys_migrated_to_stem_on_load` | State-management spec: "Legacy .qcow2 keys migrated to stem on load" — verifies _load_dependencies migration logic. |
| tests/state/test_manager.py | Add `test_already_migrated_deps_file_loaded_unchanged` | State-management spec: "Already-migrated file loaded unchanged" — verifies idempotent migration. |
| tests/state/test_manager.py | Add `test_mixed_keys_migrated_correctly` | State-management spec: "Mixed keys migrated correctly" — verifies partial migration of mixed-form keys. |
| tests/core/test_pipeline.py | Add `test_incremental_kept_due_to_active_dependent` | Cascade-deletion spec: "Incremental kept due to active dependent in keep-set" — extends ghost retention pattern to incrementals (design D5). |
| tests/core/test_pipeline.py | Add `test_incremental_deleted_when_no_active_dependents` | Cascade-deletion spec: "Incremental deleted when no active dependents" — verifies the else-branch deletes incremental when no dependents in keep-set. |
| tests/core/test_pipeline.py | Add `test_orphaned_incrementals_cascade_deleted_after_inc_deletion` | Cascade-deletion spec: "Orphaned incrementals cascade-deleted after incremental deletion" — verifies cascade-delete propagates through the chain when an incremental is deleted. |
| tests/core/test_pipeline.py | Add `test_dependency_cleaned_on_retention_driven_inc_deletion` | Cascade-deletion spec: "Dependency record cleaned on retention-driven incremental deletion" — verifies `remove_incremental_dependency` is called with resolved FULL anchor on else-branch deletion. |
| tests/core/test_pipeline.py | Add `test_reverse_dependency_map_built_correctly` | Cascade-deletion spec: "Reverse dependency map built correctly" — verifies the `_build_backing_refs()` map construction from `qemu-img info` output. |
| tests/core/test_pipeline.py | Add `test_broken_qemu_img_info_skipped_in_reverse_map` | Cascade-deletion spec: "Broken qemu-img info skipped" — verifies that failed `qemu-img info` calls are gracefully skipped during map construction (design risk: race condition). |
| tests/modules/backup/test_bitmap.py | Add `test_validate_backing_chain_valid_returns_true` | NBD spec: "Valid backing chain returns True" — tests `_validate_backing_chain()` with intact chain. |
| tests/modules/backup/test_bitmap.py | Add `test_validate_backing_chain_broken_returns_false` | NBD spec: "Broken backing chain returns False" — tests `_validate_backing_chain()` with missing backing file. |
| tests/modules/backup/test_bitmap.py | Add `test_validate_backing_chain_standalone_full_returns_true` | NBD spec: "Standalone FULL returns True" — tests `_validate_backing_chain()` on standalone file. |
| tests/modules/backup/test_bitmap.py | Add `test_previous_backup_vanished_retryable_failure` | NBD spec: "Previous backup vanished — retryable failure" — tests that missing `previous` between listing and creation returns retryable-class error. |
| tests/modules/backup/test_bitmap.py | Add `test_broken_chain_newest_backup_skipped_walk_to_valid` | NBD spec: "Broken-chain newest backup skipped — walk to valid previous" — tests backwards walk skips broken-chain files (design D4). |
| tests/modules/backup/test_bitmap.py | Add `test_all_non_full_broken_fall_back_to_full` | NBD spec: "All non-FULL backups broken — fall back to FULL" — tests fallback to FULL anchor when all incrementals have broken chains. |
| tests/modules/backup/test_bitmap.py | Add `test_no_valid_backup_found_error_with_guidance` | NBD spec: "No valid backup found — error with guidance" — tests error message directing user to `qsnap check --deep` and `qsnap reconcile`. |
| tests/core/test_state_check.py | Add `test_check_state_broken_backing_chain_detected` | State-consistency-check spec: "Broken backing chain detected" — tests that `check_state()` detects broken backing chains via `qemu-img info --backing-chain`. |
| tests/core/test_state_check.py | Add `test_check_state_all_backing_chains_intact` | State-consistency-check spec: "All backing chains intact — clean state" — tests that `broken_chains` is empty when all chains are intact. |
| tests/core/test_state_check.py | Add `test_check_state_full_backups_skipped_in_chain_validation` | State-consistency-check spec: "FULL backups skipped in chain validation" — tests that FULL backups are not validated (no backing file). |
| tests/core/test_reconcile.py | Create file with 7 reconcile unit tests | State-reconciliation spec — all 7 scenarios need unit tests with mocked `IShell` and `IStateManager`. No existing reconcile unit test file. |
| tests/integration/test_broken_chain.py | Create file with 4 integration tests | Integration coverage for broken-chain recovery, ghost retention, check --state, and reconcile in real pipeline. |
| tests/mocks/mock_modules.py | Add `_validate_backing_chain` and broken-chain walk support to MockBitmapBackupProvider | MockBitmapBackupProvider needs to support new methods used by Core pipeline tests (reverse dep map building via qemu-img info output). |
| tests/mocks/mock_state.py | Ensure `remove_incremental_dependency` already supports stem-normalized key lookup | The InMemoryStateManager's `remove_incremental_dependency` must match JsonStateManager's normalized key behavior (design D3). Add key normalization if missing. |

## Risks & Edge Cases

- **[O(n) subprocess calls in `_build_backing_refs`]** → Test: `test_reverse_dependency_map_performance_acceptable` in tests/core/test_pipeline.py verifies that for 10-50 backup files the reverse map builds within acceptable time. Use MockShell with preconfigured `qemu-img info` responses returning ~50ms each.
- **[Race condition: retention deletes a file between `list()` and `_build_backing_refs`]** → Test: `test_broken_qemu_img_info_skipped_in_reverse_map` in tests/core/test_pipeline.py covers the graceful skip. Also integration test `test_broken_chain_recovery_skips_and_chains_to_valid` exercises the full race path with real VMs.
- **[Legacy `_dependencies.json` with `.qcow2` keys]** → Tests: `test_legacy_qcow2_keys_migrated_to_stem_on_load`, `test_already_migrated_deps_file_loaded_unchanged`, and `test_mixed_keys_migrated_correctly` in tests/state/test_manager.py cover all migration scenarios.
- **[`qemu-img info --backing-chain` performance on deep chains]** → Integration test: `test_broken_chain_recovery_skips_and_chains_to_valid` creates a real chain of 5+ files and measures validation time. No dedicated stress test needed since chains are typically <10 hops (design.md line 86).
- **[Ghost retention can accumulate stale incrementals]** → Test: `test_incremental_kept_due_to_active_dependent` verifies ghost retention log message. The next retention cycle re-evaluates — covered by `test_incremental_deleted_when_no_active_dependents` which tests the dependent falling out of keep-set.
- **[No automatic repair of existing broken chains]** → Tests: `test_reconcile_detects_broken_chain_before_orphan` and `test_reconcile_detects_and_cleans_broken_chains` verify that reconcile detects and deletes broken-chain files. Manual intervention is expected as per design.md line 91.
- **[Corrupt `qemu-img info` JSON output]** → Test: `test_broken_qemu_img_info_skipped_in_reverse_map` handles the case where `qemu-img info` succeeds but returns unparseable JSON. MockShell returns invalid JSON; test verifies graceful skip.
- **[Backing-filename is a relative path]** → Test: `test_reverse_dependency_map_built_correctly` uses relative backing-filenames in qemu-img output and verifies they are resolved to absolute paths against the backup's parent directory (cascade-deletion spec line 66).
- **[`remove_incremental_dependency` called with non-existent key]** → Test: `test_dependency_cleaned_on_retention_driven_inc_deletion` verifies that calling `remove_incremental_dependency` for an incremental whose dependency record is already gone (double cleanup) is non-fatal.
- **[Dry-run mode in reconcile with broken chains]** → Test: `test_reconcile_dry_run_reports_broken_chains_no_deletion` verifies that `--dry-run` reports broken chains without deleting files (state-reconciliation spec line 51-56).
