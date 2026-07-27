## Context

qsnap manages qcow2 backup chains on target storage. Each chain consists of a FULL backup (standalone qcow2) and a sequence of incremental backups (backing-chained qcow2 deltas). The current retention system operates per-item: the `TimeBasedRetention.evaluate()` pure function marks individual backups for keep/remove, then `_cleanup_backups()` attempts to delete removed items while preserving chain integrity via "ghost retention" (skipping deletion of items that have dependents in the keep-set).

Three confirmed production bugs trace to a fundamental architectural mismatch: **backup chains have no merge operation**. Unlike snapshots (where `virsh blockcommit` merges data before deleting), backup incrementals cannot be merged into their FULL anchor. Deleting any intermediate file in a backing chain breaks the chain for all subsequent files. The per-item retention + ghost-retention approach is structurally incapable of handling this constraint correctly.

Current state after production incident: 1 FULL remains, 18-20 incrementals have broken backing chains (intermediate files were cascade-deleted by the ghost-retention bug). The system has no automatic recovery — broken files accumulate until manual `reconcile` deletes them.

## Goals / Non-Goals

**Goals:**
- Eliminate the cascade-deletion ghost-retention bug by removing the mechanism entirely
- Apply retention per-chain (keep/delete entire chains, never individual items from the middle)
- Auto-recover broken backup chains at pipeline startup (detect, delete, force FULL if needed)
- Fix checkpoint lifecycle ("Bitmap already exists" collision)
- Detect and skip temporal mismatch (snapshots predating the newest checkpoint)
- Recover from broken snapshot chains via partial blockcommit + auto-rebase
- Post-cleanup chain integrity verification
- Zero changes to retention engine (stays pure function)
- Zero changes to config parameters (same TOML, different semantics)

**Non-Goals:**
- Within-chain thinning for backups (cannot delete from middle — use more frequent FULLs via F-anchor instead)
- `qemu-img rebase -u` for backup recovery (creates inconsistent chains — data between FULL and deleted intermediates is lost)
- QMP integration for QEMU bitmap enumeration (use full `checkpoint-delete` instead)
- Changes to `IRetentionEngine` interface or `RetentionItem` dataclass
- Changes to `RetentionPolicy` or config parsing

## Decisions

### D1: Per-chain retention instead of ghost-retention patching

**Choice**: Replace per-item retention + ghost-retention cascade-deletion with per-chain retention (Core pre/post-processing).

**Rationale**: Ghost-retention is structurally broken (no memory of retained items during cascade-deletion). Patching it with `ghost_retained` set + transitive closure would leave complex, fragile code and doesn't address the root cause — per-item retention can mark middle items for removal on a structure where middle deletion is impossible. Per-chain retention eliminates the problem entirely: chains are atomic units, kept or deleted as wholes.

**Alternatives considered**:
- Fix ghost-retention with transitive closure + `ghost_retained` set. Rejected: leaves fragile cascade-deletion code, requires post-cleanup verification that doesn't exist, doesn't prevent the root cause.
- Make `TimeBasedRetention.evaluate()` chain-aware (extend `RetentionItem` with `chain_id`, `is_anchor`). Rejected: violates simplicity, requires interface/spec/test changes. Pre/post-processing in Core achieves the same result with zero engine changes.

### D2: Engine stays pure, Core does pre/post-processing

**Choice**: `TimeBasedRetention.evaluate()` receives chain-level `RetentionItem`s (name=FULL name, timestamp=FULL timestamp) and returns chain-level keep/remove. Core expands results to individual items.

**Rationale**: The engine works with abstract `RetentionItem(name, timestamp)`. It doesn't know whether items are snapshots, backups, or chains. Core groups backups by chain (via `_resolve_chain_full_anchor()`), creates one `RetentionItem` per chain, passes to engine, then expands results. The spec requirement ("SHALL remain a pure function... SHALL NOT access IStateManager") is satisfied — chain grouping is pure input preparation, not I/O.

### D3: Auto-recovery is mandatory, not optional

**Choice**: `_validate_state_at_startup()` detects and auto-deletes broken backup chains before retention runs.

**Rationale**: Per-chain grouping relies on `_resolve_chain_full_anchor()` walking the backing chain to find the FULL anchor. For broken-chain files (backing file deleted), this returns `None`. These files cannot be grouped into any chain — they'd be invisible to retention and persist forever. Auto-recovery must run BEFORE retention to clean these up. If no valid FULL remains after cleanup, FULL creation is forced on the next `_backup_target()` call.

### D4: Snapshot oldest-prefix-only retention

