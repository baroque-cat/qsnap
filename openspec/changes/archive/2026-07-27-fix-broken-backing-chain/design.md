## Context

qsnap's incremental backup system uses qcow2 backing chains: each incremental delta file references a `backing-filename` in its qcow2 header, forming a chain that ultimately resolves to a FULL anchor. The system tracks `incremental→FULL` dependencies in `_dependencies.json` for ghost retention of FULLs, but does NOT track `incremental→incremental` (immediate parent) dependencies.

This creates a gap: when `_cleanup_backups()` deletes a non-FULL incremental via its else-branch (lines 3661-3676), it performs zero dependency checks. If another incremental in the keep-set has the deleted file as its backing, the chain breaks silently. The next `_copy_dirty_blocks()` call selects the broken-chain file as `previous` (because `qemu-img info` without `--backing-chain` succeeds on broken chains), and `qemu-img create -b <broken>` fails with "Could not open backing file".

A compounding key-mismatch bug in `IStateManager` makes `reconcile` classify ALL incrementals as orphans: `record_full_backup` stores names with `.qcow2` extension, while `record_incremental_dependency` stores the FULL anchor as a stem (without `.qcow2`). Lookups using `full.name` (with extension) against stem-keyed storage return empty lists.

Finally, `check --state` only verifies file existence via `os.path.exists()`, never backing-chain integrity. A VM with a completely broken chain reports "ok".

## Goals / Non-Goals

**Goals:**
- Prevent broken backing chains from being created (B2: ghost retention for incrementals)
- Prevent broken backing chains from being used (B3: validate `previous` before use)
- Fix the key-mismatch bug that causes reconcile to delete all incrementals (B1)
- Clean up stale state records when incrementals are deleted (B4)
- Detect broken backing chains in `check --state` (B5)
- Detect broken backing chains in `reconcile` before orphan classification (B6)

**Non-Goals:**
- Adding `incremental→incremental` tracking to `IStateManager` — runtime `qemu-img info` inspection is sufficient and avoids interface changes
- Adding `qemu-img rebase` to repair broken chains — rebase is unsafe (loses intermediate dirty blocks); ghost-retain + cascade-delete is the correct approach
- Filtering broken-chain files from `list()` — `list()` is used by reconcile to detect orphans; filtering would hide them
- Changing any ABC interface (`IStateManager`, `IBackupProvider`) — all fixes are within existing method signatures

## Decisions

### D1: Runtime backing-chain inspection over state tracking

**Decision:** Use `qemu-img info --backing-chain` at runtime to detect dependencies, rather than extending `IStateManager` to track `incremental→incremental` edges.

**Rationale:** The backing chain is already stored in qcow2 file headers. `_resolve_chain_full_anchor()` already walks it via `qemu-img info`. Adding a second state-tracking layer would duplicate information already on disk, require interface changes to `IStateManager` (affecting `JsonStateManager`, `InMemoryStateManager`, all mocks, all contract tests), and introduce state-vs-disk consistency issues. Runtime inspection is always accurate — it reads the actual file headers.

**Alternatives considered:**
- Extend `IStateManager` with `record_incremental_parent(target, incremental, parent)` and `get_incremental_parent(target, incremental)` — rejected: too invasive, duplicates on-disk data, requires migration
- Extend `SnapshotInfo` with a `backing_filename` field populated by `list()` — rejected: changes the data model, affects all consumers, and `list()` is called in many contexts where the extra field is irrelevant

### D2: Reverse dependency map built once per cleanup cycle

**Decision:** Build a `dict[str, list[str]]` mapping `{backing_path → [dependent_name, ...]}` once at the start of `_cleanup_backups()`, by scanning all backups via `qemu-img info`.

**Rationale:** The else-branch deletion loop iterates over `to_delete`. Without a pre-built map, each deletion would require an O(n) scan — O(n²) total. With a pre-built map, the total cost is O(n) subprocess calls (one `qemu-img info` per backup) plus O(1) lookups per deletion. For typical backup counts (10-50 files), this is negligible.

