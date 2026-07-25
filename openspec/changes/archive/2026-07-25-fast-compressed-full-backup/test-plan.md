# Test Plan: fast-compressed-full-backup

## 1. Coverage Map

Every scenario from the five delta spec files is mapped to at least one concrete test.

### 1.1 `qemu-img-convert-full-backup` (NEW capability)

| # | Spec Scenario | Test File | Test Function | Type | Notes |
|---|-------------|-----------|---------------|------|-------|
| 1a | Running VM FULL with zstd compression | `tests/modules/backup/test_bitmap_convert.py` | `test_convert_cmd_running_vm_compressed` | unit | MockShell verifies exact `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p nbd:unix:<socket> <target>.tmp` command; asserts no `_start_write_server` or `_transfer` called |
| 1b | Running VM FULL without compression | `tests/modules/backup/test_bitmap_convert.py` | `test_convert_cmd_running_vm_uncompressed` | unit | MockShell verifies `qemu-img convert -O qcow2 -m 4 -W -p nbd:unix:<socket> <target>.tmp`; no `-c` flag |
| 1c | Stopped VM FULL with compression | `tests/modules/backup/test_bitmap_convert.py` | `test_convert_cmd_stopped_vm_compressed` | unit | MockShell, `is_vm_running` patched to return `False`; verifies `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p <source>.qcow2 <target>.tmp`; no NBD URI |
| 1d | Stopped VM FULL without compression | `tests/modules/backup/test_bitmap_convert.py` | `test_convert_cmd_stopped_vm_uncompressed` | unit | MockShell, `is_vm_running=False`; verifies `qemu-img convert -O qcow2 -m 4 -W -p <source>.qcow2 <target>.tmp`; no `-c`, no NBD |
| 1e | FULL failure leaves no final file | `tests/modules/backup/test_bitmap_convert.py` | `test_convert_failure_removes_tmp` | unit | MockShell `run_with_stall_detection` returns failure; assert `.tmp` deleted, no `vm.FULL.*.qcow2`, `BackupResult.success=False` |
| 1f | FULL success atomically renames tmp to final | `tests/modules/backup/test_bitmap_convert.py` | `test_convert_success_renames_tmp_to_final` | unit | MockShell returns success; assert `mv .tmp → .qcow2` command issued; `BackupResult(success=True, path=<final>)` |
| 1g | Running VM triggers NBD-based convert | `tests/modules/backup/test_bitmap_convert.py` | `test_running_vm_uses_nbd_convert` | unit | `is_vm_running=True`; assert `virsh backup-begin` called; assert `qemu-img convert` reads from `nbd:unix:<socket>` |
| 1h | Stopped VM triggers direct convert | `tests/modules/backup/test_bitmap_convert.py` | `test_stopped_vm_uses_direct_convert` | unit | `is_vm_running=False`; assert no `virsh backup-begin`; assert `qemu-img convert` reads from `<source>.qcow2` |
| 1i | `get_first_disk_path` returns path for first disk | `tests/utils/test_nbd_helpers.py` | `test_get_first_disk_path_returns_path` | unit | MockShell returns `virsh domblklist --details` output with `vda /var/lib/libvirt/images/testvm.qcow2 disk`; assert returns `/var/lib/libvirt/images/testvm.qcow2` |
| 1j | `get_first_disk_path` returns empty string for no disks | `tests/utils/test_nbd_helpers.py` | `test_get_first_disk_path_no_disks` | unit | MockShell returns no disk entries; assert returns `""` |
| 1k | `_start_write_server` and `_transfer` NOT called for FULLs | `tests/modules/backup/test_bitmap_convert.py` | `test_full_backup_does_not_use_write_server_or_transfer` | unit | Spy on both methods; assert neither called during `create_full_backup()` |
| 1l | `run_with_stall_detection` used for qemu-img convert | `tests/modules/backup/test_bitmap_convert.py` | `test_convert_uses_stall_detection` | unit | Spy on `mock_shell.run_with_stall_detection`; assert called with `output_file=<.tmp>`, `stall_timeout=<target.backup_stall_timeout>` |
| 1m | Compression mode verification (none, zstd, zlib) via qcow2 metadata | `tests/integration/test_full_backup.py` | `test_full_backup_compression_modes` | integration | 10 GB disk, 8 GB data; verifies ``compression-type`` field in ``qemu-img info`` for each mode |
| 1n | Speed comparison: none vs zstd vs zlib | `tests/integration/test_full_backup.py` | `test_full_backup_speed_comparison` | integration | 10 GB disk; measures throughput for all three modes; asserts uncompressed > 50 MB/s, zstd > 100 MB/s, zlib > 5 MB/s; zstd not slower than zlib |
| 1o | Incremental-after-FULL: sizes and backing chain | `tests/integration/test_incremental_backup.py` | `test_incremental_after_full` | integration | Creates FULL, writes 10 MB dirty data, external snapshot, ``transfer_missing()`` via libnbd; asserts inc actual-size < FULL, backing points to FULL, bytes_transferred proportional to dirty data |
| 1p | Stopped-VM FULL backup succeeds via direct convert | `tests/integration/test_full_backup.py` | `test_full_backup_stopped_vm` | integration | VM stopped; direct ``qemu-img convert``, no NBD socket, no ``virsh backup-begin`` |
| 1q | Running-VM FULL backup via NBD + qemu-img convert | `tests/integration/test_full_backup.py` | `test_full_backup_running_vm_nbd` | integration | VM running; ``virsh backup-begin`` + ``qemu-img convert nbd:unix:<socket>``; checkpoint created atomically; atomic rename |
| 1r | Incremental compression NOT applied (design D6) | `tests/integration/test_incremental_backup.py` | `test_incremental_compression_not_applied` | integration | Creates zstd-compressed FULL; ``transfer_missing()`` with ``compress=True``; incremental has ``compression-type: "zlib"`` (not zstd); log message confirms uncompressed |
| 1s | Incremental dirty bytes proportional to data written | `tests/integration/test_incremental_backup.py` | `test_incremental_dirty_bytes_proportional` | integration | 5 MB dirty data; ``bytes_transferred`` < 50 MB (10× overhead), not full disk |
| 1t | onchange skips when allocation unchanged | `tests/integration/test_onchange.py` | `test_onchange_skips_when_unchanged` | integration | Uses ``Core.backup()`` with ``backup_create="onchange"``; second run skips with "unchanged ... skipping" |
| 1u | onchange proceeds when allocation changed | `tests/integration/test_onchange.py` | `test_onchange_proceeds_when_changed` | integration | Writes new data between runs; allocation changes → gate open → baseline updated |
| 1v | Socket and .tmp cleanup after crash | `tests/integration/test_infrastructure.py` | `test_socket_and_tmp_cleanup` | integration | Stale socket + .tmp; verifies cleanup after ``create_full_backup()`` |
| 1w | domjobabort after backup | `tests/integration/test_infrastructure.py` | `test_domjobabort_after_backup` | integration | ``virsh domjobinfo`` reports no active block job after NBD backup |
| 1x | Stall detection kills hung / survives slow progress | `tests/integration/test_infrastructure.py` | `test_stall_detection_kills_hung`, `test_stall_detection_slow_progress_survives` | integration | ``SubprocessShell.run_with_stall_detection``; hung process killed, slow progress NOT killed |
| 1y | Stale state self-healing | `tests/integration/test_infrastructure.py` | `test_stale_state_self_healing` | integration | Stale snapshot removed from state during ``transfer_missing()`` |

