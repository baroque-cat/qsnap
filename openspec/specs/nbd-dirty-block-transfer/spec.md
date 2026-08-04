# NBD Dirty Block Transfer

## Purpose

In-process dirty-block transfer engine for bitmap-mode incrementals: negotiates NBD meta-contexts (`base:allocation` + `qemu:dirty-bitmap:backup-<disk>`) over the libvirt pull-model export, copies only dirty∩allocated extents via the `INbdClient` abstraction, and writes them into a backing-chained qcow2 delta served by a forked `qemu-nbd`. Per-disk scoping ensures each disk's incremental chain is independent — previous-backup resolution filters candidates by disk.

## Requirements

### Requirement: INbdClient abstraction for NBD transport

The system SHALL define an `INbdClient` ABC in `qsnap/interfaces/nbd.py` as the sole interface through which NBD protocol operations are performed. The interface SHALL expose: `connect(uri, export_name, meta_contexts) -> NbdResult`, `get_size() -> int`, `get_max_request_size() -> int`, `block_status(offset, length) -> NbdResult`, `pread(offset, length) -> NbdResult`, `pwrite(offset, data) -> NbdResult`, `can_flush() -> bool`, `flush() -> NbdResult`, and `disconnect() -> None`. All fallible methods SHALL return `NbdResult` (success/payload/error) and SHALL NOT raise exceptions for expected failures. Error strings SHALL be normalized so transient conditions map to retryable patterns ("eof", "timed out", "broken pipe", "connection refused"). `NbdExtent` (offset, length, data) and `NbdResult` (success, payload, error) SHALL be frozen dataclasses in `qsnap/models/results.py`.

#### Scenario: Connection failure returns result object

- **WHEN** the NBD server socket does not exist or refuses the connection
- **THEN** `connect()` returns `NbdResult(success=False, error=...)` with a normalized error string
- **AND** no exception propagates to the caller

#### Scenario: Read error normalized for retry

- **WHEN** the NBD server closes the connection mid-transfer
- **THEN** `pread()` returns `NbdResult(success=False, ...)` whose error string contains "eof" or "broken pipe" (case-insensitive)
- **AND** Core retry classification treats it as retryable


### Requirement: LibnbdClient production implementation

`LibnbdClient` in `qsnap/utils/nbd_client.py` SHALL implement `INbdClient` using the system `python3-libnbd` package. The `import nbd` SHALL be lazy, so importing qsnap never requires libnbd. When the package is missing, `connect()` SHALL return `NbdResult(success=False)` with an actionable error naming the system package. The client SHALL request exactly the meta-contexts passed to `connect()` (e.g. `base:allocation` and `qemu:dirty-bitmap:backup-<disk>`), SHALL honor the server-advertised maximum request size, and SHALL split reads/writes larger than that size into sequential chunks.

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

Extent unification and dirty∩allocated intersection SHALL be implemented as pure, deterministic, I/O-free functions in `qsnap/utils/extents.py`: `unify_extents(extents) -> list[NbdExtent]` SHALL merge consecutive extents of the same kind, and `overlap_with_allocation(dirty, allocated) -> list[NbdExtent]` SHALL return only dirty regions that are also allocated. Neither function SHALL perform I/O, access global state, or depend on wall-clock time.

#### Scenario: Consecutive same-kind extents are unified

- **WHEN** the extent list contains adjacent dirty extents
- **THEN** `unify_extents()` returns a single merged extent covering their combined range

#### Scenario: Dirty-but-unallocated regions are filtered

- **WHEN** a dirty extent overlaps a region that `base:allocation` reports as hole/zero
- **THEN** `overlap_with_allocation()` excludes the unallocated sub-range
- **AND** only dirty-and-allocated sub-ranges remain


### Requirement: Dirty-block copy loop for incrementals via _copy_dirty_blocks

