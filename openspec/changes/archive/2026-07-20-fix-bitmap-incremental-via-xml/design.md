## Context

The bitmap (NBD) incremental backup mode was introduced in the `2026-07-14-consolidate-and-bitmap-backup` change and refined through several subsequent changes. The most recent change (`2026-07-19-fix-verification-and-bitmap-issues`) made bitmap the default `incremental_mode` and added the checkpoint-only creation path (design D4).

However, the implementation contains a **fundamental bug**: `BitmapBackupProvider.transfer_missing()` passes `--incremental <checkpoint>` as a CLI flag to `virsh backup-begin`, but **this flag does not exist in any version of virsh** (confirmed on libvirt 12.5.0 — the latest release). The correct libvirt API is to embed the checkpoint name as an `<incremental>` element inside the backup XML document. The libvirt schema (`domainbackup.rng:42-44`) defines this element as an optional child of `<domainbackup>`.

This means bitmap incremental backups have **never worked** — the first run succeeds (no prior checkpoint → flag omitted → full NBD export), but every subsequent run fails with `error: command 'backup-begin' doesn't support option --incremental`, creating a permanent failure loop. The tests passed because `MockShell` does not validate that flags actually exist in virsh.

Secondary issues discovered during investigation:
- `write_backup_xml()` is duplicated in `nbd.py:120` and `bitmap.py:552` (identical logic, violates `shared-utilities` spec)
- `qemu-img rebase -F` was renamed to `-B` in QEMU 11.0 (3 call sites use deprecated `-F`)
- `BitmapBackupProvider.create_full_backup()` double-records FULLs in state (provider + Core both call `record_full_backup()`)
- `verify="full"` with `qemu-img compare` will fail for incremental NBD exports (target has only dirty blocks, source has full data via backing chain)
- No orphaned checkpoint detection exists (checkpoints live only in libvirt, not in state files)

## Goals / Non-Goals

**Goals:**
- Fix the `--incremental` bug so bitmap incremental backups actually work
- Remove code duplication and deprecated flags
- Add orphaned checkpoint detection for operational hygiene
- Add a verification guard for the `verify="full"` + bitmap incremental edge case
- Write integration tests that verify the real virsh/qemu bitmap flow
- Update README to document the correct libvirt API usage

**Non-Goals:**
- Multi-disk VM support for bitmap mode (only the first disk is pulled via `get_first_disk_target` — this is an existing limitation, not addressed here)
- Changing the ABC interfaces (no `IBackupProvider` signature changes)
- Adding `--diskspec` to `checkpoint-create-as` (works without it for single-disk VMs; libvirt auto-creates dirty-bitmaps on all disks)
- Redesigning the checkpoint lifecycle (create/delete/rotate logic is correct; only the transport mechanism is broken)
- Adding a `qsnap clean` CLI command (detection is added; cleanup is manual via `virsh checkpoint-delete` or a future change)

## Decisions

### D1: Incremental checkpoint via XML element, not CLI flag

**Decision:** Replace `backup_cmd.extend(["--incremental", prior])` with embedding `<incremental>prior</incremental>` in the backup XML.

**Rationale:** The `--incremental` flag does not exist in virsh. The libvirt schema (`domainbackup.rng:42-44`) defines `<incremental>` as an optional child of `<domainbackup>`. All three scraped documentation sources (libvirt.org, oVirt, Bacula) confirm that incremental backup is configured via the XML, not a CLI flag. The Bacula article explicitly shows `virsh backup-begin vm_name --backupxml vm_config.xml` with no `--incremental` flag.

**Alternative considered:** Use `--checkpointxml` flag to pass a checkpoint XML. Rejected — `--checkpointxml` creates a NEW checkpoint during the backup, it does not reference a PRIOR checkpoint for incremental export. The `<incremental>` element is the correct mechanism.

**Implementation:** `write_backup_xml(socket_path, incremental=None)` gains an optional parameter. When non-None, the XML includes `<incremental>{checkpoint}</incremental>` before the `<server>` element. `BitmapBackupProvider.transfer_missing()` calls `write_backup_xml(socket_path, incremental=prior)` and removes the `--incremental` CLI flag extension.

