# Lifecycle Manager

## Purpose

Backing chain lifecycle management via `virsh blockcommit` (live) and `qemu-img commit` (offline) — merges old qcow2 snapshots of a single disk back into that disk's base image to shorten the per-disk backing chain.

## Requirements

### Requirement: Blockcommit snapshots into a disk's base image

The system SHALL provide two lifecycle managers implementing `ILifecycleManager`, each accepting `IShell` as its sole constructor dependency. Managers are stateless workers: they MUST NOT inspect VM power state, MUST NOT decide deferral, and MUST NOT access the deferred-operations queue — Core performs all state detection, splitting, and deferral (adaptive fork).

`BlockCommitManager` is the **live executor**: Core invokes it only when the VM is running with `lifecycle_mode="virsh"` and only with snapshots that exclude the active layer. Its `blockcommit()` method SHALL accept keyword-only required arguments `disk: str` (the libvirt target device name, e.g. `"vda"`) and `base_image: Path` (the disk's base qcow2 path). For each snapshot (oldest first) it SHALL run:

```
virsh blockcommit --domain <vm> --path {disk} --base {base_image} --top <snap.path> --delete --verbose --wait
```

The `--path` argument SHALL be the *disk* parameter (the libvirt target), NOT derived via `virsh domblklist` inside the manager. The `--base` argument SHALL be the *base_image* parameter (the disk's base image), NOT a VM-level base image. On any failure it SHALL short-circuit: remaining snapshots are NOT processed. When stderr matches MAC denial patterns (AppArmor: "Permission denied" / "apparmor"; SELinux: "Operation not permitted" / "AVC"), the module SHALL return `CommitResult(success=False, committed_snapshot="", error="blocked by apparmor|selinux")` via the shared `detect_mac_denial` helper.

`QemuImgCommitManager` is the **offline executor**: Core invokes it only when the VM is shut off and only with snapshots that exclude the XML-referenced tip overlay. For each snapshot (oldest first) it SHALL:

1. Run `qemu-img commit -b {base_image} {snap.path}` — merge into the base image.
2. Discover the child overlay by scanning the disk's snapshot directory (resolved via `vm_config.snapshot_dir_for(disk_cfg)` or the VM-level default) for `*.qcow2` files and matching `qemu-img info --output=json` backing-filename against `snap.path`.
3. If a child exists, pivot it via `qemu-img rebase -u -F qcow2 -b {base_image} {child}`.
4. Delete the committed file (`rm -f {snap.path}`) — only after the pivot succeeded, or when no child exists.

The manager SHALL NOT rely on `qemu-img commit -d` for deletion (a no-op on QEMU 11.0.2). On any step failure it SHALL short-circuit: no deletion, no further iterations, returning `CommitResult(success=False, committed_snapshot=<failing name>, error=...)` with the chain left consistent for safe retry.

The `blockcommit()` method SHALL accept an optional keyword argument `deep_verify: bool = False`. When `True` and the commit succeeds, the manager SHALL call `deep_verify_base_image(self._shell, base_image)` — a shared verification helper that runs `qemu-img check --output=json` on the *disk's* base image, parses corruptions/errors/leaks, and returns a `CommitResult` on failure or `None` on success. The helper's internal `shell.run()` call SHALL NOT pass `check=True`.

#### Scenario: Successful live blockcommit of a single snapshot

- **WHEN** `virsh blockcommit --domain <vm> --path <disk> --base <base_image> --top <snap> --delete --verbose --wait` returns exit code 0
- **THEN** `BlockCommitManager` returns `CommitResult(success=True, committed_snapshot=<snapshot.name>)`

#### Scenario: Live blockcommit fails — virsh returns error

- **WHEN** `virsh blockcommit` returns a non-zero exit code
- **THEN** `CommitResult(success=False, error=<stderr from virsh>)` is returned

#### Scenario: Blockcommit blocked by AppArmor or SELinux

- **WHEN** stderr of either manager matches MAC denial patterns (detected via `detect_mac_denial` shared helper)
- **THEN** `CommitResult(success=False, committed_snapshot="", error="blocked by apparmor|selinux")` is returned

#### Scenario: Offline commit pivots child and deletes file

- **WHEN** `QemuImgCommitManager` commits snapshot `s1` whose child `s2` exists in the disk's snapshot directory
- **THEN** it runs `qemu-img commit -b <base_image> <s1>`, then `qemu-img rebase -u -F qcow2 -b <base_image> <s2>`, then deletes `s1`
- **AND** `s2`'s backing points to the disk's base image afterwards

#### Scenario: Offline commit of chain tip-of-subset without child

- **WHEN** the committed snapshot has no child overlay in the disk's snapshot directory
- **THEN** the rebase step is skipped and the file is deleted after a successful commit

#### Scenario: Offline commit failure short-circuits safely

- **WHEN** `qemu-img commit` or `qemu-img rebase` fails for a snapshot
- **THEN** the failing file is NOT deleted, subsequent snapshots are NOT processed
- **AND** `CommitResult(success=False, committed_snapshot=<failing name>, error=...)` is returned

#### Scenario: Empty snapshot list — nothing to merge

- **WHEN** `blockcommit()` is called with an empty list on either manager
- **THEN** `CommitResult(success=True, committed_snapshot="")` is returned immediately

#### Scenario: Blockcommit times out

- **WHEN** `virsh blockcommit` exceeds the timeout (3600 seconds)
- **THEN** the module returns `CommitResult(success=False)` with error containing "timed out"

#### Scenario: Successful blockcommit with deep verify passing

- **WHEN** `blockcommit(deep_verify=True)` succeeds
- **AND** `deep_verify_base_image(self._shell, base_image)` returns `None` (no corruption)
- **THEN** `CommitResult(success=True)` is returned

#### Scenario: Successful blockcommit but deep verify fails

- **WHEN** `blockcommit(deep_verify=True)` succeeds
- **BUT** `deep_verify_base_image()` returns `CommitResult(success=False, error="deep verify: 5 corruptions in base image")`
- **THEN** that `CommitResult` is returned directly from `blockcommit()`

#### Scenario: deep_verify=False — no check performed

- **WHEN** `blockcommit(deep_verify=False)` or `deep_verify` is omitted
- **THEN** `deep_verify_base_image()` is NOT called

#### Scenario: Deep verify on the disk's base image (not a VM-level base)

- **WHEN** `blockcommit()` is called with `base_image=/path/to/vm_vda.qcow2` and `deep_verify=True`
- **THEN** verification runs `qemu-img check` on `/path/to/vm_vda.qcow2` (the disk's base image)

### Requirement: Blockcommit of multiple snapshots

The system SHALL handle multiple snapshots for merging, executing blockcommit for each in order (nearest-to-base first). Each invocation SHALL use `--base {base_image}` (the disk's base) and `--top {snapshot.path}`.

#### Scenario: Two snapshots merged sequentially per disk

- **WHEN** `snapshots_to_merge` contains [snap1, snap2] for a single disk
- **THEN** blockcommit is executed for snap1 first (closest to base)
- **AND** then blockcommit for snap2
- **AND** if the first blockcommit fails, the second is NOT executed

### Requirement: QemuImgCommitManager scans per-disk snapshot directory

The `QemuImgCommitManager` SHALL resolve the child-discovery scan directory from the disk's own `snapshot_dir` override (via `DiskConfig`) or the VM-level default, rather than a single VM-level snapshot directory.

#### Scenario: Child discovered in disk-specific snapshot directory

- **WHEN** disk `vda` has `snapshot_dir = /data/vda_snaps` and `vdb` has `snapshot_dir = /data/vdb_snaps`
- **THEN** child discovery for `vda` snapshots scans `/data/vda_snaps`
- **AND** child discovery for `vdb` snapshots scans `/data/vdb_snaps`

### Requirement: QemuImgCommitManager child discovery filters by disk

`QemuImgCommitManager._find_child()` SHALL skip candidate files that clearly belong to a different disk before invoking `qemu-img info` on them. The disk target is extracted from the candidate file name via `parse_disk_from_snapshot_name()`; when it parses to a disk different from the snapshot's `disk`, the candidate is skipped. Candidates whose names do not parse (e.g. a base image or a non-standard file) SHALL still be inspected via `qemu-img info`, because they may legitimately be the child. This is a defense-in-depth guard for the case where two disks share one snapshot directory (which config validation separately rejects).

#### Scenario: Other-disk candidates skipped without qemu-img info

- **WHEN** the scan directory contains `vm.<ts>_vda_aaa111.qcow2` (true child) and `vm.<ts>_vdb_bbb222.qcow2`
- **AND** `_find_child` is resolving a child for a `vda` snapshot
- **THEN** `qemu-img info` is NOT invoked for the `_vdb_` file
- **AND** the `_vda_` child is returned

#### Scenario: Unparseable candidate names still inspected

- **WHEN** the scan directory contains a file whose name does not encode a disk (e.g. `base.qcow2`)
- **THEN** `_find_child` still runs `qemu-img info` on it to check its backing file

### Requirement: Factory selectable lifecycle manager

`DefaultFactory.create_lifecycle_manager()` SHALL accept an optional `mode: str = "virsh"` parameter. When `mode == "qemu-img"`, it SHALL return `QemuImgCommitManager`. When `mode == "virsh"` (default), it SHALL return `BlockCommitManager`.

#### Scenario: Default mode returns BlockCommitManager

- **WHEN** `factory.create_lifecycle_manager()` is called without mode
- **THEN** a `BlockCommitManager` instance is returned