### 1.2 `config-parsing` (MODIFIED capability)

| # | Spec Scenario | Test File | Test Function | Type | Notes |
|---|-------------|-----------|---------------|------|-------|
| 2a | `[global]` section keys parsed correctly | `tests/config/test_parser.py` | `test_parse_global_section` | unit | New TOML fixture `global_section.toml` with `[global] compress = false` and `[global] lockfile = "/run/qsnap.lock"`; asserts `GlobalConfig.compress is False` and `GlobalConfig.lockfile == "/run/qsnap.lock"` |
| 2b | `[global]` section with target-level inheritance | `tests/config/test_resolver.py` | `test_global_section_inheritance_to_target` | unit | TOML with `[global] compress = false` and target without `compress`; asserts `TargetConfig.compress is False` |
| 2c | Top-level keys override `[global]` section | `tests/config/test_parser.py` | `test_top_level_overrides_global_section` | unit | TOML with both `compress = true` at top level AND `[global] compress = false`; asserts `GlobalConfig.compress is True` |
| 2d | No `[global]` section — backward compatible | `tests/config/test_parser.py` | `test_no_global_section_backward_compatible` | unit | Uses existing `global_fields.toml`; asserts parsing works exactly as before; no regression |
| 2e | `[global]` section compress propagation to CREATE_FULL commands | `tests/modules/backup/test_bitmap_convert.py` | `test_global_section_compress_false_affects_convert_cmd` | unit | `compress=False` from `[global]` propagates to target; assert `qemu-img convert` command has no `-c` flag |

