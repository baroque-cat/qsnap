# lifecycle-manager — Delta Spec

## MODIFIED Requirements

### Requirement: Blockcommit snapshots into base image

The system SHALL provide two lifecycle managers implementing `ILifecycleManager`, selected by Core through the factory. Managers are stateless workers: they MUST NOT inspect VM power state, MUST NOT decide deferral, and MUST NOT access the deferred-operations queue — Core performs all state detection, splitting, and deferral (adaptive fork, see `core-orchestrator`).

`BlockCommitManager` is the **live executor**: Core invokes it only when the VM is running and only with snapshots that exclude the active layer. It SHALL merge each snapshot (oldest first) via `virsh blockcommit --domain <vm> --path <disk> --base <base> --top <snap> --delete --verbose --wait`, resolving the disk target via `virsh domblklist`. libvirt internally pivots child overlays and deletes committed files. It SHALL NOT be invoked on a shut-off domain (libvirt cannot run blockcommit offline — empirically `error: domain is not running`). When blockcommit is blocked by AppArmor (stderr contains "Permission denied" or "apparmor") or SELinux (stderr contains "Operation not permitted" or "AVC"), the module SHALL return `CommitResult(success=False, committed_snapshot="", error="blocked by apparmor|selinux")`.

`QemuImgCommitManager` is the **offline executor**: Core invokes it only when the VM is shut off and only with snapshots that exclude the XML-referenced tip overlay. For each snapshot `si` (oldest first) it SHALL:
1. Run `qemu-img commit -b <base_image> <si.path>` — merge into the base image (never into a kept overlay).
2. Discover the child overlay by scanning `vm_config.snapshot_dir` for `*.qcow2` files and matching `qemu-img info --output=json` backing-filename against `si.path` (linear chain — at most one child).
3. If a child exists, pivot it via `qemu-img rebase -u -F qcow2 -b <base_image> <child>` (metadata-only; safe because `si`'s data is now contained in the base image).
4. Delete the committed file (`rm -f <si.path>`) — only after the pivot succeeded, or when no child exists.

The manager SHALL NOT rely on `qemu-img commit -d` for deletion (a no-op on QEMU 11.0.2 — the file survives and the chain never shortens). On any step failure it SHALL short-circuit: no deletion, no further iterations, returning `CommitResult(success=False, committed_snapshot=<failing snapshot>, error=...)` with the chain left consistent for safe retry. MAC denial detection (AppArmor/SELinux) SHALL apply as for `BlockCommitManager`, via a shared helper in `qsnap/utils/`.

The `blockcommit()` method SHALL accept an optional keyword argument `deep_verify: bool = False`. When `True` and the commit succeeds, the manager SHALL call `deep_verify_base_image(self._shell, vm_config.base_image)` — a shared verification helper in `qsnap/utils/verification.py`. Both `BlockCommitManager` and `QemuImgCommitManager` SHALL use the same shared helper instead of inline `qemu-img check` implementations. The helper SHALL run `qemu-img check --output=json`, parse corruptions/errors/leaks, and return a `CommitResult` on failure or `None` on success. The `shell.run()` call inside the helper SHALL NOT pass `check=True` — the helper inspects `chk.success` directly, consistent with result-object error handling.

#### Scenario: Successful live blockcommit of a single snapshot

- **WHEN** `virsh blockcommit --domain <vm> --path <disk> --base <base> --top <snap> --delete --verbose --wait` returns exit code 0
- **THEN** `BlockCommitManager` returns `CommitResult(success=True, committed_snapshot=<snapshot.name>)`

#### Scenario: Live blockcommit fails — virsh returns error

- **WHEN** `virsh blockcommit` returns a non-zero exit code
- **THEN** `CommitResult(success=False, error=<stderr from virsh>)` is returned

#### Scenario: Blockcommit blocked by AppArmor or SELinux

- **WHEN** stderr of either manager contains the MAC denial patterns
- **THEN** `CommitResult(success=False, committed_snapshot="", error="blocked by apparmor|selinux")` is returned

#### Scenario: Offline commit pivots child and deletes file

- **WHEN** `QemuImgCommitManager` commits snapshot `s1` whose child `s2` exists
- **THEN** it runs `qemu-img commit -b <base> <s1>`, then `qemu-img rebase -u -F qcow2 -b <base> <s2>`, then deletes `s1`
- **AND** `s2`'s backing points to the base image afterwards (chain shortened, no dangling reference)

#### Scenario: Offline commit of chain tip-of-subset without child

- **WHEN** the committed snapshot has no child overlay in `snapshot_dir`
- **THEN** the rebase step is skipped and the file is deleted after a successful commit

#### Scenario: Offline commit failure short-circuits safely

- **WHEN** `qemu-img commit` or `qemu-img rebase` fails for a snapshot
- **THEN** the failing file is NOT deleted, subsequent snapshots are NOT processed
- **AND** `CommitResult(success=False, committed_snapshot=<failing name>, error=...)` is returned

#### Scenario: Empty snapshot list — nothing to merge

- **WHEN** `blockcommit()` is called with an empty list on either manager
- **THEN** `CommitResult(success=True, committed_snapshot="")` is returned immediately

#### Scenario: Blockcommit times out

- **WHEN** `virsh blockcommit` exceeds the timeout (3600 seconds for large disks)
- **THEN** the module returns `CommitResult(success=False)` with error containing "timed out"

#### Scenario: Successful blockcommit with deep verify passing

- **WHEN** `blockcommit(deep_verify=True)` succeeds
- **AND** `deep_verify_base_image(self._shell, vm_config.base_image)` returns `None` (no corruption)
- **THEN** `CommitResult(success=True)` is returned

#### Scenario: Successful blockcommit but deep verify fails

- **WHEN** `blockcommit(deep_verify=True)` succeeds
- **BUT** `deep_verify_base_image()` returns `CommitResult(success=False, error="deep verify: 5 corruptions in base image")`
- **THEN** that `CommitResult` is returned directly from `blockcommit()`

#### Scenario: deep_verify=False — no check performed

- **WHEN** `blockcommit(deep_verify=False)` or `deep_verify` is omitted
- **THEN** `deep_verify_base_image()` is NOT called

#### Scenario: Deep verify qemu-img check fails gracefully (no crash)

- **WHEN** `deep_verify_base_image()` runs `qemu-img check` and the command exits non-zero
- **THEN** the shell returns `ShellResult(success=False)` (no exception)
- **AND** the helper returns `CommitResult(success=False, error="deep verify: qemu-img check failed: ...")`
- **AND** the manager does NOT crash
