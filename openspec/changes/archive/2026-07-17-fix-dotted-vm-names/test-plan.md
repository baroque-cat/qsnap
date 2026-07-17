# QA Strategy & Test Plan

## Overview

This test plan provides complete spec-to-test traceability for the `fix-dotted-vm-names` change. The change has two core modifications:

1. **Breaking interface change**: `IBackupProvider.create_full_backup()` gains `vm_name: str` as its first positional parameter
2. **Pure-function rewrite**: `parse_timestamp()` switches from `split(".")[-1]` + `strptime` to regex-based pattern matching

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | Uncompressed full backup succeeds (stopped VM) | `tests/modules/backup/test_copy.py` | `test_create_full_backup_uncompressed_stopped_vm` | `file-copy-unit` |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | Compressed full backup succeeds (stopped VM) | `tests/modules/backup/test_copy.py` | `test_create_full_backup_compressed_stopped_vm` | `file-copy-unit` |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | NBD full backup succeeds (running VM) | `tests/modules/backup/test_copy.py` | `test_create_full_backup_nbd_running_vm_succeeds` | `file-copy-unit` |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | NBD full backup ignores compress flag | `tests/modules/backup/test_copy.py` | `test_nbd_full_backup_ignores_compress_flag` | `file-copy-unit` |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | Dotted VM name is passed untruncated to virsh dominfo | `tests/modules/backup/test_copy.py` | `test_create_full_backup_dotted_vm_name` | `file-copy-unit` |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | transfer_missing passes vm_config.name to create_full_backup | `tests/modules/backup/test_copy.py` | `test_transfer_missing_passes_vm_name_to_create_full` | `file-copy-unit` |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL no longer raises NotImplementedError | `tests/modules/backup/test_bitmap.py` | `test_bitmap_full_backup_does_not_raise_not_implemented` | `bitmap-unit` |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL does not create checkpoint | `tests/modules/backup/test_bitmap.py` | `test_bitmap_full_backup_no_checkpoint` | `bitmap-unit` |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bucket-driven FULL works for bitmap targets | `tests/core/test_pipeline.py` | `test_full_creation_works_for_file_copy_and_bitmap` | `core-unit` |
| backup-provider | BitmapBackupProvider.create_full_backup implemented via NBD | Bitmap FULL with dotted VM name | `tests/modules/backup/test_bitmap.py` | `test_bitmap_create_full_backup_dotted_vm_name` | `bitmap-unit` |
| parsing-utils | Shared timestamp parser | Parse long-format timestamp from snapshot name with disk suffix | `tests/utils/test_parsing.py` | `test_parse_timestamp_long_format_from_filename` | `parsing-utils-unit` |
| parsing-utils | Shared timestamp parser | Parse long-format timestamp from dotted VM name | `tests/utils/test_parsing.py` | `test_parse_timestamp_dotted_vm_name` | `parsing-utils-unit` |
| parsing-utils | Shared timestamp parser | Parse short-format timestamp | `tests/utils/test_parsing.py` | `test_parse_timestamp_short_format` | `parsing-utils-unit` |
| parsing-utils | Shared timestamp parser | Parse long-iso-format timestamp with timezone offset | `tests/utils/test_parsing.py` | `test_parse_timestamp_long_iso_format` | `parsing-utils-unit` |
| parsing-utils | Shared timestamp parser | Parse timestamp from FULL backup name | `tests/utils/test_parsing.py` | `test_parse_timestamp_full_backup_name` | `parsing-utils-unit` |
| parsing-utils | Shared timestamp parser | Parse timestamp with collision suffix | `tests/utils/test_parsing.py` | `test_parse_timestamp_collision_suffix` | `parsing-utils-unit` |
| parsing-utils | Shared timestamp parser | Fall back to file mtime | `tests/utils/test_parsing.py` | `test_parse_timestamp_falls_back_to_mtime` | `parsing-utils-unit` |
| parsing-utils | Shared timestamp parser | Long-iso pattern takes priority over long | `tests/utils/test_parsing.py` | `test_parse_timestamp_long_iso_priority_over_long` | `parsing-utils-unit` |
| live-vm-full-backup | VM running-state detection for FULL backup method selection | Running VM triggers NBD-based FULL backup | `tests/modules/backup/test_copy.py` | `test_create_full_backup_nbd_running_vm_succeeds` | `file-copy-unit` |
| live-vm-full-backup | VM running-state detection for FULL backup method selection | Stopped VM triggers direct convert FULL backup | `tests/modules/backup/test_copy.py` | `test_create_full_backup_direct_stopped_vm_succeeds` | `file-copy-unit` |
| live-vm-full-backup | VM running-state detection for FULL backup method selection | VM state detection failure falls back to direct convert with warning | `tests/modules/backup/test_copy.py` | `test_create_full_backup_vm_state_detection_fails_falls_back` | `file-copy-unit` |
| live-vm-full-backup | VM running-state detection for FULL backup method selection | Dotted VM name passed untruncated to is_vm_running | `tests/modules/backup/test_copy.py` | `test_create_full_backup_dotted_vm_name_passed_to_is_vm_running` | `file-copy-unit` |
| live-vm-full-backup | VM running-state detection for FULL backup method selection | Core passes vm_config.name to create_full_backup | `tests/core/test_pipeline.py` | `test_core_passes_vm_name_to_create_full_backup` | `core-unit` |

