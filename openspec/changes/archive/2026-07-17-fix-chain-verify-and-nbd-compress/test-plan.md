# QA Strategy & Test Plan

## Coverage Map

### chain-integrity-verification

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| chain-integrity-verification | Pre-commit backing chain integrity verification | Intact chain — blockcommit proceeds | `tests/core/test_pipeline.py` | `test_chain_verify_intact_chain_blockcommit_proceeds` | chain-verify-tests |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Intact chain with new QEMU format — blockcommit proceeds | `tests/core/test_pipeline.py` | `test_chain_verify_intact_chain_new_qemu_format_blockcommit_proceeds` | chain-verify-tests |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Missing file in chain — blockcommit skipped | `tests/core/test_pipeline.py` | `test_chain_verify_missing_file_blockcommit_skipped` | chain-verify-tests |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Non-qcow2 file in chain — blockcommit skipped | `tests/core/test_pipeline.py` | `test_chain_verify_non_qcow2_blockcommit_skipped` | chain-verify-tests |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Cyclic reference detected — blockcommit skipped | `tests/core/test_pipeline.py` | `test_chain_verify_cyclic_reference_blockcommit_skipped` | chain-verify-tests |
| chain-integrity-verification | Pre-commit backing chain integrity verification | Broken chain does NOT defer the operation | `tests/core/test_pipeline.py` | `test_chain_verify_broken_chain_does_not_defer` | chain-verify-tests |

### backup-provider

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | Uncompressed full backup succeeds (stopped VM) | `tests/modules/backup/test_copy.py` | `test_create_full_backup_uncompressed_stopped_vm` | file-copy-unit |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | Compressed full backup succeeds (stopped VM) | `tests/modules/backup/test_copy.py` | `test_create_full_backup_compressed_stopped_vm` | file-copy-unit |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | NBD full backup succeeds (running VM) | `tests/modules/backup/test_copy.py` | `test_create_full_backup_nbd_running_vm_succeeds` | file-copy-unit |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | NBD full backup supports compression | `tests/modules/backup/test_copy.py` | `test_nbd_full_backup_with_compression_succeeds` | file-copy-unit |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | Dotted VM name is passed untruncated to virsh dominfo | `tests/modules/backup/test_copy.py` | `test_create_full_backup_dotted_vm_name_passed_to_is_vm_running` | file-copy-unit |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | transfer_missing passes vm_config.name to create_full_backup | `tests/modules/backup/test_copy.py` | `test_transfer_missing_empty_target_with_copy_base_false_calls_create_full_backup` | file-copy-unit |

### nbd-bitmap-backup

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via NBD full export | Bitmap FULL via NBD succeeds | `tests/modules/backup/test_bitmap.py` | `test_bitmap_create_full_backup_nbd_succeeds` | bitmap-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via NBD full export | Bitmap FULL with compression succeeds | `tests/modules/backup/test_bitmap.py` | `test_bitmap_create_full_backup_with_compression_succeeds` | bitmap-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via NBD full export | Bitmap FULL socket cleanup | `tests/modules/backup/test_bitmap.py` | `test_bitmap_full_socket_cleanup` | bitmap-unit |
| nbd-bitmap-backup | BitmapBackupProvider.create_full_backup via NBD full export | Bucket-driven FULL no longer crashes bitmap targets | `tests/modules/backup/test_bitmap.py` | `test_bitmap_bucket_driven_full_no_longer_crashes` | bitmap-unit |

### restore-command

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| restore-command | Restore command copies backup chain to target directory | Restore a file-copy backup chain with FULL anchor | `tests/core/test_engine.py` | `test_core_restore_from_backup_returns_restore_result` | restore-tests |
| restore-command | Restore command copies backup chain to target directory | Restore chain with new QEMU format | `tests/core/test_engine.py` | `test_core_restore_from_snapshot_new_qemu_format` | restore-tests |
| restore-command | Restore command copies backup chain to target directory | Restore a nonexistent backup | `tests/cli/test_commands.py` | `test_handle_restore_nonexistent_backup_returns_exit_1` | restore-tests |
| restore-command | Restore command copies backup chain to target directory | Target directory does not exist | `tests/cli/test_commands.py` | `test_handle_restore_missing_target_dir_returns_exit_1` | restore-tests |

## Delegation Groups

### Group: chain-verify-tests
**Scope:** `tests/core/test_pipeline.py`, `tests/fixtures/shell_outputs/`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | 6 | MODIFY (1 new test for QEMU 11.0+ format; 5 existing tests continue passing as-is) |
| `tests/fixtures/shell_outputs/` | N/A | MODIFY (add `backing_chain_intact_new.json` and `backing_chain_broken_new.json` fixtures) |

