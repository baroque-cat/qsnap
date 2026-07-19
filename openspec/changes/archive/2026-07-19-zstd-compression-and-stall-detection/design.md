## Context

qsnap uses `qemu-img convert -O qcow2 -c nbd:unix:...` for all NBD-based backups. The `-c` flag enables qcow2 compression, which defaults to **zlib** (single-threaded). Benchmarks on this system (tmpfs, 500MB virtual disk, 250MB data) show zlib is **22× slower** than no compression (77 MB/s vs 1723 MB/s). On a real HDD target, this translates to 2.7–4.8 MB/s effective write speed — making a 113 GB disk take 6–11 hours, far exceeding the hardcoded 3600s (1 hour) timeout.

The timeout itself is the second problem: it kills backups that are progressing correctly but slowly. A backup at 50% completion is killed, all progress is lost, and the next run starts from scratch. The size estimation formula (`base_size × 0.3`) is the third problem: it cannot predict data compressibility and always produces misleading projections.

The project follows a strict Dependency Injection paradigm (AGENTS.md): all external calls go through `IShell`, config is immutable frozen dataclasses, modules receive config as method parameters, and every module is created through `IVMModuleFactory`.

## Goals / Non-Goals

**Goals:**
- Replace zlib with zstd as the default compression algorithm across all four compression paths (NBD FULL, NBD incremental, direct convert, rsync)
- Make compression type configurable (`compression_type` field, default `"zstd"`, alternative `"zlib"`)
- Replace hardcoded timeouts for data-transfer commands with stall detection (output file growth monitoring)
- Remove size estimation entirely (formula, projections, and pipeline step D5)
- Make stall timeout configurable (`backup_stall_timeout` field, default `"30m"`)
- Keep all changes within the DI paradigm — new config fields are immutable dataclasses, new IShell method is abstract, modules receive parameters

**Non-Goals:**
- Replacing `qemu-img convert` with libnbd Python bindings (considered, but too large a refactor for this change)
- Adding progress bar or speed logging (user explicitly rejected — systemd logs + iotop suffice for diagnostics)
- Adding a max timeout / overall deadline (stall detection is sufficient for all realistic scenarios — if data flows, let it run)
- Changing compression for verification commands (`qemu-img check`, `qemu-img compare`) — these don't compress
- Changing the qcow2 output format — backups remain qcow2, only the compression algorithm changes

## Decisions

### Decision 1: zstd via qemu-img `-o compression_type=zstd` (not Python lib)

**Choice:** Use `qemu-img convert -c -o compression_type=zstd` for qcow2 compression and `rsync --compress --compress-choice=zstd` for rsync compression.

**Rationale:** qemu-img 11.0.2 supports zstd as a qcow2 compression type (verified on this system). rsync 3.4.4 supports `--compress-choice=zstd` (added in 3.2.0). No Python zstd/lz4 packages needed — zero new dependencies, consistent with the project's "zero runtime PyPI dependencies" constraint.

**Alternatives considered:**
- Python `zstandard` module: would require `pip install zstandard`, violating the zero-dependency constraint. Also would require rewriting the data transfer pipeline (libnbd + Python compression), which is a major refactor.
- `nbdcopy --allocated`: skips zero blocks and has `--progress`, but doesn't compress. Would need a separate compression step.
- Disabling compression entirely (`compress = false`): fastest (1723 MB/s) but produces 3× larger files (113 GB vs ~40 GB). Not acceptable for storage-constrained targets.

### Decision 2: `compression_type` as a separate config field (not changing `compress` type)

**Choice:** Keep `compress: bool = True` (backward compatible). Add `compression_type: str = "zstd"` as a new field. When `compress=True`, `compression_type` selects the algorithm. When `compress=False`, no compression regardless of `compression_type`.

**Rationale:** Changing `compress` from `bool` to `str | bool` would break existing TOML configs (`compress = true` would need to become `compress = "zstd"`). A separate field is backward compatible — existing configs automatically get zstd.

**Alternatives considered:**
- `compress: str | bool` (True → zstd, "zlib" → zlib, False → none): elegant but confusing (True means zstd?).
- `compression: str = "zstd"` (replacing `compress: bool`): breaking change, all configs must update.

### Decision 3: Stall detection via output file size monitoring (not AIO/poll, not max timeout)

**Choice:** Add `IShell.run_with_stall_detection(cmd, output_file, stall_timeout, check)` that uses `subprocess.Popen()` + a 60-second polling loop checking `output_file.stat().st_size`. If the file size doesn't increase for `stall_timeout` seconds (default 1800 = 30 min), the process is killed.

