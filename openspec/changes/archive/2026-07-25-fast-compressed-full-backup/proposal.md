## Why

FULL backups with compression enabled run at ~1.5 MB/s — catastrophic throughput caused by `driver=compress` in the write-side `qemu-nbd` (synchronous per-cluster zstd compression in a single thread, plus Python `pread`/`pwrite` overhead). Without compression, the same path achieves 30–89 MB/s. Additionally, a TOML parsing bug causes `[global]` section keys to be silently ignored, making `compress = false` at the global level ineffective. Finally, stopped-VM FULL backups fail unconditionally because `virsh backup-begin` requires a running VM, with no `qemu-img convert` fallback.

## What Changes

- **Fix `[global]` TOML section parsing**: `ConfigFacade._parse()` SHALL unwrap a `[global]` section into top-level keys before resolving global options. Currently, keys under `[global]` are silently ignored because the parser reads from the top-level dict.
- **FULL backup transfer engine**: `BitmapBackupProvider` SHALL use `qemu-img convert` as the FULL backup transfer engine (replacing the Python `pread`/`pwrite` loop + write-side `qemu-nbd` with `driver=compress`). This applies to both compressed and uncompressed FULLs. The `qemu-img convert` command reads from the NBD source socket (for running VMs) or directly from the source qcow2 (for stopped VMs), and writes to the target qcow2 with optional `-c` compression and parallel coroutines (`-m`, `-W`).
- **Stopped-VM FULL backup fallback**: `BitmapBackupProvider.create_full_backup()` SHALL detect VM state. For stopped VMs, it SHALL use direct `qemu-img convert` from the source qcow2 file (no `virsh backup-begin`, no NBD). For running VMs, it SHALL use `virsh backup-begin` + `qemu-img convert nbd:unix:<socket>`.
- **Incremental backups unchanged**: The Python `libnbd` `pread`/`pwrite` loop with dirty-bitmap meta-context intersection (`overlap_with_allocation()`) SHALL remain the sole engine for incremental transfers. No `qemu-img convert` for incrementals.
- **Activate `run_with_stall_detection()`**: The `IShell.run_with_stall_detection()` method (currently dead code in production) SHALL be used to execute `qemu-img convert` commands with output-file-growth monitoring.
- **README cleanup**: Remove all references to rsync/file-copy backup provider (already removed from code but still referenced in README). Fix 10+ discrepancies between README claims and actual code (e.g., README claims `qemu-img convert -n nbd:unix:<socket>` is used for FULLs, but code uses Python `pread`/`pwrite`).
- **Remove stale rsync test references**: Tests in `test_resolver.py`, `test_parser.py`, `mock_factory.py`, and `test_mock_factory.py` still reference rsync/file-copy removal — clean up these comments and any dead test cases.

## Capabilities

### New Capabilities

- `qemu-img-convert-full-backup`: Using `qemu-img convert` as the FULL backup transfer engine — reads from NBD source (running VM) or direct file (stopped VM), writes to qcow2 target with optional compression and parallel coroutines. Replaces the Python `pread`/`pwrite` loop + write-side `qemu-nbd` for FULL backups only.

### Modified Capabilities

- `config-parsing`: Fix `[global]` TOML section handling — `ConfigFacade._parse()` SHALL unwrap `raw["global"]` into top-level keys before resolving global options. Currently, keys under `[global]` are silently ignored.
- `nbd-bitmap-backup`: Remove the requirement that forbids `qemu-img convert` in the data path. FULL backups SHALL use `qemu-img convert`; incremental backups SHALL continue using the Python `pread`/`pwrite` engine with dirty-bitmap intersection. The `_start_write_server()` and `driver=compress` path SHALL be deprecated for FULL backups but retained for any future use.
- `live-vm-full-backup`: Remove the requirement that forbids `qemu-img convert` fallback for stopped VMs. Stopped-VM FULL backups SHALL use direct `qemu-img convert` from the source qcow2 file. Running-VM FULL backups SHALL use `virsh backup-begin` + `qemu-img convert nbd:unix:<socket>`.
- `shell-abstraction`: Update the requirement describing `run_with_stall_detection()` as surviving for "future" needs — it SHALL now be actively used for `qemu-img convert` FULL backup transfers.

## Impact

- **`qsnap/config/facade.py`**: `_parse()` method — add `[global]` section unwrapping (3 lines).
- **`qsnap/modules/backup/bitmap.py`**: `create_full_backup()` and `_full_pull_lifecycle()` — replace `_start_write_server()` + `_transfer()` with `qemu-img convert` via `IShell.run_with_stall_detection()`. Add VM state check for stopped-VM fallback. Add new `_qemu_img_convert_transfer()` method.
- **`qsnap/utils/nbd.py`**: Add `get_first_disk_path()` helper (returns file path, not target device) for stopped-VM source path resolution.
- **`qsnap/shell/subprocess_shell.py`**: No changes needed — `run_with_stall_detection()` already implemented and tested.
- **`README.md`**: Remove rsync references, fix 10+ discrepancies, update architecture descriptions.
- **Tests**: New unit tests for `qemu-img convert` command construction (compressed/uncompressed, running/stopped). New integration tests measuring speed with/without compression on multi-GB data. New integration test verifying incremental-after-FULL uses dirty bytes (not a second FULL). Clean up rsync references in test files.
- **No ABC interface changes**: `IBackupProvider`, `IShell`, `INbdClient` interfaces are unchanged. All modifications are internal to `BitmapBackupProvider` and `ConfigFacade`.