### D2: Remove write_backup_xml duplication

**Decision:** Delete `_write_backup_xml` static method in `bitmap.py:552-565`. Import `write_backup_xml` from `qsnap.utils.nbd` and call it directly.

**Rationale:** The duplication was an incomplete extraction during commit `7b80022` (2026-07-18, "resolve architecture violations"). The `shared-utilities` spec (`spec.md:23-27`) explicitly requires `write_backup_xml` to live in `qsnap/utils/nbd.py` and providers to import from there. The two implementations are byte-for-byte identical.

**Alternative considered:** Keep both and add the `incremental` parameter to both. Rejected — violates DRY and the spec, and the duplication was accidental.

### D3: Update qemu-img rebase -F to -B

**Decision:** Replace `-F` with `-B` in all three `qemu-img rebase` call sites: `file_copy.py:293-302`, `file_copy.py:343-352`, `core/__init__.py:818`.

**Rationale:** QEMU 11.0 renamed the `rebase` subcommand's `--backing-format` flag from `-F` to `-B` (confirmed via `qemu-img rebase --help` on QEMU 11.0.2: `-B, --backing-format BACKING_FMT (was -F in <=10.0)`). The `-F` flag still works as a deprecated alias on QEMU 11.0, but will eventually be removed. Note: `qemu-img convert` still uses `-F` for `--backing-format` — only `rebase` changed.

**Alternative considered:** Add version detection to choose `-F` or `-B`. Rejected — qsnap already requires libvirt ≥ 6.0 (QEMU ~4.2+), and `-B` works on all QEMU versions that support the rebase subcommand. No backwards-compatibility risk.

### D4: Remove double-recording of FULL backups in bitmap mode

**Decision:** Remove `self._state.record_full_backup()` from `BitmapBackupProvider.create_full_backup()` (`bitmap.py:418-424`). State recording is Core's responsibility after post-create verification, matching `FileCopyBackupProvider` behavior.

**Rationale:** `BitmapBackupProvider.create_full_backup()` calls `record_full_backup()` at line 419 (before returning), then Core calls `record_full_backup()` again at `core/__init__.py:2384` (after post-create verification passes). This produces duplicate entries in `_full_backups.json`. `FileCopyBackupProvider.create_full_backup()` does NOT self-record — it leaves that to Core. The bitmap provider should match this pattern.

**Migration:** A one-time deduplication of `_full_backups.json` is needed. `JsonStateManager` SHALL deduplicate entries with the same `name` + `target_path` on load.

### D5: verify="full" guard for bitmap incremental

**Decision:** When `incremental_mode == "bitmap"` and `verify == "full"` is explicitly configured, ConfigFacade SHALL log a WARNING and auto-downgrade to `"metadata"`.

**Rationale:** `verify="full"` uses `qemu-img compare` to compare source (snapshot with backing chain → full data) against target (standalone NBD qcow2). For FULL NBD exports, this works (target has all data). For **incremental** NBD exports, the target contains only dirty blocks (non-dirty = zeros/unallocated), while the source resolves to full data via backing chain → `qemu-img compare` will always mismatch. This is a new edge case that becomes relevant once the `<incremental>` XML fix makes incremental backups actually work.

**Alternative considered:** Make `verify_backup()` aware of whether the backup is full or incremental, and skip `qemu-img compare` for incrementals. Rejected — this would require passing an `is_incremental` flag through the call chain, complicating the interface. The auto-downgrade is simpler and matches the existing pattern for `verify="hash"` in bitmap mode.

**Note:** This guard is conservative — it downgrades `verify="full"` for ALL bitmap transfers (both full and incremental). This is acceptable because `verify="metadata"` is the default and recommended mode for bitmap, and `verify="full"` was never practically usable for bitmap incrementals (the `--incremental` bug prevented incremental transfers from ever succeeding).

### D6: Orphaned checkpoint detection

**Decision:** Add orphaned checkpoint detection to `Core.check_state()`. The method SHALL query `virsh checkpoint-list --name --domain <vm>` for each VM in config, parse the `qsnap-{hash}-{snapshot}` naming convention, and flag any checkpoint whose `target_hash` does not match any configured target's `_target_hash(str(target.path))`.

