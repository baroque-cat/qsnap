# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `snapshot-provider` | External disk-only snapshot creation | Successful snapshot creation | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_success` | `snapshot-unit` |
| `snapshot-provider` | External disk-only snapshot creation | virsh command fails | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_virsh_fails` | `snapshot-unit` |
| `snapshot-provider` | External disk-only snapshot creation | virsh command times out | `tests/modules/snapshot/test_external.py` | `test_create_snapshot_timeout` | `snapshot-unit` |
| `snapshot-provider` | Snapshot listing via backing chain | Backing chain with snapshots | `tests/modules/snapshot/test_external.py` | `test_list_backing_chain_with_snapshots` | `snapshot-unit` |
| `snapshot-provider` | Snapshot listing via backing chain | No snapshots exist (fresh VM) | `tests/modules/snapshot/test_external.py` | `test_list_no_snapshots_fresh_vm` | `snapshot-unit` |
| `snapshot-provider` | Snapshot file deletion | Successful file deletion | `tests/modules/snapshot/test_external.py` | `test_delete_snapshot_success` | `snapshot-unit` |
| `snapshot-provider` | Snapshot file deletion | File does not exist | `tests/modules/snapshot/test_external.py` | `test_delete_snapshot_file_not_found` | `snapshot-unit` |
| `change-detection` | Change detection via allocation-size comparison | Allocation has grown — changes detected | `tests/modules/change/test_allocation.py` | `test_has_changed_allocation_grown` | `change-unit` |
| `change-detection` | Change detection via allocation-size comparison | Allocation unchanged — no changes | `tests/modules/change/test_allocation.py` | `test_has_changed_allocation_unchanged` | `change-unit` |
| `change-detection` | Change detection via allocation-size comparison | First run — no previous state | `tests/modules/change/test_allocation.py` | `test_has_changed_first_run_no_state` | `change-unit` |
| `change-detection` | Change detection via allocation-size comparison | virsh or qemu-img command fails | `tests/modules/change/test_allocation.py` | `test_has_changed_command_fails_failsafe` | `change-unit` |
| `lifecycle-manager` | Blockcommit snapshots into base image | Successful blockcommit of a single snapshot | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_single_snapshot_success` | `lifecycle-unit` |
| `lifecycle-manager` | Blockcommit snapshots into base image | Blockcommit fails — virsh returns error | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_virsh_error` | `lifecycle-unit` |
| `lifecycle-manager` | Blockcommit snapshots into base image | Empty snapshot list — nothing to merge | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_empty_list_no_op` | `lifecycle-unit` |
| `lifecycle-manager` | Blockcommit snapshots into base image | Blockcommit times out | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_timeout` | `lifecycle-unit` |
| `lifecycle-manager` | Blockcommit of multiple snapshots | Two snapshots merged sequentially | `tests/modules/lifecycle/test_blockcommit.py` | `test_blockcommit_multiple_snapshots_sequential` | `lifecycle-unit` |
| `backup-provider` | Transfer missing snapshots to backup target | New snapshot copied to empty target | `tests/modules/backup/test_copy.py` | `test_transfer_missing_new_snapshot_empty_target` | `backup-unit` |
| `backup-provider` | Transfer missing snapshots to backup target | Snapshot already exists on target — skipped | `tests/modules/backup/test_copy.py` | `test_transfer_missing_existing_snapshot_skipped` | `backup-unit` |
| `backup-provider` | Transfer missing snapshots to backup target | Incremental backup — rebase backing path | `tests/modules/backup/test_copy.py` | `test_transfer_incremental_rebase_backing_path` | `backup-unit` |
| `backup-provider` | Transfer missing snapshots to backup target | Non-incremental backup — no rebase | `tests/modules/backup/test_copy.py` | `test_transfer_non_incremental_no_rebase` | `backup-unit` |
| `backup-provider` | Transfer missing snapshots to backup target | Copy fails — disk full or permission error | `tests/modules/backup/test_copy.py` | `test_transfer_copy_fails_disk_full` | `backup-unit` |
| `backup-provider` | List existing backups on target | Target directory exists with backups | `tests/modules/backup/test_copy.py` | `test_list_backups_target_exists` | `backup-unit` |
| `backup-provider` | List existing backups on target | Target directory does not exist | `tests/modules/backup/test_copy.py` | `test_list_backups_target_not_exists` | `backup-unit` |
| `backup-provider` | List existing backups on target | Target directory exists but is empty | `tests/modules/backup/test_copy.py` | `test_list_backups_target_empty` | `backup-unit` |
| `backup-provider` | Delete backup from target | Successful backup deletion | `tests/modules/backup/test_copy.py` | `test_delete_backup_success` | `backup-unit` |
| `backup-provider` | Delete backup from target | Backup file does not exist | `tests/modules/backup/test_copy.py` | `test_delete_backup_file_not_found` | `backup-unit` |

## Delegation Groups

### Group: snapshot-unit

**Scope:** `tests/modules/snapshot/` — unit tests for `ExternalSnapshotProvider` with mocked `MockShell`.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/snapshot/test_external.py` | 7 | NEW |
| `tests/modules/snapshot/__init__.py` | — | NEW (package init) |

