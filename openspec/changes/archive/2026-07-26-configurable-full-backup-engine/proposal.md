## Why

FULL backups are hardcoded to `qemu-img convert` (C code, ~850 MB/s zstd). The alternative Python `pread`/`pwrite` loop via `libnbd` — which offers finer-grained control over the transfer — exists as dead code (`_transfer(zero_skip=True)`) but cannot be selected. Additionally, `qemu-img convert` flags `-m 4` (parallel coroutines) and `-W` (out-of-order writes) are hardcoded constants with no config override. Users with specific I/O profiles (e.g., SSDs where out-of-order writes hurt, or low-core systems where 4 coroutines cause contention) cannot tune these parameters. This change makes the FULL transfer engine configurable and exposes the convert-specific flags as config options.

## What Changes

- Add `full_transfer_engine: str = "qemu-img-convert"` field to `TargetConfig` and `GlobalConfig` — selects between `"qemu-img-convert"` (default, current behavior) and `"libnbd"` (pread/pwrite loop via `INbdClient`)
- Add `convert_parallel: int = 4` field to `TargetConfig` and `GlobalConfig` — maps to the `qemu-img convert -m` flag (range 1-8)
- Add `convert_out_of_order: bool = True` field to `TargetConfig` and `GlobalConfig` — maps to the `qemu-img convert -W` flag
- **BREAKING**: `IBackupProvider.create_full_backup()` and `IBackupProvider.transfer_missing()` signatures gain three new keyword parameters: `full_transfer_engine`, `convert_parallel`, `convert_out_of_order`. All implementations and mocks must accept these parameters.
- `BitmapBackupProvider._full_pull_lifecycle()` gains an engine-selection branch: when `full_transfer_engine == "libnbd"`, it calls a new `_full_transfer_via_libnbd()` method (reviving the `_start_write_server()` + `_transfer(zero_skip=True)` path) instead of `_qemu_img_convert_transfer()`
- `BitmapBackupProvider._qemu_img_convert_transfer()` accepts `parallel` and `out_of_order` parameters, replacing the hardcoded `-m 4 -W` constants
- Core passes `target.full_transfer_engine`, `target.convert_parallel`, `target.convert_out_of_order` to both `create_full_backup()` and `transfer_missing()`
- ConfigFacade parses and validates the three new fields with option inheritance (global → target, no VM level — matching the existing `compress`/`compression_type` pattern)
- `qsnap.toml.example` documents the three new fields in the target section
- Incremental backups remain hardcoded to `libnbd` (pread/pwrite) — not configurable, by design (design D6)

## Capabilities

### New Capabilities

(none — no new spec files created; all changes modify existing capabilities)

### Modified Capabilities

- `config-model`: Add `full_transfer_engine`, `convert_parallel`, `convert_out_of_order` fields to `TargetConfig` and `GlobalConfig` with option inheritance and validation
- `qemu-img-convert-full-backup`: Change from "THE engine" to "ONE OF TWO configurable engines"; make `-m` and `-W` configurable via `convert_parallel` and `convert_out_of_order`
- `nbd-bitmap-backup`: Allow `_full_pull_lifecycle()` to branch on `full_transfer_engine`; document the `libnbd` FULL path as a supported alternative
- `backup-provider`: Add `full_transfer_engine`, `convert_parallel`, `convert_out_of_order` parameters to `create_full_backup()` and `transfer_missing()` signatures

## Impact

**Affected code:**
- `qsnap/models/config.py` — `TargetConfig` and `GlobalConfig` dataclasses (+3 fields each)
- `qsnap/config/facade.py` — `_parse()`, `_build_vm()`, `_build_target()` (parse, validate, inherit new fields)
- `qsnap/interfaces/backup.py` — `IBackupProvider.create_full_backup()` and `transfer_missing()` signatures (+3 kwargs each)
- `qsnap/modules/backup/bitmap.py` — `_full_pull_lifecycle()` (engine branch), `_qemu_img_convert_transfer()` (configurable flags), new `_full_transfer_via_libnbd()` method, `create_full_backup()` and `transfer_missing()` (pass-through new params)
- `qsnap/core/__init__.py` — `_backup_target()` and `_transfer_with_retry()` (pass new fields from `TargetConfig` to provider calls)
- `qsnap.toml.example` — document new fields in target section

**Affected tests:**
- `tests/config/` — new field parsing, inheritance, validation tests
- `tests/modules/backup/test_bitmap.py` — engine selection branch tests, configurable flag tests
- `tests/interfaces/test_backup_provider.py` — contract tests for new parameters
- `tests/mocks/mock_modules.py` — `MockBackupProvider` and `MockBitmapBackupProvider` must accept new kwargs
- `tests/core/` — Core pass-through tests for new fields

**No migration needed:** All new fields have defaults matching current behavior (`"qemu-img-convert"`, `4`, `True`). Existing configs work unchanged.

**No state migration needed:** The new fields are config-only, not persisted in `IStateManager` JSON state.

**Risk:** Reviving the dead code path (`_transfer(zero_skip=True)`) for the `libnbd` engine requires careful testing. The `_start_write_server()` + `_transfer()` path for FULLs was last exercised before commit `8b36c23` (v0.3.0). The qcow2 file pre-creation step (needed for the libnbd path) must set the correct virtual size and compression_type.
