## Context

qsnap's FULL backup path currently uses a Python `pread`/`pwrite` loop through `libnbd`, writing to a forked `qemu-nbd` with `driver=compress`. This produces ~1.5 MB/s on SSDs — 30–60x slower than the same path without compression (30–89 MB/s). The bottleneck is `driver=compress` (synchronous per-cluster zstd in a single thread) compounded by Python overhead and no read/write pipelining.

A separate bug: `ConfigFacade._parse()` reads global keys from the top-level TOML dict, but a `[global]` section places keys in `raw["global"]` — silently ignoring all global settings (compress, lockfile, snapshot_preserve, etc.).

A third issue: `BitmapBackupProvider.create_full_backup()` unconditionally calls `virsh backup-begin`, which requires a running VM. Stopped-VM FULL backups fail with no fallback.

The `IShell.run_with_stall_detection()` method is fully implemented and tested but never called from production code (dead code).

## Goals / Non-Goals

**Goals:**
- Fix `[global]` TOML section parsing so global settings are respected.
- Replace the Python `pread`/`pwrite` + `qemu-nbd driver=compress` FULL backup path with `qemu-img convert` (C code, parallel coroutines, ~850 MB/s zstd).
- Add stopped-VM FULL backup support via direct `qemu-img convert` from source qcow2.
- Activate `run_with_stall_detection()` for `qemu-img convert` execution.
- Clean up README and tests of stale rsync/file-copy references.
- Keep incremental backups on the Python `libnbd` engine (dirty-bitmap intersection required).

**Non-Goals:**
- Changing the qcow2 output format (stays qcow2, not sparse stream).
- Changing the `IBackupProvider` ABC interface (all changes are internal).
- Adding pipelining to the incremental `pread`/`pwrite` loop (possible future optimization, not needed now — incrementals are already 30–89 MB/s).
- Removing `_start_write_server()` or `_transfer()` (retained for incremental use).
- Multi-disk support (still single-disk via `get_first_disk_target`).

## Decisions

### Decision 1: `qemu-img convert` for FULL backups only

**Choice:** Use `qemu-img convert` as the FULL backup transfer engine. Incremental backups keep the Python `libnbd` `pread`/`pwrite` loop.

**Rationale:** FULL backups copy ALL allocated blocks — no dirty-bitmap intersection needed. `qemu-img convert` reads from the NBD source (running VM) or direct file (stopped VM), uses `base:allocation` for sparse detection, and writes to qcow2 with `-c` compression using parallel coroutines (`-m 4`, `-W`). Incrementals require `qemu:dirty-bitmap:backup-<disk>` meta-context querying and `overlap_with_allocation()` intersection — `qemu-img convert` cannot do this.

**Alternatives considered:**
- *Pipelining the Python loop* (double-buffering reads/writes): ~2x improvement on incrementals, but compression stays at ~1.5 MB/s because `driver=compress` remains the bottleneck. Insufficient.
- *Post-compression* (write uncompressed, then `qemu-img convert -c` the result): Works but requires double disk space for the temporary uncompressed file and two passes over the data.
- *Switching to virtnbdbackup's sparse stream format*: Major architectural change, loses qcow2 backing chain for incrementals. Rejected.

### Decision 2: VM state detection in `create_full_backup()`

**Choice:** `create_full_backup()` SHALL call `is_vm_running()` before choosing the transfer path. Running VMs use `virsh backup-begin` + `qemu-img convert nbd:unix:<socket>`. Stopped VMs use direct `qemu-img convert <source_path> <target>`.

**Rationale:** `virsh backup-begin` requires a running QEMU process. For stopped VMs, the source qcow2 is not locked, so direct `qemu-img convert` is safe and avoids the NBD protocol entirely. This matches the pattern already used in `Core.fork()` (which has a running/stopped branch for `qemu-img convert`).

**Alternative considered:** *Always use NBD (current behavior)*: fails on stopped VMs. The integration test `test_full_backup_stopped_vm_returns_error` explicitly verifies this failure. We are replacing that with a working fallback.

### Decision 3: `run_with_stall_detection()` for `qemu-img convert` execution

**Choice:** Execute `qemu-img convert` via `IShell.run_with_stall_detection()`, passing the target `.tmp` file as `output_file` and `target.backup_stall_timeout` as `stall_timeout`.