`BitmapBackupProvider.transfer_missing()` SHALL transfer incremental data via `_copy_dirty_blocks()` using `INbdClient` instead of `qemu-img convert`. The method SHALL: (1) resolve the previous backup at the target by walking backwards through the backup list (sorted ascending by timestamp), filtering by `b.disk == disk_target` (per-disk scoping — multi-disk refactor), and selecting the newest backup for that disk with an intact backing chain, (2) create `<name>.qcow2.tmp` via `qemu-img create -f qcow2 -b <previous> -F qcow2` through `IShell`, (3) serve the `.tmp` file through a forked `qemu-nbd` with `--pid-file` and a per-disk write socket `/tmp/qsnap-write-{pid}-{disk}.sock`, (4) connect the source client to the libvirt socket requesting `base:allocation` and `qemu:dirty-bitmap:backup-<disk>` meta-contexts, (5) query block status, unify extents, and intersect with allocation, (6) `pread` each remaining dirty extent from the source and `pwrite` it to the destination, (7) disconnect both clients, terminate `qemu-nbd` via its pidfile, and (8) atomically `mv <name>.qcow2.tmp <name>.qcow2`. The provider SHALL receive the `INbdClient` as a constructor dependency.

The backwards walk in step (1) SHALL validate backing-chain integrity of each candidate via `qemu-img info --backing-chain --output=json` (which traverses the entire chain and fails if any file is missing). FULL backups (standalone files with no backing) SHALL always be considered valid and skip the chain validation. If no valid non-FULL backup is found, the walk SHALL fall back to the most recent FULL for that disk. If no valid backup of any kind is found for that disk, the transfer SHALL fail with an error message directing the user to run `qsnap check --deep` and `qsnap reconcile`.

#### Scenario: Incremental copies only dirty blocks

- **WHEN** a prior checkpoint exists and the guest wrote 100 MiB since then
- **THEN** the copy loop reads approximately 100 MiB from the source NBD export (not the full virtual disk size)
- **AND** the resulting qcow2 chains to the previous backup for the same disk

#### Scenario: First incremental chains to the FULL for same disk

- **WHEN** no previous incremental exists at the target for this disk
- **THEN** `_copy_dirty_blocks()` filters backups by disk and finds the FULL for that disk
- **AND** the `.tmp` file is created with `qemu-img create -b <FULL> -F qcow2`
- **AND** the final file's `backing-filename` names the FULL backup for the same disk

#### Scenario: Cross-disk chain prevention

- **WHEN** backup listings contain incrementals for both `vda` and `vdb`
- **AND** `_copy_dirty_blocks()` is called with `disk_target="vda"`
- **THEN** only backups with `b.disk == "vda"` are considered for previous-backup resolution
- **AND** the resulting delta chains to disk `vda`'s previous backup, never to `vdb`'s

#### Scenario: Previous backup vanished — retryable failure

- **WHEN** the previous backup file is deleted between listing and `qemu-img create`
- **THEN** the transfer returns `_CopyResult(error=...)` with an error class Core treats as retryable
- **AND** the standard failure path runs (partial cleanup, successor checkpoint deleted best-effort)

#### Scenario: Broken-chain newest backup skipped — walk to valid previous

- **WHEN** the newest backup at the target has a broken backing chain (its backing file was deleted)
- **THEN** the backwards walk skips the broken-chain file
- **AND** selects the next-newest backup for the same disk with an intact backing chain as `previous`
- **AND** a WARNING is logged for each skipped broken-chain file

#### Scenario: All non-FULL backups broken — fall back to FULL

- **WHEN** all non-FULL backups at the target for this disk have broken backing chains
- **AND** a FULL backup exists for this disk
- **THEN** the walk selects the FULL as `previous`
- **AND** the delta is created with `qemu-img create -b <FULL> -F qcow2`

#### Scenario: No valid backup found — error with guidance

- **WHEN** no backup at the target has an intact backing chain for this disk
- **THEN** the transfer returns a `_CopyResult` with an error message directing the user to run `qsnap check --deep` and `qsnap reconcile`


### Requirement: Backing-chain validation method for backup files

`BitmapBackupProvider` SHALL provide a `_validate_backing_chain(path: Path) -> bool` method that checks whether a backup file has an intact backing chain. The method SHALL run `qemu-img info --force-share --backing-chain --output=json <path>` via `IShell.run()` and return `True` if the command succeeds (exit code 0) and `False` otherwise. Standalone files (FULLs with no backing file) SHALL be considered valid (the command succeeds on standalone files). The method SHALL NOT raise exceptions — all failures return `False`.

