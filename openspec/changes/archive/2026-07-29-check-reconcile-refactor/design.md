## Context

The qsnap `check` and `reconcile` commands were designed during the bucket-based retention era. After the migration to count-based chains, these commands were partially updated but retain fundamental design flaws:

1. **check** only verifies `qemu-img info --backing-chain` exit codes — it cannot distinguish "file deleted by blockcommit" (legitimate) from "file deleted by disk failure" (data loss). It does not cross-reference state JSON, disk files, or domain XML.

2. **reconcile** deletes orphan files instead of supplementing state from reality. When a file exists on disk and in domain XML but not in state, reconcile deletes the file rather than recording it in state. It also does not call `_refresh_domain_backing_store()` for stale domain XML, and performs unsafe `qemu-img rebase -u` on broken chains automatically.

3. **Post-creation validation** is absent for snapshots — `ExternalSnapshotProvider.create()` trusts virsh exit code 0 without verifying the file landed on disk, the backing chain is correct, or libvirt pivoted the active layer.

4. **Deep check** only checks `corruptions` (not `errors` or `leaks`) with a 60s timeout (too short for large disks).

5. **Default retention values** are `None` → resolve to 0/1, causing extremely aggressive behavior (near-daily FULL creation, single-snapshot retention).

The three sources of truth in the system are: (1) qsnap state JSON files, (2) disk qcow2 files, (3) libvirt domain XML. After legitimate operations (blockcommit, retention cleanup), all three are consistent. After failures or external interference, they diverge. The refactored check detects divergence; reconcile fixes it.

## Goals / Non-Goals

**Goals:**

- Triple-source verification in `check`: cross-reference state JSON ↔ disk files ↔ domain XML for both snapshots and targets
- Correct legitimate-deletion handling: check does not alarm when all three sources agree a file was deleted
- Post-creation validation for snapshots: verify file existence, qcow2 format, backing-filename, corrupt bit, domblklist pivot
- Post-transfer validation for backups: verify chain-to-FULL traversability, checkpoint existence
- Reconcile supplements state from reality (when disk + XML agree but state is stale) instead of deleting files
- Reconcile calls `_refresh_domain_backing_store()` for stale domain XML
- Reconcile no longer performs unsafe auto-rebase on broken chains (only logs CRITICAL)
- Deep check verifies `errors` + `leaks` + `corruptions` with 7200s timeout
- Sensible default retention values: `snapshot_chain_length=24`, `target_chain_length=168`, `target_keep_generations=2`

**Non-Goals:**

- Automatic broken-chain repair (unsafe rebase) — this is the operator's responsibility, system only warns
- Retention enforcement in reconcile — that is the pipeline's job via `_cleanup_backups()`
- Full backup creation in reconcile — that is the pipeline's job via `_backup_target()`
- ABC interface changes — `ISnapshotProvider`, `IBackupProvider`, `IStateManager` remain unchanged
- State file schema migration — JSON file structures remain the same

## Decisions

### D1: Triple-source verification matrix

Check compares three sources for each snapshot/backup:

```
State (JSON)  ↔  Disk (qcow2 files)  ↔  Domain XML (libvirt)

For each snapshot:
  state_has  disk_has  xml_has  → Action
  ──────────────────────────── ─────────────────────────────
  yes        yes       yes      → OK (consistent)
  yes        no        no       → Phantom in state → reconcile: remove_snapshot()
  yes        no        yes      → Anomaly: XML references missing file → reconcile: _refresh_domain_backing_store()
  no         yes       yes      → Orphan (state incomplete) → reconcile: record_snapshot()
  no         yes       no       → Orphan (untracked) → reconcile: rm -f
  no         no        no       → OK (legitimately deleted, all agree)
```

**Rationale:** This matrix correctly handles legitimate deletions (blockcommit removes from state + disk + XML → all "no" → OK) while detecting phantoms (state says "yes" but disk says "no") and orphans (disk says "yes" but state says "no").

**Alternative considered:** Single-source check (only state vs disk). Rejected because it cannot detect stale domain XML references, which cause VM boot failures.

### D2: Reconcile supplements state instead of deleting files

When a file exists on disk and in domain XML but not in state, reconcile records it in state (`record_snapshot()` / `record_full_backup()` / `record_incremental_dependency()`) instead of deleting the file.

**Rationale:** State should follow reality (disk + XML), not the other way around. Deleting a valid file because state is incomplete is data loss. The previous behavior was a design flaw from the bucket era.

**Exception:** If the file is on disk but NOT in domain XML (truly orphan, no libvirt reference), reconcile deletes it — the file is not part of any active chain.

### D3: No automatic unsafe rebase on broken chains

When a broken backing chain is detected (file missing from the middle), reconcile logs CRITICAL and does NOT perform `qemu-img rebase -u`. The previous `_auto_rebase_stuck()` behavior is removed from reconcile.

**Rationale:** `qemu-img rebase -u` is unsafe — it changes qcow2 metadata without verifying data consistency. Data from the missing intermediate snapshot is lost. This should only be done manually by an operator who understands the consequences. The system's job is to warn, not to silently lose data.

**Exception:** `_auto_rebase_stuck()` remains in the blockcommit pipeline (not in reconcile) as a last-resort recovery during active pipeline execution, where the operator has implicitly consented to data loss by running the pipeline.

### D4: Post-creation validation in ExternalSnapshotProvider

