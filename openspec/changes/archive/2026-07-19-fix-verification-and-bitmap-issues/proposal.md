## Why

Backup verification and bitmap-mode transfer contain several bugs that cause false failures, misleading logs, and redundant full-disk exports. The `verify_backup()` actual-size tolerance check produces false positives due to a race condition between transfer completion and `qemu-img info` on the live source. Failed backup files are not deleted immediately, causing retention cleanup to delete them later with a misleading `[delete] removed backup` log. Bitmap mode performs two full NBD exports on the first run because `create_full_backup()` does not create a checkpoint and `transfer_missing()` has no checkpoint to use with `--incremental`. The `verify_backup()` "full" mode is missing `--force-share` on `qemu-img compare`, causing lock errors when the source is a live VM snapshot. The default verification mode `"metadata"` is too weak for rsync mode, where `"hash"` is cheap and race-condition-immune.

## What Changes

- **Delete failed backup files immediately** — When `verify_backup()` fails in `FileCopyBackupProvider.transfer_missing()` and `BitmapBackupProvider.transfer_missing()`, the partially-transferred target file SHALL be deleted via `rm -f` before appending `BackupResult(success=False)` and `continue`. This prevents retention cleanup from finding the file and logging a misleading `[delete] removed backup` message.
- **Remove actual-size tolerance from metadata verification** — `verify_backup()` SHALL no longer compare `actual-size` between source and target. The metadata tier SHALL check only: (a) target format is `"qcow2"`, (b) `virtual-size` matches exactly. The actual-size check is unreliable for both rsync and bitmap modes because the source is a live external snapshot that may grow between transfer and verification.
- **Make hash the default verification mode for file-copy (rsync) mode** — `TargetConfig.verify` SHALL default to `"hash"` when `incremental_mode == "file-copy"` and to `"metadata"` when `incremental_mode == "bitmap"`. SHA-256 hash verification is race-condition-immune (computed at snapshot creation time) and has negligible overhead for small incremental files. Bitmap mode cannot use hash verification because NBD-converted qcow2 files have different internal structure than the source snapshot.
- **Fix bitmap double-FULL on first run** — `BitmapBackupProvider.transfer_missing()` SHALL check if a FULL backup exists in state for the target when no prior checkpoint is found. If a FULL exists, the provider SHALL create a checkpoint via `virsh checkpoint-create-as` without performing a data transfer, then `continue` to the next snapshot. This avoids a redundant full NBD export when the bucket strategy already created a FULL in the same run.
- **Add --force-share to verify_backup() full mode** — `verify_backup()` SHALL add `--force-share` to the `qemu-img compare` command in `"full"` mode, matching the behavior of `verify_full_backup()` M3 tier. The source is an external snapshot of a running VM and may hold a write lock; without `--force-share`, `qemu-img compare` fails with a lock error.
- **Document bitmap hash limitation** — The README and spec SHALL document that `verify="hash"` is not supported in bitmap mode because NBD-converted qcow2 files have different internal structure. Users SHALL use `verify="full"` for content-level verification in bitmap mode.
- **Make NBD bitmap the default incremental mode** **BREAKING** — `TargetConfig.incremental_mode` SHALL default to `"bitmap"` instead of `"file-copy"`. The factory already falls back to `FileCopyBackupProvider` when libvirt is too old, so old systems are unaffected. Users who want rsync mode must explicitly set `incremental_mode = "file-copy"`.
- **Add compression to NBD incremental transfers** — `BitmapBackupProvider.transfer_missing()` SHALL pass the `-c` flag to `qemu-img convert` when `target.compress=True` (default), matching the existing FULL backup compression behavior.
- **Add compression to rsync incremental transfers** — `FileCopyBackupProvider.transfer_missing()` SHALL add the `--compress` flag to the rsync command when `target.compress=True`, providing transfer-level compression for network targets.
- **Warn when bitmap mode is configured with verify="hash"** — `ConfigFacade` SHALL emit a WARNING when `incremental_mode="bitmap"` and `verify="hash"` are configured together, advising the user to use `verify="metadata"` or `verify="full"` instead. The effective verify mode SHALL be downgraded to `"metadata"` automatically.
- **Update README** — The README SHALL be updated to reflect: NBD as default mode, mode-dependent verification defaults, bitmap hash limitation, compression in both modes, fixed bitmap first-run behavior, and migration path from rsync to NBD.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `backup-verification`: Remove actual-size tolerance check from metadata tier; add `--force-share` to `qemu-img compare` in full mode
- `backup-hash-verification`: Document that hash mode is the default for file-copy mode and unsupported for bitmap mode; add auto-downgrade warning when bitmap + hash configured
- `nbd-bitmap-backup`: Add checkpoint creation without data transfer when FULL exists in state and no prior checkpoint is found; add compression to incremental transfer
- `backup-provider`: Add immediate deletion of failed backup files after verification failure; add compression to rsync incremental transfer
- `config-model`: Change default `verify` field to be mode-dependent (`"hash"` for file-copy, `"metadata"` for bitmap); change default `incremental_mode` to `"bitmap"` **BREAKING**
- `backup-retry`: Add verification failure errors to retryable patterns (optional — only after actual-size removal, since race-condition failures become deterministic)

## Impact

**Affected code:**
- `qsnap/utils/verification.py` — Remove actual-size check (lines 255-264), add `--force-share` to compare command (lines 289-295)
- `qsnap/modules/backup/file_copy.py` — Add `rm -f` after verification failure (lines 351-367); add `--compress` to rsync when `target.compress=True`
- `qsnap/modules/backup/bitmap.py` — Add `rm -f` after verification failure (lines 176-187); add checkpoint-only creation path when FULL exists and no prior checkpoint (lines 83-115); add `-c` to `qemu-img convert` for incremental transfer (lines 144-151)
- `qsnap/models/config.py` — Change `TargetConfig.verify` default to be mode-dependent (line 126); change `TargetConfig.incremental_mode` default to `"bitmap"` (line 124)
- `qsnap/config/facade.py` — Implement mode-dependent default resolution for `verify` field; add bitmap+hash warning and auto-downgrade; validate `incremental_mode` default
- `qsnap/core/__init__.py` — Optionally add verification errors to retryable patterns (lines 2280-2367)
- `README.md` — Document NBD as default, compression in both modes, bitmap hash limitation, fixed first-run behavior, migration path

**Affected ABCs:** None — no interface signatures change.

**Affected specs:** 6 existing specs modified (listed above).

**Migration:** Users with explicit `incremental_mode` or `verify` settings keep their values. Users relying on defaults get NBD mode (with automatic fallback to rsync if libvirt is too old) and mode-dependent verification defaults. Existing rsync backups on target remain valid and are managed by retention alongside new NBD backups. No state migration needed — no `IStateManager` schema changes.

**Breaking changes:** `incremental_mode` default changes from `"file-copy"` to `"bitmap"` **BREAKING**. Users who rely on the default and have a sufficiently new libvirt will switch from rsync to NBD mode. The transition is graceful (existing backups coexist), but new backups will be standalone qcow2 files instead of rebased chain files. Users who want to keep rsync mode must explicitly set `incremental_mode = "file-copy"`.
