# qsnap — Test Plan: `vm-level-backup-engine-options`

Scope: `openspec/changes/vm-level-backup-engine-options/` (config-parsing delta: 5 MODIFIED + 2 ADDED requirements, 37 scenarios; config-model delta: 5 MODIFIED + 1 ADDED requirement, 22 scenarios; **59 scenarios total**). All planned tests comply with `TESTING.md` (unit/mock/contract/integration markers registered in `pyproject.toml` with `--strict-markers`; `MockShell.expect().returns()`; `MockConfigFacade`/`InMemoryStateManager`; shared fixtures `mock_shell`, `mock_state`, `mock_config`, `mock_factory`, `make_vm_config`, `make_target`, `make_global_config`; no pytest-mock — only `unittest.mock.patch` as already used throughout the suite).

---

## Section 1: Coverage Map

Legend — **Group**: G1 `config-parsing-unit`, G2 `config-unknown-keys-unit`, G3 `config-model-unit`, G4 `config-fixtures-unit`, G5 `facade-resolution-unit`, G6 `integration-vm-options`. **Action**: tests marked `NEW` do not exist yet; `EXISTING` tests were verified by reading the current suite and already cover the scenario completely (no change).

### config-parsing delta (5 MODIFIED + 2 ADDED requirements)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| config-parsing | Option inheritance (global → VM → target) | VM overrides global chain length | `tests/config/test_resolver.py` | `test_vm_overrides_global_chain_length` | G1 |
| config-parsing | Option inheritance (global → VM → target) | Target inherits VM chain length when not overridden | `tests/config/test_resolver.py` | `test_target_inherits_chain_length_from_vm` | G1 |
| config-parsing | Option inheritance (global → VM → target) | Target overrides VM chain length | `tests/config/test_resolver.py` | `test_target_overrides_vm_chain_length` | G1 |
| config-parsing | Option inheritance (global → VM → target) | VM overrides global engine option | `tests/config/test_resolver.py` | `test_vm_overrides_global_engine_option` | G1 |
| config-parsing | Option inheritance (global → VM → target) | Target inherits VM engine option when not overridden | `tests/config/test_resolver.py` | `test_target_inherits_vm_engine_option` | G1 |
| config-parsing | Option inheritance (global → VM → target) | Target overrides VM engine option | `tests/config/test_resolver.py` | `test_target_overrides_vm_engine_option` | G1 |
| config-parsing | Option inheritance (global → VM → target) | Target inherits VM verify when not overridden | `tests/config/test_resolver.py` | `test_target_inherits_vm_verify` | G1 |
| config-parsing | Parse compression_type from TOML | Global compression_type parsed from TOML | `tests/config/test_fixtures.py` | `test_zstd_config_toml_parses_without_error` | G4 |
| config-parsing | Parse compression_type from TOML | VM-level compression_type parsed from TOML | `tests/config/test_parser.py` | `test_vm_level_compression_type_parsed` | G1 |
| config-parsing | Parse compression_type from TOML | Target compression_type overrides VM | `tests/config/test_fixtures.py` | `test_vm_engine_options_toml_parses` | G4 |
| config-parsing | Parse compression_type from TOML | Target compression_type overrides global | `tests/config/test_fixtures.py` | `test_zstd_config_toml_parses_without_error` | G4 |
| config-parsing | Parse compression_type from TOML | Invalid compression_type raises ConfigError | `tests/config/test_parser.py` | `test_invalid_compression_type_raises_config_error` | G1 |
| config-parsing | Parse backup_stall_timeout from TOML | Global backup_stall_timeout parsed from TOML | `tests/config/test_fixtures.py` | `test_zstd_config_toml_parses_without_error` | G4 |
| config-parsing | Parse backup_stall_timeout from TOML | VM-level backup_stall_timeout parsed from TOML | `tests/config/test_parser.py` | `test_vm_level_backup_stall_timeout_parsed` | G1 |
| config-parsing | Parse backup_stall_timeout from TOML | Target backup_stall_timeout overrides global | `tests/config/test_fixtures.py` | `test_zstd_config_toml_parses_without_error` | G4 |
| config-parsing | Parse backup_stall_timeout from TOML | Target backup_stall_timeout inherits VM value | `tests/config/test_fixtures.py` | `test_vm_engine_options_toml_parses` | G4 |
| config-parsing | Parse backup_stall_timeout from TOML | Invalid backup_stall_timeout raises ConfigError | `tests/config/test_parser.py` | `test_invalid_backup_stall_timeout_raises_config_error` | G1 |
| config-parsing | convert_parallel validation | Valid convert_parallel value | `tests/config/test_parser.py` | `test_valid_convert_parallel_accepted` | G1 |
| config-parsing | convert_parallel validation | VM-level convert_parallel accepted | `tests/config/test_parser.py` | `test_vm_level_convert_parallel_accepted` | G1 |
| config-parsing | convert_parallel validation | convert_parallel below range raises ConfigError | `tests/config/test_parser.py` | `test_convert_parallel_below_range_raises_config_error` | G1 |
| config-parsing | convert_parallel validation | VM-level convert_parallel above range raises ConfigError (naming VM) | `tests/config/test_parser.py` | `test_vm_level_convert_parallel_above_range_raises` | G1 |
| config-parsing | TargetConfig verify parsing | Explicit verify value | `tests/config/test_fixtures.py` | `test_full_backup_toml_parses_compress` | G4 |
| config-parsing | TargetConfig verify parsing | Deprecated hash treated as compare | `tests/integration/test_config_integration.py` | `test_int_bitmap_hash_preserved` | G6 |
| config-parsing | TargetConfig verify parsing | Verify absent defaults to metadata | `tests/config/test_facade.py` | `test_target_verify_absent_defaults_to_metadata` | G5 |
| config-parsing | TargetConfig verify parsing | Target verify inherits VM value | `tests/config/test_resolver.py` | `test_target_inherits_vm_verify` | G1 |
| config-parsing | TargetConfig verify parsing | Target verify overrides VM value | `tests/config/test_resolver.py` | `test_target_overrides_vm_verify` | G1 |
| config-parsing | VM-level backup engine option parsing | All six engine options parsed at VM level | `tests/config/test_parser.py` | `test_vm_level_all_six_engine_options_parsed` | G1 |
| config-parsing | VM-level backup engine option parsing | Absent VM-level options inherit global values | `tests/config/test_resolver.py` | `test_vm_inherits_all_engine_options_from_global` | G1 |
| config-parsing | VM-level backup engine option parsing | VM-level options feed target resolution | `tests/config/test_resolver.py` | `test_vm_engine_options_feed_target_resolution` | G1 |
| config-parsing | VM-level backup engine option parsing | Invalid VM-level compression_type raises ConfigError naming the VM | `tests/config/test_parser.py` | `test_vm_level_invalid_compression_type_names_vm` | G1 |
| config-parsing | Unknown config key rejection | Unknown key at VM level raises ConfigError | `tests/config/test_unknown_keys.py` | `test_unknown_vm_level_key_raises` | G2 |
| config-parsing | Unknown config key rejection | Unknown key at target level raises ConfigError | `tests/config/test_unknown_keys.py` | `test_unknown_target_level_key_raises` | G2 |
| config-parsing | Unknown config key rejection | Unknown key at global level raises ConfigError | `tests/config/test_unknown_keys.py` | `test_unknown_global_level_key_raises` | G2 |
| config-parsing | Unknown config key rejection | Unknown key at disk level raises ConfigError | `tests/config/test_unknown_keys.py` | `test_unknown_disk_level_key_raises` | G2 |
| config-parsing | Unknown config key rejection | Hint when key belongs to another level | `tests/config/test_unknown_keys.py` | `test_unknown_key_hint_points_to_correct_level` | G2 |
| config-parsing | Unknown config key rejection | Deprecated keys remain tolerated | `tests/config/test_unknown_keys.py` | `test_deprecated_keys_remain_tolerated` | G2 |
| config-parsing | Unknown config key rejection | All fixture configs still parse | `tests/config/test_unknown_keys.py` | `test_all_fixture_configs_parse_without_unknown_key_errors` | G2 |