### 1.3 `nbd-bitmap-backup` (MODIFIED capability)

| # | Spec Scenario | Test File | Test Function | Type | Notes |
|---|-------------|-----------|---------------|------|-------|
| 3a | First backup — full via qemu-img convert with atomic checkpoint | `tests/modules/backup/test_bitmap_convert.py` | `test_first_backup_full_via_convert_with_checkpoint` | unit | No prior checkpoint; `virsh backup-begin` called with checkpoint XML; assert `qemu-img convert` executed (not `pread`/`pwrite`); assert no `_start_write_server` |
| 3b | Incremental backup — dirty blocks via NBD checkpoint (no qemu-img convert) | `tests/modules/backup/test_bitmap_incremental.py` | `test_incremental_uses_unified_engine_no_convert` (EXISTING) | unit | **Already passes** — confirms no `qemu-img convert` for incrementals; verify it still passes |
| 3c | `_start_write_server` does not accept compression_type | `tests/modules/backup/test_bitmap.py` | `test_start_write_server_signature_no_compression_type` (EXISTING) | unit | **Already passes** — verify it still passes |
| 3d | Scaffolding dedup — shared `_full_pull_lifecycle` helper | `tests/modules/backup/test_bitmap_convert.py` | `test_full_pull_lifecycle_uses_convert` | unit | Spy on `_full_pull_lifecycle`; assert called from both `create_full_backup()` and `transfer_missing()` full-pull; assert `qemu-img convert` is used inside the helper |
| 3e | `_full_pull_lifecycle` does NOT call `_start_write_server` or `_transfer` for FULLs | `tests/modules/backup/test_bitmap_convert.py` | `test_full_pull_lifecycle_no_write_server` | unit | Partial mock: spy on `_start_write_server` and `_transfer`; assert neither called inside `_full_pull_lifecycle` when doing FULL backup |
| 3f | Socket cleanup on success | `tests/modules/backup/test_bitmap.py` | `test_socket_cleanup_on_success` (EXISTING - MODIFY) | unit | Existing test; update to verify socket cleanup after `qemu-img convert` path (not write-side qemu-nbd) |
| 3g | Socket cleanup on failure | `tests/modules/backup/test_bitmap.py` | `test_socket_cleanup_on_failure` (EXISTING - MODIFY) | unit | Existing test; update to verify cleanup after `qemu-img convert` failure |

### 1.4 `live-vm-full-backup` (MODIFIED capability)

