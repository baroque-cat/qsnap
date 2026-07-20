## Why

A manual smoke test (`qsnap run` on a live VM with `preserve = "all"`) plus follow-up empirical investigation on libvirt 12.5.0 / QEMU 11.0.2 uncovered a cluster of pre-existing defects in the snapshot lifecycle path:

1. **Silent data loss via `_parse_preserve("all")`**: When a user sets `snapshot_preserve = "all"` or `target_preserve = "all"` without explicitly setting the corresponding `preserve_min`, the function falls through to `effective_min = "0h"` and the regex parser fails to match `"all"`, producing `RetentionPolicy(0,0,0,0,0, preserve_min="0h")` — a "keep nothing" policy. The retention engine then deletes **all** backups and (attempts to delete) **all** snapshots. Users who explicitly configured "keep everything" lose everything instead.

2. **`virsh blockcommit` cannot commit the active layer of a running VM**: `Core._blockcommit_snapshots()` calls the lifecycle manager without checking VM state. When the retention remove set contains the active layer (which Bug #1 makes likely), `virsh blockcommit` fails with `error: commit of 'vda' active layer requires active flag`.

3. **`virsh blockcommit` cannot run on a shut-off VM at all** (empirically confirmed): libvirt's QEMU driver implements blockcommit via block jobs, which require a running QEMU process. On an inactive domain every attempt fails with `error: Requested operation is not valid: domain is not running`. Consequences:
   - The pre-existing deferred-operations queue (reasons `"apparmor"`/`"selinux"`) can **never drain** in the default `lifecycle_mode = "virsh"`: `_check_deferred_operations()` only executes when the VM is shut off, and in virsh mode that is exactly when virsh blockcommit cannot work. Deferred entries retry and fail forever.
   - A naive "defer everything while the VM is running" guard (the first iteration of this change) fixes the crash but **regresses** the previously-working live-commit path: on `main`, `virsh blockcommit` of *non-active* layers on a running VM works fine and is the standard way chains get shortened.

4. **`qemu-img commit -d` is a no-op on QEMU 11.0.2** (empirically confirmed): `QemuImgCommitManager` relies on `qemu-img commit -b <base> -d <snap>`, but the committed overlay file is **not deleted** and the backing pointer of any child overlay is **not pivoted** to the base image. The chain never shortens, committed files accumulate as orphans in `snapshot_dir`, post-commit chain-length verification reports a false-positive CRITICAL, and state entries for committed snapshots are never cleaned (the file still exists, so the stale-state guard never fires).

Bugs #1 and #2 interact destructively (remove-everything policy → active layer in remove set → live blockcommit error). Bugs #3 and #4 mean neither lifecycle mode currently provides a working end-to-end commit path across VM power states.

## What Changes

- **Fix `_parse_preserve("all")`** (unchanged from first iteration, already implemented): map `preserve_str == "all"` to `preserve_min = "all"` in the `effective_min` cascade and early-return guard.
- **Replace the naive VM-state guard with an adaptive lifecycle fork in Core**: Core decides *per run* which commit mechanism is safe and splits the remove set accordingly:
  - VM **running** + `lifecycle_mode = "virsh"` (default, now adaptive): commit the **non-active prefix** live via `virsh blockcommit` (restores `main` behavior; libvirt pivots children and deletes files); defer only the **active layer** with reason `"vm_running"`.
  - VM **running** + `lifecycle_mode = "qemu-img"`: defer everything with reason `"vm_running"` (`qemu-img commit` on a running VM is unsafe — it writes into the base image while the guest has the chain open).
  - VM **shut off** (either mode): commit offline via `qemu-img commit`, **excluding the XML-referenced tip overlay** (the file the inactive domain XML points to — deleting it would break the domain). The excluded tip is deferred with reason `"active_layer"`.
  - VM **paused** or any other non-running state: defer everything with reason `"vm_running"`.
  - `virsh domstate` failure: non-fatal fallback to the configured mode and the full remove set (previous `main` behavior).
- **Fix `QemuImgCommitManager` offline algorithm**: per committed snapshot (oldest first) — `qemu-img commit -b <base> <snap>`, then pivot the child overlay via `qemu-img rebase -u -F qcow2 -b <base> <child>` (metadata-only), then delete the committed file explicitly (`rm -f`). This replaces reliance on the no-op `-d` flag and makes offline commits actually shorten the chain while keeping every surviving overlay's view intact. MAC denial detection (AppArmor/SELinux) is added for parity with `BlockCommitManager` (shared helper in `qsnap/utils/`).
- **Unconditional state cleanup**: after any successful commit (main path or deferred drain), remove the committed snapshots from `IStateManager` regardless of `chain_verify_after_commit`. Previously state entries were removed only inside the post-commit verification branch, leaking stale entries into backup steps (rsync "file not found" warnings; with Bug #4 — infinite re-commits).
- **Adaptive drain of the deferred queue** (`_check_deferred_operations()`): the executor is chosen by *current* VM state, not by `vm_config.lifecycle_mode` alone:
  - Shut off → execute via `QemuImgCommitManager`, excluding the XML-tip; partially drainable entries commit their non-tip part and re-queue the remainder. This also makes the pre-existing `"apparmor"`/`"selinux"` queue drainable for the first time.
  - Running + virsh mode → execute entries whose snapshots are all non-active via `BlockCommitManager` (a formerly-active deferred layer becomes committable once a newer snapshot exists above it).
  - Running + qemu-img mode, or paused → skip (unchanged conservative behavior).

## Capabilities

### New Capabilities

(none — no new capabilities are introduced)

### Modified Capabilities

- `retention-engine`: The `TimeBasedRetention` engine already handles `preserve_min = "all"` correctly. The change is in `Core._parse_preserve()` which produces the `RetentionPolicy` — it must map `preserve_str = "all"` to `preserve_min = "all"` instead of `"0h"`. *(Already covered by the first iteration's delta spec; unchanged.)*
- `preserve-min-config`: `_parse_preserve()` must correctly handle `"all"` as a `preserve_str` value. *(Unchanged from first iteration.)*
- `core-orchestrator`: Major revision. `_blockcommit_snapshots()` gains the adaptive fork (state detection, active-layer detection via `virsh domblklist`, committable/deferrable split, executor selection, unconditional state cleanup). Semantics of `lifecycle_mode = "virsh"` become "adaptive" (live when running, qemu-img when shut off); `"qemu-img"` remains offline-only.
- `lifecycle-manager`: `BlockCommitManager` is documented as the live-commit executor (Core guarantees it never sees the active layer or a shut-off VM). `QemuImgCommitManager` gains the correct offline algorithm (commit → rebase → explicit delete) and MAC denial detection.
- `deferred-operations`: New known reason value `"active_layer"`. `_check_deferred_operations()` becomes state-adaptive with partial draining; remainder entries are re-queued with their original reason.

## Impact

**Affected code:**
- `qsnap/core/__init__.py`:
  - `_parse_preserve()` — `"all"` handling (done, ~2 lines).
  - `_blockcommit_snapshots()` — replace the simple domstate guard with the fork: domstate + domblklist active-layer detection, split into committable/deferrable, executor selection, unconditional `remove_snapshot` for committed snapshots (~40-60 lines net change).
  - `_check_deferred_operations()` — state-adaptive executor selection and partial draining (~30 lines net change).
- `qsnap/modules/lifecycle/qemu_img_commit.py` — new per-snapshot algorithm (commit → child discovery → rebase → rm), MAC detection (~40 lines net change).
- `qsnap/utils/` — shared MAC-denial detection helper (moved/duplicated from `blockcommit_manager.py`, ~30 lines; `BlockCommitManager` keeps its behavior).
- `tests/` — new and revised unit + integration tests per `test-plan.md`.
- `qsnap.toml.example`, `README.md` — document the adaptive semantics of `lifecycle_mode` and the XML-tip exclusion rule.

**Affected ABCs:** None. `ILifecycleManager.blockcommit()` signature is unchanged; both call sites in Core keep using `factory.create_lifecycle_manager(mode=...)` — Core merely computes the effective mode string. No `IRetentionEngine`, `IStateManager`, `IVMModuleFactory` signature changes.

**Affected specs:** `retention-engine`, `preserve-min-config`, `core-orchestrator`, `lifecycle-manager`, `deferred-operations`.

**Behavior compatibility:**
- `lifecycle_mode = "virsh"` users on running VMs: live commits of non-active snapshots keep working (as on `main`); active-layer commits no longer hard-fail — they are deferred and drained later. On shut-off VMs, commits now work (via qemu-img) instead of failing with `domain is not running`.
- `lifecycle_mode = "qemu-img"` users: no more writes to the base image while the VM runs (previously unsafe); commits happen when the VM is shut off, as before — but now actually shorten the chain (Bug #4 fix).
- No config migration needed. `virsh domstate` and `virsh domblklist` are already used elsewhere in Core.

**Dependencies:** No new runtime dependencies.