### config-model delta (5 MODIFIED + 1 ADDED requirement)

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| config-model | VMConfig dataclass | VMConfig with required fields (incl. engine-option defaults) | `tests/config/test_model.py` | `test_vm_config_required_fields` | G3 |
| config-model | VMConfig dataclass | VMConfig get_disk finds matching disk | `tests/config/test_model.py` | `test_get_disk_returns_correct_disk` | G3 |
| config-model | VMConfig dataclass | VMConfig get_disk returns None for unknown target | `tests/config/test_model.py` | `test_get_disk_returns_none_for_missing_target` | G3 |
| config-model | VMConfig dataclass | VMConfig snapshot_dir_for uses per-disk override | `tests/config/test_model.py` | `test_snapshot_dir_for_uses_per_disk_override` | G3 |
| config-model | VMConfig dataclass | VMConfig snapshot_dir_for falls back to VM-level | `tests/config/test_model.py` | `test_snapshot_dir_for_falls_back_to_vm_level` | G3 |
| config-model | VMConfig dataclass | VMConfig snapshot_dir_for returns None when neither is set | `tests/config/test_model.py` | `test_snapshot_dir_for_returns_none_when_neither_set` | G3 |
| config-model | VMConfig dataclass | VMConfig with targets | `tests/config/test_model.py` | `test_vm_config_with_targets` | G3 |
| config-model | VMConfig dataclass | VMConfig disks defensive copy | `tests/config/test_model.py` | `test_vm_config_disks_defensive_copy` | G3 |
| config-model | compression_type field in TargetConfig | TargetConfig compression_type inherits from global | `tests/config/test_model.py` | `test_target_config_compression_type_inherits` | G3 |
| config-model | compression_type field in TargetConfig | TargetConfig compression_type inherits from VM | `tests/config/test_resolver.py` | `test_target_inherits_vm_engine_option` | G1 |
| config-model | compression_type field in TargetConfig | TargetConfig compression_type overrides global | `tests/config/test_model.py` | `test_target_config_compression_type_overrides` | G3 |
| config-model | backup_stall_timeout field in TargetConfig | TargetConfig stall timeout inherits from global | `tests/config/test_model.py` | `test_target_config_backup_stall_timeout_inherits` | G3 |
| config-model | backup_stall_timeout field in TargetConfig | TargetConfig stall timeout inherits from VM | `tests/config/test_fixtures.py` | `test_vm_engine_options_toml_parses` | G4 |
| config-model | backup_stall_timeout field in TargetConfig | TargetConfig stall timeout overrides global | `tests/config/test_model.py` | `test_target_config_backup_stall_timeout_overrides` | G3 |
| config-model | convert_parallel field in TargetConfig | TargetConfig convert_parallel inherits from global | `tests/config/test_model.py` | `test_target_config_convert_parallel_default` | G3 |
| config-model | convert_parallel field in TargetConfig | TargetConfig convert_parallel inherits from VM | `tests/config/test_resolver.py` | `test_target_inherits_vm_engine_option` | G1 |
| config-model | convert_parallel field in TargetConfig | TargetConfig convert_parallel overrides global | `tests/config/test_model.py` | `test_target_config_convert_parallel_overrides` | G3 |
| config-model | convert_out_of_order field in TargetConfig | TargetConfig convert_out_of_order inherits from global | `tests/config/test_model.py` | `test_target_config_convert_out_of_order_default` | G3 |
| config-model | convert_out_of_order field in TargetConfig | TargetConfig convert_out_of_order inherits from VM | `tests/config/test_resolver.py` | `test_target_inherits_vm_convert_out_of_order` | G1 |
| config-model | VMConfig backup engine option fields | VMConfig engine option defaults | `tests/config/test_model.py` | `test_vm_config_engine_option_defaults` | G3 |
| config-model | VMConfig backup engine option fields | VMConfig engine options are immutable | `tests/config/test_model.py` | `test_vm_config_engine_options_immutable` | G3 |
| config-model | VMConfig backup engine option fields | VMConfig carries explicit VM-level overrides | `tests/config/test_model.py` | `test_vm_config_engine_options_explicit` | G3 |