| # | Spec Scenario | Test File | Test Function | Type | Notes |
|---|-------------|-----------|---------------|------|-------|
| 4a | Running VM triggers NBD-based FULL backup | `tests/modules/backup/test_bitmap_convert.py` | `test_running_vm_uses_nbd_convert` | unit | Same as 1g — `virsh backup-begin` + `qemu-img convert nbd:unix:<socket>`; no Python `pread`/`pwrite` |
| 4b | Stopped VM uses direct qemu-img convert | `tests/modules/backup/test_bitmap_convert.py` | `test_stopped_vm_uses_direct_convert` | unit | Same as 1h — no `virsh backup-begin`; direct source path |
| 4c | Dotted VM name passed untruncated | `tests/modules/backup/test_bitmap.py` | `test_create_full_backup_dotted_vm_name_passed_untruncated` (EXISTING - MODIFY) | unit | Existing test; update to verify `virsh backup-begin --domain 3.Projects_opencode` still works with new convert path |
| 4d | Core passes vm_config.name to create_full_backup | `tests/core/test_engine.py` | (EXISTING - MODIFY) | unit | Existing Core test; update mock expectations to match new `create_full_backup` signature with `compression_type` and `stall_timeout` kwargs |
| 4e | qemu-img convert FULL creates tmp then renames | `tests/modules/backup/test_bitmap_convert.py` | `test_convert_success_renames_tmp_to_final` | unit | Same as 1f |
| 4f | qemu-img convert FULL failure leaves no final file | `tests/modules/backup/test_bitmap_convert.py` | `test_convert_failure_removes_tmp` | unit | Same as 1e |
| 4g | FULL timestamp matches snapshot, not export time | `tests/modules/backup/test_bitmap_convert.py` | `test_full_timestamp_matches_snapshot` | unit | Verify `BackupResult` timestamp equals `SnapshotInfo.timestamp`, not wall clock |

### 1.5 `shell-abstraction` (MODIFIED capability)

| # | Spec Scenario | Test File | Test Function | Type | Notes |
|---|-------------|-----------|---------------|------|-------|
| 5a | IShell is an ABC | `tests/utils/test_shell.py` (EXISTING) | (EXISTING) | contract | Already covered |
| 5b | IShell has run_with_stall_detection method | `tests/utils/test_shell.py` (EXISTING) | (EXISTING) | contract | Already covered |
| 5c | run_with_stall_detection used for qemu-img convert FULL backup | `tests/modules/backup/test_bitmap_convert.py` | `test_convert_uses_stall_detection` | unit | Same as 1l — spy on `run_with_stall_detection`; assert called with correct params |
| 5d | run_with_stall_detection output_file is .tmp file | `tests/modules/backup/test_bitmap_convert.py` | `test_stall_detection_output_file_is_tmp` | unit | Assert `output_file` ends in `.tmp` |
| 5e | run_with_stall_detection stall_timeout from target config | `tests/modules/backup/test_bitmap_convert.py` | `test_stall_detection_timeout_from_target_config` | unit | `TargetConfig.backup_stall_timeout = "5m"`; assert `stall_timeout=300` passed to `run_with_stall_detection` |

## 2. Delegation Groups

Groups are non-overlapping and can be executed in parallel.

### Group A: Unit — qemu-img convert command construction (NEW)
New file: `tests/modules/backup/test_bitmap_convert.py`

- `test_convert_cmd_running_vm_compressed`
- `test_convert_cmd_running_vm_uncompressed`
- `test_convert_cmd_stopped_vm_compressed`
- `test_convert_cmd_stopped_vm_uncompressed`
- `test_convert_failure_removes_tmp`
- `test_convert_success_renames_tmp_to_final`
- `test_running_vm_uses_nbd_convert`
- `test_stopped_vm_uses_direct_convert`
- `test_full_backup_does_not_use_write_server_or_transfer`
- `test_convert_uses_stall_detection`
- `test_stall_detection_output_file_is_tmp`
- `test_stall_detection_timeout_from_target_config`
- `test_first_backup_full_via_convert_with_checkpoint`
- `test_full_pull_lifecycle_uses_convert`
- `test_full_pull_lifecycle_no_write_server`
- `test_full_timestamp_matches_snapshot`
- `test_global_section_compress_false_affects_convert_cmd`

