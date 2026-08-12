"""Integration tests for blockcommit race-hardening on REAL libvirt.

Validates against a disposable test VM (``test_vm`` fixture) what the
mock-based suites can only approximate:

- ``_probe_blockjob`` parses REAL libvirt idle-disk output as ``"none"``
- A REAL ``virsh blockcommit --wait`` via Core + DefaultFactory +
  SubprocessShell maps to ``CommitResult(outcome="success")``; the
  merge-set file is deleted, the chain is shortened, the intent journal
  record is present during the call and cleared after, and
  ``last_commit_ts`` is written
- The intent journal (``JsonStateManager``) is durable: a fresh
  manager re-instantiated from the same state file mid-run returns the
  identical record
- Stale-intent recovery (pipeline step 0) converges state against REAL
  filesystem + REAL ``qemu-img info --backing-chain`` evidence
  (``late_success``: WARNING, ``last_commit_ts``/``remove_snapshot``
  convergence, intent cleared, VM not failed)
- A genuinely active foreign ``virsh blockcommit`` (throttled with
  ``--bandwidth``) is classified ``"active"`` by the probe; qsnap defers
  the disk (reason ``blockjob_active``), starts NO second commit, never
  issues ``virsh blockjob --abort``, and the VM pipeline continues

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  Run only when explicitly requested::

    poetry run pytest tests/integration/test_commit_intent_recovery.py -v -m integration
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import DiskConfig, GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import CommitIntent, RetentionResult, SnapshotInfo
from qsnap.modules.lifecycle.blockcommit_manager import BlockCommitManager
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.state.json_manager import JsonStateManager
from tests.mocks.mock_config import MockConfigFacade

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _build_core(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
    snapshot_dir: Path,
    target_dir: Path,
    state_dir: Path,
    lifecycle_mode: str = "virsh",
) -> tuple[Core, VMConfig, JsonStateManager]:
    """Build a Core instance with a JSON-backed state manager (temp dir).

    The JSON state manager gives the intent journal real file durability
    — a fresh manager re-instantiated from the same ``state_dir`` reads
    the same records.
    """
    state = JsonStateManager(state_dir)
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=24,
        lifecycle_mode=lifecycle_mode,
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
            )
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(),
        vms=[vm_config],
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core, vm_config, state


def _start_vm(shell: SubprocessShell, vm_name: str) -> None:
    """Start the test VM or skip when libvirt is not usable."""
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"


def _vm_is_running(shell: SubprocessShell, vm_name: str) -> bool:
    """Return True if the VM is running."""
    result = shell.run(["virsh", "domstate", "--domain", vm_name], timeout=30)
    return result.success and "running" in result.stdout.lower()


def _create_snapshots(core: Core, vm_config: VMConfig, count: int) -> list[SnapshotInfo]:
    """Create *count* external snapshots via Core and return them oldest-first."""
    for i in range(count):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(1.1)
    return sorted(core._state.get_snapshots(vm_config.name), key=lambda s: s.timestamp)


def _backing_chain_length(tip_path: Path, shell: SubprocessShell) -> int | None:
    """Return the backing chain length from *tip_path*, or None on failure."""
    result = shell.run(
        [
            "qemu-img",
            "info",
            "--force-share",
            "--backing-chain",
            "--output=json",
            str(tip_path),
        ],
        timeout=30,
    )
    if not result.success:
        return None
    chain = json.loads(result.stdout)
    return len(chain) if isinstance(chain, list) else None


def _wrap_lifecycle_manager(core: Core, shell: SubprocessShell, spy) -> None:
    """Patch the factory so Core's next ``create_lifecycle_manager("virsh")``
    returns a manager whose ``blockcommit`` runs *spy* around the real one.

    The spy signature is ``spy(vm_config, snapshots, *, disk, base_image,
    deep_verify, timeout) -> CommitResult`` — it must call through to the
    real manager to actually perform the commit.
    """
    factory: DefaultFactory = core._factory  # type: ignore[attr-defined]
    real_manager = factory.create_lifecycle_manager("virsh")
    assert isinstance(real_manager, BlockCommitManager), type(real_manager)

    orig_blockcommit = real_manager.blockcommit
    real_manager.blockcommit = (  # type: ignore[method-assign]
        lambda vm_cfg, snaps, *, disk, base_image, deep_verify, timeout: spy(
            vm_cfg,
            snaps,
            disk=disk,
            base_image=base_image,
            deep_verify=deep_verify,
            timeout=timeout,
            _real=orig_blockcommit,
        )
    )

    orig_create = factory.create_lifecycle_manager

    def _create(mode: str = "virsh"):
        if mode == "virsh":
            return real_manager
        return orig_create(mode)

    patch.object(factory, "create_lifecycle_manager", side_effect=_create).start()


# ──────────────────────────────────────────────────────────────────────
# Test 1: Real probe on an idle disk classifies "none"
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_real_probe_idle_disk_returns_none(test_vm):
    """Real ``virsh blockjob`` on an idle disk parses as ``"none"``.

    The mock-based suites script ``"No current block job\\n"``; this test
    proves the parser handles REAL libvirt output (wording, trailing
    newline, locale).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    _start_vm(shell, vm_name)
    core, vm_config, _state = _build_core(
        shell, vm_name, base_image, snapshot_dir, target_dir, tmpdir / "state"
    )

    raw = shell.run(["virsh", "blockjob", "--domain", vm_name, "--path", "vda"], timeout=30)
    assert raw.success, f"virsh blockjob probe failed: {raw.error}"
    assert "No current block job" in raw.stdout or not raw.stdout.strip(), (
        f"Idle disk must report no block job, got stdout={raw.stdout!r}"
    )

    assert core._probe_blockjob(vm_config, "vda") == "none", (
        "Core's probe parser must classify real idle-disk output as 'none'"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 2: Real blockcommit produces outcome="success"
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_real_blockcommit_produces_success_outcome(test_vm):
    """A REAL live blockcommit via Core maps to ``outcome="success"``.

    1. Start VM, create 2 snapshots.
    2. Commit the oldest via ``core._blockcommit_snapshots`` with
       DefaultFactory + SubprocessShell.
    3. Assert: outcome ``"success"`` (observed via the manager result);
       merge-set file deleted; chain shortened; intent record present
       during the call and cleared after; ``last_commit_ts`` written.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    _start_vm(shell, vm_name)
    core, vm_config, state = _build_core(
        shell, vm_name, base_image, snapshot_dir, target_dir, tmpdir / "state"
    )

    snapshots = _create_snapshots(core, vm_config, 2)
    s1, s2 = snapshots[0], snapshots[1]
    assert s1.path.exists() and s2.path.exists()

    chain_len_before = _backing_chain_length(s2.path, shell)
    assert chain_len_before is not None and chain_len_before >= 2

    observed: dict[str, object] = {}

    def _spy(
        vm_cfg: VMConfig,
        snaps: list[SnapshotInfo],
        *,
        disk: str,
        base_image: Path,
        deep_verify: bool,
        timeout: int,
        _real: object,
    ) -> object:
        # Mid-call: the intent record must be visible right now.
        observed["intent_during_call"] = list(state.get_commit_in_progress(vm_name))
        result = _real(  # type: ignore[operator]
            vm_cfg,
            snaps,
            disk=disk,
            base_image=base_image,
            deep_verify=deep_verify,
            timeout=timeout,
        )
        observed["outcome"] = str(result.outcome)
        return result

    _wrap_lifecycle_manager(core, shell, _spy)

    retention = RetentionResult(keep=[s2.name], remove=[s1.name])
    core._blockcommit_snapshots(vm_config, retention)

    # (a) The real manager's CommitResult outcome was "success".
    assert observed.get("outcome") == "success", (
        f"A real virsh blockcommit --wait must classify outcome='success', "
        f"got {observed.get('outcome')!r}"
    )

    # (b) Intent present during the call, with the exact merge set.
    mid_intents = observed.get("intent_during_call")
    assert isinstance(mid_intents, list) and len(mid_intents) == 1, (
        f"Expected exactly one intent record during the commit, got {mid_intents!r}"
    )
    mid: CommitIntent = mid_intents[0]
    assert mid.disk == "vda", mid
    assert mid.snapshots == [s1.name], mid
    assert mid.base == str(base_image), mid

    # (c) Intent cleared after the run.
    assert state.get_commit_in_progress(vm_name) == [], (
        "Intent journal must be empty after a successful commit"
    )

    # (d) Merge-set file deleted by the real commit.
    assert not s1.path.exists(), f"s1 must be deleted by the real commit: {s1.path}"

    # (e) Chain shortened (3 → 2) and tip repointed at the base image.
    chain_len_after = _backing_chain_length(s2.path, shell)
    assert chain_len_after is not None and chain_len_after < chain_len_before, (
        f"Chain must shorten: before={chain_len_before}, after={chain_len_after}"
    )

    # (f) last_commit_ts written for the disk.
    assert state.get_last_commit_ts(vm_name, "vda") is not None, (
        "last_commit_ts must be written after a successful commit"
    )

    # (g) State converged: s1 removed, s2 remains.
    remaining_names = {s.name for s in state.get_snapshots(vm_name)}
    assert s1.name not in remaining_names and s2.name in remaining_names, remaining_names

    # (h) VM still running.
    assert _vm_is_running(shell, vm_name), "VM should still be running"


# ──────────────────────────────────────────────────────────────────────
# Test 3: Intent journal survives a real run (JsonStateManager durability)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_intent_journal_survives_real_run(test_vm):
    """The intent journal round-trips through REAL state files.

    Drive a real commit with ``JsonStateManager``; mid-run (inside the
    lifecycle-manager call) assert that ``get_commit_in_progress`` returns
    the record BOTH on the live manager and on a fresh manager
    re-instantiated from the same state file; after the run the record is
    cleared on both.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]
    state_dir = tmpdir / "state"

    _start_vm(shell, vm_name)
    core, vm_config, state = _build_core(
        shell, vm_name, base_image, snapshot_dir, target_dir, state_dir
    )

    snapshots = _create_snapshots(core, vm_config, 2)
    s1, s2 = snapshots[0], snapshots[1]

    observed: dict[str, object] = {}

    def _spy(
        vm_cfg: VMConfig,
        snaps: list[SnapshotInfo],
        *,
        disk: str,
        base_image: Path,
        deep_verify: bool,
        timeout: int,
        _real: object,
    ) -> object:
        live = list(state.get_commit_in_progress(vm_name))
        fresh = JsonStateManager(state_dir).get_commit_in_progress(vm_name)
        observed["live"] = live
        observed["fresh"] = fresh
        return _real(  # type: ignore[operator]
            vm_cfg,
            snaps,
            disk=disk,
            base_image=base_image,
            deep_verify=deep_verify,
            timeout=timeout,
        )

    _wrap_lifecycle_manager(core, shell, _spy)

    retention = RetentionResult(keep=[s2.name], remove=[s1.name])
    core._blockcommit_snapshots(vm_config, retention)

    live: list[CommitIntent] = observed.get("live")  # type: ignore[assignment]
    fresh: list[CommitIntent] = observed.get("fresh")  # type: ignore[assignment]
    assert isinstance(live, list) and len(live) == 1, f"Live manager must see the intent: {live!r}"
    assert isinstance(fresh, list) and len(fresh) == 1, (
        f"A fresh manager from the same state file must see the intent: {fresh!r}"
    )
    assert live == fresh, f"Re-instantiated record must be identical: live={live} fresh={fresh}"
    assert fresh[0].disk == "vda"
    assert fresh[0].snapshots == [s1.name]
    assert fresh[0].base == str(base_image)

    # After the run: cleared on the live manager AND on a fresh manager.
    assert state.get_commit_in_progress(vm_name) == [], (
        "Live manager: intent must be cleared after the run"
    )
    assert JsonStateManager(state_dir).get_commit_in_progress(vm_name) == [], (
        "Fresh manager: intent must be cleared after the run"
    )

    # Sanity: the commit really happened.
    assert not s1.path.exists(), "s1 must be deleted by the real commit"
    assert state.get_last_commit_ts(vm_name, "vda") is not None