Coverage totals: config-parsing 37/37, config-model 22/22. No scenario is left unmapped.

---

## Section 2: Delegation Groups

Non-overlapping; every test file belongs to exactly one group.

### G1 — `config-parsing-unit`

**Scope:** `tests/config/test_parser.py` (MODIFY), `tests/config/test_resolver.py` (MODIFY)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_parser.py` | VM-level compression_type parsed (M2); VM-level backup_stall_timeout parsed (M3); VM-level convert_parallel accepted + above-range-names-VM (M4); all six engine options parsed at VM level (A1); invalid VM compression_type names VM (A1); invalid compression_type raises (M2); invalid backup_stall_timeout raises (M3) | MODIFY |
| `tests/config/test_resolver.py` | VM overrides global engine option; target inherits/overrides VM engine option; target inherits VM verify; target verify inherits/overrides VM (M1/M5); absent VM options inherit global; VM options feed target resolution (A1); convert_out_of_order inherits from VM (model M5) | MODIFY |

New test names (G1): `test_vm_overrides_global_engine_option`, `test_target_inherits_vm_engine_option`, `test_target_overrides_vm_engine_option`, `test_target_inherits_vm_verify`, `test_target_overrides_vm_verify`, `test_vm_inherits_all_engine_options_from_global`, `test_vm_engine_options_feed_target_resolution`, `test_target_inherits_vm_convert_out_of_order`, `test_vm_level_compression_type_parsed`, `test_vm_level_backup_stall_timeout_parsed`, `test_vm_level_convert_parallel_accepted`, `test_vm_level_convert_parallel_above_range_raises`, `test_vm_level_all_six_engine_options_parsed`, `test_vm_level_invalid_compression_type_names_vm`, `test_invalid_compression_type_raises_config_error`, `test_invalid_backup_stall_timeout_raises_config_error`.

### G2 — `config-unknown-keys-unit`

**Scope:** `tests/config/test_unknown_keys.py` (NEW)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_unknown_keys.py` | Unknown key at VM level; unknown key at target level; unknown key at global level; unknown key at disk level; cross-level hint; deprecated keys tolerated; all fixture configs still parse (A2, all 7 scenarios) | NEW |

