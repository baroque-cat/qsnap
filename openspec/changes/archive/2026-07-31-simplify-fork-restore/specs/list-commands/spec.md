## ADDED Requirements

### Requirement: Core.list_backups supports tree grouping
`Core.list_backups(vm_filter=None, tree=False)` SHALL accept a `tree` parameter. When `tree=True`, the method SHALL group backups by FULL anchor using `_group_backups_by_chain()` and `_resolve_chain_full_anchor()`, returning a structure that represents the chain hierarchy (FULL anchors with their dependent incrementals). When `tree=False`, the method SHALL return a flat list sorted by timestamp (existing behavior).

#### Scenario: Flat list when tree=False
- **WHEN** `core.list_backups(tree=False)` is called
- **THEN** a flat list of backups sorted by timestamp is returned (existing behavior)

#### Scenario: Tree grouping when tree=True
- **WHEN** `core.list_backups(tree=True)` is called and a VM has 2 FULL chains with 3 incrementals each
- **THEN** backups are grouped by FULL anchor
- **AND** each group contains the FULL and its dependent incrementals

#### Scenario: Orphan backups grouped separately
- **WHEN** `core.list_backups(tree=True)` is called and orphan backups exist (no FULL anchor)
- **THEN** orphans are grouped under a `"__orphan__"` key

### Requirement: CLI _print_backup_tree function
The CLI SHALL provide a `_print_backup_tree(data, vm_configs)` function in `qsnap/cli/commands.py` that displays backup chains as an indented tree. Each target is shown with a header, FULL backups at the top level, and their dependent incrementals indented beneath. The function SHALL be purely visual and SHALL NOT modify any state.

#### Scenario: Backup tree output format
- **WHEN** `_print_backup_tree(data, vm_configs)` is called with 2 FULL chains
- **THEN** output shows:
  ```
  === myvm ===
  Target: /backup/myvm
    myvm.FULL.20260701T120000_abc123.qcow2
      myvm.20260702T120000_def456.qcow2
      myvm.20260703T120000_ghi789.qcow2
    myvm.FULL.20260704T120000_jkl012.qcow2
      myvm.20260705T120000_mno345.qcow2
  ```
