# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| snapshot-preserve-min | Snapshot preserve_min post-processing filter | preserve_min inactive (default) | `tests/core/test_preserve.py` | `test_preserve_min_inactive_default` | core-pipeline |
| snapshot-preserve-min | Snapshot preserve_min post-processing filter | preserve_min preserves newest snapshots | `tests/core/test_preserve.py` | `test_preserve_min_trim_excess_from_newest` | core-pipeline |
| snapshot-preserve-min | Snapshot preserve_min post-processing filter | preserve_min does not trigger when remove is small | `tests/core/test_preserve.py` | `test_preserve_min_no_trim_when_within_limit` | core-pipeline |
| snapshot-preserve-min | Snapshot preserve_min post-processing filter | preserve_min equals total snapshots | `tests/core/test_preserve.py` | `test_preserve_min_equals_total_no_blockcommit` | core-pipeline |
| snapshot-preserve-min | Snapshot preserve_min post-processing filter | preserve_min greater than total snapshots | `tests/core/test_preserve.py` | `test_preserve_min_exceeds_total_no_blockcommit` | core-pipeline |
| snapshot-preserve-min | Snapshot preserve_min post-processing filter | preserve_min applied after oldest-prefix | `tests/core/test_preserve.py` | `test_preserve_min_applied_after_oldest_prefix` | core-pipeline |
| snapshot-preserve-min | preserve_min ordering — trim from newest end of remove | Trimming moves newest remove items to keep | `tests/core/test_preserve.py` | `test_preserve_min_trims_newest_end_of_remove` | core-pipeline |
| snapshot-preserve-min | preserve_min does not affect target retention | Target retention unaffected by preserve_min | `tests/core/test_preserve.py` | `test_preserve_min_does_not_affect_target_retention` | core-pipeline |
| independent-target-onchange | Source-disk-based backup onchange gate | Gate opens when source disk has changed | `tests/core/test_pipeline.py` | `test_onchange_source_disk_changed_gate_opens` | core-pipeline |
| independent-target-onchange | Source-disk-based backup onchange gate | Gate skips when source disk unchanged | `tests/core/test_pipeline.py` | `test_onchange_source_disk_unchanged_gate_skips` | core-pipeline |
| independent-target-onchange | Source-disk-based backup onchange gate | Gate opens on first run (no baseline) | `tests/core/test_pipeline.py` | `test_onchange_first_run_no_baseline_gate_opens` | core-pipeline |
| independent-target-onchange | Source-disk-based backup onchange gate | Gate works without snapshots in state | `tests/core/test_pipeline.py` | `test_onchange_gate_works_without_snapshots` | core-pipeline |
| independent-target-onchange | Source-disk-based backup onchange gate | Gate works when snapshot_create is always and disk unchanged | `tests/core/test_pipeline.py` | `test_onchange_always_snapshot_disk_unchanged_gate_skips` | core-pipeline |
| independent-target-onchange | Change detector selection for backup gate | Allocation-size mode for backup gate | `tests/core/test_pipeline.py` | `test_onchange_allocation_size_detector_mode` | core-pipeline |
| independent-target-onchange | Change detector selection for backup gate | Allocation-map mode for backup gate | `tests/core/test_pipeline.py` | `test_onchange_allocation_map_detector_mode` | core-pipeline |
| independent-target-onchange | Per-target baseline update after successful backup | Baseline updated after successful backup | `tests/core/test_pipeline.py` | `test_onchange_baseline_updated_after_successful_backup` | core-pipeline |
| independent-target-onchange | Per-target baseline update after successful backup | Baseline not updated after failed backup | `tests/core/test_pipeline.py` | `test_onchange_baseline_not_updated_on_failure` | core-pipeline |
| independent-target-onchange | Per-target baseline update after successful backup | Baseline not updated when gate skips | `tests/core/test_pipeline.py` | `test_onchange_baseline_not_updated_when_gate_skips` | core-pipeline |
| independent-target-onchange | Onchange gate and retention separation | Retention runs when gate skips transfer | `tests/core/test_pipeline.py` | `test_onchange_skip_runs_retention_and_cleanup` | core-pipeline |
| independent-target-onchange | Detector fail-safe behavior for backup gate | Detector failure causes gate to open | `tests/core/test_pipeline.py` | `test_onchange_detector_failure_gate_opens_fail_safe` | core-pipeline |
| count-based-retention | RetentionPolicy has three fields | Default policy | `tests/config/test_model.py` | `test_retention_policy_defaults` | config-model |
| count-based-retention | RetentionPolicy has three fields | Snapshot policy with preserve_min | `tests/config/test_model.py` | `test_retention_policy_for_snapshots_with_preserve_min` | config-model |
| count-based-retention | RetentionPolicy has three fields | Target policy | `tests/config/test_model.py` | `test_retention_policy_for_targets` | config-model |
| count-based-retention | RetentionPolicy has three fields | preserve_min defaults to zero (inactive) | `tests/config/test_model.py` | `test_retention_policy_preserve_min_defaults_zero` | config-model |
| config-model | GlobalConfig default values | GlobalConfig default values | `tests/config/test_model.py` | `test_global_config_snapshot_preserve_min_default` | config-model |
| config-model | VMConfig dataclass | VMConfig with required fields | `tests/config/test_model.py` | `test_vm_config_required_fields` | config-model |
| config-model | VMConfig dataclass | VMConfig with targets | `tests/config/test_model.py` | `test_vm_config_with_targets` | config-model |
| config-model | GlobalConfig snapshot_preserve_min field | GlobalConfig default snapshot_preserve_min is 0 | `tests/config/test_model.py` | `test_global_config_snapshot_preserve_min_default` | config-model |
| config-model | GlobalConfig snapshot_preserve_min field | GlobalConfig snapshot_preserve_min is immutable | `tests/config/test_model.py` | `test_global_config_snapshot_preserve_min_immutable` | config-model |
| config-model | VMConfig snapshot_preserve_min field | VM inherits snapshot_preserve_min from global | `tests/config/test_resolver.py` | `test_vm_inherits_snapshot_preserve_min_from_global` | config-inheritance |
| config-model | VMConfig snapshot_preserve_min field | VM overrides global snapshot_preserve_min | `tests/config/test_resolver.py` | `test_vm_overrides_snapshot_preserve_min` | config-inheritance |
| config-model | VMConfig snapshot_preserve_min field | VM sets snapshot_preserve_min to 0 (explicitly inactive) | `tests/config/test_resolver.py` | `test_vm_sets_snapshot_preserve_min_to_zero` | config-inheritance |
| config-model | snapshot_preserve_min validation | Valid snapshot_preserve_min value | `tests/config/test_resolver.py` | `test_valid_snapshot_preserve_min_accepted` | config-inheritance |
| config-model | snapshot_preserve_min validation | Negative snapshot_preserve_min raises ConfigError | `tests/config/test_resolver.py` | `test_negative_snapshot_preserve_min_raises_config_error` | config-inheritance |
| change-detection | Source-disk-based backup onchange gate | Gate uses change detector, not snapshot names | `tests/core/test_pipeline.py` | `test_onchange_gate_uses_detector_not_snapshot_names` | core-pipeline |
| change-detection | Onchange gate and retention separation | Retention runs when gate skips transfer | `tests/core/test_pipeline.py` | `test_onchange_skip_runs_retention_and_cleanup` | core-pipeline |
| change-detection | Onchange gate and retention separation | Transfer skipped but retention cleans expired backups | `tests/core/test_pipeline.py` | `test_onchange_skip_cleans_expired_backups` | core-pipeline |
| state-management | IStateManager per-target backup allocation tracking | Write and read per-target backup allocation | `tests/interfaces/test_state_manager.py` | `test_inmemory_manager_implements_backup_allocation` | interfaces |
| state-management | IStateManager per-target backup allocation tracking | Missing target state returns None | `tests/interfaces/test_state_manager.py` | `test_inmemory_manager_implements_backup_allocation` | interfaces |
| state-management | IStateManager per-target backup allocation tracking | Per-target state is independent | `tests/interfaces/test_state_manager.py` | `test_per_target_state_independent` | interfaces |
| state-management | IStateManager per-target backup allocation tracking | Baseline updated after successful backup | `tests/core/test_pipeline.py` | `test_onchange_baseline_updated_after_successful_backup` | core-pipeline |
| state-management | IStateManager per-target backup allocation tracking | Baseline not updated after failed backup | `tests/core/test_pipeline.py` | `test_onchange_baseline_not_updated_on_failure` | core-pipeline |

