# QA Strategy & Test Plan

**Change:** `fix-full-backup-state-extension`
**Scope:** Restore the `.qcow2` name invariant for `_full_backups.json` entries (Core call site + defensive state-manager normalization + idempotent load-time migration + tolerant `remove_full_backup` + mock parity).
**Documents conformed to:** `TESTING.md`, `proposal.md`, `design.md`, `specs/state-management/spec.md`, `specs/periodic-full-backup/spec.md`.
**No production or test code was modified in this plan.**

---

## Coverage Map

One row per `#### Scenario:` in both delta spec files. 15 scenarios total.

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| state-management | IStateManager tracks full backups per target with disk field | Full backup state saved and retrieved with disk | `tests/state/test_manager.py` | `test_set_last_full_backup_roundtrips_with_disk` (rewrite of `test_set_and_get_last_full_backup`) | `state-unit` |
| state-management | IStateManager tracks full backups per target with disk field | No full backup returns None | `tests/state/test_manager.py` | `test_get_last_full_backup_returns_none_when_empty` (existing, unchanged) | `state-unit` |
| state-management | IStateManager tracks full backups per target with disk field | get_full_backups returns all per-disk FULLs | `tests/state/test_manager.py` | `test_get_full_backups_returns_all_per_disk_fulls` (NEW) | `state-unit` |
| state-management | Recorded FULL names carry the `.qcow2` extension; path derived from normalized name | Recorded name carries the .qcow2 extension and path resolves to the file | `tests/state/test_manager.py` | `test_record_full_backup_extends_name_and_derives_path` (NEW) | `state-unit` |
| state-management | Recorded FULL names carry the `.qcow2` extension; path derived from normalized name | Stem name passed to record_full_backup is normalized defensively | `tests/state/test_manager.py` | `test_record_full_backup_normalizes_stem_defensively` (NEW) | `state-unit` |
| state-management | Idempotent FULL name normalization on load | Stem entry normalized on load | `tests/state/test_manager.py` | `test_load_normalizes_stem_entry_on_load` (NEW) | `state-unit` |
| state-management | Idempotent FULL name normalization on load | Mixed stem and extended twins deduplicate to one record | `tests/state/test_manager.py` | `test_load_mixed_stem_extended_twins_deduplicate_to_one` (NEW) | `state-unit` |
| state-management | Idempotent FULL name normalization on load | Already-extended entries load unchanged | `tests/state/test_manager.py` | `test_load_already_extended_entries_unchanged_no_rewrite` (NEW) | `state-unit` |
| state-management | Idempotent FULL name normalization on load | Per-field repair of asymmetric entries | `tests/state/test_manager.py` | `test_load_repairs_asymmetric_entry_field_by_field` (NEW; covers both asymmetry directions) | `state-unit` |
| state-management | remove_full_backup is name-format tolerant | Stem lookup removes an extended record | `tests/state/test_manager.py` | `test_remove_full_backup_stem_lookup_removes_extended_record` (NEW) | `state-unit` |
| state-management | remove_full_backup is name-format tolerant | Extended lookup removes the same record | `tests/state/test_manager.py` | `test_remove_full_backup_extended_lookup_removes_record` (NEW) | `state-unit` |
| state-management | remove_full_backup is name-format tolerant | Non-matching name leaves state untouched | `tests/state/test_manager.py` | `test_remove_full_backup_non_matching_returns_false` (NEW) | `state-unit` |
| periodic-full-backup | IStateManager tracks full backups per target | Full backup recorded and retrieved | `tests/state/test_manager.py` | `test_record_and_get_full_backups` (rewrite: extended-name fixture + assertions) | `state-unit` |
| periodic-full-backup | IStateManager tracks full backups per target | Old JSON with bucket_level is read-tolerant | `tests/state/test_manager.py` | `test_full_backups_json_new_format_loaded_as_is` (rewrite: extended fixtures + bucket_level ignored) | `state-unit` |
| periodic-full-backup | Core records FULL with `.qcow2` derived from stem `BackupResult.snapshot_name` | Core records the FULL with the .qcow2 extension after verification | `tests/core/test_full_backup_state_extension.py` | `test_backup_target_records_full_with_qcow2_extension` (NEW) | `core-unit` |