**Rationale:** Checkpoints live only in libvirt (not in state files). When a VM is removed from config, a target is removed, or a target path changes, checkpoints become permanently orphaned with no cleanup. The `_target_hash()` function (`bitmap.py:547`) and `list_checkpoints()` (`bitmap.py:485`) already provide the building blocks — the detection logic just needs to compare hashes.

**Non-goal:** Automatic deletion of orphaned checkpoints. Detection + WARNING logging is the scope. Users can manually delete via `virsh checkpoint-delete --metadata` or a future `qsnap clean` command.

**Implementation:** Add `orphan_checkpoints: list[str]` field to `StateCheckResult`. In `check_state()`, for each VM, compute all configured target hashes, call `BitmapBackupProvider.list_checkpoints(vm_name)`, and flag any checkpoint whose hash prefix doesn't match.

### D7: Integration tests on real virsh/qemu

**Decision:** Write integration tests in `tests/integration/` that verify the real virsh/qemu bitmap flow: (1) backup XML with `<incremental>` is accepted by virsh, (2) FULL→incremental flow works, (3) dirty-block-only export produces smaller files, (4) rotation safety (old FULL not deleted before new one exists).

**Rationale:** The current tests use `MockShell` which does not validate that virsh flags actually exist. This is why the `--incremental` bug went undetected. Integration tests with real `SubprocessShell` against a disposable test VM (existing `test_vm` fixture) would have caught this immediately.

**Test delegation:** The test plan SHALL be written by a dedicated tester agent (Mr.Tester) who receives the TESTING.md document and has access to real libvirt/virsh/qemu. The tester SHALL also identify outdated tests (e.g., tests that assert `--incremental` is passed as a CLI flag) and plan their removal.

## Risks / Trade-offs

- **[Risk] Users with orphaned checkpoints from broken `--incremental` runs** → Mitigation: The new `check_state()` detection will report them. Users clean up manually via `virsh checkpoint-delete --metadata`. Documented in README.

- **[Risk] `verify="full"` auto-downgrade may surprise users who explicitly set it** → Mitigation: WARNING log message explains why and suggests `verify="metadata"`. The downgrade only applies to bitmap mode (file-copy mode retains `verify="full"`).

- **[Risk] Double-record deduplication migration may lose data** → Mitigation: Deduplication is by `(name, target_path)` tuple — only exact duplicates are removed. The migration runs on load, is idempotent, and logs each deduplication.

- **[Risk] `<incremental>` XML element may not be supported on very old libvirt** → Mitigation: The `<incremental>` element is part of the backup XML schema since libvirt 6.0 (when `backup-begin` was introduced). qsnap already requires libvirt ≥ 6.0 via `is_libvirt_new_enough()`. No additional version check needed.

- **[Trade-off] Orphan detection adds a `virsh checkpoint-list` call per VM to `check_state()`** → Acceptable: `check_state()` is an offline diagnostic command, not a hot path. The call has a 30-second timeout and is non-fatal on failure.

## Migration Plan

1. **Code fix** (this change): Fix `--incremental` → `<incremental>`, remove duplication, update `-F` → `-B`, fix double-record, add guards and detection.
2. **User cleanup**: Users with orphaned checkpoints from broken runs run `virsh checkpoint-list --domain <vm> --name` and `virsh checkpoint-delete --domain <vm> <checkpoint> --metadata` for each `qsnap-*` checkpoint, OR run `qsnap check --state` (which will now report orphans).
3. **State deduplication**: On first run after the fix, `JsonStateManager` deduplicates `_full_backups.json` entries with matching `(name, target_path)` tuples.
4. **README update**: Document the correct libvirt API, the `<incremental>` XML mechanism, and the bitmap mode limitations.

## Open Questions

- Should `qsnap check --checkpoints` be a new CLI subcommand, or should it be a flag on the existing `qsnap check` command? (Current design: flag on `check_state()`, reported in the state consistency output.)
- Should orphaned checkpoint auto-cleanup be added in a future change? (Current design: detection only, manual cleanup.)
