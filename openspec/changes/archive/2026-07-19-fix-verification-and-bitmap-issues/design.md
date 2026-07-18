## Context

qsnap's backup verification and bitmap transfer pipeline have several interconnected bugs discovered through production usage. The `verify_backup()` function in `qsnap/utils/verification.py` compares `actual-size` between source and target with a hardcoded ±10% tolerance. This check is unreliable because the source is a live external snapshot — the running VM writes data to it between transfer completion and the `qemu-img info` call, causing the source's `actual-size` to grow beyond tolerance. This affects both rsync mode (file-copy) and bitmap mode (NBD), since both call `verify_backup()` with the source snapshot path.

Additionally, when verification fails, the partially-transferred file is not deleted — it remains on disk until retention cleanup finds it via `glob("*.qcow2")` and deletes it with a misleading `[delete] removed backup` log. In bitmap mode, the first run produces two full NBD exports: one from the bucket strategy (`create_full_backup()`) and one from `transfer_missing()` (no prior checkpoint → full export without `--incremental`).

The current default verification mode is `"metadata"`, which is too weak for rsync mode where `"hash"` (SHA-256 comparison) is cheap and immune to race conditions. However, hash mode cannot work in bitmap mode because NBD-converted qcow2 files have different internal structure than the source snapshot.

## Goals / Non-Goals

**Goals:**
- Eliminate false verification failures caused by the actual-size race condition
- Ensure failed backup files are deleted immediately with honest logging
- Make hash verification the default for rsync mode (stronger, race-immune)
- Eliminate redundant full NBD export on first bitmap run
- Fix `--force-share` inconsistency between `verify_backup()` and `verify_full_backup()`
- Document the bitmap hash limitation and recommend `verify="full"` for bitmap mode
- Make NBD bitmap the default incremental mode (with automatic fallback for old libvirt)
- Add compression to incremental transfers in both rsync and NBD modes
- Warn and auto-downgrade when bitmap mode is configured with `verify="hash"`

**Non-Goals:**
- Adding a separate `compress_incremental` config field (use existing `target.compress`)
- Redesigning the verification tier system (off/metadata/hash/full remains)
- Changing the `IStateManager` schema (no state migration needed)
- Modifying any ABC interface signatures (no breaking changes)
- Fixing the retention logic that deletes successful backups without a FULL anchor (separate issue)

## Decisions

### D1: Remove actual-size check entirely (not relax tolerance)

**Decision:** Remove the `actual-size` tolerance check (lines 255-264 of `verification.py`) completely from `verify_backup()`. Do not relax the tolerance to 20% or 50% — remove it.

**Rationale:** The actual-size metric measures physical on-disk file size, which is inherently unstable for live sources. A running VM writes to the active snapshot layer between transfer and verification, growing `actual-size`. No tolerance value is safe — under heavy write load, the source can grow by 50%+ in seconds. The remaining checks (format = qcow2, virtual-size exact match) are sufficient for metadata-level verification. Virtual-size is the logical disk capacity and does not change during VM operation.

**Alternatives considered:**
- *Relax tolerance to 20%*: Still fails under heavy write load. Kicks the can down the road.
- *Make tolerance configurable*: Adds complexity for a check that provides marginal value. Users would need to tune it per-VM based on write patterns.
- *Freeze the source before verification*: Would require pausing the VM or creating an additional snapshot — too expensive for a post-transfer check.

### D2: Delete failed backup files immediately in provider, not in Core

**Decision:** Add `self._shell.run(["rm", "-f", str(target_file)], timeout=10)` immediately after the verification failure WARNING log in both `FileCopyBackupProvider.transfer_missing()` (line 357) and `BitmapBackupProvider.transfer_missing()` (line 182), before appending `BackupResult(success=False)` and `continue`.

**Rationale:** The provider created the file; the provider should clean it up. This is consistent with the existing pattern where `create_full_backup()` deletes the `.tmp` file on failure. Moving cleanup to Core would require Core to inspect `BackupResult.target_path` and call `rm` — this couples Core to filesystem details that belong in the provider. The provider already has `IShell` injected for exactly this purpose.

**Alternatives considered:**
- *Delete in Core after `_transfer_with_retry` returns*: Requires Core to know which results failed and call `rm` on their `target_path`. Violates the principle that providers manage their own files.
- *Let retention handle it (current behavior)*: Produces misleading logs and leaves garbage on disk between transfer and retention phases (which could be minutes apart).

### D3: Mode-dependent default for `TargetConfig.verify`

**Decision:** Change `TargetConfig.verify` default from `"metadata"` to a mode-dependent resolution: `"hash"` when `incremental_mode == "file-copy"`, `"metadata"` when `incremental_mode == "bitmap"`. Implement this in `ConfigFacade._build_target()` — if the user does not explicitly set `verify`, resolve the default based on `incremental_mode`.

**Rationale:** SHA-256 hash verification is race-condition-immune (the hash is computed at snapshot creation time and never changes) and has negligible overhead for small incremental files (~0.005s for 458 KB). For bitmap mode, hash verification is impossible because NBD-converted qcow2 files have different internal structure — the SHA-256 of the source snapshot will never match the SHA-256 of the NBD-converted target. Using `"metadata"` as the bitmap default is correct and unchanged.

