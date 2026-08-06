# Test Plan: sync-spec-drift

Spec-only change — no code changes, no new tests. Every documented behavior is already implemented and covered:

| Spec item | Existing coverage |
|---|---|
| `SnapshotResult.disk` | `tests/core/test_pipeline.py` (per-disk snapshot results), dry-run simulation tests |
| `record_full_backup(..., disk)` | `tests/state/`, `tests/core/test_bitmap_dependency.py` |
| transaction-log mapping | `tests/utils/test_transaction.py` |
| `stats` columns/scope | `tests/cli/test_commands.py` (stats handler tests) |
| `list_backups` empty shape / config-driven | `tests/core/test_pipeline.py`, `tests/cli/test_commands.py` |
| fork `[vm]` filter + dry-run failure | `tests/core/test_fork.py` |
| restore/fork resolution contract | `tests/core/test_restore.py`, `tests/core/test_fork.py` |

## Execution

```bash
openspec validate sync-spec-drift --strict
openspec archive sync-spec-drift -y
openspec validate --specs
.venv/bin/pytest -m "not integration and not stress and not e2e" -q   # sanity: code untouched, suite still green
```
