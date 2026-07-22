# NBD Dirty Block Transfer

## Purpose

In-process dirty-block transfer engine for bitmap-mode incrementals: negotiates NBD meta-contexts (`base:allocation` + `qemu:dirty-bitmap:backup-<disk>`) over the libvirt pull-model export, copies only dirty∩allocated extents via the `INbdClient` abstraction, and writes them into a backing-chained qcow2 delta served by a forked `qemu-nbd` — replacing the former `qemu-img convert` pull that always copied the full disk.

## Requirements

### Requirement: INbdClient abstraction for NBD transport

The system SHALL define an `INbdClient` ABC in `qsnap/interfaces/nbd.py` as the sole interface through which NBD protocol operations are performed (meta-context negotiation, block-status queries, reads, writes). The interface SHALL expose: `connect(uri, export_name, meta_contexts) -> NbdResult`, `get_size() -> int`, `get_max_request_size() -> int`, `block_status(offset, length) -> NbdResult`, `pread(offset, length) -> NbdResult`, `pwrite(offset, data) -> NbdResult`, and `disconnect() -> None`. All fallible methods SHALL return `NbdResult` (success/payload/error) and SHALL NOT raise exceptions for expected failures (connection refused, server error, EOF). Error strings SHALL be normalized so transient conditions map to the existing retryable patterns ("eof", "timed out", "broken pipe", "connection refused"). `NbdExtent` (offset, length, data) and `NbdResult` (success, payload, error) SHALL be frozen dataclasses in `qsnap/models/results.py`.

#### Scenario: Connection failure returns result object

- **WHEN** the NBD server socket does not exist or refuses the connection
- **THEN** `connect()` returns `NbdResult(success=False, error=...)` with a normalized error string
- **AND** no exception propagates to the caller

#### Scenario: Read error normalized for retry

- **WHEN** the NBD server closes the connection mid-transfer
- **THEN** `pread()` returns `NbdResult(success=False, ...)` whose error string contains "eof" or "broken pipe" (case-insensitive)
- **AND** Core retry classification treats it as retryable without changes

### Requirement: LibnbdClient production implementation

`LibnbdClient` in `qsnap/utils/nbd_client.py` SHALL implement `INbdClient` using the system `python3-libnbd` package. The `import nbd` SHALL be lazy (inside `connect()` or module-level guarded import), so importing qsnap never requires libnbd. When the package is missing, `connect()` SHALL return `NbdResult(success=False)` with an actionable error naming the system package (`apt install python3-libnbd`). The client SHALL request exactly the meta-contexts passed to `connect()` (e.g. `base:allocation` and `qemu:dirty-bitmap:backup-<disk>`), SHALL honor the server-advertised maximum request size (default cap 32 MiB), and SHALL split reads/writes larger than that size into sequential chunks.

#### Scenario: Lazy import keeps qsnap runnable without libnbd

- **WHEN** the `nbd` Python package is not installed
- **THEN** importing `qsnap.utils.nbd_client` and constructing `LibnbdClient()` succeed
- **AND** only `connect()` reports the missing dependency

#### Scenario: Missing package yields actionable error

- **WHEN** `connect()` is called and `import nbd` fails
- **THEN** the returned error string names `python3-libnbd` as the package to install

#### Scenario: Large read is chunked

- **WHEN** `pread(offset, length)` is called with `length` greater than the server maximum request size
- **THEN** the client issues multiple sequential NBD read requests each not exceeding the maximum
- **AND** the returned payload is the concatenation in offset order

### Requirement: Pure extent-processing functions

Extent unification and dirty∩allocated intersection SHALL be implemented as pure, deterministic, I/O-free functions in `qsnap/utils/extents.py`: `unify_extents(extents) -> list[NbdExtent]` SHALL merge consecutive extents of the same kind, and `overlap_with_allocation(dirty, allocated) -> list[NbdExtent]` SHALL return only dirty regions that are also allocated (sparse/fstrim filtering). Neither function SHALL perform I/O, access global state, or depend on wall-clock time.

#### Scenario: Consecutive same-kind extents are unified

- **WHEN** the extent list contains adjacent dirty extents
- **THEN** `unify_extents()` returns a single merged extent covering their combined range

#### Scenario: Dirty-but-unallocated regions are filtered

- **WHEN** a dirty extent overlaps a region that `base:allocation` reports as hole/zero
- **THEN** `overlap_with_allocation()` excludes the unallocated sub-range
- **AND** only dirty-and-allocated sub-ranges remain

### Requirement: Dirty-block copy loop replaces qemu-img convert for incrementals

`BitmapBackupProvider.transfer_missing()` SHALL transfer incremental data via an in-process copy loop using `INbdClient` instead of `qemu-img convert`. The loop SHALL: (1) resolve the previous backup at the target (newest by timestamp; the FULL for the first incremental) and verify it still exists immediately before use, (2) create `<name>.qcow2.tmp` via `qemu-img create -f qcow2 -b <previous> -F qcow2` through `IShell`, (3) serve the `.tmp` file through a forked `qemu-nbd` with `--pid-file` and a process-unique write socket, (4) connect the source client to the libvirt socket requesting `base:allocation` and `qemu:dirty-bitmap:backup-<disk>` meta-contexts, (5) query block status, unify extents, and intersect with allocation, (6) `pread` each remaining dirty extent from the source and `pwrite` it to the destination at the same offset, (7) disconnect both clients, terminate `qemu-nbd` via its pidfile, and (8) atomically `mv <name>.qcow2.tmp <name>.qcow2`. The provider SHALL receive the `INbdClient` as a constructor dependency (third parameter after `shell` and `state`); `DefaultFactory` SHALL construct the production `LibnbdClient`. If the previous backup disappears between listing and creation, the transfer SHALL fail with a retryable-class error.

