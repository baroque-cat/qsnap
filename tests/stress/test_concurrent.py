"""Stress test: lockfile prevents concurrent pipeline runs.

This test verifies that the lockfile mechanism prevents two concurrent
``qsnap run`` invocations from corrupting state or racing on virsh
operations.

- :func:`test_lockfile_prevents_concurrent_runs` — original placeholder
  kept from the pre-bulk-collapse suite.
- :func:`test_second_run_during_bulk_job_exits_3` — the stress
  counterpart of bulk-collapse-blockcommit risk row #335: a bulk
  collapse holds the exclusive lockfile for the whole mutating run, so a
  second run attempted while the ``virsh blockcommit`` segment job is in
  flight fails closed with exit code 3 and the documented message
  "Lockfile is held by another qsnap instance".

Marked ``@pytest.mark.stress`` — requires a libvirt environment.
"""

from __future__ import annotations

import contextlib
import io
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from qsnap.cli.app import main
from qsnap.cli.errors import EXIT_LOCKFILE
from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.interfaces.shell import IShell
from qsnap.locking import LockManager
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import ShellResult
from qsnap.shell.subprocess_shell import SubprocessShell
from tests.helpers import snapshot_create
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_state import InMemoryStateManager
from tests.stress.test_long_chain import RecordingShell, _vm_is_running

#: Compact hysteresis numbers for the lock-contention race: H=8, L=3 and
#: a 9-overlay chain → ONE prune collapses a 6-layer bulk segment.
_HYST_SMALL_THRESHOLD = 8
_HYST_SMALL_FLOOR = 3
_HYST_SMALL_CHAIN = 9

#: Throttle injected into the real ``virsh blockcommit`` (bytes/sec).
#: The gate (not the throttle) makes the contention window deterministic;
#: the throttle keeps the real job alive generously after release.
_BANDWIDTH_BYTES = 1_048_576


class _GatedThrottledBlockcommitShell(RecordingShell):
    """Recording shell that throttles and deterministically gates blockcommit.

    Risk #335 needs the first run to be provably INSIDE the bulk job while
    a second run attempts the lockfile.  The first ``virsh blockcommit``
    is:

    - recorded (inherited from :class:`RecordingShell`),
    - gated: blocked in ``run_with_heartbeat`` (the mutating run keeps
      holding the lock) until :attr:`release_blockcommit` is set, so the
      contention window never depends on job duration, and
    - throttled: ``--bandwidth <bytes>`` is injected into the command
      handed to the REAL shell, so the live segment job runs slowly.

    ``blockcommit_started`` is set once the first blockcommit has been
    recorded and the run is parked inside the job.
    """

    def __init__(self, delegate: IShell, bandwidth_bytes: int) -> None:
        super().__init__(delegate)
        self._bandwidth = str(bandwidth_bytes)
        self.blockcommit_started = threading.Event()
        self.release_blockcommit = threading.Event()
        self._gated = False

    def run_with_heartbeat(
        self,
        cmd: list[str],
        timeout: int,
        heartbeat_seconds: int,
        on_heartbeat: Callable[[int], None],
        check: bool = False,
    ) -> ShellResult:
        self._commands.append(list(cmd))
        if "blockcommit" in cmd and not self._gated:
            self._gated = True
            self.blockcommit_started.set()
            if not self.release_blockcommit.wait(timeout=300):
                raise TimeoutError("gated virsh blockcommit was never released by the test")
        throttled = list(cmd)
        if "blockcommit" in throttled and "--bandwidth" not in throttled:
            throttled.extend(["--bandwidth", self._bandwidth])
        return self._delegate.run_with_heartbeat(
            throttled, timeout, heartbeat_seconds, on_heartbeat, check
        )


@pytest.mark.stress
def test_lockfile_prevents_concurrent_runs(stress_env):
    """Verify lockfile prevents concurrent pipeline execution.

    Steps (placeholder — implement when libvirt test environment is
    available):
      1. Start a ``qsnap run`` in a background thread/process.
      2. While the first run is in progress, start a second ``qsnap run``
         against the same VM.
      3. Verify the second run detects the lockfile and exits with a
         clear error message (no crash, no state corruption).
      4. Verify the first run completes successfully.
    """
    pytest.skip("Requires libvirt environment")


