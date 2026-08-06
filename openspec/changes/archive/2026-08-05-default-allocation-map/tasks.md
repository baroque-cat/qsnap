## 1. Git & Environment

- [x] 1.1 Work on branch `default-allocation-map` (or current branch if the workflow allows)
- [x] 1.2 Verify baseline: `.venv/bin/pytest -m "not integration and not stress and not e2e"` — 1474 passed, 1 xpassed

## 2. F1 — Parser fallback fix

Specs: `specs/config-parsing/spec.md`, `specs/change-detection/spec.md`. Design: D1.

- [x] 2.1 `qsnap/config/facade.py::_build_vm`: change the `change_detection_mode` fallback from `"allocation-size"` to `"allocation-map"` and sync the inline comment

## 3. F2 — Parse-level tests

Design: D3.

- [x] 3.1 `tests/config/test_facade.py`: TOML without `change_detection_mode` parses to `VMConfig.change_detection_mode == "allocation-map"`
- [x] 3.2 `tests/config/test_facade.py`: TOML with explicit `change_detection_mode = "allocation-size"` keeps the value
- [x] 3.3 Run `tests/config/` and the full non-integration suite; run ruff

## 4. Validation

- [x] 4.1 `openspec validate default-allocation-map --strict`
- [x] 4.2 Archive the change (syncs delta specs into main specs)