## Delegation Groups

### Group: file-copy-unit

**Scope:** `tests/modules/backup/test_copy.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_copy.py` | 8 | MODIFY (existing tests: update `create_full_backup()` call sites to add `vm_name` positional arg) + NEW (3 new test functions for dotted VM name scenarios) |

### Group: bitmap-unit

**Scope:** `tests/modules/backup/test_bitmap.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | 3 | MODIFY (existing tests: update `create_full_backup()` call sites to add `vm_name` positional arg) + NEW (1 new test function for dotted VM name) |

### Group: parsing-utils-unit

**Scope:** `tests/utils/test_parsing.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_parsing.py` | 8 | MODIFY (fix existing `test_parse_timestamp_long_format_from_filename` test, rename to match new semantics) + NEW (6 new test functions) |

### Group: core-unit

**Scope:** `tests/core/test_pipeline.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | 2 | MODIFY (5 spy sites: update `wraps=backup_provider.create_full_backup` to match new signature; verify `vm_name` is passed) + NEW (1 test: `test_core_passes_vm_name_to_create_full_backup`) |

### Group: mock-backup-provider

**Scope:** `tests/mocks/mock_modules.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/mocks/mock_modules.py` | 0 (mechanical) | MODIFY (2 mock `create_full_backup()` implementations: add `vm_name: str` as first positional parameter) |

### Group: interface-unit

**Scope:** `tests/interfaces/test_backup_provider.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_backup_provider.py` | 0 (mechanical) | MODIFY (1 call site in `test_ibackup_provider_create_full_backup_abstract`: add `vm_name` arg; update `test_ibackup_provider_create_full_backup_bucket_level_parameter` signature check; 2 tests that call `create_full_backup` directly: add `vm_name` arg) |

### Group: integration