## Delegation Groups

### Group: config-model
**Scope:** `tests/config/test_model.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | 7 (RetentionPolicy three fields, GlobalConfig/VMConfig snapshot_preserve_min fields) | MODIFY |

### Group: config-inheritance
**Scope:** `tests/config/test_resolver.py`, `tests/config/test_fixtures.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_resolver.py` | 5 (snapshot_preserve_min inheritance + validation) | MODIFY |
| `tests/config/test_fixtures.py` | 0 (make_global_config fixture update, preserve_min .toml fixture) | MODIFY |

### Group: core-pipeline
**Scope:** `tests/core/test_pipeline.py`, `tests/core/test_preserve.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | 18 (onchange gate rewrite + preserve_min filter) | MODIFY |
| `tests/core/test_preserve.py` | 8 (preserve_min post-processing filter) | MODIFY |

### Group: integration-onchange
**Scope:** `tests/integration/test_onchange.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_onchange.py` | 0 (full rewrite — new scenarios tested in integration-preserve-min) | MODIFY |

### Group: integration-preserve-min
**Scope:** `tests/integration/test_preserve_min.py` (NEW)
| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_preserve_min.py` | 6 (integration-level: real blockcommit, source-disk onchange, per-target baseline) | NEW |

### Group: interfaces
**Scope:** `tests/interfaces/test_state_manager.py`, `tests/interfaces/test_change_detector.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_state_manager.py` | 3 (last_backup_allocation contract — already tested, minor doc update) | MODIFY |
| `tests/interfaces/test_change_detector.py` | 0 (no spec changes — IChangeDetector interface unchanged) | MODIFY |

### Group: mocks
**Scope:** `tests/mocks/mock_modules.py`, `tests/mocks/mock_factory.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/mocks/mock_modules.py` | 0 (MockChangeDetector needs configurable `current_allocation` for gate tests) | MODIFY |
| `tests/mocks/mock_factory.py` | 0 (create_change_detector surfaces configurable change detector) | MODIFY |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/core/test_pipeline.py` | DELETE `test_onchange_backup_first_run_proceeds` | Tests old Approach B behavior — compares snapshot names via `provider.list(target)`. Replaced by source-disk-based detection tests. |
| `tests/core/test_pipeline.py` | DELETE `test_onchange_backup_no_change_skipped` | Tests old Approach B behavior — gate skips based on snapshot-name comparison. Replaced by `test_onchange_source_disk_unchanged_gate_skips`. |
| `tests/core/test_pipeline.py` | DELETE `test_onchange_backup_allocation_grew_proceeds` | Tests old Approach B behavior — gate opens based on missing backup name. Replaced by source-disk-based detection. |
| `tests/core/test_pipeline.py` | DELETE `test_onchange_baseline_updated_after_successful_transfer` | Tests that `set_last_backup_allocation` is NOT called under old Approach B. Now `set_last_backup_allocation` IS called (source-disk baseline). |
| `tests/core/test_pipeline.py` | DELETE `test_onchange_baseline_not_updated_on_failure` | Tests old Approach B assertion that `set_last_backup_allocation` is never called. Replaced by `test_onchange_baseline_not_updated_on_failure` with new semantics. |
| `tests/core/test_pipeline.py` | DELETE `test_onchange_approach_b_new_snapshot_on_target` | Tests old Approach B — snapshot-name-based gate. Replaced by source-disk detection. |
| `tests/core/test_pipeline.py` | DELETE `test_onchange_approach_b_all_backed_up` | Tests old Approach B — all snapshots on target → gate closed. Replaced by source-disk detection. |
| `tests/core/test_pipeline.py` | DELETE `test_onchange_approach_b_first_backup` | Tests old Approach B — empty target → gate open. Replaced by `test_onchange_first_run_no_baseline_gate_opens`. |
| `tests/core/test_pipeline.py` | DELETE `test_onchange_approach_b_always_snapshot_mode` | Tests old Approach B in `snapshot_create="always"` mode. Replaced by `test_onchange_always_snapshot_disk_unchanged_gate_skips`. |
| `tests/core/test_pipeline.py` | DELETE `test_onchange_approach_b_standalone_backup` | Tests old Approach B standalone backup mode. Duplicated by other Approach B tests. |
| `tests/core/test_pipeline.py` | DELETE `test_onchange_approach_b_no_allocation_access` | Tests that `get_last_backup_allocation` is NOT called under old Approach B. Now `get_last_backup_allocation` IS called (source-disk baseline). |
| `tests/core/test_pipeline.py` | DELETE `test_onchange_no_snapshots_skipped` | Old behavior: gate returns False on empty snapshots because snapshot-name comparison has nothing to compare. New behavior: gate queries source disk regardless of snapshots. |
| `tests/core/test_pipeline.py` | MODIFY `test_onchange_skip_runs_retention` | Keep the retention-separation logic but change the gate condition from snapshot-name comparison to detector-based `get_last_backup_allocation`. |
| `tests/core/test_pipeline.py` | MODIFY `test_gate_skip_retention_still_runs` | Same as above — keep retention/cleanup assertions, change gate mock setup. |
| `tests/core/test_pipeline.py` | MODIFY `test_onchange_skip_cleans_expired_backups` | Keep cleanup-while-skip logic, change gate mock setup to source-disk based. |
| `tests/core/test_pipeline.py` | MODIFY `test_always_mode_backup_gate_bypassed` | Keep the always-mode bypass test but update spy target to `_should_backup_onchange` (now detector-based). |
| `tests/integration/test_onchange.py` | DELETE `test_onchange_skips_when_unchanged` | Tests old Approach B — second run skips because all snapshot names are on target. Replaced by source-disk change detection integration tests. |
| `tests/integration/test_onchange.py` | DELETE `test_onchange_proceeds_when_changed` | Tests old Approach B — new snapshot name not on target → gate opens. Replaced by source-disk detection. |
| `tests/integration/test_onchange.py` | DELETE `test_onchange_approach_b_gate` | Tests old Approach B three-phase sequence. Replaced by source-disk detection integration tests. |
| `tests/integration/test_onchange.py` | DELETE `test_onchange_manual_deletion_recovery` | Tests old Approach B snapshot-name-based recovery validation. Replaced by source-disk-based self-healing tests. |
| `tests/config/test_model.py` | MODIFY `test_retention_policy_defaults` | Add assertion: `policy.preserve_min == 0` (third field with default). |
| `tests/config/test_model.py` | MODIFY `test_retention_policy_for_snapshots` | Construct with `preserve_min=24` and assert it is stored. |
| `tests/config/test_model.py` | MODIFY `test_retention_policy_immutable` | Add `preserve_min` mutation test — `FrozenInstanceError` on `policy.preserve_min = ...`. |
| `tests/config/test_model.py` | MODIFY `test_global_chain_length_defaults_are_sensible` | Add assertion: `cfg.snapshot_preserve_min == 0`. |
| `tests/config/test_model.py` | MODIFY `test_vm_config_required_fields` | Add assertion: `vm.snapshot_preserve_min is None` (default before resolution). |
| `tests/config/test_model.py` | MODIFY `test_global_config_immutable` | Add assertion: mutating `cfg.snapshot_preserve_min` raises `FrozenInstanceError`. |
| `tests/config/test_model.py` | ADD `test_retention_policy_for_snapshots_with_preserve_min` | Construct `RetentionPolicy(chain_length=168, keep_generations=1, preserve_min=24)` and assert all three fields. |
| `tests/config/test_model.py` | ADD `test_retention_policy_preserve_min_defaults_zero` | Construct `RetentionPolicy(chain_length=72)` without `preserve_min` — assert default is `0`. |
| `tests/config/test_model.py` | ADD `test_global_config_snapshot_preserve_min_default` | `GlobalConfig().snapshot_preserve_min == 0`. |
| `tests/config/test_model.py` | ADD `test_global_config_snapshot_preserve_min_immutable` | Mutating `snapshot_preserve_min` raises `FrozenInstanceError`. |
| `tests/config/test_resolver.py` | ADD `test_vm_inherits_snapshot_preserve_min_from_global` | Global `snapshot_preserve_min=24`, VM omits it → resolves to `24`. |
| `tests/config/test_resolver.py` | ADD `test_vm_overrides_snapshot_preserve_min` | Global `snapshot_preserve_min=24`, VM sets `48` → resolves to `48`. |
| `tests/config/test_resolver.py` | ADD `test_vm_sets_snapshot_preserve_min_to_zero` | Global `24`, VM sets `0` → resolves to `0` (disables floor). |
| `tests/config/test_resolver.py` | ADD `test_valid_snapshot_preserve_min_accepted` | TOML with `snapshot_preserve_min=24` → accepted. |
| `tests/config/test_resolver.py` | ADD `test_negative_snapshot_preserve_min_raises_config_error` | TOML with `snapshot_preserve_min=-1` → `ConfigError`. |
| `tests/config/test_fixtures.py` | MODIFY `test_make_global_config_chain_length_defaults` | Add assertion: `cfg.snapshot_preserve_min is None` (fixture default). |
| `tests/config/test_fixtures.py` | MODIFY `test_example_config_parseable` | Add assertion for `snapshot_preserve_min` default in global and VM. |
| `tests/config/test_fixtures.py` | ADD new fixture test | Verify `make_global_config(snapshot_preserve_min=24)` works. |
| `tests/core/test_preserve.py` | ADD `test_preserve_min_inactive_default` | 100 snapshots, chain_length=72, preserve_min=0 → no trimming, all 28 removed. |
| `tests/core/test_preserve.py` | ADD `test_preserve_min_trim_excess_from_newest` | 30 snapshots, chain_length=6, preserve_min=24 → trim to 6 remove, 24 keep. |
| `tests/core/test_preserve.py` | ADD `test_preserve_min_no_trim_when_within_limit` | 100 snapshots, chain_length=72, preserve_min=24 → 28 <= 76, no trim. |
| `tests/core/test_preserve.py` | ADD `test_preserve_min_equals_total_no_blockcommit` | 30 snapshots, preserve_min=30 → max_removable=0, remove empty. |
| `tests/core/test_preserve.py` | ADD `test_preserve_min_exceeds_total_no_blockcommit` | 30 snapshots, preserve_min=50 → max_removable=0, remove empty. |
| `tests/core/test_preserve.py` | ADD `test_preserve_min_applied_after_oldest_prefix` | 10 snapshots, chain_length=4, preserve_min=6 → remove=[s1..s4], keep=[s5..s10]. |
| `tests/core/test_preserve.py` | ADD `test_preserve_min_trims_newest_end_of_remove` | remove=[s1..s6], max_removable=3 → remove=[s1..s3], s4..s6 moved to keep. |
| `tests/core/test_preserve.py` | ADD `test_preserve_min_does_not_affect_target_retention` | preserve_min=24, target retention with keep_generations=2 → oldest chain removed regardless. |
| `tests/interfaces/test_state_manager.py` | ADD `test_per_target_state_independent` | `set_last_backup_allocation("/backup/A", 1000)`, `set_last_backup_allocation("/backup/B", 2000)` → verify independence. |
| `tests/mocks/mock_modules.py` | MODIFY `MockChangeDetector` | Add `current_allocation` parameter to constructor so tests can control the value returned by `has_changed()`. Use this to simulate "changed" vs "unchanged" source disk. |
| `tests/mocks/mock_factory.py` | MODIFY `create_change_detector` | Ensure `MockVMModuleFactory.create_change_detector()` returns a `MockChangeDetector` that the Core test can configure (or configure it with a default `current_allocation=2000` for gate tests). |
| `tests/integration/test_preserve_min.py` | NEW `test_preserve_min_keeps_newest_with_real_blockcommit` | Create 10 snapshots on real VM, run CLI with `snapshot_preserve_min=8`, verify only 2 oldest are blockcommitted. Uses `Core` with real virsh/qemu-img. |
| `tests/integration/test_preserve_min.py` | NEW `test_preserve_min_exceeds_total_no_blockcommit_integration` | Create 5 snapshots, set `snapshot_preserve_min=10`, verify no snapshots are blockcommitted. |
| `tests/integration/test_preserve_min.py` | NEW `test_source_disk_onchange_gate_opens_after_write` | Start VM, write data via `qemu-io`, back up with `backup_create="onchange"`. Verify gate opens (backup proceeds). Write more data, verify gate opens again. |
| `tests/integration/test_preserve_min.py` | NEW `test_source_disk_onchange_gate_skips_when_unchanged` | Write data, back up (baseline recorded). Run backup again without writing data. Verify gate skips ("disk unchanged since last backup — skipping transfer"). |
| `tests/integration/test_preserve_min.py` | NEW `test_per_target_baseline_independent` | Back up to target A (baseline A recorded). Write data. Back up to target B (new baseline, gate opens). Verify independent baselines — target A still shows "changed" because it has old baseline. |
| `tests/integration/test_preserve_min.py` | NEW `test_onchange_first_run_no_baseline_integration` | Fresh target, no prior state → `get_last_backup_allocation` returns `None` → gate opens, backup proceeds, baseline recorded. |