**Implementation detail:** The `TargetConfig` dataclass field default stays as `"metadata"` (the dataclass cannot reference `self.incremental_mode` in a field default). The mode-dependent resolution happens in `ConfigFacade._build_target()`:

```python
# In ConfigFacade._build_target():
verify = str(tgt_raw.get("verify", None))
if verify is None:
    # Mode-dependent default
    if incremental_mode == "file-copy":
        verify = "hash"
    else:
        verify = "metadata"
else:
    # Validate user-provided value
    if verify not in ("off", "metadata", "hash", "full"):
        raise ConfigError(...)
```

**Alternatives considered:**
- *Make hash the global default*: Breaks bitmap mode — hash verification would silently skip (returns `None` when `expected_hash` is `None`, which is always the case for bitmap).
- *Fix bitmap hash first, then make hash global default*: Would require changing the hash mechanism from SHA-256 to `qemu-img compare` — this is a different verification tier, not a hash. The current `"full"` tier already does this.

### D4: Checkpoint-only creation when FULL exists (bitmap first-run fix)

**Decision:** In `BitmapBackupProvider.transfer_missing()`, when `prior_checkpoints` is empty (no prior checkpoint), check `self._state.get_full_backups(str(target.path))`. If FULLs exist in state, create a checkpoint via `virsh checkpoint-create-as` without performing a data transfer, then `continue` to the next snapshot. The FULL already contains all data at this point in time; the checkpoint serves as the baseline for the next incremental run.

**Rationale:** On the first bitmap run, the bucket strategy creates a FULL via `create_full_backup()` (which does not create a checkpoint — design D3 in AGENTS.md). Then `transfer_missing()` finds no checkpoint and performs a full NBD export — redundant since the FULL already has all the data. By creating a checkpoint without data transfer, we establish the baseline for future incrementals without duplicating the full export.

This does NOT violate design D3 ("checkpoint lifecycle exclusively in `transfer_missing`") — the checkpoint creation still happens in `transfer_missing()`, just in a new code path that skips the data transfer when it's unnecessary.

**Algorithm:**
```
for snapshot in snapshots:
    if snapshot.name in existing_names:
        continue  # already on target

    prior_checkpoints = list_checkpoints_for_target(...)
    prior = prior_checkpoints[-1] if prior_checkpoints else None

    if prior is None:
        # No prior checkpoint — check if FULL exists
        if self._state is not None:
            fulls = self._state.get_full_backups(str(target.path))
            if fulls:
                # FULL already has the data — create checkpoint, skip transfer
                checkpoint_name = f"qsnap-{target_hash}-{snapshot.name}"
                create_checkpoint(vm_name, checkpoint_name)
                logger.info(
                    "Created checkpoint %s without transfer "
                    "(FULL exists in state)",
                    checkpoint_name,
                )
                continue

        # No FULL, no checkpoint — full NBD export (existing behavior)
        ...

    # Normal incremental path with --incremental (existing behavior)
    ...
```

**Alternatives considered:**
- *Create checkpoint in `create_full_backup()`*: Violates design D3. The checkpoint lifecycle is explicitly documented as exclusive to `transfer_missing()`.
- *Skip `transfer_missing` entirely in Core when FULL just created*: Requires passing a flag from Core to the provider, changing the method signature. The provider should be self-contained.
- *Always create checkpoint at start of `transfer_missing`*: Would create checkpoints even when no FULL exists, which is wrong — the checkpoint should represent the state at the time of the baseline backup.

### D5: Add --force-share to verify_backup() full mode

**Decision:** Add `--force-share` to the `qemu-img compare` command in `verify_backup()` "full" mode (line 289-295 of `verification.py`), matching the behavior of `verify_full_backup()` M3 tier (line 160).

**Rationale:** The source in `verify_backup()` is an external snapshot of a running VM. The VM holds a write lock on the active layer. Without `--force-share`, `qemu-img compare` fails with a lock error. The `verify_full_backup()` M3 tier already uses `--force-share` for the same reason — this is an inconsistency, not a design choice.

**Note:** The existing spec for `backup-verification` says: `--force-share SHALL NOT be added to qemu-img compare. qemu-img compare is a data-copying operation that reads ALL clusters — using --force-share on a live source produces false mismatches or false matches due to race conditions.` This spec was written under the assumption that `--force-share` would cause data races. However, `qemu-img compare` with `--force-share` opens the image in shared mode — it does NOT freeze the source. The comparison may indeed produce false mismatches if the VM writes during the comparison. But the alternative (no `--force-share`) produces a hard lock error, which is worse (no verification at all). The spec needs to be updated to reflect this: `--force-share` is added to avoid lock errors, with a WARNING that results may be unreliable for live sources.

**Alternatives considered:**
- *Remove "full" mode for live sources entirely*: Too restrictive — users with stopped VMs or frozen snapshots should be able to use full verification.
- *Auto-downgrade "full" to "metadata" for live sources*: Surprising behavior — the user explicitly chose "full". Better to add `--force-share` and let the comparison run, with a WARNING if it fails.

