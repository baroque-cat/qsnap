## Why

QEMU 11.0+ changed the JSON output schema of `qemu-img info --backing-chain --output=json`: the image file path key changed from `"image"` to `"filename"`, and a nested `"children"` block-graph array was added. Qsnap's chain integrity verification and restore commands parse only the old `"image"` key, causing a false CRITICAL error `"Missing 'image' field in chain entry 0"` that blocks blockcommit operations. Separately, the NBD-based FULL backup path unconditionally ignores the `compress` flag despite experimental verification confirming that `qemu-img convert -c` over NBD works correctly and produces properly compressed qcow2 output. This causes actual FULL backup sizes to be up to 3.3× larger than the projected estimate, wasting storage.

## What Changes

- Fix `qemu-img` JSON parsing to accept both `"image"` (legacy QEMU) and `"filename"` (new QEMU) keys for the file path in chain entries, in both `_verify_backing_chain()` and `_restore_snapshot()`.
- Add `compress` parameter to `nbd_full_export()` and pass the `-c` flag to `qemu-img convert` when requested, enabling compression on NBD-based FULL backups.
- Remove the misleading WARNING `"compress=True ignored for NBD-based FULL backup"` from both `FileCopyBackupProvider` and `BitmapBackupProvider`.
- Remove the `_log_size_estimate()` workaround that factored compression only for direct-convert — the estimate formula is already correct for all paths after the NBD compression fix.
- Update test fixtures to cover both legacy (`"image"`) and new (`"filename"`) `qemu-img` JSON formats.
- Identify and remove obsolete tests that assumed NBD compression is unsupported.

## Capabilities

### New Capabilities
<!-- None — this is a bugfix, not new functionality -->

### Modified Capabilities
- `chain-integrity-verification`: `_verify_backing_chain()` must accept both `"image"` (legacy QEMU) and `"filename"` (QEMU 11.0+) keys in `qemu-img info --backing-chain --output=json` output.
- `backup-provider`: `FileCopyBackupProvider.create_full_backup()` must support compression in the NBD path; `nbd_full_export()` must accept a `compress` parameter; the WARNING about ignored compression must be removed.
- `nbd-bitmap-backup`: `BitmapBackupProvider.create_full_backup()` must support compression in the NBD path; the WARNING about ignored compression must be removed.
- `restore-command`: `_restore_snapshot()` chain parsing must accept both `"image"` and `"filename"` keys.
- `size-estimation`: Remove the implicit assumption that NBD-based FULLs are uncompressed — the existing `×0.3` compression factor is now correct for ALL paths.

## Impact

- **Core**: `_verify_backing_chain()` (line 1879, 1943), `_restore_snapshot()` (line 933), `_log_size_estimate()` (no code change needed — formula is already correct).
- **Modules**: `nbd_helper.py` `nbd_full_export()` (add `compress` param, pass `-c`), `file_copy.py` `FileCopyBackupProvider.create_full_backup()` (remove WARNING, pass `compress`), `bitmap.py` `BitmapBackupProvider.create_full_backup()` (same).
- **Interfaces**: No ABC interface changes — parameter additions are backwards-compatible with defaults.
- **State**: No `IStateManager` schema changes.
- **Tests**: New fixtures (`backing_chain_intact_new.json`, `backing_chain_broken_new.json`), new test cases for new QEMU format in chain verification and restore, new test for NBD compression, removal of tests asserting WARNING on NBD compression.
- **Config**: No config changes.
- **CLI**: No CLI changes.