Contract/mock secondary coverage (same scenarios, parametrized over `JsonStateManager` + `InMemoryStateManager`) lives in `tests/interfaces/test_state_manager.py` and `tests/mocks/test_mock_state.py` — see the `state-contract` and `mock-parity` groups below.

---

## Delegation Groups

### Group: `state-unit`

**Scope:** All `JsonStateManager` persistence semantics for the `.qcow2` invariant: record normalization, load-time migration, dedup ordering, per-field repair, tolerant removal, read paths. Zero I/O (tmp_path-backed JSON files only). Runs with `-m "not integration and not stress and not e2e"`.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/state/test_manager.py` | 12 primary + 4 new idempotency/regression tests | MODIFY (11 rewrites) + NEW (9 tests) |

New tests:
- `test_record_full_backup_extends_name_and_derives_path`
- `test_record_full_backup_normalizes_stem_defensively`
- `test_record_full_backup_idempotent_no_double_append`
- `test_get_full_backups_returns_all_per_disk_fulls`
- `test_load_normalizes_stem_entry_on_load` (asserts extended result AND persisted write-back)
- `test_load_mixed_stem_extended_twins_deduplicate_to_one`
- `test_load_already_extended_entries_unchanged_no_rewrite` (asserts byte-identical file)
- `test_load_repairs_asymmetric_entry_field_by_field` (stem name + extended path; extended name + stem path)
- `test_remove_full_backup_stem_lookup_removes_extended_record`
- `test_remove_full_backup_extended_lookup_removes_record`
- `test_remove_full_backup_non_matching_returns_false`
- `test_remove_full_backup_after_set_last_full_backup_delegation` (D1: delegation path inherits normalization)

### Group: `state-contract`

**Scope:** Contract tests parametrized over **both** `JsonStateManager` and `InMemoryStateManager` so mock/production divergence fails CI (design D4 — the gap that let the regression escape). Follows TESTING.md contract-test pattern.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/interfaces/test_state_manager.py` | 8 scenarios re-asserted per implementation | NEW (9 parametrized tests) |

New tests (all `@pytest.mark.parametrize("mgr_cls", [JsonStateManager, InMemoryStateManager])` with per-class construction):
- `test_contract_record_full_backup_normalizes_stem`
- `test_contract_record_full_backup_extended_no_double_append`
- `test_contract_record_full_backup_derives_path_from_extended_name`
- `test_contract_get_full_backups_returns_per_disk_fulls`
- `test_contract_set_last_full_backup_roundtrip_with_disk`
- `test_contract_get_last_full_backup_empty_returns_none`
- `test_contract_remove_full_backup_stem_lookup`
- `test_contract_remove_full_backup_extended_lookup`
- `test_contract_remove_full_backup_non_matching_returns_false`

### Group: `mock-parity`

**Scope:** `InMemoryStateManager` (mock) is FIXED to mirror the production contract (D4) and its own tests assert the parity. `tests/mocks/mock_state.py` is the modified mock (Step 3); `tests/mocks/test_mock_state.py` gains the parity tests.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/mocks/mock_state.py` | — (mock, not a test) | MODIFY (record/remove normalization + `_to_extended_name`) |
| `tests/mocks/test_mock_state.py` | 3 scenarios mirrored | NEW (4 tests) + MODIFY (fixture hygiene) |

New tests (all `@pytest.mark.mock`):
- `test_inmemory_record_full_backup_normalizes_stem`
- `test_inmemory_record_full_backup_derives_extended_path`
- `test_inmemory_remove_full_backup_accepts_stem_lookup`
- `test_inmemory_remove_full_backup_non_matching_returns_false`

### Group: `core-unit`

**Scope:** Core orchestration boundary: the `_backup_target` call-site fix (D1), the "no phantom after a real run" consumer behavior, the stem-lookup removal path in `_cleanup_backups` (D3), plus repairs to core tests whose fixtures encoded the extensionless path.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/core/test_full_backup_state_extension.py` | 1 primary (scenario 15) + 4 regression tests | NEW (5 tests) |
| `tests/core/test_state_check.py` | — | MODIFY (2 tests) |
| `tests/core/test_dry_run_prediction.py` | — | MODIFY (1 test) |

