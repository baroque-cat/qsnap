## ADDED Requirements

### Requirement: Per-chain backup retention evaluation

Core SHALL group backups by chain before evaluating retention. Each chain consists of a FULL backup and all incrementals that transitively reference it via the qcow2 backing chain. Core SHALL create one `RetentionItem` per chain, using the FULL backup's name and timestamp as the chain's representative. The retention engine (`IRetentionEngine.evaluate()`) SHALL receive these chain-level items and return keep/remove decisions at the chain level. Core SHALL expand chain-level results to individual items: all members of a kept chain SHALL be in the keep list; all members of a removed chain SHALL be in the remove list.

#### Scenario: Single chain entirely kept

- **WHEN** one chain exists (FULL + 3 incrementals) and retention policy keeps 1 hourly chain
- **THEN** all 4 backups (FULL + inc1 + inc2 + inc3) are in the keep list
- **AND** the remove list is empty

#### Scenario: Old chain entirely removed

- **WHEN** two chains exist (Chain A: FULL_Jan + 2 incrementals, Chain B: FULL_Feb + 1 incremental) and retention policy keeps 1 monthly chain
- **THEN** all 3 members of Chain A (FULL_Jan + inc1 + inc2) are in the remove list
- **AND** all 2 members of Chain B (FULL_Feb + inc1) are in the keep list

#### Scenario: No middle deletion possible

- **WHEN** one chain exists (FULL + inc1 + inc2 + inc3 + inc4) and retention policy would mark inc2 and inc3 for removal in per-item mode
- **THEN** per-chain retention keeps the entire chain (1 chain = 1 RetentionItem)
- **AND** nothing is removed

### Requirement: Chain grouping via backing chain walk

Core SHALL group backups by walking the qcow2 backing chain. For each FULL backup (filename contains `.FULL.`), `chain_id` SHALL be the FULL's name. For each incremental, `chain_id` SHALL be resolved via `_resolve_chain_full_anchor()` which walks `backing-filename` pointers until reaching a `.FULL.` file. Incrementals whose backing chain is broken (anchor resolution returns `None`) SHALL be classified as orphans and placed in the remove list for auto-recovery cleanup.

#### Scenario: Incrementals grouped to correct FULL

- **WHEN** two FULLs exist (FULL_A, FULL_B) and inc1 chains to FULL_A, inc2 chains to FULL_B
- **THEN** the grouping produces `{FULL_A: [FULL_A, inc1], FULL_B: [FULL_B, inc2]}`

#### Scenario: Broken-chain incremental classified as orphan

- **WHEN** an incremental's backing file has been deleted and `_resolve_chain_full_anchor()` returns `None`
- **THEN** the incremental is classified as an orphan
- **AND** placed in the remove list for auto-recovery

### Requirement: Cleanup deletes entire chains atomically

`Core._cleanup_backups()` SHALL delete all members of a removed chain. For FULLs, M1 verification SHALL run before deletion (non-configurable). M2 verification SHALL run if configured. For incrementals, the FULL anchor SHALL be resolved via `_resolve_chain_full_anchor()` before deletion for state cleanup. Ghost-retention, cascade-deletion, and the reverse backing-chain dependency map (`_build_backing_refs()`) SHALL NOT be used.

#### Scenario: Entire chain deleted atomically

- **WHEN** Chain A (FULL + 3 incrementals) is in the remove list
- **THEN** all 4 files are deleted from the target
- **AND** `remove_full_backup()` is called for the FULL
- **AND** `remove_all_incremental_dependencies()` is called for the FULL
- **AND** `remove_incremental_dependency()` is called for each incremental

#### Scenario: No ghost-retention or cascade-deletion

- **WHEN** per-chain retention is active
- **THEN** `_build_backing_refs()` is NOT called
- **AND** no ghost-retention check is performed
- **AND** no cascade-deletion of orphaned dependents is performed

### Requirement: Post-cleanup chain integrity verification

After `_cleanup_backups()` completes, Core SHALL verify that all keep-set items with backing chains have intact chains. For each non-FULL backup in the keep-set, Core SHALL run `qemu-img info --force-share --backing-chain --output=json`. If the command fails, a CRITICAL log SHALL be emitted with the backup name and guidance to run `qsnap check --deep`.

#### Scenario: All keep-set chains intact after cleanup

- **WHEN** cleanup deletes Chain A and keeps Chain B (FULL + inc1)
- **THEN** post-cleanup verification runs `qemu-img info --backing-chain` on inc1
- **AND** the command succeeds (chain intact)
- **AND** no CRITICAL log is emitted

#### Scenario: Post-cleanup detects broken chain

- **WHEN** cleanup completes and a keep-set incremental has a broken backing chain
- **THEN** a CRITICAL log is emitted: "post-cleanup verification FAILED for {name}"
- **AND** the log includes guidance to run `qsnap check --deep`
