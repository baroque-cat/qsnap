## Context

`ConfigFacade` resolves backup engine options (`compress`, `compression_type`, `convert_parallel`, `convert_out_of_order`, `backup_stall_timeout`) at exactly two levels: global (`facade.py:134-151`) and `[[vm.target]]` (`facade.py:626-659`). `_build_vm` (`facade.py:306-517`) never reads these keys from `vm_raw`, and `VMConfig` (`models/config.py:214`, fields at `:245-262`) has no fields for them, so per-VM values are silently discarded. `_build_target` is fed with `global_cfg.*` directly (`facade.py:491-495`), skipping the VM hop. `verify` has the same defect: parsed only from `tgt_raw` (`facade.py:671-693`), no VM-level support. There is no unknown-key detection at any level, and `qsnap.toml.example` comments (`:81`, `:224`, `:235`) plus the `TargetConfig` docstring (`models/config.py:157-164`) falsely advertise a "global → VM → target" chain.

Downstream of config parsing everything is correct and stays untouched: Core forwards `target.*` faithfully (`core/__init__.py:4990-4999`, `:5138-5146`), `BitmapBackupProvider._qemu_img_convert_transfer` renders the argv correctly (`bitmap.py:787-795`), and the factory passes only infrastructure deps by design (`factory/default.py:58-76`).

Constraints: pure-stdlib Python ≥3.11, frozen config dataclasses, `ConfigError` as the single config exception type (`facade.py:24`), no ABC signature changes, no state schema changes, all code/docs in English.

## Goals / Non-Goals

**Goals:**

- Per-VM assignment (fragmented, per-VM overrides) of backup engine options with resolution order global → VM → target; more specific level wins.
- `verify` resolvable at VM level (VM → target; default stays `metadata`).
- Strict unknown-key rejection at all four table levels (global, `[[vm]]`, `[[vm.disk]]`, `[[vm.target]]`) with actionable error messages.
- Documentation truthfulness: example config, model docstrings, and provider docstrings match implemented behavior.
- Close the test gap: no test today exercises `[[vm]] TOML → TargetConfig`; add unit, fixture, and integration coverage.

**Non-Goals:**

- No changes to Core, factory, backup provider logic, or any ABC interface.
- No `IStateManager` schema change or state migration (state stores no compression metadata — verified across `json_manager.py`).
- No new global-level `verify` key (see Open Questions).
- No changes to retention, snapshot, lifecycle, or change-detection options (their VM-level inheritance already works).
- No warning-only mode for unknown keys — rejection is unconditional (see D3).

## Decisions

### D1: Store the six options as `VMConfig` fields (not transient resolution)

`VMConfig` gains six frozen fields: `compress: bool`, `compression_type: str`, `convert_parallel: int`, `convert_out_of_order: bool`, `backup_stall_timeout: str`, `verify: str` — each defaulting to the same value as the corresponding `GlobalConfig`/`TargetConfig` default (`compress=True`, `compression_type="zstd"`, `convert_parallel=4`, `convert_out_of_order=True`, `backup_stall_timeout="30m"`, `verify="metadata"`).

Rationale: mirrors the existing VM-level retention fields (`snapshot_chain_length` etc., resolved at `facade.py:345-351`); makes VM-level values inspectable and unit-testable; fulfills the promise already made by existing docstrings.

Alternative rejected: resolve VM values locally in `_build_vm` and pass them down without storing — leaves `VMConfig` incomplete, untestable in isolation, and divergent from the retention-field pattern.

### D2: Resolution wiring — VM-resolved values become `_build_target` fallbacks

`_build_vm` resolves each option as `vm_raw.get(key, global_cfg.<key>)` (with the same validation as global level), stores results in `VMConfig`, and passes them into `_build_target` in place of today's `global_cfg.*` arguments (`facade.py:491-495`). `_build_target` signature parameters are renamed `global_compress` → `vm_compress`, `global_compression_type` → `vm_compression_type`, etc.; its body is unchanged (`tgt_raw.get(key, vm_*)`). For `verify`: `_build_vm` resolves `vm_raw.get("verify", "metadata")` (validated against `off|metadata|check|compare` plus the existing deprecated-value mapping `facade.py:684-690`) and passes it as a new `vm_verify` fallback parameter; `_build_target` resolves `tgt_raw.get("verify", vm_verify)`.

Rationale: minimal diff, preserves the established scalar-parameter style of `_build_target`, keeps `TargetConfig` construction unchanged.

Alternative rejected: passing the whole `VMConfig` into `_build_target` — increases coupling and breaks the current signature style for one consumer.

### D3: Strict unknown-key validation via per-level whitelists