New test names (G2): `test_unknown_vm_level_key_raises`, `test_unknown_target_level_key_raises`, `test_unknown_global_level_key_raises`, `test_unknown_disk_level_key_raises`, `test_unknown_key_hint_points_to_correct_level`, `test_deprecated_keys_remain_tolerated`, `test_all_fixture_configs_parse_without_unknown_key_errors`, plus `test_global_verify_key_hints_vm_or_target` (risk-item guard, see Section 4).

### G3 — `config-model-unit`

**Scope:** `tests/config/test_model.py` (MODIFY)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | VMConfig required fields extended with six engine-option defaults (M1); get_disk ×2, snapshot_dir_for ×3, with-targets, disks defensive copy (M1, all EXISTING); compression_type / backup_stall_timeout / convert_parallel / convert_out_of_order dataclass defaults+overrides (M2–M5, EXISTING); VMConfig engine-option defaults / immutability / explicit overrides (A1) | MODIFY |

New test names (G3): `test_vm_config_engine_option_defaults`, `test_vm_config_engine_options_immutable`, `test_vm_config_engine_options_explicit`. Existing modified: `test_vm_config_required_fields` (adds six default assertions).

### G4 — `config-fixtures-unit`

**Scope:** `tests/config/test_fixtures.py` (MODIFY), `tests/fixtures/configs/vm_engine_options.toml` (NEW), `tests/fixtures/configs/full_backup.toml` (MODIFY)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_fixtures.py` | Global compression_type/stall parsed + target-overrides-global (M2/M3 via `zstd_config.toml`, EXISTING); explicit verify value (M5 via `full_backup.toml`, EXISTING); VM-level fixture parses: all-six at VM, target inherits VM stall, target overrides VM compression_type, VM options feed targets, stall-inherits-from-VM (M2/M3/A1 + model M3) | MODIFY |
| `tests/fixtures/configs/vm_engine_options.toml` | New fixture: global defaults (compress=false, zlib, -m 2, out-of-order=false, "1h"); `vm_all_vm_level` sets all six at VM level with a bare target; `vm_target_override` sets VM values with one inheriting and one overriding target; `vm_global_inherit` sets nothing | NEW |
| `tests/fixtures/configs/full_backup.toml` | Remove `full_verify_after_create = "compare"` from `[[vm.target]]` (global-only key; silently ignored today, would fail strict whitelist) | MODIFY |

New test names (G4): `test_vm_engine_options_toml_parses`, `test_vm_engine_options_toml_target_inheritance`, `test_make_vm_config_forwards_engine_option_kwargs`.

### G5 — `facade-resolution-unit`

**Scope:** `tests/config/test_facade.py` (MODIFY)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_facade.py` | Verify absent defaults to metadata on a full parse (M5); keeps all existing facade scenarios untouched | MODIFY |

New test names (G5): `test_target_verify_absent_defaults_to_metadata`.

### G6 — `integration-vm-options`