@pytest.mark.stress
@pytest.mark.timeout(3600)
def test_second_run_during_bulk_job_exits_3(stress_env):
    """Risk #335: a second run during a bulk blockcommit exits 3.

    The bulk collapse holds the exclusive lockfile for the whole mutating
    run (locking spec: exit code 3, "Lockfile is held by another qsnap
    instance").  While a real throttled ``virsh blockcommit --bandwidth``
    segment job is in flight (deterministically gated by a recording
    shell wrapper), a second ``qsnap run`` must fail closed.

    1. Build a 9-overlay chain (H=8, L=3) on the stress VM and switch to
       hysteresis so ONE prune collapses a 6-layer bulk segment.
    2. Run the first prune in a background thread under the SAME
       ``LockManager`` contract the CLI uses; the gated+throttled shell
       blocks the real ``virsh blockcommit`` (with ``--bandwidth``
       injected) until the second run has attempted the lock.
    3. Invoke the real CLI ``main()`` for a second ``run`` against the
       same lockfile while the bulk job is parked in flight → exit code
       ``EXIT_LOCKFILE`` (3) and stderr contains the documented message.
    4. Release the gate: the first run completes the collapse, the chain
       reaches the floor, nothing is deferred, and the VM is still
       running.
    """
    shell: SubprocessShell = stress_env["shell"]
    vm_name: str = stress_env["vm_name"]
    base_image: Path = stress_env["base_image"]
    snapshot_dir: Path = stress_env["snapshot_dir"]
    target_dir: Path = stress_env["target_dir"]
    tmpdir: Path = stress_env["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    state = InMemoryStateManager()

    # 1. Compact deep chain: 9 overlays > H=8 → a 6-layer bulk segment.
    snapshots = []
    for i in range(_HYST_SMALL_CHAIN):
        hex_sfx = secrets.token_hex(3)
        snap = snapshot_create(
            shell,
            vm_name,
            f"{vm_name}.conc-{i:03d}-{hex_sfx}",
            "vda",
            snapshot_dir,
            base_image,
        )
        state.record_snapshot(vm_name, snap)
        snapshots.append(snap)
        time.sleep(0.35)  # unique timestamps
    assert len(state.get_snapshots(vm_name)) == _HYST_SMALL_CHAIN, (
        f"Expected {_HYST_SMALL_CHAIN} snapshots, got {len(state.get_snapshots(vm_name))}"
    )

    # Gated + throttled recording shell: the real virsh blockcommit gets
    # --bandwidth and is parked until the second run has attempted.
    gated = _GatedThrottledBlockcommitShell(shell, bandwidth_bytes=_BANDWIDTH_BYTES)

    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=_HYST_SMALL_THRESHOLD,
        snapshot_preserve_min=_HYST_SMALL_FLOOR,
        snapshot_retention_mode="hysteresis",
        lifecycle_mode="virsh",
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
            )
        ],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir / "concurrent.toml",
    )
    factory = DefaultFactory(shell=gated, state=state)
    core = Core(config=config, factory=factory, state=state, shell=gated)

    lockfile = tmpdir / "qsnap-stress.lock"

    # Real CLI config for the second run.  It only needs to PARSE: the
    # lock failure returns before any VM work is dispatched.
    cli_config = tmpdir / "concurrent-cli.toml"
    cli_config.write_text(
        f"[global]\n"
        f'state_dir = "{tmpdir / "cli-state"}"\n'
        f"\n"
        f"[[vm]]\n"
        f'name = "{vm_name}"\n'
        f'snapshot_dir = "{snapshot_dir}"\n'
        f"snapshot_chain_length = {_HYST_SMALL_THRESHOLD}\n"
        f"snapshot_preserve_min = {_HYST_SMALL_FLOOR}\n"
        f'snapshot_retention_mode = "hysteresis"\n'
        f'lifecycle_mode = "virsh"\n'
        f"\n"
        f"  [[vm.disk]]\n"
        f'  target = "vda"\n'
        f'  base_image = "{base_image}"\n'
        f"\n"
        f"  [[vm.target]]\n"
        f'  path = "{target_dir}"\n'
        f"  compress = false\n"
        f'  verify = "off"\n'
    )

    errors: list[BaseException] = []

    def _first_run() -> None:
        # Same lock contract as the CLI layer (qsnap/cli/app.py):
        # mutating commands acquire the exclusive lockfile around Core.
        try:
            lock = LockManager(lockfile)
            if not lock.acquire():
                errors.append(RuntimeError("first run could not acquire the lockfile"))
                return
            try:
                result = core.prune(vm_name)
                if not result.results[0].success:
                    errors.append(RuntimeError(f"first prune failed: {result.results[0].error}"))
            finally:
                lock.release()
        except Exception as exc:  # surface any unexpected thread failure
            errors.append(exc)

    thread = threading.Thread(target=_first_run, name="qsnap-first-run")
    thread.start()
    try:
        # Wait until the bulk blockcommit is provably parked in flight
        # (lock held by the first run).
        deadline = time.monotonic() + 180
        while not gated.blockcommit_started.is_set():
            if time.monotonic() > deadline:
                pytest.fail("the bulk virsh blockcommit never started")
            time.sleep(0.1)

        assert len(gated.blockcommit_commands()) == 1, (
            "the first run must hold the lock while exactly ONE bulk "
            f"blockcommit is in flight, got {gated.blockcommit_commands()}"
        )

        # 3. Second run fails closed with the documented exit-3 message.
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            code = main(["--config", str(cli_config), "--lockfile", str(lockfile), "run"])
        assert code == EXIT_LOCKFILE, (
            f"second run must exit with {EXIT_LOCKFILE} while the bulk job "
            f"holds the lock, got {code}"
        )
        assert "Lockfile is held by another qsnap instance" in stderr_buf.getvalue(), (
            "second run must print the documented lock contention message, "
            f"got: {stderr_buf.getvalue()!r}"
        )
    finally:
        # 4. Release the gate so the first run's real (throttled) segment
        #    job can complete.
        gated.release_blockcommit.set()
        thread.join(timeout=600)
    assert not thread.is_alive(), "first run did not finish in time"
    assert not errors, f"first run failed: {errors}"

    # The first run completed the bulk collapse: chain at the floor (3),
    # nothing deferred, VM still running.
    surviving = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)
    assert len(surviving) == _HYST_SMALL_FLOOR, (
        f"Chain must sit at the floor {_HYST_SMALL_FLOOR} after the collapse, got {len(surviving)}"
    )
    assert state.get_deferred_operations(vm_name) == [], (
        "No deferred blockcommit entries may remain after the collapse"
    )
    assert _vm_is_running(gated, vm_name), "VM should still be running after the bulk job"

    shell.run(["virsh", "destroy", vm_name], timeout=30)