Module-level `frozenset` whitelists — `_GLOBAL_KEYS`, `_VM_KEYS`, `_DISK_KEYS`, `_TARGET_KEYS` — enumerate every accepted key per table level. After building each table's objects, `_parse`/`_build_vm`/`_build_disk`/`_build_target` compute `unknown = set(raw) - whitelist` and raise `ConfigError` naming the table, the offending key(s), and — when the unknown key is known at another level — a hint (e.g. `Unknown key 'compression_type' in [[vm]] 'web01' — did you mean to set it in [[vm.target]]? Note: VM-level engine options are supported as of this change`).

Whitelists include:

- every key currently parsed at that level;
- the six newly VM-aware options at `[[vm]]`;
- deprecated-but-tolerated keys (warn-and-ignore today): global `snapshot_preserve`, `target_preserve`, `target_preserve_min`, `preserve_day_of_week` (`facade.py:108-118`), `rate_limit` (`:218-222`), `full_verify_after_create` alias handling (`:275`), deprecated target `verify` values remain value-level (not key-level) concerns;
- structural keys: `vm` at top level; `disk`, `target` inside `[[vm]]`; `[global]` section keys are unwrapped to top-level before validation (`facade.py:68-72`).

Rationale: silent discard is the root cause of the reported bug; a warning-only mode would perpetuate it. Erroring on unknown keys is the only mechanism that guarantees a misplaced key can never again silently change backup behavior.

Alternative rejected: warn by default, error behind a `strict = true` flag — unsafe default, extra surface, and the reported bug proves warnings would go unnoticed.

### D4: Validation parity across levels

VM-level values pass through the identical validation already applied at global/target level: `compression_type ∈ {zstd, zlib}`, `convert_parallel` integer in 1-8, `backup_stall_timeout` same type/parsing as global, `verify ∈ {off, metadata, check, compare}` with deprecated-value mapping. Failures raise `ConfigError` whose message includes the VM name for context. No new validation rules are introduced.

### D5: Documentation corrections (behavior-neutral)

- `qsnap.toml.example`: fix inheritance comments (`:81`, `:224`, `:235`) to state the real chain; add commented VM-level examples for all six options under a `[[vm]]` block.
- `models/config.py`: rewrite the `TargetConfig` docstring (`:157-164`) to describe global → VM → target; document the new `VMConfig` fields.
- `modules/backup/bitmap.py`: fix stale docstrings at `:21` and `:774-776` claiming `-m 4`/`-W` are always included (they are parameterized since commit `157d371`).
- `README.md`: add the six keys to the VM Keys table (`:166-180` region).

### D6: Test strategy (detailed in test-plan.md)

Unit tests for VM-level parsing and the full inheritance matrix (global-only / VM override / target override / VM+target), unknown-key rejection at each level, deprecated-key tolerance; a new TOML fixture exercising VM-level engine options end-to-end into `TargetConfig`; integration tests asserting VM-level values reach the actual `qemu-img convert` argv / resulting qcow2 `compression-type`. A delegated test-analysis pass produces a deletion list of obsolete tests and the integration-test amendment list.

## Risks / Trade-offs

- [Configs with previously-silent typos now fail to start] → Intended behavior; `ConfigError` messages name table + key + hint. Documented in change notes. Deprecated keys remain tolerated (warn-and-ignore) so legitimate legacy configs keep working.
- [Whitelist drift when future keys are added] → Whitelists live in `facade.py` next to parsing code; a unit test asserts every key in each fixture TOML is accepted, and fixture coverage spans all levels.
- [Changing `compression_type` between runs] → Verified safe: FULL backups are standalone qcow2 without backing file (`bitmap.py:1586-1614`); incrementals are uncompressed by design D6 (`bitmap.py:1304-1309, 1319-1327`) and attach to the newest FULL automatically (`bitmap.py:1233-1252`); M1/M2/M3 verification never reads `compression-type` (`verification.py:44-348`); state files store no compression metadata (`json_manager.py`); restore flattens without `-c` (`utils/convert.py:52-95`); retention/cleanup are name/timestamp-based (`core/__init__.py:5525-5684`). Old generations retire via `target_keep_generations`.
- [`free_space_factor` estimate shifts when switching algorithms] → Heuristic only (affects the proactive free-space gate estimate, not correctness); noted in example-config comments.
- [VM-level `verify` without global `verify` is asymmetric] → Accepted; global `verify` listed as Open Question. Error hint for a global-level `verify` key directs users to `[[vm]]`/`[[vm.target]]`.

## Migration Plan

1. Ship the change; no state migration, no ABC changes.
2. Users move engine options into `[[vm]]` (new) or keep them at global/`[[vm.target]]` (unchanged behavior).
3. First new FULL backup created after the switch uses the new compression; prior generations remain valid and restorable until retired by retention.
4. Rollback: revert the binary — VM-level keys become silently ignored again (old behavior); no data corruption, no state incompatibility.

## Open Questions

- Should a future change add a global-level `verify` key for full symmetry (global → VM → target)? Out of scope here to limit blast radius; the unknown-key hint will surface demand.
- Should `full_verify_after_create` / `full_verify_before_delete` ever become per-VM? Currently global-only FULL-lifecycle settings; no user demand.
