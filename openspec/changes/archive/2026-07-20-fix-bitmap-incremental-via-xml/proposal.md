## Why

The bitmap (NBD) incremental backup mode is fundamentally broken: `BitmapBackupProvider` passes a `--incremental` CLI flag to `virsh backup-begin`, but **this flag does not exist in any version of virsh** (confirmed on libvirt 12.5.0). The first pipeline run succeeds (no prior checkpoint → flag omitted → full NBD export), but every subsequent run fails with `error: command 'backup-begin' doesn't support option --incremental`, creating a permanent failure loop. The correct libvirt API is to embed the checkpoint name as an `<incremental>` element inside the backup XML document, not as a CLI flag.

Additionally, several secondary issues were discovered during investigation: a duplicated `write_backup_xml` function, a deprecated `qemu-img rebase -F` flag (renamed to `-B` in QEMU 11.0), a double-recording of FULL backups in state for bitmap mode, no orphaned checkpoint detection, and no integration tests verifying the real virsh/qemu bitmap flow.

## What Changes

- **Fix incremental checkpoint passing**: Replace the non-existent `--incremental` CLI flag with an `<incremental>` element in the backup XML. Modify `write_backup_xml()` to accept an optional `incremental` parameter.
- **Remove `write_backup_xml` duplication**: Delete the duplicate `_write_backup_xml` static method in `bitmap.py` and use the shared `write_backup_xml` from `qsnap/utils/nbd.py` (violates `shared-utilities` spec).
- **Update `qemu-img rebase -F` to `-B`**: The `-F` (backing-format) flag was renamed to `-B` in QEMU 11.0. Update all three call sites in `file_copy.py` and `core/__init__.py`.
- **Fix double-recording of FULL backups**: `BitmapBackupProvider.create_full_backup()` calls `self._state.record_full_backup()` internally, then Core calls it again after post-create verification. Remove the provider-internal recording to match `FileCopyBackupProvider` behavior.
- **Add `verify="full"` guard for bitmap incremental**: `qemu-img compare` between a source snapshot (with backing chain → full data) and an incremental NBD target (standalone, only dirty blocks) will always mismatch. Add a WARNING and auto-downgrade when bitmap mode + `verify="full"` is configured for incremental transfers.
- **Add orphaned checkpoint detection**: Checkpoints live only in libvirt (not in state files). When a VM/target is removed or a target path changes, checkpoints become permanently orphaned with no cleanup. Add detection logic to `check_state()` and pre-flight cleanup.
- **Add integration tests on real virsh/qemu**: Write tests that verify backup XML validity, FULL→incremental flow, dirty-block-only export, and rotation safety on a real libvirt environment.
- **Update README**: Document the correct libvirt version requirements, the `<incremental>` XML mechanism, and the bitmap mode limitations.

## Capabilities

### New Capabilities
- `orphan-checkpoint-detection`: Detect and optionally clean up libvirt checkpoints that no longer correspond to any configured VM or target. Covers detection logic, CLI integration (`qsnap check --checkpoints`), and reporting.

### Modified Capabilities
- `nbd-bitmap-backup`: Replace the `--incremental` CLI flag with the `<incremental>` XML element in the backup XML. The checkpoint is now passed via XML, not CLI.
- `shared-utilities`: Remove the duplicate `_write_backup_xml` in `bitmap.py`; `BitmapBackupProvider` SHALL import `write_backup_xml` from `qsnap.utils.nbd`. The function signature gains an optional `incremental: str | None = None` parameter.
- `backup-provider`: Update `qemu-img rebase` flag from `-F` to `-B` (QEMU 11.0+). Remove double-recording of FULL backups in `BitmapBackupProvider.create_full_backup()` — state recording is Core's responsibility (matches `FileCopyBackupProvider`).
- `backup-verification`: Add a guard for `verify="full"` in bitmap incremental mode — `qemu-img compare` between a backing-chain source and a dirty-block-only target always mismatches. Auto-downgrade to `"metadata"` with a WARNING for incremental transfers.

## Impact

**Affected code:**
- `qsnap/utils/nbd.py` — `write_backup_xml()` gains `incremental` parameter
- `qsnap/modules/backup/bitmap.py` — remove `_write_backup_xml`, use shared function; remove `--incremental` CLI flag; pass checkpoint via XML; remove double `record_full_backup` call
- `qsnap/modules/backup/file_copy.py` — update `qemu-img rebase -F` → `-B` (2 sites)
- `qsnap/core/__init__.py` — update `qemu-img rebase -F` → `-B` (1 site, restore); add orphan checkpoint detection to `check_state()`
- `qsnap/config/facade.py` — add `verify="full"` guard for bitmap incremental
- `README.md` — document libvirt version requirements and bitmap mode

**Affected ABCs:** None (no interface signatures change).

**Affected specs:** `nbd-bitmap-backup`, `shared-utilities`, `backup-provider`, `backup-verification` (delta specs), plus new `orphan-checkpoint-detection`.

**Migration:** Users with orphaned checkpoints from the broken `--incremental` runs must clean them up manually (`virsh checkpoint-delete --domain <vm> <checkpoint> --metadata`) or via the new `qsnap check --checkpoints` command. The state files (`_full_backups.json`) may contain duplicate FULL entries from the double-recording bug — a one-time deduplication migration is needed.

**Dependencies:** No new runtime dependencies. Requires libvirt ≥ 6.0 for `backup-begin` (unchanged). The `<incremental>` XML element is supported in all libvirt versions that support `backup-begin`.
