## Why

VMs with dots in their names (e.g. `3.Projects_opencode`) cause qsnap's FULL backup to fail: `create_full_backup()` extracts the VM name from the snapshot filename via `source_snapshot.name.split(".")[0]`, truncating `3.Projects_opencode` to `3`. This causes `virsh dominfo --domain 3` to fail, which makes the code fall back from the NBD path to direct `qemu-img convert`, which then fails with `Failed to get shared "write" lock` because the VM is running. A secondary bug in `parse_timestamp()` causes it to always fall back to file mtime for ALL snapshots (not just dotted ones), because it tries `%Y%m%dT%H%M%S` — a format that matches none of the three actual timestamp formats (`%Y%m%d`, `%Y%m%dT%H%M`, `%Y%m%dT%H%M%S%z`).

## What Changes

- **BREAKING**: Add `vm_name: str` as the first parameter of `IBackupProvider.create_full_backup()`, eliminating fragile name parsing. Both call sites (`Core._backup_target()` and `FileCopyBackupProvider.transfer_missing()`) already have `vm_config.name` available.
- Remove `vm_name = source_snapshot.name.split(".")[0]` from `FileCopyBackupProvider.create_full_backup()` (line 347) and `BitmapBackupProvider.create_full_backup()` (line 290).
- Rewrite `parse_timestamp()` in `qsnap/utils/parsing.py` to use regex-based extraction supporting all three timestamp formats (`short`, `long`, `long-iso`) and the `_{disk}` suffix in snapshot names, instead of `split(".")[-1]` which includes the disk suffix and never matches.
- Update all mock implementations (`MockBackupProvider`, `MockBitmapBackupProvider`) and ~40 test call sites to pass the new `vm_name` parameter.
- Add test coverage for VM names containing dots — currently zero tests use dotted VM names.

## Capabilities

### New Capabilities

(none — this is a bugfix, not a new feature)

### Modified Capabilities

- `backup-provider`: The `create_full_backup` requirement changes — the method signature gains a `vm_name: str` parameter as the first positional argument, passed from Core's `vm_config.name`. The method no longer extracts VM name from the snapshot filename. The `transfer_missing()` internal call to `create_full_backup()` also passes `vm_config.name`.
- `parsing-utils`: The `parse_timestamp` requirement changes — the function SHALL support all three configured timestamp formats (`%Y%m%d`, `%Y%m%dT%H%M`, `%Y%m%dT%H%M%S%z`) and SHALL correctly handle the `_{disk}` suffix in snapshot names. The current implementation tries only `%Y%m%dT%H%M%S` (which matches no production format) and takes `split(".")[-1]` (which includes the disk suffix), causing it to always fall back to mtime.
- `live-vm-full-backup`: The VM running-state detection requirement changes — `create_full_backup()` receives `vm_name` as an explicit parameter instead of parsing it from the snapshot name, ensuring `is_vm_running(shell, vm_name)` and `nbd_full_export(shell, vm_name, ...)` receive the full, untruncated VM name.

## Impact

- **Interface change** (`IBackupProvider.create_full_backup`): BREAKING — all implementations and callers must add `vm_name: str` as the first parameter. Affects 2 production implementations (`FileCopyBackupProvider`, `BitmapBackupProvider`), 2 mock implementations, and ~40 test call sites.
- **Utility change** (`parse_timestamp`): Non-breaking signature, but behavior changes — previously always returned mtime, now returns the actual parsed timestamp. This improves retention bucket alignment correctness.
- **No state migration needed**: `vm_name` is not added to `SnapshotInfo` or persisted in state JSON. It flows only through method parameters.
- **No new factory branches**: `DefaultFactory` is unchanged — it already creates the correct provider types.
- **FULL backup naming**: FULL files will now be named with the full VM name (e.g. `3.Projects_opencode.FULL.20260717.qcow2` instead of `3.FULL.20260717.qcow2`). Previously-created truncated FULLs will be orphaned by retention — acceptable since they were created by a bug.
- **Affected modules**: `interfaces/backup.py`, `modules/backup/file_copy.py`, `modules/backup/bitmap.py`, `core/__init__.py`, `utils/parsing.py`, `tests/mocks/mock_modules.py`, plus ~40 test call sites across `tests/modules/backup/`, `tests/integration/`, `tests/core/`, `tests/interfaces/`, `tests/utils/`.