## Risks & Edge Cases

- **[Risk] `qemu-img map` performance on large disks** → Covered by existing `tests/modules/change/test_map_detector.py` which already tests map querying. The backup gate uses the same detector — no new performance test needed but the integration test `test_source_disk_onchange_gate_opens_after_write` implicitly exercises the map path when `change_detection_mode="allocation-map"`. Add parametrize over `change_detection_mode` to `tests/integration/test_preserve_min.py` onchange tests.

- **[Risk] Switching `change_detection_mode` mid-lifecycle** → New unit test `test_onchange_mode_switch_detects_changed` in `tests/core/test_pipeline.py`: store `last_backup_allocation` as an integer (allocation-size mode), then construct a detector that returns a different-type value. The gate should return True (fail-safe). Also covered by integration test: write data, backup in `allocation-size` mode, switch config to `allocation-map`, rerun — verify gate opens (type mismatch treated as changed).

- **[Risk] `preserve_min` > total snapshots** → Covered by unit tests `test_preserve_min_equals_total_no_blockcommit` and `test_preserve_min_exceeds_total_no_blockcommit` in `tests/core/test_preserve.py`, and integration test `test_preserve_min_exceeds_total_no_blockcommit_integration` in `tests/integration/test_preserve_min.py`.

- **[Trade-off] `RetentionPolicy` is no longer "exactly two fields"** → Covered by contract tests in `tests/config/test_model.py`: `test_retention_policy_defaults`, `test_retention_policy_for_snapshots_with_preserve_min`, `test_retention_policy_preserve_min_defaults_zero`, `test_retention_policy_immutable`. All `RetentionPolicy(...)` construction sites in tests must be reviewed for compatibility with the third field (defaults to 0 so no behavior change).

- **[Trade-off] `last_backup_allocation` semantics change** → Covered by `tests/interfaces/test_state_manager.py` (contract unchanged — methods already work), `tests/core/test_pipeline.py` new onchange tests (verify `set_last_backup_allocation` stores detector's `current_allocation`), and integration tests in `tests/integration/test_preserve_min.py` (verify storage/retrieval across runs with real state).

- **[Risk] Detector queries source disk even when VM is shut off** → Covered by new unit test `test_onchange_gate_works_vm_shut_off` in `tests/core/test_pipeline.py`: mock `virsh domblklist` to return base image path, verify detector queries it correctly and gate returns expected result. Also covered by integration test: shut off VM, write to base image via `qemu-io`, verify gate still detects changes.