## Risks / Trade-offs

- **[Risk] Removing actual-size check weakens metadata verification** → Mitigation: The remaining checks (format + virtual-size) catch the most critical corruption types (wrong file format, truncated disk). Actual-size only catches partial transfers, which rsync's exit code already detects. For bitmap mode, the NBD export either succeeds completely or fails — there is no partial transfer.

- **[Risk] Hash default for rsync may surprise users with large incremental files** → Mitigation: SHA-256 throughput is ~200 MB/s on modern CPUs. A 1 GB incremental takes ~5s to hash. For typical qsnap incrementals (458 KB - 10 MB), the overhead is negligible. Users with unusually large incrementals can explicitly set `verify = "metadata"`.

- **[Risk] Checkpoint-only creation path may fail if state is stale (FULL recorded in state but file deleted on disk)** → Mitigation: Core's `_backup_target()` already performs phantom FULL detection (lines 2504-2519) before calling `transfer_missing()`. Stale FULLs are removed from state before the provider sees them. Additionally, the provider can verify the FULL file exists via `os.path.exists` before creating the checkpoint.

- **[Risk] --force-share on qemu-img compare may produce false mismatches for live sources** → Mitigation: This is already the behavior of `verify_full_backup()` M3 tier. The alternative (no --force-share) produces a hard lock error. A false mismatch is better than no verification — the user can investigate and re-run with `verify="metadata"`.

- **[Risk] Changing default verify mode may break existing test fixtures** → Mitigation: Tests that explicitly set `verify` in their `make_target()` calls are unaffected. Tests that rely on the default will need to be updated — this is expected and will be covered in the test plan.

### D6: Make NBD bitmap the default incremental mode

**Decision:** Change `TargetConfig.incremental_mode` default from `"file-copy"` to `"bitmap"`. The factory already falls back to `FileCopyBackupProvider` when `is_libvirt_new_enough()` returns `False`, so old systems are unaffected.

**Rationale:** NBD bitmap mode is crash-consistent (point-in-time export), has no race condition, produces standalone backups (easier restoration), and transfers only dirty blocks (faster for active VMs). The only disadvantage was the double-FULL bug on first run, which is fixed by D4. Making it the default aligns qsnap with modern backup best practices.

**Migration:** Existing rsync backups on target remain valid. New NBD backups coexist as standalone files. The transition is graceful — no manual intervention needed. Users who want to keep rsync mode set `incremental_mode = "file-copy"` explicitly.

**Alternatives considered:**
- *Keep file-copy as default, recommend bitmap in docs*: Users stick with the default. Documentation alone doesn't drive adoption.
- *Add a migration wizard*: Over-engineering. The transition is already graceful.

### D7: Compression for incremental transfers in both modes

**Decision:** Use the existing `target.compress` field (default `True`) to control incremental compression in both modes:
- **NBD bitmap**: Add `-c` flag to `qemu-img convert` in `BitmapBackupProvider.transfer_missing()` (line 144-151). This compresses the output qcow2 file (zlib per-cluster compression).
- **rsync file-copy**: Add `--compress` flag to the rsync command in `FileCopyBackupProvider.transfer_missing()` (lines 130-151). This provides transfer-level compression (useful for network targets; minimal effect for local targets).

**Rationale:** FULL backups already compress via `target.compress`. Incrementals should be consistent. For NBD, `-c` compresses the output file (saves disk space). For rsync, `--compress` compresses the transfer stream (saves bandwidth). Both use the same config flag — no new field needed.

**Note on hash verification:** Compression does not affect `verify="hash"` for rsync mode because SHA-256 is computed on the target file after transfer — a compressed transfer produces the same file bytes. For NBD mode, hash verification is not available regardless of compression.

**Alternatives considered:**
- *Add `compress_incremental` field*: Unnecessary complexity. Users who want compression for FULLs want it for incrementals too.
- *Post-rsync `qemu-img convert -c`*: Doubles I/O (read+write the entire file again). Not worth it for small incrementals.
- *No rsync compression*: Inconsistent with NBD. `--compress` is cheap (CPU-only, no extra I/O).

### D8: Warn and auto-downgrade when bitmap mode configured with verify="hash"

**Decision:** In `ConfigFacade._build_target()`, when `incremental_mode="bitmap"` and `verify="hash"` are configured together, emit a WARNING and automatically downgrade `verify` to `"metadata"`. The user is informed via log: "verify='hash' is not supported in bitmap mode (NBD-converted qcow2 has different internal structure). Downgrading to verify='metadata'. Use verify='full' for content-level verification."

**Rationale:** Hash verification silently skips when `expected_hash` is `None` (which is always the case in bitmap mode). This is confusing — the user thinks they have hash verification, but they don't. A WARNING + auto-downgrade is explicit and honest. The user can switch to `verify="full"` if they need content-level verification.

**Alternatives considered:**
- *Raise ConfigError*: Too harsh — the configuration is not invalid, just suboptimal. Auto-downgrade + WARNING is friendlier.
- *Silent skip (current behavior)*: Misleading. The user doesn't know hash verification is not working.