### Group B: Unit — [global] section parsing (NEW + EXISTING)
Files: `tests/config/test_parser.py`, `tests/config/test_resolver.py`

- `test_parse_global_section` (NEW, test_parser.py)
- `test_top_level_overrides_global_section` (NEW, test_parser.py)
- `test_no_global_section_backward_compatible` (NEW, test_parser.py)
- `test_global_section_inheritance_to_target` (NEW, test_resolver.py)

**Fixture:** New `tests/fixtures/configs/global_section.toml` with `[global]` section format.

### Group C: Unit — get_first_disk_path (NEW)
New file: `tests/utils/test_nbd_helpers.py`

- `test_get_first_disk_path_returns_path`
- `test_get_first_disk_path_no_disks`

### Group D: Unit — MODIFIED existing bitmap tests
Files: `tests/modules/backup/test_bitmap.py`, `tests/modules/backup/test_bitmap_incremental.py`

Tests that ASSERT "No qemu-img convert" which are now wrong:

**In `test_bitmap.py`** — these FULL-backup tests currently assert `len(convert_cmds) == 0` and must be updated to accept qemu-img convert on the FULL path:
- `test_create_full_backup_unified_engine_succeeds` → expect `qemu-img convert` in FULL path, assert NO `_start_write_server`
- `test_create_full_backup_with_compression` → expect `qemu-img convert -c` in FULL path, no `qemu-nbd --image-opts driver=compress`
- `test_create_full_backup_no_compress_driver_when_compress_false` → expect `qemu-img convert` (no `-c`), no `qemu-nbd`
- `test_bitmap_full_socket_cleanup` → update to verify new cleanup (no write-side qemu-nbd)
- `test_bitmap_bucket_driven_full_no_longer_crashes` → update assertions
- `test_create_full_backup_returns_standalone_qcow2` → update assertions
- `test_create_full_backup_atomic_rename_tmp_to_final` → update assertions
- `test_create_full_backup_failure_removes_tmp` → update to expect `qemu-img convert` failure path
- `test_no_checkpoints_triggers_full_export` → update (transfer_missing full-pull now uses qemu-img convert)
- `test_socket_cleanup_on_success` → update
- `test_socket_cleanup_on_failure` → update

**In `test_bitmap_incremental.py`** — these INCREMENTAL tests correctly assert NO `qemu-img convert`:
- `test_copy_loop_reads_only_dirty_extents` → **Still valid** (incremental, no convert) — verify passes
- `test_incremental_uses_unified_engine_no_convert` → **Still valid** (incremental, no convert) — verify passes
- `test_bitmap_incremental_ignores_compress_setting` → **Still valid** — verify passes
- All other incremental tests → **Still valid** — verify passes

### Group E: Unit — MODIFIED Core tests
File: `tests/core/test_engine.py`

- Update mock expectations for `create_full_backup` to include `compression_type` and `stall_timeout` kwargs
- Update `_full_pull_lifecycle` related assertions

### Group F: Unit — MODIFIED Factory test
File: `tests/factory/test_default.py`

- Line 341: Remove stale comment about "no file-copy fallback (design R4)" → rewrite comment to simply state behavior

### Group G: Integration — FULL backup tests (NEW)
File: `tests/integration/test_full_backup.py`

- `test_full_backup_compression_modes` — `@pytest.mark.integration`, `@pytest.mark.timeout(3600)`, disk 10G
- `test_full_backup_speed_comparison` — `@pytest.mark.integration`, `@pytest.mark.timeout(3600)`, disk 10G
- `test_full_backup_stopped_vm` — `@pytest.mark.integration`
- `test_full_backup_running_vm_nbd` — `@pytest.mark.integration`