New tests (MockFactory, `InMemoryStateManager`, MockShell — zero real I/O):
- `test_backup_target_records_full_with_qcow2_extension` — spy on `record_full_backup`; assert name arg == `f"{result.snapshot_name}.qcow2"` (spec scenario 15).
- `test_recorded_full_path_exists_on_disk` — after a FULL run, `get_full_backups()[0].path` resolves to the physical file.
- `test_second_run_creates_delta_not_full_after_recorded_full` — with an extended record + real file, `_backup_target` computes `needs_full=False`; run_backup receives no force_full; only one FULL record remains.
- `test_check_reports_no_phantom_fulls_for_extended_records` — `core.check_state()`/`core.check()` status ok; `_detect_phantom_fulls` empty for extended records whose files exist.
- `test_cleanup_backups_removes_record_via_stem_lookup` — `_cleanup_backups` passes `BackupInfo.name` (stem, per D5 `provider.list()`) to `remove_full_backup` and the extended record is removed (D3).

### Group: `integration-workaround-cleanup`

**Scope:** Every integration test whose helper/inline block papered over the bug (`_normalize_full_state`, `_align_recorded_full_with_disk`, and inline "Phase 2 quirk" re-record blocks). Workarounds are DELETED (Step 5) and the call sites are converted into assertions of the corrected behavior (Step 3). Requires libvirt; `-m integration`.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/integration/test_check_targets.py` | — | MODIFY (3 tests) + DELETE helper |
| `tests/integration/test_preserve_min.py` | — | MODIFY (2 tests) + DELETE helper |
| `tests/integration/test_coverage_gaps.py` | — | MODIFY (3 tests) |
| `tests/integration/test_dry_run.py` | — | MODIFY (1 test) |
| `tests/integration/test_rollback_retry.py` | — | MODIFY (1 test) |

### Group: `integration-behavior`

**Scope:** Integration tests that exercise FULL recording / phantom detection / reconcile / check paths end-to-end and must now assert the FUTURE corrected behavior (extended names, paths that exist, deltas on second run). Requires libvirt; `-m integration`.

| Test File | Scenarios count | Action |
|---|---|---|
| `tests/integration/test_reconcile.py` | — | MODIFY (1 test — stem-match assertion breaks) |
| `tests/integration/test_startup_validation.py` | — | MODIFY (1 test — add pre-deletion path-exists assertion) |
| `tests/integration/test_reconcile_targets.py` | — | MODIFY (1 test — strengthen name assertion) |
| `tests/integration/test_count_based_full.py` | — | MODIFY (2 tests — assert `.qcow2` name + existing path) |

---

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/mocks/mock_state.py` | `record_full_backup()` appends `.qcow2` to `name` when missing and derives `path` from the normalized name; `remove_full_backup()` normalizes the lookup name before exact match; add private `_to_extended_name()` helper mirroring `JsonStateManager` | Design D4: the mock mirrored the bug, which is why unit tests missed the regression. Contract must match production or `state-contract` fails. |
| `tests/state/test_manager.py` | Rewrite `test_set_and_get_last_full_backup`: record `"full-2024-01-01.qcow2"`; assert `name`, `timestamp`, `disk` and `path == Path(target)/name` | Old test recorded a stem and asserted the stem-derived (nonexistent) path — encodes the bug. Spec scenario "Full backup state saved and retrieved with disk". |
| `tests/state/test_manager.py` | Rewrite `test_full_backup_state_saved_and_retrieved`: extended name; assert `.qcow2` path round-trips across manager instances | Same bug encoding. Spec scenario "Recorded name carries the .qcow2 extension…". |
| `tests/state/test_manager.py` | Rewrite `test_record_and_get_full_backups`: extended name; assert name and derived path | Same. Spec scenario "Full backup recorded and retrieved". |
| `tests/state/test_manager.py` | Rewrite `test_multiple_fulls_tracked_per_target`: extended names (`full-2024-01-01.qcow2`, …) | Same. Spec scenario "get_full_backups returns all per-disk FULLs". |
| `tests/state/test_manager.py` | Rewrite `test_full_backups_json_old_format_auto_migrated`: old dict-format fixture with stem entries now loads with normalized `.qcow2` name/path (dict→list AND stem→extended migration both apply) | Load-time normalization (spec "Stem entry normalized on load"). |
| `tests/state/test_manager.py` | Rewrite `test_full_backups_json_new_format_loaded_as_is`: fixture entries carry `.qcow2`; assert `bucket_level` silently ignored and no rewrite of content | Old fixture used stems that now normalize — conflated two behaviors. Spec "Old JSON with bucket_level is read-tolerant" + "Already-extended entries load unchanged". |
| `tests/state/test_manager.py` | Rewrite `test_deduplicate_duplicate_full_entries`: duplicate stem entries normalize before dedup; assert remaining entries carry `.qcow2` | Normalization now runs BEFORE dedup (D2); name assertions must match extended form. |
| `tests/state/test_manager.py` | Rewrite `test_deduplicate_no_duplicates_noop`: assert names are extended | Same reason. |
| `tests/state/test_manager.py` | Rewrite `test_deduplicate_is_idempotent`: assert persisted names on disk are extended after first load | Same reason (write-back now also persists normalization). |
| `tests/state/test_manager.py` | Rewrite `test_reset_target_disk_state_clears_only_given_vm_disk`: use extended `full_vda`/`full_vdb`/`full_other` names (`…a1b2c3.qcow2`) | Recorded names are normalized to `.qcow2`; the old `full_vdb in backup_names` assertion would fail. Per-disk reset resolves via `vm_prefix` + disk field, so extended names keep semantics. |
| `tests/state/test_manager.py` | Rewrite `test_reset_target_disk_state_atomic`: assert `backups[0].name == full_vda` with `full_vda` extended | Same normalization reason. |
| `tests/state/test_manager.py` | Fixture hygiene: `test_reset_target_state_removes_from_full_backups`, `test_reset_target_state_saves_atomically`, and `test_inmemory_reset_target_state_removes_from_full_backups` — record extended names | Count-only assertions survive today, but stem fixtures would silently rely on normalization; keep fixtures honest. |
| `tests/core/test_state_check.py` | `test_check_state_consistent`: record `full.FULL.monthly.qcow2` and touch the `.qcow2` file (not the extensionless path) | Old test touched `backup_dir/full.FULL.monthly` while the fixed mock derives the `.qcow2` path — the phantom filter would now flag the FULL and break `status == "ok"`. |
| `tests/core/test_state_check.py` | `test_check_state_orphaned_dependency_detected`: touch the extended `full.FULL.monthly.qcow2` path | Same reason; without the fix the FULL becomes a phantom and status flips from `stale_deps` to `stale_fulls`. |
| `tests/core/test_dry_run_prediction.py` | `test_delta_prediction_uses_incremental_size_estimate`: touch `target_dir / f"{full_name}.qcow2"` (or record the extended name) | Test recorded a stem and touched the extensionless path; after the fix the phantom filter sees a missing file and the run predicts a FULL instead of the asserted delta. |
| `tests/integration/test_check_targets.py` | `test_check_real_targets_all_consistent`: delete `_normalize_full_state(state, target_dir)` call (line 191); replace with assertions: after run 1 `fulls[0].name.endswith(".qcow2")` and `fulls[0].path.exists()`; after run 2 exactly one FULL record remains and `check()` status is "ok" | Workaround removed (Step 5); the test must prove the corrected end-to-end behavior: real FULLs are never phantoms, second run creates a delta. |
| `tests/integration/test_check_targets.py` | `test_check_real_targets_broken_chain`: delete `_normalize_full_state` call (line 285); assert run 2/3 create incrementals (state FULL count stays 1) so the chain-break scenario still exercises real incrementals | Workaround removed; without it the old bug would re-create FULLs every run and the test would skip. |
| `tests/integration/test_check_targets.py` | `test_check_real_targets_after_retention`: delete `_normalize_full_state` call (line 614); assert `check()` status "ok" with no phantom cleanup needed | Workaround removed; corrected behavior assertion. |
| `tests/integration/test_preserve_min.py` | `test_source_disk_onchange_gate_skips_when_unchanged`: delete `_align_recorded_full_with_disk(state, target_dir)` call (line 700); assert recorded FULL `path.exists()` after run 1; second run still skips with "no disk changed" and file count unchanged | Workaround removed; the onchange baseline must survive across runs WITHOUT re-alignment (the bug cleared baselines via phantom cleanup). |
| `tests/integration/test_preserve_min.py` | `test_onchange_first_run_no_baseline_integration`: delete `_align_recorded_full_with_disk` call (line 958); assert recorded FULL path exists; gate closes on second run; no new files | Same reason. |
| `tests/integration/test_coverage_gaps.py` | `test_pipeline_continues_after_broken_chain_auto_recovery`: delete the inline re-record block (lines 205–214); assert `fulls[0].name.endswith(".qcow2")` and `fulls[0].path.exists()` right after run 1 | Workaround removed; corrected behavior: the recorded path must resolve to the real file so startup validation sees it. |
| `tests/integration/test_coverage_gaps.py` | `test_reconcile_detects_and_removes_stale_incremental_dep`: delete the inline re-record block (lines 345–355); assert recorded FULL path exists before manual inc deletion | Workaround removed; stale-dep reconciliation must run against a real (non-phantom) FULL record. |
| `tests/integration/test_coverage_gaps.py` | `test_startup_validation_preserves_corrupt_full_for_verify_gate`: delete the inline re-record block (lines 491–499); assert recorded FULL path exists before truncation | Workaround removed; startup validation must see the (corrupt but existing) file via the recorded path. |
| `tests/integration/test_dry_run.py` | `test_dry_run_incremental_predictions_approximate`: delete the re-record loop (lines 627–640); assert the recorded FULL name carries `.qcow2`; dry-run predicts a delta (backup_transfer) with no re-recording | Workaround removed; the dry-run phantom filter must see the anchor through the corrected record. |
| `tests/integration/test_rollback_retry.py` | `test_stopped_vm_failed_full_deletes_no_checkpoint`: delete the re-record block (lines 379–392); assert the seed FULL's recorded path exists before the second run | Workaround removed; startup validation must keep the seed FULL via the corrected path. |
| `tests/integration/test_reconcile.py` | `test_reconcile_command`: replace `tracked_names = {full.name for full in fulls_before if full.path.name == full_name}` (stem match) with `full.name == full_path.name` and `full.path.exists()` | After the fix `FullBackupInfo.path.name` carries `.qcow2` and `full_name = full_path.stem` no longer matches — the assertion would fail. Becomes a corrected-behavior assertion. |
| `tests/integration/test_startup_validation.py` | Phantom-detection test: before `first_full_path.unlink()`, add `assert state_fulls_before[0].path.exists()` and `state_fulls_before[0].name.endswith(".qcow2")` | Under the bug the recorded path never existed (phantom-by-default). The fix makes the pre-deletion state truthful; the new assertion verifies it. |
| `tests/integration/test_reconcile_targets.py` | In the FULL-deleted-then-reconciled test (line 767): rename `full_stem = fulls[0].name` to `full_name` and add `assert fulls[0].name.endswith(".qcow2")` | `fulls[0].name` is now extended; `remove_full_backup` tolerance keeps the test green, and the added assertion verifies the new invariant. |
| `tests/integration/test_count_based_full.py` | `test_first_backup_to_target_always_creates_full`: after `fulls_in_state` assertion, add `fulls_in_state[0].name.endswith(".qcow2")` and `fulls_in_state[0].path.exists()`; same for the chain-length tests that re-check `get_full_backups` | Step 6: verify the future corrected behavior after real backup runs (no phantom, real path). |

