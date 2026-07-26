## Context

The qsnap backup system currently hardcodes `qemu-img convert` as the sole FULL backup transfer engine (since v0.3.0, commit `8b36c23`). The alternative Python `pread`/`pwrite` loop via `libnbd` — which was the previous FULL engine — exists as dead code in `BitmapBackupProvider._transfer(zero_skip=True)` but cannot be selected. Additionally, the `qemu-img convert` flags `-m 4` (parallel coroutines) and `-W` (out-of-order writes) are hardcoded constants with no config override.

The system follows a strict Dependency Injection paradigm with ABC interfaces (AGENTS.md). Config flows through frozen dataclasses (`TargetConfig`, `GlobalConfig`) constructed by `ConfigFacade`. The `BitmapBackupProvider` receives config as method parameters, not stored as instance state. The factory (`DefaultFactory`) creates provider instances but does not forward `vm_config`/`target` to the constructor — engine selection must happen via method parameters or `TargetConfig` fields.

**Current FULL transfer flow:**
```
Core._backup_target()
  → provider.create_full_backup(..., compress=target.compress, compression_type=target.compression_type, ...)
    → _full_pull_lifecycle(...)
      → _qemu_img_convert_transfer(...)     ← ALWAYS, no branch
        → qemu-img convert [-c] -O qcow2 [-o compression_type=X] -m 4 -W -p <source> <target>.tmp
```

**Current incremental transfer flow (unchanged by this change):**
```
Core._backup_target()
  → provider.transfer_missing(...)
    → if prior is None: _full_pull_lifecycle(...)     ← FULL (always qemu-img convert)
    → else: _copy_dirty_blocks(...)                    ← Incremental (always pread/pwrite)
```

## Goals / Non-Goals

**Goals:**
- Make the FULL backup transfer engine configurable: `"qemu-img-convert"` (default) or `"libnbd"` (pread/pwrite)
- Make `qemu-img convert` flags `-m` (parallel coroutines) and `-W` (out-of-order writes) configurable
- All three new config fields inherit global → target (matching the existing `compress`/`compression_type` pattern — no VM-level intermediate)
- All new fields have defaults matching current behavior — zero-config migration
- Explicit parameters on `IBackupProvider` methods (not implicit reads from `TargetConfig` inside the provider) — per AGENTS.md "config as immutable dataclass in method parameters"

**Non-Goals:**
- Making the incremental transfer engine configurable — incrementals are always `libnbd` (pread/pwrite) by design (design D6: bitmap incrementals are uncompressed, qcow2 compressed clusters can only be produced by `qemu-img convert`)
- Adding a compression level parameter — qcow2+zstd in qemu-img convert does not support a separate compression level
- Adding a `-p` (progress) config flag — cosmetic, always included
- Adding VM-level config fields for the new parameters — inheritance is global → target only (matching `compress`/`compression_type`)
- Changing the factory to select different provider classes — `BitmapBackupProvider` remains the sole provider; engine selection happens inside it via method parameters

## Decisions

### D1: Engine selection via method parameters, not factory branching

**Decision:** Add `full_transfer_engine`, `convert_parallel`, `convert_out_of_order` as keyword parameters to `IBackupProvider.create_full_backup()` and `IBackupProvider.transfer_missing()`. Core reads them from `TargetConfig` and passes them explicitly.

**Rationale:** AGENTS.md mandates "config as immutable dataclass in method parameters." The factory does not forward `vm_config`/`target` to the `BitmapBackupProvider` constructor, so the provider cannot read `TargetConfig` fields directly. Adding a factory branch would require forwarding `target` to the constructor, which changes the constructor signature and breaks the "stateless worker" contract. Method parameters are the cleanest approach.

**Alternative considered:** Read `target.full_transfer_engine` directly inside `BitmapBackupProvider` from the `target: TargetConfig` parameter that `create_full_backup()` and `transfer_missing()` already receive. Rejected because it creates an implicit dependency on `TargetConfig` field names — the provider should not know which fields exist on the config object, only what it receives as parameters.

### D2: Branch point in `_full_pull_lifecycle()`, not in callers

**Decision:** The engine-selection branch is added to `_full_pull_lifecycle()`, which already receives all transfer parameters. Both `create_full_backup()` and `transfer_missing()` call `_full_pull_lifecycle()` — by branching there, we avoid duplicating the branch in two callers.

**Rationale:** `_full_pull_lifecycle()` is the shared scaffolding helper (design D7). It already handles qemu-img convert, mv .tmp → final, and finally cleanup. Adding the branch here means the cleanup logic (socket removal, domjobabort, XML removal) is shared regardless of engine choice.