### Group H: Integration — Incremental backup tests (NEW)
File: `tests/integration/test_incremental_backup.py`

- `test_incremental_after_full` — `@pytest.mark.integration`, `@pytest.mark.timeout(3600)`, disk 5G
- `test_incremental_compression_not_applied` — `@pytest.mark.integration`, `@pytest.mark.timeout(3600)`
- `test_incremental_dirty_bytes_proportional` — `@pytest.mark.integration`, `@pytest.mark.timeout(3600)`

### Group I: Integration — onchange tests (NEW)
File: `tests/integration/test_onchange.py`

- `test_onchange_skips_when_unchanged` — `@pytest.mark.integration`, `@pytest.mark.timeout(3600)`
- `test_onchange_proceeds_when_changed` — `@pytest.mark.integration`, `@pytest.mark.timeout(3600)`

### Group J: Integration — Infrastructure tests (NEW)
File: `tests/integration/test_infrastructure.py`

- `test_socket_and_tmp_cleanup` — `@pytest.mark.integration`, `@pytest.mark.timeout(1800)`
- `test_domjobabort_after_backup` — `@pytest.mark.integration`
- `test_stall_detection_kills_hung` — `@pytest.mark.integration`
- `test_stall_detection_slow_progress_survives` — `@pytest.mark.integration`
- `test_stale_state_self_healing` — `@pytest.mark.integration`

### Group K: Cleanup — DELETE old scattered integration test files (DONE)
The following files were deleted and their logic consolidated into the four new files above:

| Deleted File | Consolidated Into |
|---|---|
| `test_convert_performance.py` | `test_full_backup.py` + `test_incremental_backup.py` |
| `test_zstd_backup.py` | `test_full_backup.py` |
| `test_compress_driver.py` | ✗ (obsolete — tested qemu-nbd driver=compress) |
| `test_nbd_full_backup.py` | `test_full_backup.py` + `test_infrastructure.py` |
| `test_onchange_backup.py` | `test_onchange.py` |
| `test_unified_engine.py` | `test_full_backup.py` + `test_incremental_backup.py` |
| `test_bitmap_atomic.py` | `test_infrastructure.py` |
| `test_bitmap_dirty_transfer.py` | `test_incremental_backup.py` |
| `test_bitmap_integration.py` | `test_incremental_backup.py` |
| `test_zero_skip.py` | `test_incremental_backup.py` |
| `test_flush_connect.py` | `test_infrastructure.py` |
| `test_stall_detection.py` | `test_infrastructure.py` |
| `test_stall_inprocess.py` | `test_infrastructure.py` |
| `test_stale_state_recovery.py` | `test_infrastructure.py` |
| `test_retry_integration.py` | `test_infrastructure.py` |

Files kept (separate concerns):
- `test_verification_bitmap.py`, `test_blockcommit_defer.py`, `test_env_validation.py`
- `test_nbd_import_hardening.py`, `test_log_levels.py`, `test_config_integration.py`
- `test_preserve_all.py`, `test_pkgbuild_structure.py`

## 3. Test Modifications

### 3.1 Existing tests that MUST change (breaking asserts)

These tests currently assert that `qemu-img convert` is NOT used. After this change, FULL backups WILL use `qemu-img convert`. These tests must be updated, not deleted.

#### `tests/modules/backup/test_bitmap.py`

