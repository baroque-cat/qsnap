# Test Plan: simplify-fork-restore

## 1. Coverage Map

Every `#### Scenario:` from every spec file under `openspec/changes/simplify-fork-restore/specs/` mapped to a concrete test.

| # | Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|---|
| 1 | fork-mode | qsnap fork command creates independent VM from snapshot | Fork creates standalone writable qcow2 from snapshot | tests/core/test_fork.py | test_fork_creates_standalone_qcow2_from_snapshot | fork-unit |
| 2 | fork-mode | qsnap fork command creates independent VM from snapshot | Fork creates standalone qcow2 from backup target | tests/core/test_fork.py | test_fork_creates_standalone_qcow2_from_backup_target | fork-unit |
| 3 | fork-mode | qsnap fork command creates independent VM from snapshot | Fork from incremental backup flattens chain | tests/core/test_fork.py | test_fork_from_incremental_flattens_chain | fork-unit |
| 4 | fork-mode | qsnap fork command creates independent VM from snapshot | Fork logs estimated size before converting | tests/core/test_fork.py | test_fork_logs_chain_size_before_convert | fork-unit |
| 5 | fork-mode | qsnap fork command creates independent VM from snapshot | Fork fails on nonexistent snapshot | tests/core/test_fork.py | test_fork_snapshot_not_found_returns_failure | fork-unit |
| 6 | fork-mode | Core.fork method | fork returns RestoreResult on success | tests/core/test_fork.py | test_fork_returns_restore_result_on_success | fork-unit |
| 7 | fork-mode | Core.fork method | fork fails on nonexistent snapshot | tests/core/test_fork.py | test_fork_nonexistent_returns_failure | fork-unit |
| 8 | restore-command | Restore command copies backup chain to target directory | Restore from snapshot replaces VM disk | tests/core/test_restore.py | test_restore_from_snapshot_replaces_vm_disk | restore-unit |
| 9 | restore-command | Restore command copies backup chain to target directory | Restore from backup target replaces VM disk | tests/core/test_restore.py | test_restore_from_backup_replaces_vm_disk | restore-unit |
| 10 | restore-command | Restore command copies backup chain to target directory | Restore aborts on running VM | tests/core/test_restore.py | test_restore_aborts_on_running_vm | restore-unit |
| 11 | restore-command | Restore command copies backup chain to target directory | Restore aborts on broken source chain | tests/core/test_restore.py | test_restore_aborts_on_broken_chain | restore-unit |
| 12 | restore-command | Restore command copies backup chain to target directory | Restore with --dry-run shows planned actions | tests/core/test_restore.py | test_restore_dry_run_shows_planned_actions | restore-unit |
| 13 | restore-command | Restore command copies backup chain to target directory | Restore with --yes skips confirmation | tests/core/test_restore.py | test_restore_yes_skips_confirmation | restore-unit |
| 14 | restore-command | Restore command copies backup chain to target directory | Restore prompts for confirmation without --yes | tests/core/test_restore.py | test_restore_prompts_confirmation_without_yes | restore-unit |
| 15 | restore-command | Restore command copies backup chain to target directory | Restore performs best-effort checkpoint cleanup | tests/core/test_restore.py | test_restore_best_effort_checkpoint_cleanup | restore-unit |
| 16 | restore-command | Restore command copies backup chain to target directory | Restore resets all VM state | tests/core/test_restore.py | test_restore_resets_all_vm_state | restore-unit |
| 17 | restore-command | Restore command copies backup chain to target directory | Restore from nonexistent snapshot | tests/core/test_restore.py | test_restore_nonexistent_snapshot_returns_failure | restore-unit |
| 18 | restore-command | Core.restore method | Restore from snapshot | tests/core/test_restore.py | test_core_restore_from_snapshot_replaces_disk | restore-unit |
| 19 | restore-command | Core.restore method | Restore from backup | tests/core/test_restore.py | test_core_restore_from_backup_replaces_disk | restore-unit |
| 20 | restore-command | Core.restore method | Restore fails on running VM | tests/core/test_restore.py | test_core_restore_fails_on_running_vm | restore-unit |
| 21 | cli-interface | CLI entry point | Help text | tests/cli/test_app.py | test_help_text_excludes_deploy | cli-app |
| 22 | cli-interface | CLI entry point | Subcommand dispatch | tests/cli/test_commands.py | test_run_subcommand_dispatches_to_core_run | cli-app |
| 23 | cli-interface | qsnap fork subcommand | Fork command succeeds | tests/cli/test_commands.py | test_fork_command_dispatches_to_core_fork | cli-app |
| 24 | cli-interface | qsnap fork subcommand | Fork command fails on missing snapshot | tests/cli/test_commands.py | test_fork_command_missing_snapshot_exit_one | cli-app |
| 25 | cli-interface | qsnap fork subcommand | Fork without --output fails | tests/cli/test_app.py | test_fork_without_output_fails | cli-app |
| 26 | cli-interface | qsnap restore subcommand | Restore command invocation | tests/cli/test_commands.py | test_handle_restore_dispatches_to_core_restore | cli-app |
| 27 | cli-interface | qsnap restore subcommand | Restore with --dry-run | tests/cli/test_commands.py | test_handle_restore_dry_run_flag | cli-app |
| 28 | cli-interface | qsnap restore subcommand | Restore with --yes skips confirmation | tests/cli/test_commands.py | test_handle_restore_yes_flag | cli-app |
| 29 | cli-interface | qsnap list backups --tree flag | Tree output for backup chains | tests/cli/test_tree.py | test_backup_tree_output_for_chains | cli-tree |
| 30 | cli-interface | qsnap list backups --tree flag | Tree output for orphan backups | tests/cli/test_tree.py | test_backup_tree_output_orphan_backups | cli-tree |
| 31 | change-detection | Change detection via allocation-size comparison | Allocation has grown — changes detected | tests/modules/change/test_allocation.py | test_allocation_grown_changes_detected | change-detection |
| 32 | change-detection | Change detection via allocation-size comparison | Allocation unchanged — no changes | tests/modules/change/test_allocation.py | test_allocation_unchanged_no_changes | change-detection |
| 33 | change-detection | Change detection via allocation-size comparison | First run — no previous state | tests/modules/change/test_allocation.py | test_first_run_no_previous_state | change-detection |
| 34 | change-detection | Change detection via allocation-size comparison | virsh or qemu-img command fails | tests/modules/change/test_allocation.py | test_command_failure_returns_changed_true | change-detection |
| 35 | change-detection | Change detection via allocation-size comparison | Default change detection mode is allocation-map | tests/config/test_model.py | test_change_detection_mode_default_is_allocation_map | change-detection |
| 36 | change-detection | Change detection via allocation-size comparison | Explicit allocation-size still works | tests/config/test_model.py | test_change_detection_mode_explicit_allocation_size | change-detection |
| 37 | list-commands | Core.list_backups supports tree grouping | Flat list when tree=False | tests/core/test_list_commands.py | test_list_backups_tree_false_flat_list | cli-tree |
| 38 | list-commands | Core.list_backups supports tree grouping | Tree grouping when tree=True | tests/core/test_list_commands.py | test_list_backups_tree_true_groups_by_full | cli-tree |
| 39 | list-commands | Core.list_backups supports tree grouping | Orphan backups grouped separately | tests/core/test_list_commands.py | test_list_backups_tree_true_orphans_grouped | cli-tree |
| 40 | list-commands | CLI _print_backup_tree function | Backup tree output format | tests/cli/test_tree.py | test_backup_tree_output_format | cli-tree |
| 41 | state-management | IStateManager reset_vm_state method | reset_vm_state clears all per-VM state | tests/state/test_manager.py | test_reset_vm_state_clears_all_state | state-management |
| 42 | state-management | IStateManager reset_vm_state method | reset_vm_state is atomic | tests/state/test_manager.py | test_reset_vm_state_atomic_write | state-management |
| 43 | state-management | IStateManager reset_vm_state method | reset_vm_state for nonexistent VM | tests/state/test_manager.py | test_reset_vm_state_nonexistent_vm_no_error | state-management |
| 44 | state-management | IStateManager reset_vm_state method | JsonStateManager implements reset_vm_state | tests/state/test_manager.py | test_json_manager_reset_vm_state_implementation | state-management |
| 45 | state-management | IStateManager reset_vm_state method | InMemoryStateManager implements reset_vm_state | tests/mocks/test_mock_state.py | test_inmemory_reset_vm_state_implementation | state-management |
| 46 | state-management | IStateManager reset_target_state method | reset_target_state clears all per-target state | tests/state/test_manager.py | test_reset_target_state_clears_all_state | state-management |
| 47 | state-management | IStateManager reset_target_state method | reset_target_state is atomic | tests/state/test_manager.py | test_reset_target_state_atomic_write | state-management |
| 48 | state-management | IStateManager reset_target_state method | reset_target_state for nonexistent target | tests/state/test_manager.py | test_reset_target_state_nonexistent_no_error | state-management |
| 49 | state-management | IStateManager reset_target_state method | JsonStateManager implements reset_target_state | tests/state/test_manager.py | test_json_manager_reset_target_state_implementation | state-management |
| 50 | state-management | IStateManager reset_target_state method | InMemoryStateManager implements reset_target_state | tests/mocks/test_mock_state.py | test_inmemory_reset_target_state_implementation | state-management |
| 51 | state-management | IStateManager implementations must implement reset methods | JsonStateManager implements reset_vm_state | tests/interfaces/test_state_manager.py | test_json_manager_implements_reset_vm_state | state-management |
| 52 | state-management | IStateManager implementations must implement reset methods | InMemoryStateManager implements reset_vm_state | tests/interfaces/test_state_manager.py | test_inmemory_implements_reset_vm_state | state-management |
| 53 | fork-mode | fork-mode integration | Fork from snapshot produces standalone qcow2 with no backing file | tests/integration/test_fork.py | test_fork_from_snapshot_produces_standalone_qcow2 | integration |
| 54 | fork-mode | fork-mode integration | Fork from incremental backup flattens chain | tests/integration/test_fork.py | test_fork_from_incremental_flattens_chain | integration |
| 55 | restore-command | restore-command integration | Restore replaces VM disk and VM boots from new disk | tests/integration/test_restore.py | test_restore_replaces_vm_disk_and_boots | integration |
| 56 | restore-command | restore-command integration | Restore resets all state (snapshots, baselines, FULLs, deps) | tests/integration/test_restore.py | test_restore_resets_all_state | integration |
| 57 | restore-command | restore-command integration | Restore cleans up libvirt checkpoints | tests/integration/test_restore.py | test_restore_cleanup_libvirt_checkpoints | integration |
| 58 | restore-command | restore-command integration | Restore with --dry-run shows planned actions | tests/integration/test_restore.py | test_restore_dry_run_no_changes | integration |
| 59 | list-commands | list-commands integration | list backups --tree shows correct chain hierarchy | tests/integration/test_backup_tree.py | test_list_backups_tree_shows_chain_hierarchy | integration |
| 60 | fork-mode | fork-mode integration | Fork uses --force-share for snapshot sources (no NBD) | tests/integration/test_fork.py | test_fork_uses_force_share_no_nbd | integration |
| 61 | restore-command | restore-command integration | Restore pre-checks chain integrity | tests/integration/test_restore.py | test_restore_prechecks_chain_integrity | integration |