#### Scenario: Incremental copies only dirty blocks

- **WHEN** a prior checkpoint exists and the guest wrote 100 MiB since then
- **THEN** the copy loop reads approximately 100 MiB from the source NBD export (not the full virtual disk size)
- **AND** the resulting qcow2 chains to the previous backup

#### Scenario: First incremental chains to the FULL

- **WHEN** no previous incremental exists at the target
- **THEN** the `.tmp` file is created with `qemu-img create -b <FULL> -F qcow2`
- **AND** the final file's `backing-filename` names the FULL backup

#### Scenario: Previous backup vanished — retryable failure

- **WHEN** the previous backup file is deleted between listing and `qemu-img create`
- **THEN** the transfer returns `BackupResult(success=False, ...)` with an error class Core treats as retryable
- **AND** the standard failure path runs (partial cleanup, successor checkpoint deleted best-effort)

### Requirement: Write-side lifecycle is crash-safe

The `.tmp` output, write socket, `qemu-nbd` process, and libvirt backup job SHALL be cleaned up on every outcome (success, transfer failure, verification failure, exception). Cleanup SHALL mirror the existing `nbd_full_export` discipline: `virsh domjobabort` (WARNING-only on failure), `rm -f` of both sockets, temp XML removal, `qemu-nbd` termination via pidfile, and `.tmp` removal on failure paths. A crash that orphans the `qemu-nbd` process or write socket SHALL NOT affect subsequent runs (process-unique paths; stale-socket removal before use).

#### Scenario: Failure mid-copy cleans everything

- **WHEN** a `pread` fails after 50% of dirty blocks were copied
- **THEN** the destination client is disconnected, `qemu-nbd` is terminated, the `.tmp` file is removed, the backup job is aborted, and both sockets are removed
- **AND** the prior checkpoint remains the newest valid baseline

#### Scenario: Successful transfer leaves no artifacts

- **WHEN** the copy loop and verification complete successfully
- **THEN** no `.tmp` file, no write socket, and no `qemu-nbd` process remain
- **AND** the final qcow2 is present at the target path

### Requirement: In-process stall watchdog for the copy loop

The copy loop SHALL implement stall detection without `IShell`: a progress timestamp updated after every successful chunk write. If no chunk completes for `stall_timeout` seconds, the loop SHALL abort and the transfer SHALL return `error="Stall detected: no progress for {N}s"` (the exact string produced by `IShell.run_with_stall_detection`). When `stall_timeout` is 0, the watchdog SHALL be disabled. The watchdog SHALL NOT spawn threads; progress is checked between chunk writes.

#### Scenario: Stall aborts the transfer

- **WHEN** no `pwrite` completes for `stall_timeout` seconds
- **THEN** the loop aborts, the failure path runs, and the error string is `"Stall detected: no progress for {N}s"`

#### Scenario: Slow but progressing transfer completes

- **WHEN** chunks complete steadily, each slower than usual but within `stall_timeout` cumulatively
- **THEN** the loop is not aborted and the transfer completes

### Requirement: Incremental output is a backing-chained COW delta

The unified NBD transfer engine SHALL produce backing-chained qcow2 deltas for incremental transfers and standalone qcow2 files for FULL transfers. For incrementals, the delta SHALL be created via `qemu-img create -f qcow2 -b <previous_backup> -F qcow2 <target>.tmp` and served by a forked `qemu-nbd`. For FULLs, the target SHALL be created via `qemu-img create -f qcow2 [-o compression_type=zstd] <target>.tmp` (standalone, no backing). The same `pread`/`pwrite` engine transfers data in both cases — the only difference is meta-contexts (`base:allocation` only for FULL; `base:allocation` + `qemu:dirty-bitmap` for incremental), extent filtering (allocated only for FULL; dirty∩allocated for incremental), and `zero_skip` (True for FULL, False for incremental).

#### Scenario: qemu-img info shows the backing chain (incremental)
- **WHEN** an incremental transfer completes
- **THEN** `qemu-img info` on the delta shows `backing file: <previous_backup>.qcow2`
- **AND** the delta contains only dirty∩allocated blocks written via `pwrite`

#### Scenario: qemu-img info shows no backing file (FULL)
- **WHEN** a FULL transfer completes
- **THEN** `qemu-img info` on the target shows `backing file: <none>`
- **AND** the target contains all allocated blocks (zero blocks may be skipped via `zero_skip`)

#### Scenario: Restore resolves bitmap chains unchanged
- **WHEN** a backup chain (FULL + incrementals) is restored
- **THEN** the standard qcow2 backing-chain resolution produces the correct virtual disk content
- **AND** no special restore tool is needed (standard `qemu-img rebase -u` + chain copy)
