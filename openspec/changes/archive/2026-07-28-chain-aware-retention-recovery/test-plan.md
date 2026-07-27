# QA Strategy & Test Plan

## Overview

This test plan covers the `chain-aware-retention-recovery` change, which replaces per-item ghost-retention with per-chain retention for backups, adds oldest-prefix-only snapshot processing, auto-recovery of broken chains, checkpoint lifecycle fixes, temporal mismatch detection, and blockcommit recovery. The plan spans 51 spec scenarios across 8 capabilities, organized into 9 delegation groups.

All tests follow the **[TESTING.md](/home/openuser/vm/qsnap/TESTING.md)** paradigm: production hierarchy mirrored in test directories, factory injection, result objects, zero real I/O for unit tests, and real libvirt/qemu for integration/stress/e2e tests.

---

## Coverage Map

### Capability: `per-chain-retention` (NEW)

| # | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| 1 | Per-chain backup retention evaluation | Single chain entirely kept | `tests/core/test_pipeline.py` | `test_per_chain_retention_keeps_entire_chain` | G1 |
| 2 | Per-chain backup retention evaluation | Old chain entirely removed | `tests/core/test_pipeline.py` | `test_per_chain_retention_removes_entire_old_chain` | G1 |
| 3 | Per-chain backup retention evaluation | No middle deletion possible | `tests/core/test_pipeline.py` | `test_per_chain_no_middle_deletion` | G1 |
| 4 | Chain grouping via backing chain walk | Incrementals grouped to correct FULL | `tests/core/test_pipeline.py` | `test_group_backups_by_chain_correct_full` | G1 |
| 5 | Chain grouping via backing chain walk | Broken-chain incremental classified as orphan | `tests/core/test_pipeline.py` | `test_group_backups_by_chain_orphan_from_broken_chain` | G1 |
| 6 | Cleanup deletes entire chains atomically | Entire chain deleted atomically | `tests/core/test_pipeline.py` | `test_per_chain_cleanup_entire_chain_deleted` | G1 |
| 7 | Cleanup deletes entire chains atomically | No ghost-retention or cascade-deletion | `tests/core/test_pipeline.py` | `test_per_chain_cleanup_no_ghost_retention` | G1 |
| 8 | Post-cleanup chain integrity verification | All keep-set chains intact after cleanup | `tests/core/test_pipeline.py` | `test_per_chain_post_cleanup_verification_pass` | G1 |
| 9 | Post-cleanup chain integrity verification | Post-cleanup detects broken chain | `tests/core/test_pipeline.py` | `test_per_chain_post_cleanup_verification_fail` | G1 |
| 10 | Per-chain retention multiple chains over time (integration) | Multi-chain monthly retention | `tests/integration/test_auto_recovery.py` | `test_per_chain_retention_multiple_chains_over_time` | G2 |

### Capability: `snapshot-oldest-prefix` (NEW)

| # | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| 11 | Oldest-prefix-only snapshot retention | Contiguous oldest prefix removed | `tests/core/test_pipeline.py` | `test_snapshot_oldest_prefix_contiguous_removed` | G1 |
| 12 | Oldest-prefix-only snapshot retention | Middle snapshots moved to keep | `tests/core/test_pipeline.py` | `test_snapshot_oldest_prefix_middle_moved_to_keep` | G1 |
| 13 | Oldest-prefix-only snapshot retention | Mixed prefix and gap fillers | `tests/core/test_pipeline.py` | `test_snapshot_oldest_prefix_mixed` | G1 |
| 14 | Blockcommit receives only oldest prefix | Blockcommit processes contiguous prefix | `tests/core/test_pipeline.py` | `test_blockcommit_receives_oldest_prefix` | G1 |

### Capability: `auto-recovery` (NEW, overlaps with `startup-state-validation` ADDED)

| # | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| 15 | Auto-recovery of broken backup chains at startup | Broken-chain backups auto-deleted at startup | `tests/integration/test_auto_recovery.py` | `test_auto_recovery_broken_backup_chain` | G2 |
| 16 | Auto-recovery of broken backup chains at startup | No broken chains — no recovery needed | `tests/integration/test_auto_recovery.py` | `test_auto_recovery_no_broken_chains_noop` | G2 |
| 17 | Force FULL creation when no valid FULL remains | Force FULL after all FULLs lost | `tests/integration/test_auto_recovery.py` | `test_auto_recovery_no_full_remains` | G2 |
| 18 | Force FULL creation when no valid FULL remains | Force FULL not triggered when FULL exists | `tests/core/test_pipeline.py` | `test_auto_recovery_force_full_not_triggered_when_full_exists` | G1 |
| 19 | Auto-recovery is non-fatal | Auto-recovery error does not abort pipeline | `tests/core/test_pipeline.py` | `test_auto_recovery_error_non_fatal` | G1 |

### Capability: `startup-state-validation` (MODIFIED)

| # | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| 20 | Startup state validation before onchange gate | Startup validation cleans phantom FULLs | `tests/integration/test_startup_validation.py` | (existing — no change) | G4 |
| 21 | Startup state validation before onchange gate | Startup validation clears stale baseline when no FULLs remain | `tests/integration/test_startup_validation.py` | (existing — no change) | G4 |
| 22 | Startup state validation before onchange gate | Startup validation clears stale baseline when no FULLs in state | `tests/integration/test_startup_validation.py` | (existing — no change) | G4 |
| 23 | Startup state validation before onchange gate | Startup validation is non-fatal | `tests/integration/test_startup_validation.py` | (existing — no change) | G4 |
| 24 | Startup state validation before onchange gate | Startup validation runs for standalone backup | `tests/integration/test_startup_validation.py` | (existing — no change) | G4 |
| 25 | Startup state validation before onchange gate | Startup validation does not delete checkpoints | `tests/integration/test_startup_validation.py` | (existing — no change) | G4 |
| 26 | Broken backup chain auto-recovery at startup | Broken-chain backups auto-deleted at startup | `tests/integration/test_auto_recovery.py` | `test_auto_recovery_broken_backup_chain` | G2 |
| 27 | Broken backup chain auto-recovery at startup | No broken chains — no recovery needed | `tests/integration/test_auto_recovery.py` | `test_auto_recovery_no_broken_chains_noop` | G2 |
| 28 | Broken backup chain auto-recovery at startup | Auto-recovery forces FULL when no valid FULL remains | `tests/integration/test_auto_recovery.py` | `test_auto_recovery_no_full_remains` | G2 |
| 29 | Broken backup chain auto-recovery at startup | Auto-recovery error does not abort pipeline | `tests/core/test_pipeline.py` | `test_auto_recovery_error_non_fatal` | G1 |

### Capability: `cascade-deletion` (MODIFIED state cleanup)

| # | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| 30 | State cleanup when FULL backup is deleted | State cleaned after FULL deletion | `tests/core/test_pipeline.py` | `test_per_chain_cleanup_entire_chain_deleted` | G1 |
| 31 | State cleanup when incremental backup is deleted | Dependency record cleaned on chain removal | `tests/core/test_pipeline.py` | `test_per_chain_cleanup_incremental_state_cleaned` | G1 |

### Capability: `nbd-bitmap-backup` (ADDED)

| # | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| 32 | Full checkpoint deletion (not metadata-only) | Full checkpoint delete succeeds | `tests/modules/backup/test_bitmap.py` | `test_checkpoint_full_delete_succeeds` | G3 |
| 33 | Full checkpoint deletion (not metadata-only) | Fallback to metadata-only when VM shut off | `tests/modules/backup/test_bitmap.py` | `test_checkpoint_full_delete_fallback_metadata` | G3 |
| 34 | UUID suffix in checkpoint names | Checkpoint name includes UUID suffix | `tests/modules/backup/test_bitmap.py` | `test_new_checkpoint_name_includes_uuid_suffix` | G3 |
| 35 | UUID suffix in checkpoint names | Timestamp still parseable with suffix | `tests/modules/backup/test_bitmap.py` | `test_parse_checkpoint_timestamp_with_uuid_suffix` | G3 |
| 36 | "Bitmap already exists" collision recovery | Bitmap collision triggers force cleanup and retry | `tests/modules/backup/test_bitmap.py` | `test_checkpoint_collision_force_cleanup_and_retry` | G3 |
| 37 | "Bitmap already exists" collision recovery | Force cleanup deletes all qsnap checkpoints | `tests/modules/backup/test_bitmap.py` | `test_force_cleanup_checkpoints_deletes_all` | G3 |
| 38 | Temporal mismatch detection | Snapshot predating checkpoint is skipped | `tests/modules/backup/test_bitmap_incremental.py` | `test_temporal_mismatch_snapshot_predates_checkpoint` | G5 |
| 39 | Temporal mismatch detection | Snapshot after checkpoint proceeds normally | `tests/modules/backup/test_bitmap_incremental.py` | `test_temporal_mismatch_snapshot_after_checkpoint_proceeds` | G5 |
| 40 | Size-based sanity check for temporal mismatch | Large transfer triggers warning | `tests/modules/backup/test_bitmap_incremental.py` | `test_size_sanity_check_warns_on_large_transfer` | G5 |
| 41 | Checkpoint lifecycle — full delete prevents collision (integration) | Checkpoint full delete + retry | `tests/integration/test_auto_recovery.py` | `test_checkpoint_full_delete_prevents_collision` | G2 |

### Capability: `blockcommit-recovery` (NEW)

| # | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| 42 | Partial blockcommit on broken chain | Partial blockcommit before break point | `tests/integration/test_blockcommit_recovery.py` | `test_blockcommit_recovery_broken_snapshot_chain` | G6 |
| 43 | Partial blockcommit on broken chain | No committable snapshots before break | `tests/integration/test_blockcommit_recovery.py` | `test_blockcommit_recovery_no_committable_before_break` | G6 |
| 44 | Auto-rebase for stuck snapshots | Stuck snapshot rebased to valid ancestor | `tests/integration/test_blockcommit_recovery.py` | `test_rebase_stuck_to_valid_ancestor` | G6 |
| 45 | Auto-rebase for stuck snapshots | Rebase safe for snapshots (data in active layer) | `tests/integration/test_blockcommit_recovery.py` | `test_rebase_safe_for_snapshots` | G6 |
| 46 | ChainVerifyResult reports broken file | Broken file reported on missing file | `tests/core/test_pipeline.py` | `test_chain_verify_result_broken_file_on_missing` | G1 |
| 47 | ChainVerifyResult reports broken file | No broken file on other failures | `tests/core/test_pipeline.py` | `test_chain_verify_result_no_broken_file_on_cycle` | G1 |

### Capability: `chain-integrity-verification` (MODIFIED + ADDED)

| # | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| 48 | Pre-commit backing chain integrity verification | Intact chain — blockcommit proceeds | `tests/core/test_pipeline.py` | (existing — no change) | G7 |
| 49 | Pre-commit backing chain integrity verification | Intact chain with new QEMU format — blockcommit proceeds | `tests/core/test_pipeline.py` | (existing — no change) | G7 |
| 50 | Pre-commit backing chain integrity verification | Missing file in chain — partial blockcommit attempted | `tests/core/test_pipeline.py` | `test_chain_verify_broken_returns_broken_file_and_attempts_partial` | G7 |
| 51 | Pre-commit backing chain integrity verification | Non-qcow2 file in chain — blockcommit skipped | `tests/core/test_pipeline.py` | (existing — no change) | G7 |
| 52 | Pre-commit backing chain integrity verification | Cyclic reference detected — blockcommit skipped | `tests/core/test_pipeline.py` | (existing — no change) | G7 |
| 53 | Pre-commit backing chain integrity verification | Broken chain does NOT defer the operation | `tests/core/test_pipeline.py` | `test_chain_verify_broken_chain_does_not_defer` | G7 |
| 54 | Post-cleanup chain integrity verification | All keep-set chains intact after cleanup | `tests/core/test_pipeline.py` | `test_per_chain_post_cleanup_verification_pass` | G1 |
| 55 | Post-cleanup chain integrity verification | Post-cleanup detects broken chain | `tests/core/test_pipeline.py` | `test_per_chain_post_cleanup_verification_fail` | G1 |