# ──────────────────────────────────────────────────────────────────────
# Test 4: Stale intent real recovery converges state
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_stale_intent_real_recovery_converges_state(test_vm, caplog):
    """Step-0 stale-intent recovery against REAL filesystem + qemu-img.

    1. Start VM, create 2 snapshots; commit the oldest for real (file
       deleted, chain shortened, state A converged).
    2. Plant a STALE intent in a fresh state B: the merge-set snapshot
       record is present but its file was deleted by the real commit.
    3. Run pipeline step 0 (``_recover_commit_intents``) on state B.
    4. Assert: WARNING ("commit completed after previous run timed
       out"), ``last_commit_ts``/``remove_snapshot`` convergence, intent
       cleared, VM NOT failed.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    _start_vm(shell, vm_name)

    # ── Phase 1: a REAL prior commit deletes the merge-set file ────────
    core_a, vm_config, _state_a = _build_core(
        shell, vm_name, base_image, snapshot_dir, target_dir, tmpdir / "state_a"
    )
    snapshots = _create_snapshots(core_a, vm_config, 2)
    s1, s2 = snapshots[0], snapshots[1]

    core_a._blockcommit_snapshots(
        vm_config,
        RetentionResult(keep=[s2.name], remove=[s1.name]),
    )
    assert not s1.path.exists(), "Prior real commit must delete the merge-set file"
    # Chain shortened by the real commit: base ← s2 only.
    assert _backing_chain_length(s2.path, shell) == 2, (
        "Prior real commit must shorten the chain to base ← s2"
    )

    # ── Phase 2: plant a stale intent + stale snapshot record ─────────
    # State B simulates the crash leftovers: the intent was written, the
    # commit completed (file deleted), but the state convergence and the
    # intent clear were lost.
    state_b = JsonStateManager(tmpdir / "state_b")
    state_b.record_snapshot(vm_name, s1)  # stale record — file is gone
    state_b.record_snapshot(vm_name, s2)
    state_b.set_commit_in_progress(
        vm_name,
        "vda",
        [s1.name],
        str(base_image),
        "20260812T000000",
    )

    config_b = MockConfigFacade(global_config=GlobalConfig(), vms=[vm_config])
    factory_b = DefaultFactory(shell=shell, state=state_b)
    core_b = Core(config=config_b, factory=factory_b, state=state_b, shell=shell)

    # ── Phase 3: pipeline step 0 recovery ─────────────────────────────
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        core_b._recover_commit_intents(vm_config)

    # (a) WARNING: the recovery recognizes the late success.
    assert any(
        "commit completed after previous run timed out" in r.message for r in caplog.records
    ), f"Expected late-success WARNING, got: {[r.message for r in caplog.records]}"

    # (b) last_commit_ts convergence.
    assert state_b.get_last_commit_ts(vm_name, "vda") is not None, (
        "Recovery must write last_commit_ts for the late-success commit"
    )

    # (c) remove_snapshot convergence: stale s1 record gone, s2 kept.
    remaining = {s.name for s in state_b.get_snapshots(vm_name)}
    assert s1.name not in remaining, "Stale merge-set snapshot must be removed from state"
    assert s2.name in remaining, "The surviving snapshot must stay in state"

    # (d) Intent cleared.
    assert state_b.get_commit_in_progress(vm_name) == [], "Recovery must clear the stale intent"

    # (e) VM not failed: no exception was raised and the VM still runs.
    assert _vm_is_running(shell, vm_name), "VM must not be failed by recovery"


# ──────────────────────────────────────────────────────────────────────
# Test 5: Active foreign blockjob defers (never clobber, never abort)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_active_foreign_blockjob_defers(test_vm):
    """A genuinely active foreign blockjob defers qsnap's commit.

    1. Start VM; create s1; stop VM; write 192M of data into s1 (so the
       commit has real work); start VM; create s2.
    2. Start a REAL background ``virsh blockcommit --wait`` OUTSIDE qsnap,
       throttled to 1 byte/s so the job is guaranteed to be active.
    3. Assert qsnap's probe classifies it ``"active"``.
    4. Run the pre-commit path with s1 in the remove set: the disk is
       deferred with reason ``blockjob_active``, NO second commit is
       started, NO ``virsh blockjob --abort`` is issued, no intent is
       written, and the VM pipeline continues.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    _start_vm(shell, vm_name)
    core, vm_config, state = _build_core(
        shell, vm_name, base_image, snapshot_dir, target_dir, tmpdir / "state"
    )

    # ── Build a chain whose oldest overlay holds real data ────────────
    _create_snapshots(core, vm_config, 1)
    s1 = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)[0]
    s1_path = s1.path

    # Populate s1 while the VM is stopped (safe), so the foreign commit
    # has measurable work even without a bootable guest.
    stop = shell.run(["virsh", "destroy", vm_name], timeout=30)
    assert stop.success, f"virsh destroy failed: {stop.error}"
    time.sleep(0.5)
    write = shell.run(
        ["qemu-io", "-f", "qcow2", "-c", "write 0 192M", str(s1_path)],
        timeout=60,
    )
    assert write.success, f"qemu-io write failed: {write.error}"

    start_again = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_again.success:
        pytest.skip(f"virsh start after data write failed: {start_again.error}")
    time.sleep(2)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    _create_snapshots(core, vm_config, 1)
    snapshots = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)
    s1, s2 = snapshots[0], snapshots[1]
    assert s1.name == s1_path.stem, f"s1 mismatch: {s1.name} vs {s1_path.stem}"
    assert s1_path.exists() and s2.path.exists()

    # ── Start the FOREIGN throttled commit (outside qsnap) ────────────
    # 1 byte/s over 192 MiB ⇒ the job stays active for hours; the probe
    # below is guaranteed to observe it.
    commit_cmd = [
        "virsh",
        "blockcommit",
        "--domain",
        vm_name,
        "--path",
        "vda",
        "--base",
        str(base_image),
        "--top",
        str(s1_path),
        "--delete",
        "--verbose",
        "--wait",
        "--bandwidth",
        "1",
        "--bytes",
    ]
    proc = subprocess.Popen(commit_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        time.sleep(2)

        # (a) The real probe sees the foreign job as "active".
        raw = shell.run(["virsh", "blockjob", "--domain", vm_name, "--path", "vda"], timeout=30)
        assert raw.success and "No current block job" not in raw.stdout, (
            f"Foreign job should be visible to virsh blockjob: {raw.stdout!r}"
        )
        assert core._probe_blockjob(vm_config, "vda") == "active", (
            "Core's probe must classify the foreign job as 'active'"
        )

        # (b) Run qsnap's pre-commit path with s1 in the remove set.
        commands: list[list[str]] = []
        orig_run = shell.run

        def _recording_run(cmd: list[str], timeout: int, check: bool = False):
            commands.append(list(cmd))
            return orig_run(cmd, timeout=timeout, check=check)

        with patch.object(shell, "run", side_effect=_recording_run):
            core._blockcommit_snapshots(
                vm_config,
                RetentionResult(keep=[s2.name], remove=[s1.name]),
            )

        # (c) Deferred entry with reason "blockjob_active".
        deferred = state.get_deferred_operations(vm_name)
        assert len(deferred) == 1, f"Expected 1 deferred entry, got {len(deferred)}"
        assert deferred[0].reason == "blockjob_active", (
            f"Expected reason 'blockjob_active', got {deferred[0].reason!r}"
        )
        assert s1.name in deferred[0].snapshots, (
            f"Deferred entry must hold the blocked snapshot: {deferred[0].snapshots}"
        )

        # (d) No intent record written for the deferred disk.
        assert state.get_commit_in_progress(vm_name) == [], (
            "Deferral paths must never write a commit intent"
        )

        # (e) No second commit started by qsnap.
        commit_cmds = [c for c in commands if "blockcommit" in c]
        assert commit_cmds == [], f"qsnap must not start a second commit: {commit_cmds}"

        # (f) Never clobber: no `virsh blockjob --abort` issued by qsnap.
        abort_cmds = [c for c in commands if "blockjob" in c and "--abort" in c]
        assert abort_cmds == [], f"qsnap must never abort a foreign job: {abort_cmds}"

        # (g) VM pipeline continues; the foreign job is untouched.
        assert _vm_is_running(shell, vm_name), "VM should still be running"
        assert s1_path.exists(), "Foreign job (throttled) must not have deleted s1 yet"

        # (h) The foreign job is still reported active.
        assert core._probe_blockjob(vm_config, "vda") == "active", (
            "Foreign job must still be active after qsnap's deferral"
        )
    finally:
        # Test-side cleanup: abort the foreign job, then let the fixture
        # destroy/undefine the VM.  qsnap never issued this abort.
        shell.run(
            ["virsh", "blockjob", "--abort", "--domain", vm_name, "--path", "vda"],
            timeout=30,
        )
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
