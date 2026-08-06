# Design: default-allocation-map

## D1 — One-line parser fix

`ConfigFacade._build_vm` reads `change_detection_mode` with a hardcoded fallback. The fallback value changes from `"allocation-size"` to `"allocation-map"` so the parser matches:

- `change-detection` spec ("The default `change_detection_mode` in `VMConfig` SHALL be `"allocation-map"`"),
- `config-model` spec (`change_detection_mode="allocation-map"` in the `VMConfig` field list),
- `VMConfig` dataclass default (`qsnap/models/config.py`),
- documented example config (`qsnap.toml.example`).

No validation of the mode value is added at parse time: `DefaultFactory.create_change_detector` already falls back to the allocation-size detector for unrecognized values (existing tested behavior), and adding parse-time validation is out of scope for this fix.

## D2 — Migration semantics (accepted, fail-safe)

After upgrade, a VM without an explicit mode switches from `AllocationSizeDetector` to `MapChangeDetector`. The stored per-disk baseline is an integer byte size from the size detector; the map detector compares a region-hash integer. The mismatch makes the first post-upgrade comparison unequal, so `changed=True` and one extra snapshot is created per disk. The run then rewrites the baseline in map format. This is the existing fail-safe direction (never miss changes) and needs no state migration.

## D3 — Test placement

Parse-level tests live in `tests/config/test_facade.py` (TOML → `VMConfig`), complementing the dataclass-level default tests already in `tests/config/test_model.py`. Tests write a minimal TOML to `tmp_path` following the existing multi-disk test pattern.