**Scope:** `tests/integration/test_full_backup.py` (MODIFY), `tests/integration/test_incremental_backup.py` (MODIFY), `tests/integration/test_config_integration.py` (EXISTING, no edit)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_full_backup.py` | VM-level TOML → parsed TargetConfig → real `qemu-img convert` argv (`-m 8`, `-o compression_type=zstd`, no `-W`) and resulting qcow2 `compression-type` (A1 end-to-end; M1 target-inherits-VM) | MODIFY |
| `tests/integration/test_incremental_backup.py` | VM-level `backup_stall_timeout` + `verify` parsed from TOML reach `transfer_missing` (stall_timeout=120) while incrementals stay uncompressed (M3/M5 end-to-end, D6 guard) | MODIFY |
| `tests/integration/test_config_integration.py` | Deprecated `verify="hash"` → `"compare"` with warning (M5) | EXISTING |

New test names (G6): `test_vm_level_engine_options_reach_convert_command`, `test_vm_level_stall_timeout_reaches_incremental`.

**Files explicitly out of scope (no changes):** `tests/modules/backup/test_bitmap.py`, `tests/modules/backup/test_bitmap_convert.py`, `tests/modules/backup/test_bitmap_incremental.py` (provider logic untouched — existing argv tests `test_create_full_backup_custom_convert_parallel`, `test_create_full_backup_convert_out_of_order_disabled`, `test_create_full_backup_with_compression`, `test_convert_cmd_*` remain valid regression guards); `tests/core/test_pipeline.py` (Core untouched — existing forwarding tests `test_core_passes_convert_parallel_to_create_full_backup` etc. remain valid); `tests/interfaces/test_config.py`, `tests/interfaces/test_backup_provider.py` (no ABC change); `tests/conftest.py` (`make_vm_config` already forwards `**kwargs`, so the six new `VMConfig` fields are settable without fixture changes).

---

## Section 3: Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/config/test_model.py` | Extend `test_vm_config_required_fields` to assert the six new defaults (`compress is True`, `compression_type == "zstd"`, `convert_parallel == 4`, `convert_out_of_order is True`, `backup_stall_timeout == "30m"`, `verify == "metadata"`) and the absence of `base_image`. | Design D1 / config-model M1 scenario "VMConfig with required fields" now includes engine-option defaults; keeps the dataclass-level default contract testable. |
| `tests/config/test_parser.py` | Extend `test_parse_minimal_valid_config` to assert the six defaults on the parsed `VMConfig` of `minimal.toml`. | D1 defaults at parse level; catches silent default-drift between `facade.py` and `models/config.py`. |
| `tests/fixtures/configs/full_backup.toml` | Remove `full_verify_after_create = "compare"` from the `[[vm.target]]` block of `vm_with_full` (or hoist to top level). | Design D3: `full_verify_after_create` is a global-only key currently silently discarded at target level — precisely the bug class this change eliminates. Strict whitelists would reject it and break the "All fixture configs still parse" scenario (config-parsing A2) and `test_full_backup_toml_parses_compress`. |
| `tests/fixtures/configs/vm_engine_options.toml` | New fixture (Section 2, G4). | Closes the documented gap: no fixture today places engine options at `[[vm]]` level; required for A1 "VM-level options feed target resolution" and model M2/M3/M4/M5 "inherits from VM" scenarios. |
| `tests/config/test_fixtures.py` | Add `test_vm_engine_options_toml_parses` (+ target-inheritance variant) and `test_make_vm_config_forwards_engine_option_kwargs`. | Fixture validation + conftest fixture coverage per `test_fixtures.py`'s stated purpose. |
| `tests/config/test_unknown_keys.py` | New file (Section 2, G2). | The unknown-key-rejection requirement (A2, 7 scenarios) has no current coverage — no strict validation exists today. |
| `tests/integration/test_full_backup.py` | Add `test_vm_level_engine_options_reach_convert_command` (Section 6). | End-to-end proof that VM-level TOML values reach the real `qemu-img convert` command and the resulting qcow2 metadata (A1, M1). |
| `tests/integration/test_incremental_backup.py` | Add `test_vm_level_stall_timeout_reaches_incremental` (Section 6). | End-to-end proof that VM-level `backup_stall_timeout`/`verify` reach the incremental transfer path (M3/M5), preserving the D6 uncompressed-incrementals invariant. |
| `tests/config/test_facade.py` | Add `test_target_verify_absent_defaults_to_metadata`. | M5 "Verify absent defaults to metadata" lacks a full-parse assertion today (only the dataclass default is tested in `test_model.py`). |
| `tests/config/test_facade.py` / `tests/config/test_parser.py` / `tests/config/test_fixtures.py` (deprecation tests: `test_deprecated_snapshot_preserve_warning`, `test_incremental_toml_key_logs_deprecation_warning`, `test_full_every_deprecation_warning`, `test_full_compress_mapped_to_compress_with_warning`, `test_deprecated_fields_toml_parses_with_logging_warnings`, `test_safety_fields_toml_parses_correctly`) | **No change needed** — verified these pass as-is *only if* the `_GLOBAL_KEYS`/`_TARGET_KEYS` whitelists keep all currently-tolerated deprecated keys (`snapshot_preserve`, `target_preserve`, `target_preserve_min`, `preserve_day_of_week`, global `rate_limit`; target `incremental`, `incremental_mode`, `rate_limit`, `copy_base`, `full_every`, `full_compress`). | D3 deprecated-key tolerance; these tests are the regression net that proves tolerance is preserved. |
| `tests/config/test_fixtures.py` (`test_zstd_config_toml_parses_without_error`, `test_engine_config_toml_parses_correctly`) | **No change needed** — existing global→target assertions remain valid under the new VM hop (VM sets nothing ⇒ VM inherits global ⇒ target inherits VM). | Non-breaking guarantee from the proposal; these stay as global→target regression guards. |
| `tests/conftest.py` | **No change needed.** | `make_vm_config` forwards `**kwargs` to `VMConfig`, so the six new frozen fields are constructible without fixture edits (verified in `tests/conftest.py:217-243`). |

