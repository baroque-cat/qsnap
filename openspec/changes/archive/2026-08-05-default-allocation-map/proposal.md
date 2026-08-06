## Why

The specs (`change-detection`, `config-model`) and the `VMConfig` dataclass agree that the default `change_detection_mode` is `"allocation-map"`, but `ConfigFacade._build_vm` falls back to `"allocation-size"` when the TOML key is absent (`qsnap/config/facade.py`). Every VM without an explicit `change_detection_mode` therefore runs the size detector, contradicting the spec and the documented example config (`qsnap.toml.example` shows `allocation-map`).

This is a pure code bug — the specs are already correct. The fix aligns the parser with the spec and adds the missing parse-level test coverage (only the dataclass default is tested today, in `tests/config/test_model.py`).

## What Changes

- **F1**: `ConfigFacade._build_vm` fallback for absent `change_detection_mode` changes from `"allocation-size"` to `"allocation-map"` (one line + comment sync).
- **F2**: New parse-level tests in `tests/config/test_facade.py`: TOML without the key parses to `"allocation-map"`; TOML with an explicit `"allocation-size"` keeps it.
- **F3**: Spec scenarios: `change-detection` gains a parse-level default scenario; `config-parsing` gains a requirement pinning the parse-time default.

## Migration

VMs without an explicit `change_detection_mode` switch from the size detector to the map detector after upgrade. The stored per-disk baseline (`last_allocation`, an integer byte size) will not match the map detector's region-hash value, so the first run after upgrade reports `changed=True` and creates one extra snapshot per disk. This is fail-safe (never misses changes) and self-healing (the baseline is rewritten in map format on that run). Operators should expect one additional snapshot per disk on the first post-upgrade run.

## Capabilities

### Modified Capabilities

- `change-detection`: parse-level default scenario added to the existing default requirement.
- `config-parsing`: new requirement — absent `change_detection_mode` key parses to the spec default `"allocation-map"`.

## Impact

- **Code**: `qsnap/config/facade.py` (1 line + comment).
- **ABCs**: unchanged. **Factory**: unchanged (both detector branches already exist). **State**: no schema change; baseline format mismatch handled fail-safe by existing first-run/changed semantics.
- **Tests**: `tests/config/test_facade.py` (+2 tests). Existing explicit-mode tests (`tests/integration/test_preserve_min.py`, `tests/core/test_pipeline.py`) set the mode explicitly and are unaffected.