### Group: change-unit

**Scope:** `tests/modules/change/` — unit tests for `AllocationSizeDetector` with mocked `MockShell` and `InMemoryStateManager`.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/change/test_allocation.py` | 4 | NEW |
| `tests/modules/change/__init__.py` | — | NEW (package init) |

### Group: lifecycle-unit

**Scope:** `tests/modules/lifecycle/` — unit tests for `BlockCommitManager` with mocked `MockShell`.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/lifecycle/test_blockcommit.py` | 5 | NEW |
| `tests/modules/lifecycle/__init__.py` | — | NEW (package init) |

### Group: backup-unit

**Scope:** `tests/modules/backup/` — unit tests for `FileCopyBackupProvider` with mocked `MockShell`.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_copy.py` | 10 | NEW |
| `tests/modules/backup/__init__.py` | — | NEW (package init) |

### Group: interface-contracts

**Scope:** `tests/interfaces/` — contract tests for all 4 new modules.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_snapshot_provider.py` | 7 contract tests | NEW |
| `tests/interfaces/test_change_detector.py` | 5 contract tests | NEW |
| `tests/interfaces/test_lifecycle_manager.py` | 5 contract tests | NEW |
| `tests/interfaces/test_backup_provider.py` | 7 contract tests | NEW |

### Group: factory-unit

**Scope:** `tests/factory/` — update existing test for `DefaultFactory`.

| Test File | Scenarios | Action |
|---|---|---|
| `tests/factory/test_default.py` | 1 modified, 1 added | MODIFY |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/factory/test_default.py` | Replace parametrized `NotImplementedError` test with return-type verification test. Map each factory method to its ABC interface (`ISnapshotProvider`, `IBackupProvider`, `IChangeDetector`, `ILifecycleManager`). | After implementing 4 modules, factory methods return concrete instances — old `pytest.raises(NotImplementedError)` will fail. |

## Risks & Edge Cases

- **[R1] Blockcommit on running VM:** AppArmor may block. → Covered by `test_blockcommit_virsh_error` — module returns `CommitResult(success=False)` with stderr, regardless of error cause.
- **[R2] Incremental chain fragility:** Lost intermediate .qcow2 breaks subsequent backups. → `test_transfer_incremental_rebase_backing_path` asserts `qemu-img rebase -u` flag.
- **[R3] Large qcow2 copy:** `cp` copies entire file including backing data. → Non-Goal for MVP. Deferred.
- **[R4] Hardcoded disk="vda":** `test_create_snapshot_success` asserts virsh command contains `vda`. Accepted limitation.
- **[D1] Modules do NOT inherit Core:** Contract tests assert `not issubclass(ConcreteClass, Core)`.
- **[D3] domblklist for active image:** Detector resolves active path via `virsh domblklist`, not `base_image`.
- **[D4] Sequential blockcommit:** Multiple snapshots merged one-at-a-time, short-circuit on first failure.
- **[D5] rebase -u metadata-only:** `qemu-img rebase -u -b` modifies only qcow2 header, not data blocks.