---

## Section 4: Risks & Edge Cases

Mapping of `design.md` "Risks / Trade-offs" items (plus issues found during suite analysis) to concrete test coverage:

| Risk / Edge Case | Source | Dedicated Test Coverage | Notes |
|---|---|---|---|
| Configs with previously-silent typos now fail to start | design.md Risks #1 (D3) | `test_unknown_vm_level_key_raises`, `test_unknown_target_level_key_raises`, `test_unknown_global_level_key_raises`, `test_unknown_disk_level_key_raises` (G2) — each asserts `ConfigError` names the table, the VM name (VM/target rows), and the offending key. | `ConfigError` message format is part of the assertion (`pytest.raises(..., match=...)`). |
| Deprecated keys must stay tolerated (warn-and-ignore), not rejected | design.md Risks #1 (D3) | `test_deprecated_keys_remain_tolerated` (G2) parses `deprecated_fields.toml` (global `snapshot_preserve`/`target_preserve`, target `incremental`/`full_every`/`full_compress`/`verify="hash"`) and asserts no `ConfigError` + existing warnings still emitted. Existing deprecation tests (Section 3, "no change needed" rows) re-run as the wider net. | **Suite-analysis finding:** the target whitelist MUST also contain `incremental`, `incremental_mode`, `rate_limit`, `copy_base`, `full_every`, `full_compress` — `minimal.toml`, `multi_vm.toml`, `bucket_driven.toml`, `zstd_config.toml`, `engine_config.toml`, `deprecated_fields.toml`, `safety_fields.toml`, `deferred_thresholds.toml` all carry target-level `incremental = true`. |
| Whitelist drift when future keys are added | design.md Risks #2 (D3) | `test_all_fixture_configs_parse_without_unknown_key_errors` (G2) — parametrized scan over every `tests/fixtures/configs/*.toml` asserting `ConfigFacade` raises no unknown-key `ConfigError`; fixture set covers all four levels (global, VM, disk, target). | Also guarded by `test_example_config_parseable` (G4, EXISTING) — `qsnap.toml.example` must keep parsing after the doc corrections (D5). |
| `full_backup.toml` target-level `full_verify_after_create` (global-only key) | Suite analysis | `test_all_fixture_configs_parse_without_unknown_key_errors` + Section 3 fixture modification. | If the implementer prefers tolerance over fixture cleanup, this key must be added to `_TARGET_KEYS` with a cross-level hint; the plan's default is the fixture cleanup. Either way the scan test documents the resolution. |
| `[global]` section unwrapping order vs. validation | design.md D3 ("structural keys … unwrapped to top level before validation") | `test_all_fixture_configs_parse_without_unknown_key_errors` covers `global_section.toml` / `global_section_override.toml`; existing `test_parse_global_section` and `test_top_level_overrides_global_section` (G5, EXISTING) re-run as regression. | Validation MUST run after `raw.pop("global")` + merge (`facade.py:68-72`). |
| Global-level `verify` has no global key (asymmetric) | design.md Risks #5 / Open Questions | `test_global_verify_key_hints_vm_or_target` (G2) — top-level `verify = "compare"` raises `ConfigError` whose hint points to `[[vm]]`/`[[vm.target]]`. | Design D3 hint mechanism; guards the documented asymmetry. |
| Cross-level hint message quality | design.md Risks #1 (D3) | `test_unknown_key_hint_points_to_correct_level` (G2) — VM-level `backup_retry_max` (target-only key) ⇒ hint names `[[vm.target]]`; also asserts the hint text mentions the VM name. | Spec scenario "Hint when key belongs to another level" (A2). |
| Changing `compression_type` between runs | design.md Risks #3 (D6) | No new tests required — already covered end-to-end: `test_full_backup_compression_modes` (standalone FULLs, per-run compression-type), `test_incremental_compression_not_applied` (incrementals uncompressed), `test_verification_bitmap.py` (verification never reads compression-type), restore path tests in `test_restore.py`. | Claim verified against `tests/integration/`; do not add redundant coverage. |
| `free_space_factor` estimate shifts when switching algorithms | design.md Risks #4 | Docs-only (comment in `qsnap.toml.example`); no behavior change, no dedicated test. | Noted explicitly as intentionally untested. |
| Invalid values at VM level must name the VM | design.md D4 / spec A1 | `test_vm_level_convert_parallel_above_range_raises` (match contains VM name + "1-8"), `test_vm_level_invalid_compression_type_names_vm` (match contains `web01`-style VM name + valid values), `test_invalid_backup_stall_timeout_raises_config_error` (VM-level variant asserted in the same test via `name` context). | D4 validation parity: identical rules at global/VM/target, error messages carry VM context. |
| Deprecated `verify` VALUES at VM level (not just target) | spec M5 "at whichever level they appear" | `test_vm_level_verify_deprecated_hash_mapped` (folded into `test_vm_level_all_six_engine_options_parsed` or standalone in G1) — VM-level `verify="hash"` logs WARNING and resolves to `"compare"`. | The existing mapping (`facade.py:684-690`) is currently target-only; D2 extends it to the VM-level fallback. |