After `virsh snapshot-create-as` returns exit code 0, the provider performs:

1. `test -f <snapshot_path>` — file exists on disk
2. `qemu-img info --force-share --output=json` — parse and verify:
   - `format == "qcow2"`
   - `virtual-size` matches base image
   - `backing-filename` points to previous active layer
   - `incompatible-features` does not contain `corrupt`
   - `actual-size` is reasonable (not ~virtual-size, which would indicate full copy)
3. `virsh domblklist --domain <vm>` — verify source path = snapshot_path (libvirt pivot confirmed)

If any check fails, `SnapshotResult(success=False, error=...)` is returned. Core does not call `record_snapshot()` — the failed snapshot is not recorded in state.

**Rationale:** `--no-metadata` means there is no libvirt snapshot to roll back. If virsh returns 0 but the chain is broken, the system must detect it before recording state. Cost: ~50-100ms (one `virsh domblklist` call; rest is parsing already-obtained `qemu-img info`).

### D5: Post-transfer validation in BitmapBackupProvider

After `transfer_missing()` creates an incremental:
1. `qemu-img info --backing-chain` — verify chain from incremental to FULL is traversable
2. `virsh checkpoint-list --name --domain <vm>` — verify checkpoint exists (dirty-bitmap baseline for next incremental)

After `create_full_backup()` creates a FULL:
1. `qemu-img info` — verify `backing-filename` is `<none>` (standalone, not chained)
2. `virsh checkpoint-list` — verify checkpoint exists

**Rationale:** Without checkpoint verification, the next incremental transfer will fail silently (no dirty-bitmap baseline). Without chain verification, a broken incremental could be recorded in state, making the next incremental chain to a broken link. Cost: ~100-300ms per transfer (seconds to minutes). <1% overhead.

### D6: Deep check improvements

`_deep_check_file()` now checks all three fields from `qemu-img check --output=json`:
- `corruptions > 0` → "warning"
- `errors > 0` → "warning" (was not checked)
- `leaks > 0` → "warning" (was not checked)

Timeout increased from 60s to 7200s (2 hours) to match `verify_full_backup()` M2 timeout.

**Rationale:** The current deep check misses `errors` and `leaks` that `verify_full_backup()` M2 catches. This inconsistency means deep check can report "ok" for a file that M2 verification would fail. The 60s timeout is too short for multi-GB disks.

### D7: Default retention values

```
GlobalConfig:
  snapshot_chain_length: int | None = 24     (was None → 0)
  target_chain_length: int | None = 168      (was None → 0)
  target_keep_generations: int | None = 2    (was None → 1)
```

**Rationale:** With hourly pipeline runs, these defaults provide: 24 hours of snapshot rollback history, 7 days between FULL backups, 2 weeks of backup chain redundancy. The previous None→0/1 defaults caused: blockcommit on nearly every run (keep_count=1), FULL creation on nearly every run (incremental_count > 0), and no redundancy (only 1 chain kept).

**Migration:** Existing configs with explicit values are unaffected. Only configs relying on defaults change behavior — from aggressive to reasonable. No state migration needed (chain_length is a config concept, not stored in state).

### D8: ReconcileResult new fields

```python
@dataclass(frozen=True)
class ReconcileResult:
    # ... existing fields ...
    state_supplemented: int = 0    # count of state entries added from disk+XML reality
    xml_refreshed: bool = False    # whether _refresh_domain_backing_store() was called
    allocation_fixed: bool = False # whether last_allocation was corrected
```

**Rationale:** The existing `ReconcileResult` only tracks deletions (phantom_snapshots_removed, orphan_files_removed, etc.). The new fields track the new "supplement state" and "refresh XML" actions, giving operators visibility into what reconcile changed.

## Ris / Trade-offs

- **[Performance: triple-source check adds ~150-450ms per VM]** → Mitigation: `qemu-img info --backing-chain` is called once on the active layer (not per-snapshot). `virsh dumpxml` and `virsh domblklist` are lightweight libvirt API calls. Total overhead <5% of snapshot creation time.

- **[Behavior change: reconcile no longer deletes orphan files when XML references them]** → Mitigation: This is intentional — state should follow reality. Operators who relied on the old behavior (file deletion) should use `--dry-run` first to review changes. The behavior change is documented in the proposal.

- **[Behavior change: reconcile no longer auto-rebases broken chains]** → Mitigation: CRITICAL log clearly states "blockcommit impossible, restore from backup target." Operators who depended on auto-rebase should run `qemu-img rebase -u` manually after reviewing the situation. The blockcommit pipeline still has `_auto_rebase_stuck()` for in-pipeline recovery.

- **[Default values change may surprise existing users]** → Mitigation: Users with explicit config values are unaffected. Users relying on defaults will see less aggressive behavior (fewer FULLs, more snapshots retained). This is an improvement, not a regression. Document in release notes.

- **[Post-creation validation may reject snapshots that virsh reported as successful]** → Mitigation: This is intentional — if virsh returns 0 but the file doesn't exist or the chain is broken, recording it in state would cause cascading failures. The validation catches the problem early. Failed snapshots are logged with actionable error messages.

- **[Deep check timeout increase from 60s to 7200s may cause long-running checks]** → Mitigation: Deep check is already a scheduled operation (weekly via systemd timer), not run on every pipeline execution. The 7200s timeout matches `verify_full_backup()` M2 and is appropriate for multi-GB disks.