---

## Tests to Delete (Refactoring)

| File | Item | Why obsolete | Replacement |
|---|---|---|---|
| `tests/integration/test_check_targets.py` | Helper `_normalize_full_state` (lines 100–115) | Papers over the bug: re-records stem FULLs with `.qcow2` ("Phase 2 quirk"). The fix makes recorded paths real, so the helper is a no-op at best and a lie at worst. | Deleted; its 3 call sites (lines 191, 285, 614) replaced by corrected-behavior assertions (see Test Modifications). |
| `tests/integration/test_preserve_min.py` | Helper `_align_recorded_full_with_disk` (lines 122–143) | Same — re-aligns recorded paths to disk; encodes the extensionless-path bug. | Deleted; its 2 call sites (lines 700, 958) replaced by "recorded path exists / gate stays closed" assertions. |
| `tests/integration/test_coverage_gaps.py` | Inline re-record block in `test_pipeline_continues_after_broken_chain_auto_recovery` (lines 205–214) | Inline copy of the same "Phase 2 quirk" workaround. | Deleted; replaced with extended-name/path-exists assertions. |
| `tests/integration/test_coverage_gaps.py` | Inline re-record block in `test_reconcile_detects_and_removes_stale_incremental_dep` (lines 345–355) | Same. | Deleted; replaced with path-exists assertion. |
| `tests/integration/test_coverage_gaps.py` | Inline re-record block in `test_startup_validation_preserves_corrupt_full_for_verify_gate` (lines 491–499) | Same. | Deleted; replaced with path-exists assertion. |
| `tests/integration/test_dry_run.py` | Inline re-record loop in `test_dry_run_incremental_predictions_approximate` (lines 627–640) | Same workaround; dry-run prediction now sees the real anchor. | Deleted; replaced with extended-name assertion + delta prediction assert. |
| `tests/integration/test_rollback_retry.py` | Inline re-record block in `test_stopped_vm_failed_full_deletes_no_checkpoint` (lines 379–392) | Same workaround. | Deleted; replaced with seed-FULL path-exists assertion. |
| `tests/state/test_manager.py` | `test_set_and_get_last_full_backup` | REWRITE: records stem `"full-2024-01-01"` and asserts the extensionless derived path — encodes the bug. | Rewritten as `test_set_last_full_backup_roundtrips_with_disk` (extended name, disk field). |
| `tests/state/test_manager.py` | `test_full_backup_state_saved_and_retrieved` | REWRITE: same stem/path encoding. | Rewritten with extended name; round-trips across manager instances. |
| `tests/state/test_manager.py` | `test_record_and_get_full_backups` | REWRITE: same stem/path encoding. | Rewritten with extended name (periodic-full-backup scenario). |
| `tests/state/test_manager.py` | `test_multiple_fulls_tracked_per_target` | REWRITE: asserts stem names and stem paths. | Rewritten with extended names. |
| `tests/state/test_manager.py` | `test_full_backups_json_old_format_auto_migrated` | REWRITE: asserts stem name/path after load; the new load-time normalization changes the result. | Rewritten to assert `.qcow2` name/path (dual migration: dict→list + stem→extended). |
| `tests/state/test_manager.py` | `test_full_backups_json_new_format_loaded_as_is` | REWRITE: stem fixtures now normalize on load. | Rewritten with extended fixtures + bucket_level tolerance assertion. |
| `tests/state/test_manager.py` | `test_deduplicate_duplicate_full_entries` | REWRITE: stem-name assertions conflict with pre-dedup normalization (D2). | Rewritten asserting extended names; dedup ordering behavior preserved. |
| `tests/state/test_manager.py` | `test_deduplicate_no_duplicates_noop` | REWRITE: same. | Rewritten with extended names. |
| `tests/state/test_manager.py` | `test_deduplicate_is_idempotent` | REWRITE: same. | Rewritten with extended names on disk. |
| `tests/state/test_manager.py` | `test_reset_target_disk_state_clears_only_given_vm_disk` | REWRITE: records stems; `full_vdb in backup_names` fails once records normalize. | Rewritten with extended names. |
| `tests/state/test_manager.py` | `test_reset_target_disk_state_atomic` | REWRITE: asserts `backups[0].name == full_vda` (stem). | Rewritten with extended name. |

