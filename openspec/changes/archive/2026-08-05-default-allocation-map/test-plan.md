# Test Plan: default-allocation-map

## Coverage Map

| Group | Tests | Verifies |
|---|---|---|
| `config-parse-default` | `test_change_detection_mode_default_parses_as_allocation_map` | absent TOML key → `"allocation-map"` (F1, spec `config-parsing`) |
| `config-parse-explicit` | `test_change_detection_mode_explicit_allocation_size_preserved` | explicit value passes through unchanged |
| regression | existing `tests/config/` (169 tests), full non-integration suite | no fallout; explicit-mode tests elsewhere set the mode explicitly |

## Execution

```bash
.venv/bin/pytest tests/config/ -q
.venv/bin/pytest -m "not integration and not stress and not e2e" -q
.venv/bin/ruff check qsnap tests && .venv/bin/ruff format --check qsnap tests
```

## Not covered (by design)

- First-run baseline mismatch after upgrade (D2): accepted operational behavior, already fail-safe via existing `changed=True` semantics; covered conceptually by `tests/core/test_pipeline.py` mode-switch tests.