---

## Section 5: Obsolete Test Deletion List

Study basis: full read of `tests/config/test_parser.py`, `tests/config/test_facade.py`, `tests/config/test_resolver.py`, `tests/config/test_model.py`, `tests/config/test_fixtures.py`, all `tests/fixtures/configs/*.toml`, plus spot-checks across `tests/integration/` and `tests/modules/backup/`.

| File | Test/Fixture | Deletion reason |
|---|---|---|
| `tests/config/test_parser.py` | `test_parse_target_compress` | Fully subsumed by `tests/config/test_fixtures.py::test_bucket_driven_toml_parses_without_error` — both parse `bucket_driven.toml` and assert the identical two targets' `compress` values (True for `vm_bucket`, False for `vm_no_compress`); the fixture test additionally asserts the global default and lives in the canonical fixture-validation home per TESTING.md. |
| `tests/config/test_parser.py` | `test_full_every_deprecation_warning` | Fully subsumed by `tests/config/test_fixtures.py::test_deprecated_fields_toml_parses_with_logging_warnings` — same fixture (`deprecated_fields.toml`), same assertion (`"full_every is deprecated"` in caplog), and the fixture test covers strictly more (`full_compress` warning, compress mapping, both VMs). |
| `tests/config/test_facade.py` | `test_target_compress_parsed` | Fully subsumed by `tests/config/test_fixtures.py::test_full_backup_toml_parses_compress` — both parse `full_backup.toml` and assert `vm_with_full`'s target `compress is True`; the fixture test additionally asserts `verify == "compare"`. |
| `tests/config/test_facade.py` | `test_vm_chain_length_overrides_global` | Exact duplicate of `tests/config/test_resolver.py::test_vm_overrides_global_chain_length` — identical inline TOML (global 168 → VM 336 `snapshot_chain_length`) and identical assertions. `test_resolver.py` is the canonical inheritance home per TESTING.md; the resolver copy is kept. |
| `tests/config/test_facade.py` | `test_target_chain_length_overrides_vm` | Exact duplicate of `tests/config/test_resolver.py::test_target_overrides_vm_chain_length` — identical inline TOML (VM 200 → target 150) and identical assertions. The resolver copy is kept and is the primary mapping for the spec scenario. |

Explicit statements (no deletions justified):

- **Tests asserting the old global→target-only engine inheritance:** none exist. No fixture places any of the six engine options at `[[vm]]` level today (verified by reading all `tests/fixtures/configs/*.toml`), so no test encodes the silent-discard bug; there is nothing to supersede.
- **Fixture deletions:** none. `engine_config.toml` and `zstd_config.toml` remain valuable as global→target regression guards for the proposal's non-breaking guarantee; `deprecated_fields.toml` remains the canonical deprecated-key fixture; `bucket_driven.toml`, `full_backup.toml`, `safety_fields.toml` are referenced by surviving tests. `full_backup.toml` is MODIFIED (Section 3), not deleted.
- **Provider/core/interface test deletions:** none. `tests/modules/backup/*` argv tests and `tests/core/test_pipeline.py` forwarding tests verify untouched production code and stay as regression guards.

---

## Section 6: Integration Test Amendments

Context verified in the current tree: `tests/integration/test_nbd_full_backup.py` and `tests/integration/test_stale_state_recovery.py` (referenced in TESTING.md) no longer exist — the suite evolved (`test_nbd_import_hardening.py`, `test_full_backup.py`, `test_incremental_backup.py` now carry the NBD/FULL/incremental coverage). Amendments below target the files that actually exist. All new tests follow the existing conventions: `@pytest.mark.integration`, `test_vm` disposable-VM fixture (`tests/integration/conftest.py`), `_cleanup_checkpoints` teardown, libvirt-version guards (`is_libvirt_new_enough`), `time.sleep` after `virsh start`, and skip-on-environment-mismatch.