**Rationale:**
- Stall detection catches all realistic hang scenarios: NBD server hung, disk I/O stalled, process deadlock, process in infinite loop. In all these cases, the output file stops growing.
- A max timeout (overall deadline) is NOT needed: if data is flowing (even slowly), the backup should continue. Killing a 50%-complete backup and restarting is wasteful — the restart would be equally slow.
- The "very slow but progressing" case (e.g., 1 KB/s) is not a hang — it's slow I/O. Stall detection correctly allows it. A max timeout would incorrectly kill it.
- AIO + `poll(timeout)` (libnbd's only timeout mechanism on version 1.24.2) is not needed because we're not using libnbd — we're using `qemu-img convert` which manages its own NBD connection internally.

**Alternatives considered:**
- `subprocess.run(timeout=N)` with a very large N (e.g., 86400 = 24h): doesn't detect stalls, kills progressing backups, user explicitly rejected.
- Watchdog thread monitoring process CPU usage: over-engineered, CPU usage doesn't reliably indicate stalls (process can be in I/O wait).
- libnbd AIO + `poll(timeout)`: requires switching to libnbd (major refactor, out of scope).

### Decision 4: Stall detection only for data-transfer commands (not all commands)

**Choice:** Use `run_with_stall_detection()` only for `qemu-img convert` (NBD FULL, NBD incremental, direct convert) and `rsync` (file-copy incremental). Keep `run(timeout=N)` for all short commands (virsh backup-begin, domjobabort, domblklist, checkpoint-create/delete, qemu-img info/check/compare).

**Rationale:** Short commands don't have an output file to monitor and should complete in seconds. A fixed timeout is appropriate for them. Stall detection requires an output file path, which only data-transfer commands have.

### Decision 5: Remove size estimation entirely

**Choice:** Delete `_log_size_estimate()` method, remove the `base_size × 0.3` formula from `schedule_summary()` and `estimate()`, and remove pipeline step D5 from AGENTS.md. Log only factual data: `base_size` (from `qemu-img info`) and `compression_type` (from config).

**Rationale:** The 0.3 factor is a guess that cannot predict data compressibility. Real data (text, binaries, encrypted, already-compressed) has wildly different ratios (0.1–0.8). The estimate always lies, providing false confidence or false alarm. Removing it simplifies the codebase and the pipeline.

**Alternatives considered:**
- Historical compression ratio (measure actual compressed_size / base_size from past FULLs): more accurate but requires state schema changes and several FULLs to build history. Can be added later if needed.
- Configurable ratio (`compress_ratio: float = 0.4`): still a guess, just user-configurable. Doesn't solve the fundamental problem.

### Decision 6: `backup_stall_timeout` as duration string (not integer seconds)

**Choice:** `backup_stall_timeout: str = "30m"` — parsed as a duration string (like `rate_limit`, `deferred_warn_age`, etc.).

**Rationale:** Consistent with existing config fields that use duration strings (`"7d"`, `"14d"`, `"30m"`, `"2s"`). More readable than raw seconds (1800). Reuses the existing `parse_duration()` utility.

## Ris / Trade-offs

- **[Risk] zstd produces slightly larger files than zlib** → zstd level 1 compresses ~5–10% less than zlib level 6. Trade-off: 11× faster compression for ~5–10% larger files. Acceptable — storage is cheaper than CPU time for backups.
- **[Risk] Stall detection false positive during long compression bursts** → qemu-img convert compresses in small clusters (64KB), so output file grows frequently. 30-minute stall timeout is generous enough to avoid false positives. If needed, user can increase `backup_stall_timeout`.
- **[Risk] Stall detection misses "1 byte per minute" infinite loop** → Unrealistic for qemu-img convert and rsync, which work in blocks (64KB minimum). If it ever happens, the process would eventually complete or the user would notice via iotop.
- **[Risk] IShell grows beyond "thin wrapper"** → Adding `run_with_stall_detection()` adds business logic to IShell. Justified because: (1) AGENTS.md requires ALL calls to go through IShell, (2) stall detection IS a form of timeout enforcement, (3) the alternative (bypassing IShell) violates the paradigm more severely.
- **[Risk] Backward compatibility: existing configs get zstd instead of zlib** → zstd is strictly better (faster, slightly larger files). Users who need zlib can set `compression_type = "zlib"`. The change is documented in README and qsnap.toml.example.
- **[Risk] `IShell` ABC change breaks MockShell** → MockShell must implement the new method. This is expected — AGENTS.md requires every ABC to have a mock. The mock implementation is straightforward (return predefined ShellResult).

## Migration Plan

1. **Config migration**: No action required. Existing configs with `compress = true` automatically get zstd. Users who need zlib add `compression_type = "zlib"`.
2. **State migration**: No state schema changes. Existing state files are fully compatible.
3. **Backup file compatibility**: zstd-compressed qcow2 files are readable by qemu-img 11.0.2+ (verified). Older qemu-img versions (< 5.2) cannot read zstd-compressed qcow2 — users on old QEMU should set `compression_type = "zlib"`.
4. **Systemd**: Add `TimeoutStartSec=0` to qsnap.service. Without this, systemd kills the service after 90s (default oneshot timeout), defeating stall detection.
5. **Rollback**: Set `compression_type = "zlib"` and `backup_stall_timeout = "0s"` (disables stall detection, falls back to fixed timeout behavior — but fixed timeouts are also removed, so rollback requires reverting code changes).

## Open Questions

- Should `backup_stall_timeout` be per-target or per-command-type? Current design: per-target (inherited from global). This means the same stall timeout applies to both FULL (large) and incremental (small) transfers. If needed, per-command-type timeouts can be added later.
- Should the stall detection polling interval (60s) be configurable? Current design: hardcoded 60s. If needed, can be exposed as `stall_poll_interval` config field.