Note: `tests/mocks/mock_state.py` is intentionally NOT listed for deletion — it is a mock to be FIXED (see Test Modifications), per the task instructions.

---

## Risks & Edge Cases

- **Mixed-format duplicate entries during migration** (design.md risk 1) → normalization runs before dedup (D2). Covered by `test_load_mixed_stem_extended_twins_deduplicate_to_one` (state-unit) which writes a stem entry + its extended twin and asserts exactly one extended record survives.
- **Double `.qcow2` append on already-correct entries** (risk 2) → per-field `endswith(".qcow2")` guard. Covered by `test_record_full_backup_extends_name_and_derives_path` + `test_record_full_backup_idempotent_no_double_append` (record the same extended name twice) and `test_load_already_extended_entries_unchanged_no_rewrite` (load twice, assert byte-identical file → no write-back).
- **Per-field asymmetry** → `name` and `path` normalized independently. Covered by `test_load_repairs_asymmetric_entry_field_by_field` in both directions (stem name + extended path; extended name + stem path).
- **Mock/production divergence hides regressions again** (risk 3) → contract tests parametrized over `JsonStateManager` AND `InMemoryStateManager` for every record/remove/read contract (state-contract group, 9 tests) + mock-parity tests in `test_mock_state.py`. This is the exact gap that let commit 0811599 escape CI.
- **Downgrade to the buggy binary re-contaminates state** (risk 4, accepted) → reads remain safe and re-upgrade self-heals via D2. Covered by `test_load_normalizes_stem_entry_on_load` (proves a stem-contaminated file is repaired on the next load) and its idempotency companion (load the same stem file twice → stable extended result, one write-back).
- **Integration tests that relied on the workarounds change meaning** (risk 5) → every workaround deletion (Step 5) is paired with an explicit corrected-behavior assertion (Step 3); no test is silently weakened. Cross-checked: each deleted helper call site has a listed replacement assertion.
- **`set_last_full_backup` delegation** (D1) → `test_remove_full_backup_after_set_last_full_backup_delegation` proves the delegated write path normalizes identically.
- **Stem callers vs extended callers of `remove_full_backup`** (D3) → `_cleanup_backups` passes stems from `provider.list()` (D5); covered by `test_cleanup_backups_removes_record_via_stem_lookup` (core-unit) and both remove-lookup contract tests (state-contract).
- **Phantom cascade / onchange baseline clearing** — the user-visible failure mode: covered at integration level by the `test_preserve_min` onchange-gate tests and `test_check_targets` consistent-target tests after workaround removal (a second run must create a delta and keep the gate closed).