| Test | What Changes |
|------|-------------|
| `test_create_full_backup_unified_engine_succeeds` | Remove `assert len(convert_cmds) == 0`. Assert `qemu-img convert` IS present in command history. Assert `_start_write_server` and `_transfer` are NOT called. |
| `test_create_full_backup_with_compression` | Remove `assert len(convert_cmds) == 0`. Assert `qemu-img convert -c` IS present. Assert NO `qemu-nbd --image-opts driver=compress` is started (write-side server removed). Assert `run_with_stall_detection` was used. |
| `test_create_full_backup_no_compress_driver_when_compress_false` | Remove assertions about `qemu-nbd --format=qcow2`. Assert `qemu-img convert` (no `-c` flag) IS used. |
| `test_bitmap_full_backup_does_not_raise_not_implemented` | Update mock expectations; remove `_setup_full_unified_expectations` usage — replace with `_setup_convert_expectations`. |
| `test_create_full_backup_atomic_rename_tmp_to_final` | Update mock expectations; Assert `mv .tmp → .qcow2` issued after `qemu-img convert`. |
| `test_create_full_backup_failure_removes_tmp` | Update to mock `qemu-img convert` failure via `run_with_stall_detection` returning error. Assert `.tmp` removed. |
| `test_no_checkpoints_triggers_full_export` | `transfer_missing()` full-pull now uses `qemu-img convert`. Update to expect convert command instead of `_start_write_server`. |
| `test_bitmap_full_socket_cleanup` | Update: no write-side `qemu-nbd` socket to clean up. Verify source NBD socket cleanup still works. |
| `test_socket_cleanup_on_success` | Update: no write-side `qemu-nbd` expectations needed. |
| `test_socket_cleanup_on_failure` | Update: no write-side `qemu-nbd` kill needed. |
| `test_bitmap_bucket_driven_full_no_longer_crashes` | Update to use new convert expectations; verify all bucket_level values work. |
| `test_create_full_backup_returns_standalone_qcow2` | Update to use new convert expectations. |
| `test_create_full_backup_dotted_vm_name_passed_untruncated` | Update to use new convert expectations; verify VM name still correct. |

**Helper change:** `_setup_full_unified_expectations()` must be replaced or renamed to `_setup_convert_expectations()` that registers `run_with_stall_detection` expectations instead of `_start_write_server` + `_transfer` expectations.

#### `tests/modules/backup/test_bitmap_incremental.py`

All incremental tests remain valid. No changes needed — they correctly assert NO `qemu-img convert` for incremental paths.

### 3.2 Existing tests that need minor signature/expectation updates

#### `tests/core/test_engine.py`
- `create_full_backup()` now receives `compression_type` and `stall_timeout` kwargs. Update mock expectations.
- `transfer_missing()` receives same kwargs. Update mock expectations.

#### `tests/factory/test_default.py`
- Line 341: Rewrite comment — no longer about "file-copy fallback", just "provider is None when prerequisite missing."

### 3.3 New fixture file needed

#### `tests/fixtures/configs/global_section.toml`
```toml
# Config using [global] section format
[global]
compress = false
lockfile = "/run/qsnap.lock"

[[vm]]
name = "testvm"
base_image = "/var/lib/libvirt/images/testvm.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/testvm"

  [[vm.target]]
  path = "/mnt/backup/testvm"
```

#### `tests/fixtures/configs/global_section_override.toml`
```toml
# Config with both top-level and [global] section — top-level overrides
compress = true
[global]
compress = false

[[vm]]
name = "testvm"
base_image = "/var/lib/libvirt/images/testvm.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/testvm"

  [[vm.target]]
  path = "/mnt/backup/testvm"
```

### 3.4 Stale rsync/file-copy references to clean up

| File | Line | Stale Text | Action |
|------|------|-----------|--------|
| `tests/config/test_resolver.py` | 14 | "With rsync/file-copy removed, verify always defaults to ``"metadata"`` (there is no mode-dependent default)." | Remove "With rsync/file-copy removed", keep only "verify always defaults to ..." |
| `tests/config/test_parser.py` | 11 | "- Removed rsync/file-copy fields trigger deprecation WARNINGs." | Remove this line entirely |
| `tests/mocks/mock_factory.py` | 33-34 | "# After rsync/file-copy removal, the factory always returns the # bitmap backup provider. Alias _backup_provider..." | Rewrite: "# The factory always returns the bitmap backup provider. Alias _backup_provider..." |
| `tests/mocks/mock_factory.py` | 51-52 | "# after rsync/file-copy removal)." | Remove this fragment |
| `tests/mocks/test_mock_factory.py` | 72 | "after rsync/file-copy removal." | Remove this fragment from docstring |
| `tests/factory/test_default.py` | 341 | "no file-copy fallback (design R4)" | Rewrite: "No bitmap provider when prerequisites are unmet" |