**Rationale:** `qemu-img convert` is a long-running data-transfer command. The stall-detection method monitors output file growth and kills the process only when no growth is observed for `stall_timeout` seconds. This is exactly what `run_with_stall_detection()` was designed for (per the stall-detection spec: "Subprocess transfers unchanged — When a FULL backup runs `qemu-img convert`, it still uses `IShell.run_with_stall_detection`"). The method is already implemented in `SubprocessShell` and `MockShell`, with full test coverage.

### Decision 4: `[global]` section unwrapping in `_parse()`

**Choice:** After `raw = tomllib.load(fh)`, if `"global"` key exists in `raw`, pop it and merge into top-level: `raw = {**raw, **raw.pop("global")}`.

**Rationale:** The parser reads global settings from the top-level dict (`if "compress" in raw`). A `[global]` section in TOML places keys in `raw["global"]`, not at top level. The example config (`qsnap.toml.example`) uses top-level keys (no `[global]` section), which is why the bug was hidden. The fix is minimal and backward-compatible: top-level keys still work, and `[global]` section keys are now also accepted.

**Alternative considered:** *Reject `[global]` section with an error*: Would break user configs that use `[global]`. The merge approach is more user-friendly.

### Decision 5: Command construction for `qemu-img convert`

**Choice:** The `qemu-img convert` command SHALL be constructed as follows:

- **Running VM, compressed:** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p nbd:unix:<socket> <target>.tmp`
- **Running VM, uncompressed:** `qemu-img convert -O qcow2 -m 4 -W -p nbd:unix:<socket> <target>.tmp`
- **Stopped VM, compressed:** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p <source>.qcow2 <target>.tmp`
- **Stopped VM, uncompressed:** `qemu-img convert -O qcow2 -m 4 -W -p <source>.qcow2 <target>.tmp`

**Rationale:** `-c` enables compression (uses QEMU's optimized C zstd, ~850 MB/s). `-m 4` uses 4 parallel coroutines. `-W` enables out-of-order writes. `-p` shows progress. `-O qcow2 -o compression_type=zstd` sets the output format and compression algorithm. The NBD URI `nbd:unix:<socket>` is the source for running VMs.

### Decision 6: `_start_write_server()` and `_transfer()` retained for incrementals

**Choice:** Do NOT remove `_start_write_server()` or `_transfer()`. They remain the engine for incremental backups (`_copy_dirty_blocks()` calls `_transfer()` with `zero_skip=False` and dirty-bitmap meta-contexts).

**Rationale:** Incrementals require dirty-bitmap meta-context intersection, which only the Python `libnbd` loop can do. The `_start_write_server()` starts an uncompressed `qemu-nbd` (`compress=False`, design D6) for the backing-chained delta. This path is already fast (30–89 MB/s) because it transfers only dirty blocks and uses no compression.

## Risks / Trade-offs

- **[NBD socket race condition]** `virsh backup-begin` starts the NBD server asynchronously. `qemu-img convert` may fail to connect if started too early. → **Mitigation:** Wrap `qemu-img convert` in the existing `_transfer_with_retry()` wrapper (up to 3 retries with exponential backoff). The retry classification already handles "connection refused" errors.

- **[Progress bar output]** `qemu-img convert -p` writes progress to stderr. `run_with_stall_detection()` captures stderr via `subprocess.PIPE`. Progress output may interfere with stall detection if stderr is not drained. → **Mitigation:** `run_with_stall_detection()` already handles `subprocess.PIPE` and polls `proc.wait(timeout=60)`. The progress bar writes to stderr, not stdout. The output file growth is monitored independently.

- **[Temporary disk space for stopped-VM fallback]** Stopped-VM `qemu-img convert` reads directly from the source qcow2 — no temporary NBD socket needed. But the target `.tmp` file is still created. → **Mitigation:** Same as current behavior — `.tmp` file is atomically renamed on success and deleted on failure.

- **[Spec conflict resolution]** The `nbd-bitmap-backup` spec currently says "No `qemu-img convert` SHALL be used in the data path." The `stall-detection` spec already anticipated `qemu-img convert` for FULLs. → **Mitigation:** The delta spec for `nbd-bitmap-backup` SHALL remove this prohibition for FULL backups and clarify that it applies to incremental transfers only.

- **[Backward compatibility]** Users with `compress = true` (default) will see a behavior change: FULL backups now use `qemu-img convert -c` instead of `driver=compress`. The output is still a compressed qcow2. → **Mitigation:** The output format is identical (compressed qcow2 with zstd clusters). The only observable difference is speed (30–60x faster).