---

## Test Suite Discoveries (beyond the change scope)

- `tests/core/test_reconcile.py:193` — `test_reconcile_cleans_dependency_records_on_orphan_deletion` calls `record_full_backup("vda", str(target.path), full_name, datetime.now())` with **swapped argument order** (target_path/name/timestamp/disk all in the wrong slots). It passes only because the garbage record lands under target `"vda"`, which the reconcile flow never reads. Not blocking for this change, but worth fixing opportunistically.
- `tests/integration/test_restore.py` (per-disk restore test, ~line 753) — records a legacy stem FULL (`testvm.FULL.20250714`) that the fixed mock will normalize to `.qcow2`. The test's assertions (counts + stem-form dep lookups) survive, but the comment block above it documents a separate latent "SOURCE BUG" in `reset_target_disk_state` name resolution — unrelated to this change, flagged for the product owner.
- `tests/integration/test_backup_tree.py` and `tests/core/test_pipeline.py` build `BackupInfo` with `name=stem`, `path=extended` — consistent with D5 (`provider.list()` contract). No changes needed; they already model the provider contract correctly.
- `tests/core/test_schedule_summary.py` records stem FULL names but only counts them; survives the fix unchanged (hygiene-only candidate).
- `tests/integration/test_startup_validation.py` and `tests/integration/test_reconcile_targets.py` currently pass ONLY because the bug made every recorded FULL a phantom (deleting the file changed nothing observable). After the fix their semantics become truthful; they still pass but gain the strengthened assertions listed in Step 3.
- Contract tests for `remove_full_backup` are absent today in `tests/interfaces/test_state_manager.py` (only abstract-method presence is checked) — this is where the tolerant-lookup contract needs to be added.