### Production Incident Reproduction Test

| # | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| 56 | Full production incident reproduction | Reproduce cascade-deletion bug + verify recovery | `tests/integration/test_auto_recovery.py` | `test_production_incident_reproduction` | G2 |

### Contract Tests

| # | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| 57 | ChainVerifyResult dataclass | broken_file field present and optional | `tests/models/test_results.py` | `test_chain_verify_result_broken_file_field` | G9 |
| 58 | Mock implementations still satisfy ABCs | MockBitmapBackupProvider satisfies IBackupProvider | `tests/interfaces/test_backup_provider.py` | (existing — verify still passes) | G8 |

---

## Delegation Groups

### Group: G1 — Per-Chain Retention Unit Tests
**Scope:** `tests/core/test_pipeline.py` (new tests + modifications)
**Priority:** CRITICAL — covers the core architectural change

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | `test_per_chain_retention_keeps_entire_chain` (#1) | NEW |
| `tests/core/test_pipeline.py` | `test_per_chain_retention_removes_entire_old_chain` (#2) | NEW |
| `tests/core/test_pipeline.py` | `test_per_chain_no_middle_deletion` (#3) | NEW |
| `tests/core/test_pipeline.py` | `test_group_backups_by_chain_correct_full` (#4) | NEW |
| `tests/core/test_pipeline.py` | `test_group_backups_by_chain_orphan_from_broken_chain` (#5) | NEW |
| `tests/core/test_pipeline.py` | `test_per_chain_cleanup_entire_chain_deleted` (#6, #30) | NEW |
| `tests/core/test_pipeline.py` | `test_per_chain_cleanup_no_ghost_retention` (#7) | NEW |
| `tests/core/test_pipeline.py` | `test_per_chain_cleanup_incremental_state_cleaned` (#31) | NEW |
| `tests/core/test_pipeline.py` | `test_per_chain_post_cleanup_verification_pass` (#8, #54) | NEW |
| `tests/core/test_pipeline.py` | `test_per_chain_post_cleanup_verification_fail` (#9, #55) | NEW |
| `tests/core/test_pipeline.py` | `test_snapshot_oldest_prefix_contiguous_removed` (#11) | NEW |
| `tests/core/test_pipeline.py` | `test_snapshot_oldest_prefix_middle_moved_to_keep` (#12) | NEW |
| `tests/core/test_pipeline.py` | `test_snapshot_oldest_prefix_mixed` (#13) | NEW |
| `tests/core/test_pipeline.py` | `test_blockcommit_receives_oldest_prefix` (#14) | NEW |
| `tests/core/test_pipeline.py` | `test_auto_recovery_force_full_not_triggered_when_full_exists` (#18) | NEW |
| `tests/core/test_pipeline.py` | `test_auto_recovery_error_non_fatal` (#19, #29) | NEW |
| `tests/core/test_pipeline.py` | `test_chain_verify_result_broken_file_on_missing` (#46) | NEW |
| `tests/core/test_pipeline.py` | `test_chain_verify_result_no_broken_file_on_cycle` (#47) | NEW |
| `tests/core/test_pipeline.py` | `test_chain_verify_broken_returns_broken_file_and_attempts_partial` (#50) | NEW |
| `tests/core/test_pipeline.py` | `test_per_chain_null_retention_result_noop` (edge: None passed as retention) | NEW |

### Group: G2 — Integration: Auto-Recovery & Production Incident
**Scope:** `tests/integration/test_auto_recovery.py` (NEW file)
**Priority:** CRITICAL — validates the core fix against real libvirt/qemu

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_auto_recovery.py` | `test_auto_recovery_broken_backup_chain` (#15, #26) | NEW |
| `tests/integration/test_auto_recovery.py` | `test_auto_recovery_no_broken_chains_noop` (#16, #27) | NEW |
| `tests/integration/test_auto_recovery.py` | `test_auto_recovery_no_full_remains` (#17, #28) | NEW |
| `tests/integration/test_auto_recovery.py` | `test_per_chain_retention_multiple_chains_over_time` (#10) | NEW |
| `tests/integration/test_auto_recovery.py` | `test_checkpoint_full_delete_prevents_collision` (#41) | NEW |
| `tests/integration/test_auto_recovery.py` | `test_production_incident_reproduction` (#56) | NEW |

### Group: G3 — Unit: Checkpoint Lifecycle
**Scope:** `tests/modules/backup/test_bitmap.py` (new tests)
**Priority:** HIGH — fixes "Bitmap already exists" production bug

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | `test_checkpoint_full_delete_succeeds` (#32) | NEW |
| `tests/modules/backup/test_bitmap.py` | `test_checkpoint_full_delete_fallback_metadata` (#33) | NEW |
| `tests/modules/backup/test_bitmap.py` | `test_new_checkpoint_name_includes_uuid_suffix` (#34) | NEW |
| `tests/modules/backup/test_bitmap.py` | `test_parse_checkpoint_timestamp_with_uuid_suffix` (#35) | NEW |
| `tests/modules/backup/test_bitmap.py` | `test_checkpoint_collision_force_cleanup_and_retry` (#36) | NEW |
| `tests/modules/backup/test_bitmap.py` | `test_force_cleanup_checkpoints_deletes_all` (#37) | NEW |

### Group: G4 — Integration: Startup Validation (existing, verify unchanged)
**Scope:** `tests/integration/test_startup_validation.py`
**Priority:** MEDIUM — existing tests must still pass after changes

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_startup_validation.py` | Scenarios #20–#25 | EXISTING (verify pass, no changes) |

### Group: G5 — Unit: Temporal Mismatch Detection
**Scope:** `tests/modules/backup/test_bitmap_incremental.py` (new tests)
**Priority:** MEDIUM — detects wasteful transfers

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap_incremental.py` | `test_temporal_mismatch_snapshot_predates_checkpoint` (#38) | NEW |
| `tests/modules/backup/test_bitmap_incremental.py` | `test_temporal_mismatch_snapshot_after_checkpoint_proceeds` (#39) | NEW |
| `tests/modules/backup/test_bitmap_incremental.py` | `test_size_sanity_check_warns_on_large_transfer` (#40) | NEW |
| `tests/modules/backup/test_bitmap_incremental.py` | `test_temporal_mismatch_no_checkpoint_proceeds` (edge) | NEW |

### Group: G6 — Integration: Blockcommit Recovery
**Scope:** `tests/integration/test_blockcommit_recovery.py` (NEW file)
**Priority:** HIGH — recovers from stuck snapshot chains

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_blockcommit_recovery.py` | `test_blockcommit_recovery_broken_snapshot_chain` (#42) | NEW |
| `tests/integration/test_blockcommit_recovery.py` | `test_blockcommit_recovery_no_committable_before_break` (#43) | NEW |
| `tests/integration/test_blockcommit_recovery.py` | `test_rebase_stuck_to_valid_ancestor` (#44) | NEW |
| `tests/integration/test_blockcommit_recovery.py` | `test_rebase_safe_for_snapshots` (#45) | NEW |

### Group: G7 — Unit: Chain Integrity Verification (existing + update)
**Scope:** `tests/core/test_pipeline.py` (verify existing + new tests)
**Priority:** HIGH — chain verification now supports partial blockcommit

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | Scenarios #48, #49, #51, #52 — intact chain, QEMU 11.0 format, non-qcow2, cycles | EXISTING (verify pass) |
| `tests/core/test_pipeline.py` | `test_chain_verify_broken_returns_broken_file_and_attempts_partial` (#50) | NEW |
| `tests/core/test_pipeline.py` | `test_chain_verify_broken_chain_does_not_defer` (#53) | EXISTING (verify still passes) |

### Group: G8 — Contract: Interface & Mock Compliance
**Scope:** `tests/interfaces/`, `tests/mocks/`
**Priority:** LOW — verify no regressions

| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_backup_provider.py` | Contract test for IBackupProvider | EXISTING (verify pass) |
| `tests/interfaces/test_retention_engine.py` | Contract test for IRetentionEngine | EXISTING (verify pass — engine unchanged) |
| `tests/mocks/` | `test_mock_factory_returns_interface_types` | EXISTING (verify pass) |

### Group: G9 — Model: ChainVerifyResult Update
**Scope:** `tests/models/test_results.py` (new test)
**Priority:** LOW — verify new field on existing dataclass

| Test File | Scenarios | Action |
|---|---|---|
| `tests/models/test_results.py` | `test_chain_verify_result_broken_file_field` (#57) | NEW |

---

## Test Modifications

These existing tests must be updated or removed because the ghost-retention and cascade-deletion mechanisms they test are being replaced by per-chain retention.

| File | Change | Reason |
|---|---|---|
| `tests/core/test_pipeline.py` | **REMOVE** `test_orphaned_incrementals_cascade_deleted` (line 2465) | Cascade-deletion removed; replaced by per-chain atomic deletion |
| `tests/core/test_pipeline.py` | **REMOVE** `test_kept_incremental_rebased_to_new_anchor` (line 2534) | Ghost-retention ("FULL ghost-retained") removed; per-chain makes this impossible |
| `tests/core/test_pipeline.py` | **REMOVE** `test_core_post_processes_retention_for_dependencies` (line 2588) | Core no longer post-processes retention for dependencies; per-chain eliminates FULL-in-remove-with-incrementals-in-keep |
| `tests/core/test_pipeline.py` | **REMOVE** `test_incremental_kept_due_to_active_dependent` (line 6363) | Ghost-retention ("T0008 ghost-retained because T0141 depends") removed |
| `tests/core/test_pipeline.py` | **MODIFY** `test_incremental_deleted_when_no_active_dependents` (line 6427) | Update to use per-chain semantics: entire chain in remove → both FULL + inc deleted; verify remove_incremental_dependency + remove_full_backup |
| `tests/core/test_pipeline.py` | **REMOVE** `test_orphaned_incrementals_cascade_deleted_after_inc_deletion` (line 6495) | Cascade-deletion of orphaned incrementals after inc deletion removed |
| `tests/core/test_engine.py` | **REMOVE** `test_ghost_retention_info_log` (line 1676) | Ghost-retention info log no longer emitted |
| `tests/core/test_full_verification_pipeline.py` | **MODIFY** `test_cascade_deletion_blocked_on_corrupt_full` (line 974) | Rename/reword: cascade-deletion replaced by per-chain deletion; M1 failure should still block deletion |
| `tests/core/test_full_verification_pipeline.py` | **MODIFY** `test_cleanup_backups_m1_fails_cascade_blocked` (line 370) | Same as above: per-chain cleanup, M1 verification still runs before FULL deletion |
| `tests/core/test_full_verification_pipeline.py` | **MODIFY** `test_orphaned_incrementals_cascade_deleted` (line 1045) | Update to use per-chain semantics; remove cascade-deletion references |
| `tests/core/test_full_verification_pipeline.py` | **MODIFY** `test_incremental_deleted_dependency_removed_from_state` (line 1319) | Update to verify `remove_incremental_dependency` is called per the new per-chain cleanup path |
| `tests/integration/test_broken_chain.py` | **MODIFY** `test_ghost_retention_incrementals_real_pipeline` (line 364) | Update to per-chain semantics: no ghost-retention, verify entire chain kept or removed; keep the overall shape but replace internal assertions |
| `tests/integration/test_broken_chain.py` | **MODIFY** `test_broken_chain_recovery_skips_and_chains_to_valid` (line 199) | Verify still works; chain walk logic remains but cleanup path changed |
| `tests/integration/test_broken_chain.py` | **MODIFY** `test_check_state_detects_broken_chains` (line 533) | Verify `broken_chains` still detected correctly; state check uses own logic, not cleanup path |
| `tests/integration/test_broken_chain.py` | **MODIFY** `test_reconcile_detects_and_cleans_broken_chains` (line 643) | Verify reconcile still works; its logic is independent of per-chain retention |
| `tests/core/test_reconcile.py` | **MODIFY** `test_reconcile_detects_broken_chain_before_orphan` (line 317) | Update `broken_chains` assertions if behavior changes; reconcile logic may need minor adjustment |
| `tests/state/test_manager.py` | **NO CHANGE** | `test_remove_all_incremental_deps_existing`, `test_get_incremental_dependencies_empty` etc. — state manager API unchanged |

---

## Risks & Edge Cases

Extracted from **[design.md](design.md)** § Risks / Trade-offs and **[plan.md](plan.md)** § 6 (Recovery).

### Risk: Per-chain grouping performance
- **Description:** `_resolve_chain_full_anchor()` makes up to 64 `qemu-img info` calls per incremental. For 100 incrementals, that's 6400 calls.
- **Mitigation in design:** Real chains are short (20-30 elements, 1-3 hops). Caching can be added later.
- **Test coverage:** `test_group_backups_by_chain_correct_full` (G1) — validates grouping with realistic chain sizes. Integration test `test_per_chain_retention_multiple_chains_over_time` (G2) — exercises full pipeline with 3 chains of 30+ incrementals each.

### Risk: Full checkpoint-delete slower
- **Description:** Full delete may take 1-5s longer than `--metadata` only.
- **Mitigation in design:** Acceptable for backup operations that already take minutes.
- **Test coverage:** `test_checkpoint_full_delete_succeeds` and `test_checkpoint_full_delete_fallback_metadata` (G3) — verify timing tolerance; integration `test_checkpoint_full_delete_prevents_collision` (G2) — validates real-world performance.

### Risk: UUID suffix breaks timestamp parsing
- **Description:** `_parse_checkpoint_timestamp()` must handle the new format.
- **Mitigation in design:** Updated regex: `r"qsnap-([0-9a-f]{8})-(\d{8}T\d{6})(?:-[0-9a-f]+)?"`
- **Test coverage:** `test_parse_checkpoint_timestamp_with_uuid_suffix` (G3) — verify both old format (no suffix) and new format (with suffix) parse correctly.

### Risk: Auto-recovery deletes data without confirmation
- **Description:** Broken-chain files are auto-deleted at startup without user confirmation.
- **Mitigation in design:** Log every deleted file at WARNING level. `auto_recover` config flag (default True) for manual control.
- **Test coverage:** `test_auto_recovery_broken_backup_chain` (G2) — verify WARNING logs emitted. `test_auto_recovery_error_non_fatal` (G1) — verify errors don't abort pipeline. Add `test_auto_recovery_disabled_by_config` edge case if `auto_recover` flag is implemented.

### Risk: More backups retained than per-item
- **Description:** Per-chain retention keeps all incrementals within a kept chain. A chain with 100 incrementals stays entirely.
- **Mitigation in design:** Use F-anchor syntax to create FULLs more frequently.
- **Test coverage:** `test_per_chain_no_middle_deletion` (G1) — confirms no middle deletion. `test_per_chain_retention_multiple_chains_over_time` (G2) — verifies chain rotation via new FULLs.

### Risk: Snapshot oldest-prefix less aggressive
- **Description:** Some snapshots that per-item retention would remove are kept as chain gap fillers.
- **Mitigation in design:** Blockcommit still processes the oldest prefix. Net effect: slightly more snapshots retained, no stuck blockcommits.
- **Test coverage:** `test_snapshot_oldest_prefix_mixed` (G1) — verifies gap fillers are kept. `test_blockcommit_receives_oldest_prefix` (G1) — verifies blockcommit processes only the prefix.

### Risk: Blockcommit auto-rebase loses point-in-time
- **Description:** `qemu-img rebase -u` for snapshots skips the missing file's data.
- **Mitigation in design:** The file was already missing — data is already lost. Rebase acknowledges and allows progress.
- **Test coverage:** `test_rebase_safe_for_snapshots` (G6) — verifies rebase is safe (active layer has all data). `test_blockcommit_recovery_broken_snapshot_chain` (G6) — full integration test of rebase recovery.

### Edge Case: Empty backup target
- **Description:** Target has no backups at all. Per-chain grouping returns empty dict.
- **Test coverage:** Per-chain retention should be a no-op; tested implicitly by existing tests. Consider adding `test_per_chain_null_retention_result_noop` in G1.

### Edge Case: Single backup (FULL only, no incrementals)
- **Description:** Chain has only a FULL, no incrementals. Cleanup deletes a single file.
- **Test coverage:** Implicitly tested by `test_per_chain_retention_removes_entire_old_chain` (G1) — chain with 1 member (FULL only).

### Edge Case: `preserve_min = "all"` with per-chain
- **Description:** `preserve_min = "all"` should keep all chains regardless of bucket rules.
- **Test coverage:** `test_preserve` tests in `test_preserve.py` (existing) — verify still pass. No new test needed (preserve logic unchanged in engine).

### Edge Case: When chain verification times out during auto-recovery
- **Description:** `qemu-img info --backing-chain` times out → backup left in place, not deleted.
- **Test coverage:** `test_auto_recovery_error_non_fatal` (G1) — covers timeout scenario. Mock `qemu-img info` returning subprocess.TimeoutExpired.

### Edge Case: `qemu-img rebase -u` fails (permission error)
- **Description:** Stuck snapshot rebase fails due to permissions or disk error.
- **Test coverage:** `test_blockcommit_recovery_broken_snapshot_chain` (G6) — exercise rebase with real filesystem. Add assertion: pipeline continues even if rebase fails (non-fatal).

---

## Test Execution Commands

```bash
# Unit + mock + contract (fast, no I/O):
poetry run pytest tests/core/test_pipeline.py tests/modules/backup/ tests/models/ \
  -m "not integration and not stress and not e2e" -v

# Integration (needs libvirt) — auto-recovery + blockcommit:
poetry run pytest tests/integration/test_auto_recovery.py \
  tests/integration/test_blockcommit_recovery.py \
  tests/integration/test_broken_chain.py \
  tests/integration/test_startup_validation.py \
  -m integration -v

# Full regression (all tests):
poetry run pytest tests/ -v
```

---

## Test Implementation Notes

### Mock Setup for G1 (Per-Chain Unit Tests)

Tests in `tests/core/test_pipeline.py` use `mock_factory`, `mock_state`, `mock_shell` from `conftest.py`. Key mock configurations:

- **Backup list mocking:** Mock `BitmapBackupProvider.list()` to return pre-constructed `SnapshotInfo` lists with known names/timestamps.
- **Backing chain mocking:** For `_resolve_chain_full_anchor()` calls, mock `qemu-img info --output=json` to return backing-filename pointing to FULL. For orphan tests, return failure.
- **Verification for FULL deletion:** Mock `qemu-img info` and `qemu-img check` for M1/M2 verification in cleanup tests.
- **Retention engine:** Use `MockRetentionEngine` with configurable return via `mock_factory._retention_engine`. Override `.evaluate()` return for chain-level keep/remove cases.

### Integration Test Setup (G2, G6)

- Require `libvirt` running with test VM from `tests/integration/conftest.py`.
- Each test is marked `@pytest.mark.integration`.
- **test_auto_recovery.py:** Create disposable chain by calling `ExternalSnapshotProvider` manually, delete intermediate files with `os.unlink()`, then run Core pipeline.
- **test_blockcommit_recovery.py:** Create snapshot chain, delete intermediate file, run pipeline with retention marking snapshots for removal. Assert partial blockcommit + rebase.
- **test_production_incident_reproduction:** Full setup: create FULL + 24 incrementals, delete 0500 intermediate file (cascade-deletion bug simulation), run pipeline, verify auto-recovery.

### No Changes to:
- **`tests/interfaces/test_retention_engine.py`** — `IRetentionEngine` interface unchanged; `TimeBasedRetention.evaluate()` pure function unchanged. All existing tests must still pass.
- **`tests/modules/retention/test_time_based.py`** — Retention logic unchanged. All existing tests must still pass.
- **`tests/config/`** — Config parsing unchanged. All existing tests must still pass.
- **`tests/state/test_manager.py`** — StateManager API unchanged. All existing tests must still pass.
- **`tests/mocks/mock_factory.py`** — MockVMModuleFactory does not change. All existing contract tests must still pass.