**Alternatives considered:**
- Scan only keep-set backups (not all backups) — rejected: dependents might be in the remove-set too (cascade-delete case), and we need to know about them
- Cache the map in `IStateManager` — rejected: violates stateless-module principle, introduces cross-run state

### D3: Key normalization at lookup, not at storage

**Decision:** Normalize `full_name` keys in `get_incremental_dependencies`, `remove_incremental_dependency`, and `remove_all_incremental_dependencies` to accept both stem and `.qcow2`-extended forms. Add migration in `_load_dependencies` to normalize legacy `.qcow2` keys to stem on load.

**Rationale:** The storage format (stem, from `_resolve_chain_full_anchor`) is correct and should not change. The bug is in the lookup path (callers pass `full.name` with `.qcow2`). Normalizing at lookup is backward-compatible: existing `_dependencies.json` files with stem keys work unchanged. Legacy files with `.qcow2` keys (if any exist from bugs) are migrated on load.

**Alternatives considered:**
- Change `record_full_backup` to store stem instead of `.qcow2` — rejected: would break `full.path` construction and other code that expects the full filename
- Change `_resolve_chain_full_anchor` to return `.qcow2`-extended names — rejected: would require changing all existing `_dependencies.json` files

### D4: Walk backwards through backups for valid `previous`

**Decision:** In `_copy_dirty_blocks()`, replace `previous = backups[-1]` with a backwards walk through `backups` (sorted ascending by timestamp) that validates backing-chain integrity via `qemu-img info --backing-chain` before selecting a file as `previous`.

**Rationale:** If the newest backup has a broken chain, chaining to it produces a broken delta. Walking backwards skips broken-chain files and chains to the last available valid backup. FULLs are standalone (no backing) and always valid. If no valid non-FULL backup exists, the walk falls back to the FULL.

**Alternatives considered:**
- Delete broken-chain files automatically — rejected: deletion is `_cleanup_backups()`'s responsibility, not the transfer path's
- Error immediately when newest is broken — rejected: the system should self-heal by chaining to a valid backup, not fail

### D5: Ghost retention pattern extended to incrementals

**Decision:** The else-branch of `_cleanup_backups()` shall check `backing_refs` (the reverse dependency map) before deleting an incremental. If any dependent is in the keep-set, the incremental is ghost-retained (same pattern as FULL ghost retention at lines 3567-3577). If no dependents are in the keep-set, the incremental is deleted and orphaned dependents are cascade-deleted.

**Rationale:** This is the minimal change that prevents broken chains. The ghost retention pattern already exists for FULLs — extending it to incrementals is a natural application of the same principle. The cascade-delete pattern also already exists (lines 3632-3660) — extending it to the else-branch is symmetric.

## Risks / Trade-offs

- **[O(n) subprocess calls in `_build_backing_refs`]** → Acceptable: n is typically 10-50; `qemu-img info` is fast (~50ms per file); total overhead <2.5s. If n grows to 100+, consider caching `backing-filename` in `list()` output.

- **[Race condition: retention deletes a file between `list()` and `_build_backing_refs`]** → Mitigated: `_build_backing_refs` handles `qemu-img info` failure gracefully (skips the file). The `test -f` check in `_copy_dirty_blocks` already guards the transfer path.

- **[Legacy `_dependencies.json` with `.qcow2` keys]** → Mitigated: migration in `_load_dependencies` normalizes keys on load. If migration fails (corrupt JSON), the existing corrupt-file recovery path handles it.

- **[`qemu-img info --backing-chain` performance on deep chains]** → Acceptable: qcow2 backing chains are typically <10 hops. The command traverses the entire chain in one call, so even 50-hop chains complete in <1s.

- **[Ghost retention can accumulate stale incrementals]** → Mitigated: ghost-retained incrementals are logged with INFO level. The next retention cycle will re-evaluate them. If the dependent falls out of the keep-set, both are deleted. `reconcile` can also clean them up.

- **[No automatic repair of existing broken chains]** → Accepted: this change prevents NEW broken chains. Existing broken chains require manual intervention (delete the broken file) or `qsnap reconcile` (after B6 is implemented, which classifies them as orphans and deletes them). Automatic `qemu-img rebase` is explicitly out of scope (unsafe for incremental chains).