| File | Amendment | Verifies scenario |
|---|---|---|
| `tests/integration/test_full_backup.py` | **NEW** `test_vm_level_engine_options_reach_convert_command(test_vm, caplog)`: write an inline TOML (per `test_config_integration.py` style) with global `compress=false, compression_type="zlib", convert_parallel=2, convert_out_of_order=false, backup_stall_timeout="1h"` and a `[[vm]]` block setting `compress=true, compression_type="zstd", convert_parallel=8, convert_out_of_order=false, backup_stall_timeout="30m", verify="compare"` with a bare `[[vm.target]]` (path = `target_dir`). Parse with `ConfigFacade`; take `target = vm.targets[0]`. Start VM, `_cleanup_checkpoints`, then `BitmapBackupProvider(shell).create_full_backup(vm_name, snapshot, target, compress=target.compress, compression_type=target.compression_type, convert_parallel=target.convert_parallel, convert_out_of_order=target.convert_out_of_order, stall_timeout=parse_stall_timeout(target.backup_stall_timeout))`. Assert at DEBUG level: convert argv contains `-m 8` and `-o compression_type=zstd`, and **no** `-W` (VM-level `convert_out_of_order=false`); then `_assert_standalone_qcow2` + `_get_compression_type(...) == "zstd"` (pattern of `test_full_backup_custom_convert_parallel_and_out_of_order` and `test_full_backup_compression_modes`, reusing their `_qemu_img_info`/`_get_compression_type` helpers). Cleanup: `_cleanup_checkpoints`. | config-parsing A1 "All six engine options parsed at VM level", A1 "VM-level options feed target resolution", M1 "Target inherits VM engine option when not overridden" — end-to-end: VM-level TOML → parsed `TargetConfig` → real `qemu-img convert` argv → real qcow2 `compression-type` metadata. |
| `tests/integration/test_full_backup.py` | Keep `test_full_backup_custom_convert_parallel_and_out_of_order` and `test_full_backup_compression_modes` unchanged (EXISTING). | Direct-kwarg `-m 2`/no-`-W` argv and zstd/zlib metadata remain covered; the new test above adds the TOML-driven variant without duplicating them. |
| `tests/integration/test_incremental_backup.py` | **NEW** `test_vm_level_stall_timeout_reaches_incremental(test_vm, caplog)`: inline TOML with VM-level `backup_stall_timeout="2m"` and `verify="check"` (target sets neither); parse → assert `target.backup_stall_timeout == "2m"` and `target.verify == "check"`. Follow the flow of `test_incremental_compression_not_applied` (FULL zstd → write data → external snapshot → `transfer_missing`) but wrap `shell.run_with_stall_detection` with a recording delegate (pattern of `test_dry_run.py`'s shell wrapper, lines ~198-202) and assert the incremental `qemu-img convert`/NBD transfer received `stall_timeout == 120` (parsed from the VM-inherited "2m"); assert the incremental output stays uncompressed (`_get_compression_type != "zstd"`, D6). Cleanup: `_cleanup_checkpoints` + VM destroy via fixture teardown. | config-parsing M3 "Target backup_stall_timeout inherits VM value", M5 "Target verify inherits VM value" — end-to-end on the incremental path, and a D6 regression guard (VM-level engine options must not change the incremental-compression invariant). |
| `tests/integration/test_config_integration.py` | No amendment (EXISTING `test_int_bitmap_hash_preserved` already asserts deprecated `verify="hash"` → `"compare"` with WARNING on a full TOML parse). | config-parsing M5 "Deprecated hash treated as compare". |
| `tests/e2e/test_from_config.py` / `tests/e2e/conftest.py` | **No amendment.** | Documented blocker: the module docstring (lines 13-18) states the backup stage aborts because `SnapshotResult.disk` is never populated, so an engine-option assertion through the full e2e pipeline cannot pass today. Revisit once that source bug is fixed; the e2e config fixture (`tests/e2e/conftest.py:94-108`) is a candidate place to add VM-level options then. |
| `tests/integration/test_full_backup.py` (existing) | `test_full_backup_qemu_img_convert_engine_default`, `test_full_backup_compression_modes`, `test_incremental_compression_not_applied` — no change. | Default argv (`-m 4 -W`), standalone-FULL compression metadata, and D6 uncompressed-incrementals are already covered; re-run as regression net for the untouched provider. |

---

### Implementation order for parallel delegation

1. **G2** (unknown keys) is the contract-shaping group — its error-message assertions define the D3 whitelist/hint API surface.
2. **G3 + G4** (model + fixtures) define the dataclass fields and the new fixture the resolver/parser groups consume.
3. **G1 + G5** (parser/resolver/facade) implement the inheritance matrix against the G4 fixture.
4. **G6** (integration) can start last; it depends on the final field names but uses only `ConfigFacade` public API.

Run commands (per TESTING.md): `poetry run pytest tests/config/ -m "not integration and not stress and not e2e"` for G1–G5; `poetry run pytest tests/integration/test_full_backup.py tests/integration/test_incremental_backup.py -m integration` for G6 (requires libvirt).
