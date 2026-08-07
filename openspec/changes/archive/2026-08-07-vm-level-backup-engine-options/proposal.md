## Why

Users report that backup engine options (`compression_type`, `convert_parallel`, `convert_out_of_order`, and also `compress`, `backup_stall_timeout`) placed in a `[[vm]]` table are silently discarded: the spawned command shows global defaults (`-o compression_type=zstd -m 4 -W`) instead of the per-VM values. The same silent drop affects `verify` at VM level (parsed only at target level). Root cause: `ConfigFacade._build_vm` never reads these keys from `vm_raw`, `VMConfig` has no fields for them, and inheritance resolves global → target only, skipping the VM hop. There is no unknown-key detection, so misplaced keys vanish without diagnostics, and misleading comments in `qsnap.toml.example` ("inherited from global → VM → target") actively steer users into this misconfiguration. This builds on the archived change `2026-07-26-configurable-full-backup-engine`, which parameterized the convert command but deliberately stopped at global → target inheritance.

## What Changes

- Implement the missing VM inheritance hop for backup engine options: `compress`, `compression_type`, `convert_parallel`, `convert_out_of_order`, `backup_stall_timeout` become parseable at the `[[vm]]` level with resolution order **global → VM → target** (each more specific level overrides the previous). `verify` (post-transfer verification mode) also becomes parseable at the `[[vm]]` level with resolution **VM → target** (it has no global-level key today; the default remains `metadata`). This enables fragmented per-VM assignment of backup engine parameters.
- Extend `VMConfig` with the six option fields (`compress`, `compression_type`, `convert_parallel`, `convert_out_of_order`, `backup_stall_timeout`, `verify`) so VM-level values are part of the immutable config model and testable in isolation.
- Add strict unknown-key validation to `ConfigFacade` for the global, `[[vm]]`, `[[vm.disk]]`, and `[[vm.target]]` tables: any unrecognized key raises a config error identifying the table and key name. This converts today's silent discard into an immediate, actionable diagnostic.
- Fix misleading documentation: `qsnap.toml.example` comments that claim a VM hop for engine options (lines ~81, ~224, ~235), the `TargetConfig` docstring in `models/config.py` (~157-164), and stale docstrings in `modules/backup/bitmap.py` (~21, ~774-776) that claim `-m 4`/`-W` are always included.
- Add an end-to-end config test path: TOML fixture with VM-level engine options → parsed `TargetConfig` → asserted values (closing the gap where no test exercises `[[vm]] TOML → TargetConfig`).
- Test-suite refactoring: review existing tests for redundancy introduced by this change, delete obsolete ones, and extend integration tests to verify the new VM-level behavior against real `qemu-img`.

Non-breaking: no ABC interface (`IConfigFacade`, `IBackupProvider`, `IVMModuleFactory`) changes signature; Core already forwards `target.*` values faithfully; `IStateManager` schema is untouched (state stores no compression metadata — verified), so no state migration is required. Existing configs that set these options only at global or target level parse identically.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `config-parsing`: requirements change to (a) parse the backup engine options (`compress`, `compression_type`, `convert_parallel`, `convert_out_of_order`, `backup_stall_timeout`) and `verify` at the `[[vm]]` level with global → VM → target resolution (VM → target for `verify`), and (b) reject unknown keys in global/VM/disk/target tables with a descriptive error.
- `config-model`: requirements change to add `compress`, `compression_type`, `convert_parallel`, `convert_out_of_order`, `backup_stall_timeout`, and `verify` fields to `VMConfig` (frozen dataclass), with the same defaults and validation ranges as `GlobalConfig`/`TargetConfig`.

Unchanged by design: `backup-provider`, `qemu-img-convert-full-backup`, `nbd-bitmap-backup`, `live-vm-full-backup` — the provider already accepts these parameters from Core's `target.*` fields; only the config resolution upstream changes. `core-orchestrator` and `module-factory` are unaffected (Core forwards `TargetConfig` fields; factory passes only infrastructure deps).

## Impact

- **Affected modules:** `qsnap/config/facade.py` (VM-level parsing, inheritance plumbing into `_build_target`, unknown-key validation), `qsnap/models/config.py` (`VMConfig` fields + docstring fixes), `qsnap.toml.example` (comment corrections + VM-level examples), `qsnap/modules/backup/bitmap.py` (stale docstrings only — no logic change).
- **APIs:** none changed. `VMConfig` gains fields (additive, frozen dataclass — constructed only by `ConfigFacade` and test factory helpers).
- **State:** none. No `IStateManager` schema change; no migration path needed.
- **Factory:** no new branches in `IVMModuleFactory.create_*`.
- **Tests:** new unit tests in `tests/config/` (parser + facade inheritance), new/updated TOML fixtures in `tests/fixtures/configs/`, updated integration tests in `tests/integration/` verifying VM-level options reach the real `qemu-img convert` command, plus a deletion list of obsolete tests produced during test-plan analysis.
- **Behavioral compatibility:** configs relying on the old silent-discard behavior (keys misplaced at VM level) will now either take effect (the six newly VM-aware options) or fail loudly (any other unknown key) — the intended correction. Migration safety for changing `compression_type` between runs is established: FULL backups are standalone, incrementals are uncompressed by design (D6), verification/retention/cleanup/restore never read compression metadata.