### Group: file-copy-unit
**Scope:** `tests/modules/backup/test_copy.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_copy.py` | 6 | MODIFY (add 1 new test for NBD compression; REMOVE 1 obsolete test `test_nbd_full_backup_ignores_compress_flag`) |

### Group: bitmap-unit
**Scope:** `tests/modules/backup/test_bitmap.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_bitmap.py` | 4 | MODIFY (add 1 new test for NBD compression `test_bitmap_create_full_backup_with_compression_succeeds`; update `test_bitmap_create_full_backup_nbd_succeeds` to accept `compress` parameter pass-through) |

### Group: restore-tests
**Scope:** `tests/core/test_engine.py`, `tests/cli/test_commands.py`
| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_engine.py` | 2 | MODIFY (add 1 new test `test_core_restore_from_snapshot_new_qemu_format` for `"filename"` key parsing; existing `test_core_restore_from_snapshot_returns_restore_result` verified via `"image"` key) |
| `tests/cli/test_commands.py` | 2 | No change (existing tests continue to pass) |

## Test Modifications

### Tests to REMOVE

| File | Change | Reason |
|---|---|---|
| `tests/modules/backup/test_copy.py` — `test_nbd_full_backup_ignores_compress_flag` | **REMOVE** entire test function (lines 1988–2070) | This test explicitly asserts the old behavior: (a) a WARNING `"compress=True ignored for NBD-based FULL backup"` is logged, and (b) the `-c` flag is absent from `qemu-img convert nbd:unix:...`. After the fix, `compress=True` should actually work over NBD, producing compressed output — this test's assertions are the exact opposite of the new behavior. The test must be removed, not modified, because there is no correct variant that asserts NBD ignores compression (that scenario no longer exists). |

### Tests to ADD

| File | Change | Reason |
|---|---|---|
| `tests/modules/backup/test_copy.py` | **ADD** `test_nbd_full_backup_with_compression_succeeds` | New scenario: `compress=True` + running VM → `qemu-img convert -c nbd:unix:<socket>` with `-c` flag present. Verify no WARNING logged, compression proceeds. |
| `tests/modules/backup/test_bitmap.py` | **ADD** `test_bitmap_create_full_backup_with_compression_succeeds` | New scenario: `BitmapBackupProvider.create_full_backup(compress=True)` → `-c` passed through to `nbd_full_export()`. Verify `-c` appears in `qemu-img convert` command. |
| `tests/core/test_pipeline.py` | **ADD** `test_chain_verify_intact_chain_new_qemu_format_blockcommit_proceeds` | New scenario: chain verification with QEMU 11.0+ JSON output using `"filename"` keys and nested `"children"` arrays. Uses new fixture `backing_chain_intact_new.json`. |
| `tests/core/test_engine.py` | **ADD** `test_core_restore_from_snapshot_new_qemu_format` | New scenario: `restore()` command correctly parses `"filename"` keys in JSON output (QEMU 11.0+ format). |
| `tests/fixtures/shell_outputs/` | **ADD** `backing_chain_intact_new.json` | New fixture: 5-file intact chain using `"filename"` keys + nested `"children"` arrays (QEMU 11.0+ format). |
| `tests/fixtures/shell_outputs/` | **ADD** `backing_chain_broken_new.json` | New fixture: broken chain (MISSING_FILE) using `"filename"` keys + nested `"children"` arrays (QEMU 11.0+ format). |

### Tests to VERIFY (pass unchanged)

| File | Tests | Reason |
|---|---|---|
| `tests/core/test_pipeline.py` | `test_chain_verify_intact_chain_blockcommit_proceeds`, `test_chain_verify_missing_file_blockcommit_skipped`, `test_chain_verify_non_qcow2_blockcommit_skipped`, `test_chain_verify_cyclic_reference_blockcommit_skipped`, `test_chain_verify_broken_chain_does_not_defer`, `test_chain_verify_inconsistent_backing_filename_blockcommit_skipped`, `test_chain_verify_disabled_skips_pre_commit_check` | All 7 existing tests use `"image"` key — must continue working with the `item.get("image") or item.get("filename", "")` fallback. The `test_chain_verify_non_qcow2_blockcommit_skipped` and `test_chain_verify_cyclic_reference_blockcommit_skipped` tests construct inline JSON with `"image"` — these verify the legacy path still works. |
| `tests/core/test_pipeline.py` | `test_post_commit_chain_shortened_as_expected`, `test_post_commit_chain_length_unchanged_critical`, `test_post_commit_verification_fails_snapshots_preserved` | Post-commit verification tests use `_get_chain_length` (unaffected by key name change). |
| `tests/core/test_engine.py` | `test_core_restore_from_snapshot_returns_restore_result`, `test_core_restore_from_backup_returns_restore_result` | These use `"image"` key inline — the fallback to `"filename"` must not break them. |
| `tests/modules/backup/test_copy.py` | `test_create_full_backup_nbd_running_vm_succeeds`, `test_create_full_backup_compressed_stopped_vm`, `test_create_full_backup_uncompressed_stopped_vm` | Existing tests for non-NBD compression (stopped VM `-c`) and NBD without compression must continue to pass. |
| `tests/modules/backup/test_bitmap.py` | `test_bitmap_create_full_backup_nbd_succeeds`, `test_bitmap_full_socket_cleanup`, `test_bitmap_full_backup_no_checkpoint`, `test_bitmap_bucket_driven_full_no_longer_crashes` | Existing bitmap FULL tests use `compress=False` and must pass unchanged. |
| `tests/modules/backup/test_copy.py` | `test_nbd_full_export_produces_standalone_qcow2`, `test_nbd_no_force_share_no_backup_begin`, `test_nbd_full_no_force_share_on_convert`, `test_nbd_socket_cleanup_on_success`, `test_create_full_backup_dotted_vm_name_passed_to_is_vm_running`, `test_transfer_missing_empty_target_with_copy_base_false_calls_create_full_backup` | These existing NBD tests don't involve compression and must pass unchanged. |
| `tests/modules/backup/test_verification.py` | All tests | Verification logic does not parse `qemu-img info --backing-chain` — it uses `qemu-img info` on single files. Unaffected by key name change or NBD compression changes. |
| `tests/cli/test_commands.py` | `test_handle_restore_dispatches_to_core_restore_with_positional_args`, `test_handle_restore_nonexistent_backup_returns_exit_1`, `test_handle_restore_missing_target_dir_returns_exit_1` | CLI dispatch tests don't parse JSON — unaffected. |

## Risks & Edge Cases

- **[Risk: Old QEMU without "filename" key]** `item.get("filename", "")` returns `""` on pre-11.0 QEMU, which is falsy; `"image"` is tried first and will be found → Mitigation: `test_chain_verify_intact_chain_blockcommit_proceeds` (existing, uses `"image"`) validates the legacy path, and `test_chain_verify_intact_chain_new_qemu_format_blockcommit_proceeds` (new) validates the new path.
- **[Risk: "children" nested array causes chain length confusion]** The `"children"` array is nested INSIDE each top-level element, not at the array level. `len(chain_data)` counts top-level elements only → Mitigation: `test_chain_verify_intact_chain_new_qemu_format_blockcommit_proceeds` uses a fixture with nested `"children"` arrays to verify correct parsing.
- **[Risk: -c over NBD may fail on untested QEMU versions]** The feature was confirmed with qemu-img 11.0.2 + qemu-nbd -k. If it fails on other versions, the existing `not nbd_result.success` error handling catches it → Mitigation: `test_nbd_full_backup_with_compression_succeeds` verifies the happy path; the `test_create_full_backup_nbd_failure_cleans_up_tmp` test (existing) covers the failure path.
- **[Risk: Removing WARNING may surprise users]** Users who previously saw the WARNING and expected uncompressed NBD output now get compressed output when `compress=true` → Mitigation: This is a fix, not a regression. The documentation and README should be updated.
- **[Risk: Compression over NBD may be slower than direct convert]** Acceptable tradeoff documented in design.md; live VM compression is now possible → Mitigation: Users who prefer speed can set `compress=false`. The `_log_size_estimate` formula (`base_size * 0.3`) now accurately reflects compressed NBD output.
- **[Risk: Dry-run mode interacts with new NBD compression]** Dry-run logs must reflect the new compression support when `compress=True` → Mitigation: Existing `test_dry_run_logs_full_would_be_created` verifies the NBD method dry-run log. A new assertion should be added to verify `compress` flag is mentioned in the dry-run log.
- **[Risk: Inline JSON tests for chain verification use "image" key in old format]** Tests like `test_chain_verify_non_qcow2_blockcommit_skipped` construct inline JSON with `{"image": "...", "format": "raw"}` → Mitigation: The `item.get("image") or item.get("filename", "")` fallback means these tests continue working. No modification needed.
- **[Risk: Restore command with "filename" key in new QEMU]** `Core.restore()` uses `item.get("image")` to extract chain paths. With QEMU 11.0+, this returns `None` → Mitigation: `test_core_restore_from_snapshot_new_qemu_format` (new) validates the fix; `test_core_restore_from_snapshot_returns_restore_result` (existing) validates backward compatibility.

## New Fixture Files

### `tests/fixtures/shell_outputs/backing_chain_intact_new.json`
```json
[
  {
    "filename": "/var/lib/libvirt/snapshots/testvm/snap4.qcow2",
    "format": "qcow2",
    "virtual-size": 21474836480,
    "actual-size": 4194304,
    "backing-filename": "/var/lib/libvirt/snapshots/testvm/snap3.qcow2",
    "backing-format": "qcow2",
    "children": [
      {
        "name": "/var/lib/libvirt/snapshots/testvm/snap4.qcow2",
        "format": "qcow2"
      }
    ]
  },
  {
    "filename": "/var/lib/libvirt/snapshots/testvm/snap3.qcow2",
    "format": "qcow2",
    "virtual-size": 21474836480,
    "actual-size": 3145728,
    "backing-filename": "/var/lib/libvirt/snapshots/testvm/snap2.qcow2",
    "backing-format": "qcow2",
    "children": [
      {
        "name": "/var/lib/libvirt/snapshots/testvm/snap3.qcow2",
        "format": "qcow2"
      }
    ]
  },
  {
    "filename": "/var/lib/libvirt/snapshots/testvm/snap2.qcow2",
    "format": "qcow2",
    "virtual-size": 21474836480,
    "actual-size": 2097152,
    "backing-filename": "/var/lib/libvirt/snapshots/testvm/snap1.qcow2",
    "backing-format": "qcow2",
    "children": [
      {
        "name": "/var/lib/libvirt/snapshots/testvm/snap2.qcow2",
        "format": "qcow2"
      }
    ]
  },
  {
    "filename": "/var/lib/libvirt/snapshots/testvm/snap1.qcow2",
    "format": "qcow2",
    "virtual-size": 21474836480,
    "actual-size": 1048576,
    "backing-filename": "/var/lib/libvirt/images/testvm.qcow2",
    "backing-format": "qcow2",
    "children": [
      {
        "name": "/var/lib/libvirt/snapshots/testvm/snap1.qcow2",
        "format": "qcow2"
      }
    ]
  },
  {
    "filename": "/var/lib/libvirt/images/testvm.qcow2",
    "format": "qcow2",
    "virtual-size": 21474836480,
    "actual-size": 1073741824,
    "children": [
      {
        "name": "/var/lib/libvirt/images/testvm.qcow2",
        "format": "qcow2"
      }
    ]
  }
]
```

### `tests/fixtures/shell_outputs/backing_chain_broken_new.json`
```json
[
  {
    "filename": "/var/lib/libvirt/snapshots/testvm/MISSING_FILE.qcow2",
    "format": "qcow2",
    "virtual-size": 21474836480,
    "actual-size": 2097152,
    "backing-filename": "/var/lib/libvirt/snapshots/testvm/snap1.qcow2",
    "backing-format": "qcow2",
    "children": [
      {
        "name": "/var/lib/libvirt/snapshots/testvm/MISSING_FILE.qcow2",
        "format": "qcow2"
      }
    ]
  },
  {
    "filename": "/var/lib/libvirt/snapshots/testvm/snap1.qcow2",
    "format": "qcow2",
    "virtual-size": 21474836480,
    "actual-size": 1048576,
    "backing-filename": "/var/lib/libvirt/images/testvm.qcow2",
    "backing-format": "qcow2",
    "children": [
      {
        "name": "/var/lib/libvirt/snapshots/testvm/snap1.qcow2",
        "format": "qcow2"
      }
    ]
  },
  {
    "filename": "/var/lib/libvirt/images/testvm.qcow2",
    "format": "qcow2",
    "virtual-size": 21474836480,
    "actual-size": 1073741824,
    "children": [
      {
        "name": "/var/lib/libvirt/images/testvm.qcow2",
        "format": "qcow2"
      }
    ]
  }
]
```

## Test Execution Plan

```bash
# 1. Verify existing tests still pass (regression check):
poetry run pytest tests/core/test_pipeline.py tests/core/test_engine.py tests/modules/backup/test_copy.py tests/modules/backup/test_bitmap.py tests/cli/test_commands.py -v

# 2. Run the full non-integration suite:
poetry run pytest tests/ -m "not integration and not stress and not e2e" -v

# 3. Integration (requires libvirt, skip if unavailable):
poetry run pytest tests/integration/ -v -m integration --ignore=tests/integration/test_nbd_full_backup.py
# The NBD full backup integration test should be updated to also test compression.

# 4. Coverage report:
poetry run pytest tests/ --cov=qsnap --cov-report=term-missing -m "not integration and not stress and not e2e"
```