#### Scenario: Valid backing chain returns True

- **WHEN** `_validate_backing_chain(path)` is called on a file whose entire backing chain is intact
- **THEN** the method returns `True`

#### Scenario: Broken backing chain returns False

- **WHEN** `_validate_backing_chain(path)` is called on a file whose backing file has been deleted
- **THEN** the method returns `False`

#### Scenario: Standalone FULL returns True

- **WHEN** `_validate_backing_chain(path)` is called on a FULL backup (no backing file)
- **THEN** the method returns `True`


### Requirement: Write-side lifecycle is crash-safe

The `.tmp` output, write socket, `qemu-nbd` process, and libvirt backup job SHALL be cleaned up on every outcome (success, transfer failure, verification failure, exception). Cleanup SHALL include: `virsh domjobabort` (WARNING-only on failure), `rm -f` of both sockets, temp XML removal, `qemu-nbd` termination via pidfile, and `.tmp` removal on failure paths. A crash that orphans the `qemu-nbd` process or write socket SHALL NOT affect subsequent runs (process-unique and disk-unique paths; stale-socket removal before use).

#### Scenario: Failure mid-copy cleans everything

- **WHEN** a `pread` fails after 50% of dirty blocks were copied
- **THEN** the destination client is disconnected, `qemu-nbd` is terminated, the `.tmp` file is removed, the backup job is aborted, and both sockets are removed
- **AND** the prior checkpoint remains the newest valid baseline

#### Scenario: Successful transfer leaves no artifacts

- **WHEN** the copy loop and verification complete successfully
- **THEN** no `.tmp` file, no write socket, and no `qemu-nbd` process remain
- **AND** the final qcow2 is present at the target path


### Requirement: In-process stall watchdog for the copy loop

The copy loop SHALL implement stall detection without `IShell`: a progress timestamp updated after every successful chunk write. If no chunk completes for `stall_timeout` seconds, the loop SHALL abort and the transfer SHALL return `error="Stall detected: no progress for {N}s"`. When `stall_timeout` is 0, the watchdog SHALL be disabled. The watchdog SHALL NOT spawn threads; progress is checked between chunk writes.

#### Scenario: Stall aborts the transfer

- **WHEN** no `pwrite` completes for `stall_timeout` seconds
- **THEN** the loop aborts, the failure path runs, and the error string is `"Stall detected: no progress for {N}s"`

#### Scenario: Slow but progressing transfer completes

- **WHEN** chunks complete steadily, each slower than usual but within `stall_timeout` cumulatively
- **THEN** the loop is not aborted and the transfer completes


### Requirement: Incremental output is a backing-chained COW delta

The unified NBD transfer engine SHALL produce backing-chained qcow2 deltas for incremental transfers and standalone qcow2 files for FULL transfers. For incrementals, the delta SHALL be created via `qemu-img create -f qcow2 -b <previous_backup> -F qcow2 <target>.tmp` and served by a forked `qemu-nbd` for uncompressed `pwrite`. For FULLs, the target SHALL be created via `qemu-img convert` (standalone, no backing). `qemu-img convert` is the sole FULL transfer engine; the `INbdClient` pread/pwrite engine is used only for incrementals with `meta_contexts=["base:allocation", "qemu:dirty-bitmap:backup-<disk>"]` and `zero_skip=False`.

#### Scenario: qemu-img info shows the backing chain (incremental)

- **WHEN** an incremental transfer completes
- **THEN** `qemu-img info` on the delta shows `backing file: <previous_backup>.qcow2`
- **AND** the delta contains only dirty∩allocated blocks written via `pwrite`

#### Scenario: qemu-img info shows no backing file (FULL)

- **WHEN** a FULL transfer completes via `qemu-img convert`
- **THEN** `qemu-img info` on the target shows `backing file: <none>`

#### Scenario: Restore resolves bitmap chains unchanged

- **WHEN** a backup chain (FULL + incrementals) is restored
- **THEN** the standard qcow2 backing-chain resolution produces the correct virtual disk content
- **AND** no special restore tool is needed