## 2. Delegation Groups

### Group: fork-unit
**Scope:** `tests/core/test_fork.py`, `tests/cli/test_commands.py` (fork/deploy sections), `tests/cli/test_app.py` (fork/deploy args)

| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_fork.py | 1, 2, 3, 4, 5, 6, 7 | REWRITE |
| tests/cli/test_commands.py | 23, 24 | MODIFY |
| tests/cli/test_app.py | 25 | MODIFY |

### Group: restore-unit
**Scope:** `tests/core/test_restore.py` (NEW), `tests/cli/test_commands.py` (restore section), `tests/cli/test_app.py` (restore args)

| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_restore.py | 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 | NEW |
| tests/cli/test_commands.py | 26, 27, 28 | MODIFY |
| tests/cli/test_app.py | — | MODIFY |

### Group: cli-app
**Scope:** `tests/cli/test_app.py` (help text, deploy removal), `tests/cli/test_commands.py` (deploy removal)

| Test File | Scenarios | Action |
|---|---|---|
| tests/cli/test_app.py | 21 | MODIFY |
| tests/cli/test_commands.py | 22 | MODIFY |

### Group: cli-tree
**Scope:** `tests/cli/test_tree.py`, `tests/core/test_list_commands.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/cli/test_tree.py | 29, 30, 40 | MODIFY |
| tests/core/test_list_commands.py | 37, 38, 39 | MODIFY |

### Group: change-detection
**Scope:** `tests/config/test_model.py`, `tests/modules/change/test_allocation.py` (existing tests confirmed unchanged)

| Test File | Scenarios | Action |
|---|---|---|
| tests/config/test_model.py | 35, 36 | MODIFY |
| tests/modules/change/test_allocation.py | 31, 32, 33, 34 | UNCHANGED (existing tests already cover these) |

### Group: state-management
**Scope:** `tests/interfaces/test_state_manager.py`, `tests/state/test_manager.py`, `tests/mocks/mock_state.py`

| Test File | Scenarios | Action |
|---|---|---|
| tests/interfaces/test_state_manager.py | 51, 52 | MODIFY |
| tests/state/test_manager.py | 41, 42, 43, 44, 46, 47, 48, 49 | MODIFY |
| tests/mocks/mock_state.py | 45, 50 (implementation, tested via state/ and interfaces/) | MODIFY |

### Group: integration
**Scope:** `tests/integration/test_fork.py` (NEW), `tests/integration/test_restore.py` (NEW), `tests/integration/test_backup_tree.py` (NEW)

| Test File | Scenarios | Action |
|---|---|---|
| tests/integration/test_fork.py | 53, 54, 60 | NEW |
| tests/integration/test_restore.py | 55, 56, 57, 58, 61 | NEW |
| tests/integration/test_backup_tree.py | 59 | NEW |

## 3. Test Modifications

### 3.1 Deletions

**Entire test file rewrites (functionality removed):**

| File | Reason |
|---|---|
| `tests/core/test_fork.py` (all tests) | Fork rewritten: removes XML manipulation, UUID generation, virsh define, NBD pull-model, `--as-vm`, `--storage`, `--add-to-config`, `add_to_config`, `deploy`, old signature `fork(name, vm_name, storage_dir)`. All ~13 existing tests test removed functionality. Rewrite from scratch. |
| `tests/e2e/test_restore.py` | Currently a placeholder (skips). Must be rewritten for new restore semantics (VM disk replacement rather than chain copying). |

**Individual tests to delete:**

| File | Test to Delete | Reason |
|---|---|---|
| `tests/core/test_fork.py` | `test_fork_direct_convert_stopped_vm` | Tests old NBD vs direct decision logic — replaced by always-direct `qemu-img convert --force-share` |
| `tests/core/test_fork.py` | `test_fork_nbd_running_vm` | NBD pull-model removed from fork (D1) |
| `tests/core/test_fork.py` | `test_fork_chain_size_estimation_uses_force_share` | Kept but rewritten — signature changed |
| `tests/core/test_fork.py` | `test_fork_defines_new_libvirt_vm_with_modified_xml` | virsh define removed |
| `tests/core/test_fork.py` | `test_fork_from_backup_resolves_via_backup_provider` | Kept but rewritten — signature changed |
| `tests/core/test_fork.py` | `test_fork_add_to_config_appends_vm_block` | --add-to-config removed |
| `tests/core/test_fork.py` | `test_fork_returns_restore_result_on_success` | Kept but rewritten — signature changed |
| `tests/core/test_fork.py` | `test_fork_snapshot_not_found_returns_failure` | Kept but rewritten — signature changed |
| `tests/core/test_fork.py` | `test_fork_generates_new_uuid_not_source_vm_uuid` | UUID generation removed |
| `tests/core/test_fork.py` | `test_fork_logs_chain_size_before_convert` | Kept but rewritten — uses `qemu-img info --backing-chain --force-share` |
| `tests/core/test_fork.py` | `test_deploy_full_backup_delegates_to_fork` | deploy command removed |
| `tests/core/test_fork.py` | `test_deploy_incremental_backup_flattens_chain` | deploy command removed |
| `tests/core/test_fork.py` | `test_deploy_delegates_to_fork` | deploy command removed |
| `tests/core/test_fork.py` | `test_fork_warns_when_source_vm_running` | VM state check removed from fork — always uses --force-share |
| `tests/core/test_fork.py` | `test_core_fork_method_succeeds` | Replaced with new test for new signature |
| `tests/cli/test_commands.py` | `test_deploy_command_dispatches_to_core_deploy` | deploy handler removed |
| `tests/cli/test_commands.py` | `test_deploy_command_storage_and_add_to_config_flags` | deploy handler removed |
| `tests/cli/test_commands.py` | `test_fork_command_dispatches_to_core_fork` | Modified — new CLI args (--output instead of --as-vm/--storage) |
| `tests/cli/test_commands.py` | `test_fork_command_add_to_config_flag` | --add-to-config removed |
| `tests/cli/test_commands.py` | `test_handle_restore_dispatches_to_core_restore_with_positional_args` | Modified — target_dir positional arg removed |
| `tests/cli/test_commands.py` | `test_handle_restore_nonexistent_backup_returns_exit_1` | Modified — new signature |
| `tests/cli/test_commands.py` | `test_handle_restore_missing_target_dir_returns_exit_1` | target_dir removed — test deleted |
| `tests/cli/test_app.py` | `test_restore_subcommand_parses_positional_args` | target_dir positional arg removed |
| `tests/cli/test_app.py` | `test_help_text_lists_subcommands_and_flags` | Modified — deploy removed, restore args changed, fork args changed |

**Imports and references to clean up:**

| File | Change |
|---|---|
| `tests/cli/test_commands.py` | Remove `handle_deploy` import from `qsnap.cli.commands` |
| `tests/cli/test_commands.py` | Remove `core.deploy` mock setup in `_make_mock_core()` |
| `tests/cli/test_commands.py` | Remove `handle_deploy` import line from top of file |
| `tests/core/test_fork.py` | Remove `import uuid as uuid_module` (no UUID generation) |
| `tests/core/test_fork.py` | Remove `from xml.etree import ElementTree as ET` (no XML manipulation) |
| `tests/core/test_fork.py` | Remove `SAMPLE_SOURCE_XML` constant (no XML manipulation) |

### 3.2 Modifications (Existing Tests)

| File | Test | Change | Reason |
|---|---|---|---|
| `tests/core/test_fork.py` | Entire file | Rewrite: new tests for `core.fork(name, output_path, vm_filter=None)` with `qemu-img convert --force-share -O qcow2` only. No XML, no virsh define, no NBD, no deploy. | Fork completely redesigned (specs: fork-mode/spec.md) |
| `tests/cli/test_commands.py` | `test_fork_command_dispatches_to_core_fork` | Update: args change from `fork snap1 --as-vm newvm --storage /tmp/storage` to `fork snap1 --output /tmp/output.qcow2`. Core.fork called with `(snap_name, output_path, vm_filter)` | CLI spec: fork subcommand |
| `tests/cli/test_commands.py` | `test_fork_command_missing_snapshot_exit_one` | Update: same dispatch test but with new Core.fork signature | CLI spec: fork subcommand |
| `tests/cli/test_commands.py` | `test_handle_restore_dispatches_to_core_restore_with_positional_args` | Update: `restore SNAP [VM]` (no target_dir positional arg). Core.restore called with `(snap_name, vm_filter=None)` | CLI spec: restore subcommand |
| `tests/cli/test_commands.py` | `test_handle_restore_nonexistent_backup_returns_exit_1` | Update: same dispatch test but with new signature | CLI spec: restore subcommand |
| `tests/cli/test_commands.py` | `_make_mock_core()` helper | Remove `core.deploy` mock; update `core.fork` mock to new signature; update `core.restore` mock to new signature | deploy removed, fork/restore signatures changed |
| `tests/cli/test_app.py` | `test_help_text_lists_subcommands_and_flags` | Update: remove `"deploy"` from expected subcommand list; add `--output` to fork args; remove target_dir from restore; add `--dry-run` and `--yes` to restore args | CLI spec: entry point |
| `tests/cli/test_app.py` | `test_restore_subcommand_parses_positional_args` | Delete (tested old target_dir arg — removed) | CLI spec: restore subcommand |
| `tests/cli/test_app.py` | New test needed: `test_fork_parses_output_flag` | Add: verifies `--output` is a required argument for fork | CLI spec: fork subcommand |
| `tests/cli/test_app.py` | New test needed: `test_restore_parses_dry_run_flag` | Add: verifies `--dry-run` flag on restore | CLI spec: restore subcommand |
| `tests/cli/test_app.py` | New test needed: `test_restore_parses_yes_flag` | Add: verifies `--yes` flag on restore | CLI spec: restore subcommand |
| `tests/cli/test_app.py` | New test needed: `test_deploy_not_in_help` | Verify deploy does NOT appear in help text | CLI spec: deploy removed |
| `tests/cli/test_app.py` | New test needed: `test_deploy_not_in_dispatch_map` | Verify `"deploy"` not in `_DISPATCH` map | CLI spec: deploy removed |
| `tests/cli/test_tree.py` | `_make_list_args()` helper | Add `list_subcommand="backups"` option to helper | list-commands spec |
| `tests/cli/test_tree.py` | New test: `test_backup_tree_output_for_chains` | Verifies backup tree output with 2 FULL chains | list-commands spec |
| `tests/cli/test_tree.py` | New test: `test_backup_tree_output_orphan_backups` | Verifies orphan backups under `(orphan)` header | list-commands spec |
| `tests/cli/test_tree.py` | New test: `test_backup_tree_output_format` | Verifies exact indentation format matches spec example | list-commands spec |
| `tests/core/test_list_commands.py` | New test: `test_list_backups_tree_false_flat_list` | Verifies flat list when tree=False | list-commands spec |
| `tests/core/test_list_commands.py` | New test: `test_list_backups_tree_true_groups_by_full` | Verifies grouping by FULL anchor when tree=True | list-commands spec |
| `tests/core/test_list_commands.py` | New test: `test_list_backups_tree_true_orphans_grouped` | Verifies orphans under `"__orphan__"` key | list-commands spec |
| `tests/interfaces/test_state_manager.py` | New test: `test_istate_manager_reset_vm_state_abstract` | Verifies reset_vm_state is in `__abstractmethods__` | state-management spec |
| `tests/interfaces/test_state_manager.py` | New test: `test_istate_manager_reset_target_state_abstract` | Verifies reset_target_state is in `__abstractmethods__` | state-management spec |
| `tests/interfaces/test_state_manager.py` | New test: `test_istate_manager_reset_methods_missing_causes_typeerror` | Verifies TypeError on missing reset methods | state-management spec |
| `tests/interfaces/test_state_manager.py` | New test: `test_json_manager_implements_reset_vm_state` | Verifies JsonStateManager has callable reset_vm_state | state-management spec |
| `tests/interfaces/test_state_manager.py` | New test: `test_inmemory_implements_reset_vm_state` | Verifies InMemoryStateManager has callable reset_vm_state | state-management spec |
| `tests/state/test_manager.py` | New test: `test_reset_vm_state_clears_all_state` | Verifies snapshots, last_allocation, deferred_operations all cleared | state-management spec |
| `tests/state/test_manager.py` | New test: `test_reset_vm_state_atomic_write` | Verifies atomic write pattern (tmp + os.replace) | state-management spec |
| `tests/state/test_manager.py` | New test: `test_reset_vm_state_nonexistent_vm_no_error` | Verifies no error for nonexistent VM | state-management spec |
| `tests/state/test_manager.py` | New test: `test_reset_target_state_clears_all_state` | Verifies full_backups, incremental_deps, backup_allocation all cleared | state-management spec |
| `tests/state/test_manager.py` | New test: `test_reset_target_state_atomic_write` | Verifies atomic writes for all three target state files | state-management spec |
| `tests/state/test_manager.py` | New test: `test_reset_target_state_nonexistent_no_error` | Verifies no error for nonexistent target | state-management spec |
| `tests/mocks/mock_state.py` | Add `reset_vm_state()` method | Implement reset_vm_state clearing snapshots, last_allocation, deferred_operations | state-management spec |
| `tests/mocks/mock_state.py` | Add `reset_target_state()` method | Implement reset_target_state clearing full_backups, dependencies, target_state | state-management spec |
| `tests/config/test_model.py` | New test: `test_change_detection_mode_default_is_allocation_map` | Verifies VMConfig default is "allocation-map" | change-detection spec |
| `tests/config/test_model.py` | New test: `test_change_detection_mode_explicit_allocation_size` | Verifies explicit "allocation-size" still works | change-detection spec |

### 3.3 Integration Test Changes

| File | Test | Change | Reason |
|---|---|---|---|
| `tests/e2e/test_restore.py` | Entire file | Rewrite: new test `test_restore_from_backup_boots_vm` — take backup, restore to existing VM (disk replacement), verify VM starts from new disk | Restore redesigned as VM disk replacement |
| `tests/integration/test_fork.py` | NEW FILE | `test_fork_from_snapshot_produces_standalone_qcow2` — create snapshot, fork it, verify no backing file, file is qcow2 | fork-mode integration |
| `tests/integration/test_fork.py` | NEW FILE | `test_fork_from_incremental_flattens_chain` — create FULL + 2 incrementals, fork the incremental, verify standalone result | fork-mode integration |
| `tests/integration/test_fork.py` | NEW FILE | `test_fork_uses_force_share_no_nbd` — running VM, fork a snapshot, verify `--force-share` in command, verify no NBD | fork-mode integration (D1) |
| `tests/integration/test_restore.py` | NEW FILE | `test_restore_replaces_vm_disk_and_boots` — stop VM, restore from snapshot, verify base image replaced, VM starts | restore-command integration |
| `tests/integration/test_restore.py` | NEW FILE | `test_restore_resets_all_state` — verify after restore, snapshots cleared, FULLs cleared, deps cleared, allocation baselines gone | restore-command integration |
| `tests/integration/test_restore.py` | NEW FILE | `test_restore_cleanup_libvirt_checkpoints` — verify qsnap-* checkpoints deleted after restore | restore-command integration (D5) |
| `tests/integration/test_restore.py` | NEW FILE | `test_restore_dry_run_no_changes` — verify --dry-run shows planned actions, no files modified | restore-command integration |
| `tests/integration/test_restore.py` | NEW FILE | `test_restore_prechecks_chain_integrity` — create broken chain, verify restore aborts before modification | restore-command integration (D6) |
| `tests/integration/test_backup_tree.py` | NEW FILE | `test_list_backups_tree_shows_chain_hierarchy` — create 2 FULLs with increments, verify tree output matches expected indentation | list-commands integration |

## 4. Risk Coverage

Edge cases from `design.md` Risks / Trade-offs section:

| Risk | Mitigation in Design | Test Coverage |
|---|---|---|
| Fork on active layer of running VM may produce inconsistent image | Documented in --help, recommend stopping VM | **Unit:** `test_fork_warns_active_layer_inconsistency` in `tests/core/test_fork.py` — verifies WARNING log when forking from active (domblklist-matched) snapshot of running VM |
| Restore is destructive — replaces VM disk, deletes snapshots, resets state | --dry-run flag, --yes flag, confirmation prompt | **Unit:** `test_restore_dry_run_shows_planned_actions` (scenario 12), `test_restore_prompts_confirmation_without_yes` (scenario 14), `test_restore_yes_skips_confirmation` (scenario 13) in `tests/core/test_restore.py` |
| Restore from target with broken backing chain | Pre-restore scan_backing_chain() check | **Unit:** `test_restore_aborts_on_broken_chain` (scenario 11) in `tests/core/test_restore.py`; **Integration:** `test_restore_prechecks_chain_integrity` in `tests/integration/test_restore.py` |
| Checkpoint cleanup fails (stale/invalid checkpoints) | Best-effort deletion, WARNING log, does not block restore | **Unit:** `test_restore_best_effort_checkpoint_cleanup` (scenario 15) in `tests/core/test_restore.py` — mock checkpoint-delete failure, verify WARNING logged, restore still succeeds |
| reset_vm_state() clears deferred operations | Correct behavior — old deferred ops meaningless after disk replacement | **Unit:** `test_restore_resets_all_vm_state` (scenario 16) in `tests/core/test_restore.py` — verify deferred_operations cleared as part of state reset |
| allocation-map default is slower | Sensitivity > speed; operators can opt back to allocation-size | **Unit:** `test_change_detection_mode_default_is_allocation_map` (scenario 35) and `test_change_detection_mode_explicit_allocation_size` (scenario 36) in `tests/config/test_model.py` |
| deploy command removal breaks scripts | Migration note; operators use `qsnap fork` directly | **Unit:** `test_help_text_excludes_deploy` (scenario 21) in `tests/cli/test_app.py`, `test_deploy_not_in_dispatch_map` in `tests/cli/test_app.py` |
| Fork without --output should fail with argparse error | Required --output flag | **Unit:** `test_fork_without_output_fails` (scenario 25) in `tests/cli/test_app.py` |
| Restore from nonexistent snapshot returns clear error | Error handling in Core.restore | **Unit:** `test_restore_nonexistent_snapshot_returns_failure` (scenario 17) in `tests/core/test_restore.py` |
| Atomic tempfile + mv for restore — crash during convert leaves original intact | D2: writes to .tmp path, then mv to base_image | **Unit:** `test_restore_atomic_replace_preserves_original_on_crash` in `tests/core/test_restore.py` — mock os.replace to fail, verify base_image unchanged |
| reset_target_state atomic — crash during write leaves state intact | D4: atomic writes for all three state files | **Unit:** `test_reset_target_state_atomic_write` (scenario 47) in `tests/state/test_manager.py` |
| reset_vm_state for nonexistent VM — no error | Graceful handling | **Unit:** `test_reset_vm_state_nonexistent_vm_no_error` (scenario 43) in `tests/state/test_manager.py` |
| Restore requires VM stopped — running VM aborted immediately | D3: is_vm_running() check at start, no files modified | **Unit:** `test_restore_aborts_on_running_vm` (scenario 10) in `tests/core/test_restore.py`; **Unit:** `test_core_restore_fails_on_running_vm` (scenario 20) |

## Test Execution Summary

```bash
# Unit + mock + contract (fast, no I/O — covers all groups except integration):
poetry run pytest tests/ -m "not integration and not stress and not e2e"

# Integration only (needs libvirt):
poetry run pytest tests/integration/ -m integration -v

# E2E only (needs libvirt + disposable VM):
poetry run pytest tests/e2e/ -m e2e -v

# All tests:
poetry run pytest tests/ -m ""
```

**Total new/modified test files:** 15
**Total brand-new test files:** 3 (`tests/core/test_restore.py`, `tests/integration/test_fork.py`, `tests/integration/test_restore.py`, `tests/integration/test_backup_tree.py`)
**Total rewritten test files:** 2 (`tests/core/test_fork.py`, `tests/e2e/test_restore.py`)
**Total modified existing test files:** 9
**Total new test cases (across all files):** 61 spec-mapped scenarios + ~12 risk/edge cases = ~73 test cases