**Choice**: Core post-processes snapshot retention results to only remove a contiguous oldest prefix. Middle snapshots marked for removal are moved to keep (chain gap fillers).

**Rationale**: Snapshots form a linear chain (`base → snap1 → ... → snapN → active`). Blockcommit CAN merge, so middle deletion is safe for data integrity. But if pre-commit chain verification fails (missing file), the ENTIRE blockcommit is skipped, causing snapshots to be stuck forever. By only removing the oldest prefix, blockcommit always processes a contiguous range from the base, which is simpler and more reliable.

### D5: Checkpoint full delete with fallback

**Choice**: `_delete_checkpoint_best_effort()` uses `virsh checkpoint-delete` (without `--metadata`) with fallback to `--metadata` if the VM is shut off.

**Rationale**: `--metadata` only removes libvirt's checkpoint metadata; the QEMU dirty bitmap persists. On retry, `_new_checkpoint_name()` generates the same name (libvirt doesn't see the old checkpoint), and QEMU rejects it with "Bitmap already exists". Full delete removes both libvirt metadata and QEMU bitmap. Fallback to `--metadata` is needed when the VM is shut off (full delete requires a running QEMU instance).

### D6: UUID suffix for checkpoint names

**Choice**: Checkpoint names get a 6-char hex suffix: `qsnap-{hash}-{timestamp}-{suffix}`.

**Rationale**: Even with full delete, race conditions can leave orphaned QEMU bitmaps. UUID suffix ensures `_new_checkpoint_name()` never generates a colliding name, regardless of QEMU's internal state. The timestamp is still parseable for rotation logic.

### D7: Blockcommit partial + auto-rebase

**Choice**: When pre-commit chain verification fails, split `to_merge` into committable (before break) and stuck (after break). Blockcommit committable snapshots. For stuck snapshots, use `qemu-img rebase -u` to skip the missing file and re-chain to a valid ancestor. Remove stale state entry for the missing file. Continue blockcommit for rebased stuck snapshots.

**Rationale**: `qemu-img rebase -u` is safe for snapshots (unlike backups) because the active layer (running VM) contains all current data. The missing file's data is already lost (file doesn't exist). Rebase acknowledges this loss and allows the system to continue. Without this, snapshots are stuck forever when any intermediate file is missing.

### D8: `qemu-img rebase -u` NOT used for backup recovery

**Choice**: Broken backup chains are deleted, not rebased. If no FULL remains, a new FULL is force-created.

**Rationale**: `qemu-img rebase -u` changes the backing-file pointer without checking data consistency. For backups with missing intermediate files, this creates an inconsistent chain — the incremental would point to a FULL, but data between the FULL and the deleted intermediates would be lost. The only safe recovery for backups is delete broken + create new FULL.

## Risks / Trade-offs

- **[Per-chain grouping performance]** `_resolve_chain_full_anchor()` makes up to 64 `qemu-img info` calls per incremental. For 100 incrementals, that's 6400 calls. → Mitigation: real chains are short (20-30 elements, 1-3 hops typically). Can add caching if needed.

- **[Full checkpoint-delete slower]** Full delete may take 1-5s longer than `--metadata` only. → Mitigation: acceptable for backup operations that already take minutes.

- **[UUID suffix breaks timestamp parsing]** `_parse_checkpoint_timestamp()` must handle the new format. → Mitigation: update regex to `r"qsnap-([0-9a-f]{8})-(\d{8}T\d{6})(?:-[0-9a-f]+)?"`. Timestamp is still in the same position.

- **[Auto-recovery deletes data without confirmation]** Broken-chain files are auto-deleted at startup. → Mitigation: log every deleted file at WARNING/INFO level. These files are already useless (broken chain = unrestorable). Add `auto_recover` config flag (default True) for operators who want manual control.

- **[More backups retained than per-item]** Per-chain retention keeps all incrementals within a kept chain. A chain with 100 incrementals stays entirely. → Mitigation: use F-anchor syntax to create FULLs more frequently, starting new chains. This is by design — you cannot delete from the middle of a backup chain.

- **[Snapshot oldest-prefix less aggressive]** Some snapshots that per-item retention would remove are kept as chain gap fillers. → Mitigation: blockcommit still processes the oldest prefix. Gap fillers are kept only when needed for chain continuity. Net effect: slightly more snapshots retained, but no stuck blockcommits.

- **[Blockcommit auto-rebase loses point-in-time]** `qemu-img rebase -u` for snapshots skips the missing file's data. The point-in-time snapshot is lost. → Mitigation: the file was already missing (that's why verification failed). The data is already lost. Rebase just acknowledges this and allows progress.