**Scope:** `tests/integration/test_nbd_full_backup.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_nbd_full_backup.py` | 0 (mechanical) | MODIFY (6 call sites: add `vm_name` as first positional arg) |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/modules/backup/test_copy.py` | ~20 call sites: add `vm_name` string as first positional argument to every `provider.create_full_backup(...)` call. Also add 3 NEW tests: `test_create_full_backup_dotted_vm_name`, `test_transfer_missing_passes_vm_name_to_create_full`, `test_create_full_backup_dotted_vm_name_passed_to_is_vm_running`. | Breaking interface change (Design Decision 2): `vm_name: str` is now the first parameter. Spec scenarios: "Dotted VM name is passed untruncated to virsh dominfo", "transfer_missing passes vm_config.name to create_full_backup", "Dotted VM name passed untruncated to is_vm_running". |
| `tests/modules/backup/test_bitmap.py` | ~12 call sites: add `vm_name` string as first positional argument to every `provider.create_full_backup(...)` call. Also add 1 NEW test: `test_bitmap_create_full_backup_dotted_vm_name`. | Breaking interface change. Spec scenario: "Bitmap FULL with dotted VM name". |
| `tests/integration/test_nbd_full_backup.py` | 6 call sites: add `vm_name` string as first positional argument to every `provider.create_full_backup(...)` call. | Breaking interface change — tests marked `@pytest.mark.integration` and are skipped in normal runs, but must still compile and pass type checking. |
| `tests/core/test_pipeline.py` | 5 spy sites (`patch.object(..., "create_full_backup", wraps=...)`): ensure `full_spy.call_args.args[0]` now receives `vm_name` (the mock provider accepts the new signature). Add 1 NEW test: `test_core_passes_vm_name_to_create_full_backup`. | Spec scenario: "Core passes vm_config.name to create_full_backup". The new parameter must flow from `Core._backup_target(vm_config, ...)` → `provider.create_full_backup(vm_config.name, ...)`. |
| `tests/interfaces/test_backup_provider.py` | 1 direct call site in `test_ibackup_provider_create_full_backup_abstract`: add `vm_name="testvm"` argument. 1 call site in `test_backup_provider_create_full_backup_returns_backup_result`: add `vm_name="testvm"`. Update `test_ibackup_provider_create_full_backup_bucket_level_parameter` to also verify `vm_name` is in the signature. | Breaking interface change: `IBackupProvider.create_full_backup` gains `vm_name` as first parameter. The contract test must verify the new signature. |
| `tests/mocks/mock_modules.py` | Both `MockBackupProvider.create_full_backup()` and `MockBitmapBackupProvider.create_full_backup()`: add `vm_name: str` as first positional parameter (before `source_snapshot`). | Breaking interface change: mock implementations must match updated ABC signature so `isinstance(mock, IBackupProvider)` remains valid and callers (Core tests via MockVMModuleFactory) can pass `vm_name`. |
| `tests/utils/test_parsing.py` | Fix `test_parse_timestamp_long_format_from_filename`: the old test uses name `"vm.20250101T120000"` with implied `%Y%m%dT%H%M%S` format (6 digit seconds), but the actual `long` format is `%Y%m%dT%H%M` (4 digit minutes). Rename to `test_parse_timestamp_long_format_from_filename_with_disk_suffix` and use name `"vm.20250101T1200_vda"`. Add 6 NEW tests: `test_parse_timestamp_dotted_vm_name`, `test_parse_timestamp_short_format`, `test_parse_timestamp_long_iso_format`, `test_parse_timestamp_full_backup_name`, `test_parse_timestamp_collision_suffix`, `test_parse_timestamp_long_iso_priority_over_long`. | Design Decision 3: `parse_timestamp()` is rewritten to use regex-based extraction. The existing test uses a format (`%Y%m%dT%H%M%S`) that never matches real snapshot names and always falls through to mtime. All 8 scenarios from the parsing-utils spec need coverage. |
| `tests/mocks/test_mock_factory.py` | `test_mock_backup_provider_has_create_full_backup`: add `vm_name="testvm"` as first argument to the `provider.create_full_backup(...)` call. | Breaking interface change consistency: mock factory tests must pass the new parameter even though the test only checks success/failure of the call. |

## Risks & Edge Cases

- **[BREAKING interface change]** All ~40 `create_full_backup()` call sites across tests must add `vm_name` as the first positional argument. If any call site is missed, pyright strict mode will catch it at compile time. The mechanical nature of the change is verified by the `interfaces` contract test group which parametrizes over all concrete implementations and validates the signature. → Covered by `tests/interfaces/test_backup_provider.py` modifications.

- **[parse_timestamp behavior change]** Previously, `parse_timestamp()` always fell back to file `mtime` for every input (the `%Y%m%dT%H%M%S` format never matched real snapshot names). After the fix, it returns the actual parsed timestamp. This changes retention bucket alignment for existing backups. The `parsing-utils-unit` group adds comprehensive coverage for all three timestamp formats (`long-iso`, `long`, `short`) with dotted VM names, disk suffixes, collision suffixes, and FULL backup names. → Covered by `tests/utils/test_parsing.py` new tests.

- **[Orphaned FULL files]** FULL backups previously created with truncated names (e.g. `3.FULL.20260717.qcow2` for VM `3.Projects_opencode`) will not match the new naming pattern (`3.Projects_opencode.FULL.*.qcow2`) and will be orphaned by retention. This is an acceptable outcome per design — these were created by a bug. No dedicated test is needed, but the scenario "Dotted VM name is passed untruncated" verifies that new FULLs use the correct full VM name in the filename. → Covered by `test_create_full_backup_dotted_vm_name` in file-copy-unit group.

- **[~40 test call sites to update]** Mechanical but tedious. The mock-backup-provider group updates the mock signatures first (these are the dependency for all other groups). Once mocks are updated, all other call sites need `"testvm"` (or appropriate VM name) as the first argument. The contract test in the interface-unit group acts as a canary: if the mocks or concrete implementations don't match the ABC signature, those parametrized tests fail immediately. → Covered by all groups collectively; verified by pyright strict type checking.

- **[Regex specificity ordering]** `parse_timestamp()` must try `long-iso` before `long` before `short`, otherwise the `long` pattern (`%Y%m%dT%H%M`) would match a prefix of `long-iso` timestamps (`20250101T1200` would match the first 12 chars of `20250101T120000+0200`), producing a wrong result. → Covered by `test_parse_timestamp_long_iso_priority_over_long` in parsing-utils-unit group.

- **[Mock signature mismatch]** If `MockBackupProvider.create_full_backup()` or `MockBitmapBackupProvider.create_full_backup()` are not updated to match the new ABC signature, every Core pipeline test that spies on `create_full_backup` via `patch.object(..., wraps=...)` will fail at runtime (argument count mismatch when Core passes `vm_name`). The mock-backup-provider group must be implemented first to unblock all other groups. → Covered by mock-backup-provider group modifications and downstream cascade verification in core-unit group.

- **[Implicit vm_name from SnapshotInfo.name.split(".")[0]]** The core implementation must be verified to pass `vm_config.name` explicitly and not fall back to extracting the VM name from `source_snapshot.name`. → Covered by `test_core_passes_vm_name_to_create_full_backup` in core-unit group.