## 4. Risks & Edge Cases

### 4.1 From design.md

| Risk | Mitigation in Tests |
|------|-------------------|
| **NBD socket race condition**: `virsh backup-begin` starts NBD asynchronously; `qemu-img convert` may fail if socket not ready | Existing `_transfer_with_retry()` wraps the convert call with exponential backoff. **Test**: `test_convert_retries_on_connection_refused` — MockShell returns ECONNREFUSED on first two `qemu-img convert` calls, success on third; assert 3 attempts made. |
| **Progress bar output**: `qemu-img convert -p` writes progress to stderr; stall detection monitors output file growth independently | **Test**: `test_stall_detection_not_confused_by_progress_output` — Unit test verifying `run_with_stall_detection` is called and stderr is captured but does not interfere with output-file growth monitoring. |
| **Temporary disk space for stopped-VM fallback**: `.tmp` file created on target; atomic rename on success, deleted on failure | **Covered by**: 1e, 1f, 4e, 4f — tests already assert `.tmp` cleanup on failure and rename on success. |
| **Spec conflict resolution**: `nbd-bitmap-backup` spec previously said "No `qemu-img convert`" — updated spec limits this to incrementals only | **Covered by**: 3b (incremental assert no convert) + all Group D updates (FULL asserts convert IS used). |
| **Backward compatibility**: Users with `compress = true` see behavior change from `driver=compress` to `qemu-img convert -c`; output format identical | **Covered by**: 1m, 1n (integration speed tests prove correctness). Contract test verifies `IBackupProvider` interface unchanged. |

### 4.2 Edge cases to cover

| Edge Case | Test |
|-----------|------|
| `compression_type="zlib"` — different algorithm | `test_convert_cmd_zlib_compression` — Unit test verifying `-o compression_type=zlib` in command |
| `backup_stall_timeout = "0"` — disables stall detection | `test_convert_zero_stall_timeout_passes_0` — Unit test verifying `stall_timeout=0` passed through |
| `compress=False` at global level propagates correctly | `test_global_compress_false_no_c_flag` — Already covered by 2e |
| `get_first_disk_path` returns wrong path → `qemu-img convert` fails | `BackupResult(success=False, error=...)` with clear error message; covered by 1e failure path |
| Concurrent FULL backup on same VM (two targets) | Stress test: `tests/stress/test_concurrent.py` — lockfile prevents parallel runs |
| VM has multiple disks — only first disk backed up | `test_create_full_backup_only_backs_up_first_disk` — Integration test verifying `get_first_disk_path` behavior with multi-disk VM |
| `qemu-img convert` binary missing from system | `test_convert_binary_missing_returns_error` — Unit test: MockShell `.expect("qemu-img convert").raises(FileNotFoundError)`, assert `BackupResult(success=False)` |
| Rename fails after successful convert (e.g. permissions) | `test_convert_rename_failure_returns_error` — Unit test: `mv` command fails, assert `BackupResult.error` contains meaningful message |

### 4.3 Regression guardrails

All existing unit tests in `tests/modules/backup/test_bitmap_incremental.py` MUST pass unchanged — they test the incremental path which is NOT modified. This serves as a regression guard for the `pread`/`pwrite` engine.

The `tests/interfaces/` contract tests MUST pass — `BitmapBackupProvider` still implements `IBackupProvider` with the same method signatures (plus new kwargs with defaults).

The `tests/config/test_model.py` immutability tests MUST pass — `VMConfig`, `TargetConfig` are unchanged. `GlobalConfig` adds no new fields.
