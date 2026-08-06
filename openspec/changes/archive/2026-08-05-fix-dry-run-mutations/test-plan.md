# Test Plan: fix-dry-run-mutations

## Coverage Map

| Group | Tests | Verifies |
|---|---|---|
| CLI argparse | `test_restore_global_dry_run_not_clobbered`, `test_restore_dry_run_short_alias`, `test_action_subcommand_dry_run_both_positions` (×4 subcommands) | F5 — SUPPRESS pattern, both flag positions |
| L1 preflight | new regression in `tests/core/test_validation.py` | no `rm` in dry-run with stale `*.tmp`; files survive |
| L2 deferred | new regression in `tests/core/test_deferred.py` | no `update_deferred_warning` in dry-run |
| L3 stale entry | new regression in `tests/core/test_dry_run_prediction.py` | no `remove_snapshot`; `[dry-run] Would remove stale state entry` logged |
| L4 deep check | new regression in `tests/core/test_validation.py` | `_last_deep_check.json` not created by `check --deep` in dry-run |
| existing | `test_dry_run_does_not_drain_deferred_queue`, phantom/stale-baseline prediction tests, `test_dry_run_simulated_snapshots_not_in_state` | zero-mutation invariant overall |

## Execution

```bash
.venv/bin/pytest tests/core/test_validation.py tests/core/test_deferred.py tests/core/test_dry_run_prediction.py tests/cli/ -q
.venv/bin/pytest -m "not integration and not stress and not e2e" -q
.venv/bin/ruff check qsnap tests && .venv/bin/ruff format --check qsnap tests
```

## Not covered (by design)

- Real filesystem cleanup behavior on stale files in production (integration tier).
- `fork`/`reconcile` SUPPRESS behavior — already covered by their existing tests (pattern predates this change).