**Alternative considered:** Branch in `create_full_backup()` and `transfer_missing()` separately. Rejected because it duplicates the cleanup scaffolding.

### D3: New `_full_transfer_via_libnbd()` private method

**Decision:** Create a new `_full_transfer_via_libnbd()` private method that orchestrates the libnbd FULL path: (1) create empty qcow2 with correct virtual size and compression_type, (2) start write-server via `_start_write_server()`, (3) connect source `INbdClient`, (4) call `_transfer(zero_skip=True)`, (5) flush, (6) disconnect, (7) terminate qemu-nbd.

**Rationale:** The libnbd FULL path requires more steps than qemu-img convert (which is a single command). Encapsulating these steps in a dedicated method keeps `_full_pull_lifecycle()` clean and makes the libnbd path independently testable.

**Alternative considered:** Inline the libnbd steps in `_full_pull_lifecycle()`. Rejected because it would make the method too long and mix two different transfer paradigms.

### D4: qcow2 pre-creation for libnbd FULL path

**Decision:** When `full_transfer_engine == "libnbd"`, the `_full_transfer_via_libnbd()` method SHALL create an empty qcow2 file via `qemu-img create -f qcow2 [-o compression_type=<type>] <tmp_file> <virtual_size>` before starting the write-server. The virtual size is obtained from `INbdClient.get_size()` (running VM) or `qemu-img info` (stopped VM).

**Rationale:** `_start_write_server()` starts `qemu-nbd` pointing at a target file. For the libnbd path, the target file must exist and have the correct virtual size and compression type before `qemu-nbd` can serve it. The existing `_query_virtual_size()` method (bitmap.py:154-175) handles the stopped-VM case; for running VMs, the size comes from the NBD connection.

**Alternative considered:** Let `qemu-nbd` create the file. Rejected because `qemu-nbd` does not create qcow2 files — it requires an existing file to serve.

### D5: Configurable `-m` and `-W` only apply to qemu-img convert engine

**Decision:** `convert_parallel` and `convert_out_of_order` are only consumed by the `qemu-img-convert` engine path. The `libnbd` engine path ignores them (it uses `INbdClient.pread`/`pwrite` which has no parallelism or out-of-order concept).

**Rationale:** The two engines have fundamentally different architectures. The libnbd path is a sequential Python loop with auto-chunking. Exposing parallelism settings for it would be misleading.

### D6: No compression_type passed to `_start_write_server()`

**Decision:** `_start_write_server()` continues to accept only `compress: bool` (no `compression_type` parameter). The compression algorithm is set at qcow2 creation time via `qemu-img create -o compression_type=<type>`, and the compress driver auto-detects it from the qcow2 header. This is the existing design (nbd-bitmap-backup spec, requirement "qemu-nbd compress driver for write-side compression").

**Rationale:** This was an explicit design decision in the existing spec. The compress driver reads the qcow2 metadata to determine the algorithm. Passing `compression_type` to `_start_write_server()` would be redundant.

## Risks / Trade-offs

**[Risk: Reviving dead code `_transfer(zero_skip=True)`]** → The `zero_skip=True` branch in `_transfer()` (bitmap.py:861-865, 897-900) has not been exercised in production since v0.3.0. It may contain hidden bugs. **Mitigation:** Comprehensive mock tests for the libnbd FULL path before enabling; integration test with a real libvirt VM; default to `"qemu-img-convert"` so users must explicitly opt in.

**[Risk: Virtual size discovery for libnbd FULL]** → For running VMs, the virtual size must be obtained from the NBD connection before creating the qcow2 file. But the NBD export is started by `virsh backup-begin`, which also starts the transfer clock. The qcow2 creation must happen after `backup-begin` but before `_start_write_server()`. **Mitigation:** The `_full_transfer_via_libnbd()` method calls `backup-begin`, then connects `INbdClient` to get `get_size()`, then creates the qcow2, then starts the write-server. The NBD export waits for the client to connect — there is no timeout on the export side.

**[Risk: Performance regression for libnbd FULL]** → The Python pread/pwrite loop with compress driver was measured at ~1.5 MB/s (v0.3.0 changelog), vs ~850 MB/s for qemu-img convert. Users selecting `"libnbd"` for FULLs will see ~570x slower transfers. **Mitigation:** Log a WARNING when `full_transfer_engine == "libnbd"` is selected for a FULL backup; document the trade-off in `qsnap.toml.example`.

**[Trade-off: Interface breakage]** → Adding parameters to `IBackupProvider.create_full_backup()` and `transfer_missing()` is a BREAKING change for any external code implementing these interfaces. All mocks must be updated. **Mitigation:** New parameters have defaults matching current behavior; the breakage is compile-time (kwargs), not runtime.
