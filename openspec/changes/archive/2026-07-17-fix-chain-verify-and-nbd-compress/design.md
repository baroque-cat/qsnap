## Context

QEMU 11.0+ (`qemu-img` version 11.0.2, confirmed in the user's environment) changed the JSON output schema of `qemu-img info --backing-chain --output=json`. The image file path key changed from `"image"` (legacy) to `"filename"` (new), and a nested `"children"` block-graph array was added alongside the flat fields. Qsnap's `_verify_backing_chain()` and `_restore_snapshot()` hardcode `item.get("image")`, producing `None` on new QEMU output and a false CRITICAL error.

Separately, qsnap's NBD-based FULL backup path (`nbd_full_export()`) unconditionally strips the `-c` (compress) flag from `qemu-img convert`, logging a WARNING `"compress=True ignored for NBD-based FULL backup"`. The code comments assert "NBD path does not support compression." However, experimental testing with `qemu-img 11.0.2` over a `qemu-nbd` Unix socket confirmed that `qemu-img convert -c -O qcow2 nbd:unix:<socket> <output>` works correctly and produces properly compressed qcow2 output (compression ratio: 51 MiB → 382 KiB on uniform data). The `qemu-img` documentation makes no mention of any `-c`/NBD incompatibility. This is a false assumption in the code.

## Goals / Non-Goals

**Goals:**
- Make `_verify_backing_chain()` and `_restore_snapshot()` compatible with both legacy QEMU (`"image"` key) and QEMU 11.0+ (`"filename"` key) `qemu-img info` JSON output.
- Enable `-c` compression on NBD-based FULL backups in `nbd_full_export()`, `FileCopyBackupProvider`, and `BitmapBackupProvider`.
- Remove the misleading WARNING and incorrect code comments about NBD compression being unsupported.
- Update test fixtures and tests to cover both old and new QEMU JSON formats.
- Identify and remove obsolete tests that asserted NBD compression is unsupported.

**Non-Goals:**
- Changing `IBackupProvider` ABC — `create_full_backup()` already accepts `compress: bool`.
- Changing `nbd_full_export()` public signature in a breaking way — the new `compress` parameter has a safe default of `False`.
- Adding support for `-o compression_type=zstd` — out of scope; `-c` uses default zlib.
- Changing `_get_chain_length()` — it only checks `len(chain_data)`, unaffected by key names.
- Changing `_log_size_estimate()` formula — the `base_size * 0.3` formula is already correct for all paths after this fix.

## Decisions

### Decision 1: Use `item.get("image") or item.get("filename", "")` for backwards compatibility

**Alternatives considered:**
- A. Check QEMU version with `qemu-img --version` and branch → too fragile, version strings vary across distros.
- B. Auto-detect format from first array element → more complex, same end result.
- C. `item.get("image") or item.get("filename", "")` → simplest, handles both formats transparently, no version detection needed.

**Choice: Option C.** Fallback chain: try `"image"` (legacy), fall back to `"filename"` (new). Both keys are strings when present; empty string is treated as missing. The `"children"` nested array is irrelevant — we only need the top-level file path, format, and backing-filename fields which are present at the top level in both formats.

### Decision 2: Add `compress: bool = False` parameter to `nbd_full_export()`, remove WARNINGs from callers

**Alternatives considered:**
- A. Keep WARNING, add `compress` to `nbd_full_export()` → WARNING contradicts the now-working feature.
- B. Remove `nbd_full_export()`, always use direct convert → breaks live VM backups (lock conflict).
- C. Add `compress` to `nbd_full_export()`, remove WARNINGs → simplest, correct.

**Choice: Option C.** The parameter has a safe default (`False`), preserving existing callers that don't pass `compress`. The `convert_cmd` construction in `nbd_full_export()` conditionally inserts `-c` before `nbd_uri`. Both `FileCopyBackupProvider` and `BitmapBackupProvider` pass their existing `compress` argument through.

### Decision 3: Keep `_log_size_estimate()` formula unchanged

The formula `full_size = int(base_size * 0.3) if target.compress else base_size` was already correct. The bug was in the execution path (NBD ignored compression), not in the estimation. After fixing NBD to support compression, the estimate matches reality for ALL paths.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| `"filename"` key may not exist in extremely old QEMU (pre-2.0) → | `item.get("filename", "")` returns `""`, which is falsy; `"image"` is tried first and will be found in old QEMU |
| `-c` over NBD may fail on some QEMU versions or configurations not tested → | The feature was tested with `qemu-img 11.0.2` + `qemu-nbd -k`; if it fails on a specific setup, the existing `not nbd_result.success` error handling catches it and returns `BackupResult(success=False)` |
| Compression over NBD may be slower than direct convert with `-c` → | Acceptable trade-off; live VM compression is now possible where it was entirely unavailable before. Users who prefer speed can set `compress = false` |
| Removing WARNING may surprise users who relied on it to know their backups were uncompressed → | The behavior now matches the configuration — `compress = true` actually compresses. This is a fix, not a regression |
| `_get_chain_length()` uses `len(chain_data)` which counts all array elements including potential `"children"` nesting → | The `"children"` array is nested INSIDE each top-level element, not at the array level. `len(chain_data)` counts top-level elements only, which is correct for chain length in both formats |

## Migration Plan

1. **No state migration needed** — `IStateManager` schema is unchanged.
2. **No config migration needed** — `compress` field already exists in `TargetConfig`.
3. **Rollback**: Revert the three source files (no data changes). Old behavior is restored immediately.
4. **Deployment**: Standard package update. No database migrations, no config changes.

## Open Questions

- None at this time. All technical decisions are resolved.
