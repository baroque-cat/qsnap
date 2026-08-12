# Lifecycle Manager — delta

## MODIFIED Requirements

### Requirement: Blockcommit snapshots into a disk's base image

The system SHALL provide two lifecycle managers implementing `ILifecycleManager`, each accepting `IShell` as its sole constructor dependency. Managers are stateless workers: they MUST NOT inspect VM power state, MUST NOT decide deferral, and MUST NOT access the deferred-operations queue — Core performs all state detection, splitting, and deferral (adaptive fork).

`BlockCommitManager` is the **live executor**: Core invokes it only when the VM is running with `lifecycle_mode="virsh"` and only with snapshots that exclude the active layer. Its `blockcommit()` method SHALL accept keyword-only required arguments `disk: str` (the libvirt target device name, e.g. `"vda"`) and `base_image: Path` (the disk's base qcow2 path), and an additive keyword argument `timeout: int = 1800` (seconds). For each snapshot (oldest first) it SHALL run:

```
virsh blockcommit --domain <vm> --path {disk} --base {base_image} --top <snap.path> --delete --verbose --wait
```

via `IShell.run_with_heartbeat(cmd, timeout=timeout, heartbeat_seconds=60, on_heartbeat=<callback>)`. The heartbeat callback SHALL log an INFO line naming the VM, disk, snapshot, and elapsed seconds. The manager SHALL NOT use `IShell.run` for this command and SHALL NOT hard-code any timeout value.

The `--path` argument SHALL be the *disk* parameter (the libvirt target), NOT derived via `virsh domblklist` inside the manager. The `--base` argument SHALL be the *base_image* parameter (the disk's base image), NOT a VM-level base image. On any failure it SHALL short-circuit: remaining snapshots are NOT processed. When stderr matches MAC denial patterns (AppArmor: "Permission denied" / "apparmor"; SELinux: "Operation not permitted" / "AVC"), the module SHALL return `CommitResult(success=False, committed_snapshot="", error="blocked by apparmor|selinux", outcome="failure")` via the shared `detect_mac_denial` helper.

Outcome mapping: exit code 0 → `CommitResult(success=True, outcome="success")`; non-zero exit → `CommitResult(success=False, outcome="failure", error=<stderr>)`; timeout or killed process (shell error containing "timed out") → `CommitResult(success=False, outcome="unknown", error="Command timed out after {timeout}s")`. A timeout is an UNKNOWN outcome, never a definitive failure — reconciliation is Core's responsibility, not the manager's.

`QemuImgCommitManager` is the **offline executor**: Core invokes it only when the VM is shut off and only with snapshots that exclude the XML-referenced tip overlay. Its `blockcommit()` SHALL accept the same additive `timeout: int = 1800` keyword argument and use it for the `qemu-img commit` call (previously hard-coded 3600). For each snapshot (oldest first) it SHALL:

1. Run `qemu-img commit -b {base_image} {snap.path}` — merge into the base image.
2. Discover the child overlay by scanning the disk's snapshot directory (resolved via `vm_config.snapshot_dir_for(disk_cfg)` or the VM-level default) for `*.qcow2` files and matching `qemu-img info --output=json` backing-filename against `snap.path`.
3. If a child exists, pivot it via `qemu-img rebase -u -F qcow2 -b {base_image} {child}`.
4. Delete the committed file (`rm -f {snap.path}`) — only after the pivot succeeded, or when no child exists.

The manager SHALL NOT rely on `qemu-img commit -d` for deletion (a no-op on QEMU 11.0.2). On any step failure it SHALL short-circuit: no deletion, no further iterations, returning `CommitResult(success=False, committed_snapshot=<failing name>, error=..., outcome="failure")` with the chain left consistent for safe retry. A `qemu-img commit` timeout SHALL map to `outcome="unknown"` with the same semantics as the live executor.

The `blockcommit()` method SHALL accept an optional keyword argument `deep_verify: bool = False`. When `True` and the commit succeeds, the manager SHALL call `deep_verify_base_image(self._shell, base_image)` — a shared verification helper that runs `qemu-img check --output=json` on the *disk's* base image, parses corruptions/errors/leaks, and returns a `CommitResult` on failure or `None` on success. The helper's internal `shell.run()` call SHALL NOT pass `check=True`.

#### Scenario: Successful live blockcommit of a single snapshot

- **WHEN** `virsh blockcommit --domain <vm> --path <disk> --base <base_image> --top <snap> --delete --verbose --wait` returns exit code 0
- **THEN** `BlockCommitManager` returns `CommitResult(success=True, committed_snapshot=<snapshot.name>, outcome="success")`

#### Scenario: Live blockcommit fails — virsh returns error

- **WHEN** `virsh blockcommit` returns a non-zero exit code
- **THEN** `CommitResult(success=False, outcome="failure", error=<stderr from virsh>)` is returned

#### Scenario: Blockcommit blocked by AppArmor or SELinux

- **WHEN** stderr of either manager matches MAC denial patterns (detected via `detect_mac_denial` shared helper)
- **THEN** `CommitResult(success=False, committed_snapshot="", error="blocked by apparmor|selinux", outcome="failure")` is returned

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
- **AND** `CommitResult(success=False, committed_snapshot=<failing name>, error=..., outcome="failure")` is returned

#### Scenario: Empty snapshot list — nothing to merge

- **WHEN** `blockcommit()` is called with an empty list on either manager
- **THEN** `CommitResult(success=True, committed_snapshot="", outcome="success")` is returned immediately

#### Scenario: Blockcommit times out — unknown outcome

- **WHEN** `virsh blockcommit` exceeds the injected timeout (default 1800 seconds)
- **THEN** the module returns `CommitResult(success=False, outcome="unknown")` with error containing "timed out"
- **AND** the result is NOT classified as a definitive failure by the manager

#### Scenario: Injected timeout is honored

- **WHEN** `blockcommit(timeout=600)` is called and the shell records the command
- **THEN** `run_with_heartbeat` is invoked with `timeout=600`

#### Scenario: Heartbeat callback invoked during the wait

- **WHEN** the shell simulates a live commit lasting longer than one heartbeat interval
- **THEN** the `on_heartbeat` callback is invoked with increasing elapsed values and logs name the VM, disk, and snapshot

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
