## 1. Spec deltas (spec-only, no code)

- [x] 1.1 `result-types`: `SnapshotResult` + `disk: str | None = None`
- [x] 1.2 `cascade-deletion`: `bucket_level` → `disk` in FULL tracking requirement (RENAMED-superseded → REMOVED → ADDED pattern for scenario rename)
- [x] 1.3 `transaction-log`: ADD action→type mapping requirement (`_TYPE_MAP`, `error` type, `unknown` fallback)
- [x] 1.4 `list-commands`: ADD `stats` requirement; MODIFY `Core.list_backups()` (config-driven scope, empty shape `{vm_name: []}`)
- [x] 1.5 `fork-mode`: MODIFY `Core.fork method` (`[vm]` filter scenarios, dry-run failure contract)
- [x] 1.6 `restore-command`: MODIFY `_resolve_snapshot` requirement (two-layer failure contract resolving the ":132 vs fork-mode:66" contradiction)

## 2. Validation

- [x] 2.1 `openspec validate sync-spec-drift --strict`
- [x] 2.2 Archive the change (syncs delta specs into main specs)
- [x] 2.3 `openspec validate --specs` — all main specs valid after sync
