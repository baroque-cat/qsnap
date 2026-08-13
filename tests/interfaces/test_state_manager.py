"""Contract test: IStateManager ABC and concrete implementations."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from qsnap.interfaces.state import IStateManager
from qsnap.state.json_manager import JsonStateManager
from tests.mocks.mock_state import InMemoryStateManager


def _make_state_manager(mgr_cls, tmp_path) -> IStateManager:
    """Construct the manager under test.

    ``JsonStateManager`` needs a state directory; ``InMemoryStateManager``
    is constructed without arguments.
    """
    if mgr_cls is JsonStateManager:
        return mgr_cls(state_dir=tmp_path)
    return mgr_cls()


def test_istate_manager_is_abstract():
    """IStateManager is an ABC; cannot be instantiated directly.

    JsonStateManager is a subclass, and InMemoryStateManager instances pass
    ``isinstance`` against ``IStateManager``.
    """
    # IStateManager is an ABC with non-empty abstract methods.
    assert hasattr(IStateManager, "__abstractmethods__")
    assert len(IStateManager.__abstractmethods__) > 0

    # Cannot instantiate the ABC directly.
    with pytest.raises(TypeError):
        IStateManager()  # type: ignore[abstract]

    # JsonStateManager is a subclass of IStateManager.
    assert issubclass(JsonStateManager, IStateManager)

    # An InMemoryStateManager instance is an IStateManager.
    assert isinstance(InMemoryStateManager(), IStateManager)


def test_istate_manager_deferred_operations_methods_exist():
    """IStateManager declares deferred blockcommit operations as abstract.

    The methods ``get_deferred_operations``, ``add_deferred_blockcommit``,
    and ``clear_deferred_operations`` must all be in
    ``IStateManager.__abstractmethods__`` so that every concrete
    implementation is forced to provide them.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_deferred_operations" in abstract_methods
    assert "add_deferred_blockcommit" in abstract_methods
    assert "clear_deferred_operations" in abstract_methods


def test_istate_manager_full_backup_methods_abstract():
    """IStateManager declares full-backup tracking methods as abstract.

    The methods ``get_last_full_backup`` and ``set_last_full_backup`` must
    be in ``IStateManager.__abstractmethods__`` so that every concrete
    implementation (e.g. ``JsonStateManager``, ``InMemoryStateManager``)
    is forced to provide them.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_last_full_backup" in abstract_methods
    assert "set_last_full_backup" in abstract_methods


def test_istate_manager_update_deferred_warning_abstract(tmp_path):
    """IStateManager declares update_deferred_warning as abstract.

    The method ``update_deferred_warning`` must be in
    ``IStateManager.__abstractmethods__`` so that every concrete
    implementation is forced to provide it.  Both ``JsonStateManager``
    and ``InMemoryStateManager`` must implement it.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "update_deferred_warning" in abstract_methods

    # Both concrete implementations implement the method.
    json_mgr = JsonStateManager(state_dir=tmp_path)
    inmemory_mgr = InMemoryStateManager()
    assert callable(json_mgr.update_deferred_warning)
    assert callable(inmemory_mgr.update_deferred_warning)


def test_istate_manager_multi_full_backup_methods_abstract():
    """IStateManager declares multi-FULL backup tracking methods as abstract.

    The methods ``get_full_backups``, ``record_full_backup``,
    ``record_incremental_dependency``, and ``get_incremental_dependencies``
    must be in ``IStateManager.__abstractmethods__`` so that every concrete
    implementation is forced to provide them.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_full_backups" in abstract_methods
    assert "record_full_backup" in abstract_methods
    assert "record_incremental_dependency" in abstract_methods
    assert "get_incremental_dependencies" in abstract_methods


def test_istate_manager_concrete_implementations_have_new_methods(tmp_path):
    """JsonStateManager and InMemoryStateManager implement new abstract methods.

    Both concrete implementations must provide ``get_full_backups``,
    ``record_full_backup``, ``record_incremental_dependency``, and
    ``get_incremental_dependencies`` with correct signatures.
    """
    json_mgr = JsonStateManager(state_dir=tmp_path)
    inmemory_mgr = InMemoryStateManager()

    for mgr, label in [
        (json_mgr, "JsonStateManager"),
        (inmemory_mgr, "InMemoryStateManager"),
    ]:
        assert callable(mgr.get_full_backups), f"{label} missing get_full_backups"
        assert callable(mgr.record_full_backup), f"{label} missing record_full_backup"
        assert callable(mgr.record_incremental_dependency), (
            f"{label} missing record_incremental_dependency"
        )
        assert callable(mgr.get_incremental_dependencies), (
            f"{label} missing get_incremental_dependencies"
        )


def test_istate_manager_new_methods_cause_typeerror_on_instantiation():
    """Instantiating a subclass missing the new abstract methods raises TypeError.

    A bare subclass that implements older abstract methods but not the four
    new ones (``get_full_backups``, ``record_full_backup``,
    ``record_incremental_dependency``, ``get_incremental_dependencies``)
    must raise ``TypeError`` because the ABC enforces all abstract methods.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_full_backups" in abstract_methods

    # A subclass that is missing the new methods must fail to instantiate.
    class _MissingNewMethods(IStateManager):
        def get_last_allocation(self, vm_name): ...
        def set_last_allocation(self, vm_name, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...

    with pytest.raises(TypeError):
        _MissingNewMethods()


def test_json_state_manager_passes_isinstance_after_corruption_recovery(
    tmp_path,
) -> None:
    """JsonStateManager remains an IStateManager after corruption recovery.

    Create a ``JsonStateManager``, verify ``isinstance(manager,
    IStateManager)`` is True.  Then trigger corruption recovery (write
    a corrupt file, load), and verify ``isinstance`` is still True.
    """

    manager = JsonStateManager(state_dir=tmp_path)

    # Before corruption — passes isinstance.
    assert isinstance(manager, IStateManager), (
        "JsonStateManager should be an IStateManager before corruption"
    )

    # Write corrupt file and trigger recovery.
    state_file = tmp_path / "testvm_corrupt.json"
    state_file.write_text("{ this is not valid json", encoding="utf-8")

    # Loading triggers corruption recovery (rename + empty state).
    last_alloc = manager.get_last_allocation("testvm_corrupt", "vda")
    assert last_alloc is None, "Corrupt state should return None after recovery"

    # After corruption recovery — still passes isinstance.
    assert isinstance(manager, IStateManager), (
        "JsonStateManager should still be an IStateManager after corruption recovery"
    )


# ── per-target backup allocation contract ──────────────────────────────────


def test_istate_manager_backup_allocation_methods_abstract():
    """get_last_backup_allocation and set_last_backup_allocation are abstract on IStateManager.

    A subclass missing only these two new methods must fail to instantiate
    with TypeError because the ABC enforces all abstract methods.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_last_backup_allocation" in abstract_methods
    assert "set_last_backup_allocation" in abstract_methods
    assert "clear_last_backup_allocation" in abstract_methods
    assert "remove_all_incremental_dependencies" in abstract_methods

    # A subclass that implements everything EXCEPT the backup allocation
    # methods and the bulk dependency removal method must fail to instantiate.
    class _MissingBackupAlloc(IStateManager):
        def get_last_allocation(self, vm_name): ...
        def set_last_allocation(self, vm_name, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def remove_snapshot(self, vm_name, snapshot_name): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...
        def get_full_backups(self, target_path): ...
        def record_full_backup(self, target_path, name, timestamp, disk): ...
        def record_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_incremental_dependencies(self, target_path, full_name): ...
        def remove_full_backup(self, target_path, name): ...
        def remove_incremental_dependency(self, target_path, incremental_name, full_name): ...

    with pytest.raises(TypeError):
        _MissingBackupAlloc()


def test_inmemory_manager_implements_backup_allocation():
    """InMemoryStateManager implements get_last_backup_allocation and set_last_backup_allocation."""
    mgr = InMemoryStateManager()

    # Verify methods exist and are callable.
    assert callable(mgr.get_last_backup_allocation)
    assert callable(mgr.set_last_backup_allocation)

    # Verify they work correctly.
    mgr.set_last_backup_allocation("/mnt/backup/test", "vda", 5000)
    assert mgr.get_last_backup_allocation("/mnt/backup/test", "vda") == 5000

    # Missing target returns None.
    assert mgr.get_last_backup_allocation("/nonexistent", "vda") is None


def test_json_manager_implements_backup_allocation(tmp_path):
    """JsonStateManager implements get_last_backup_allocation and set_last_backup_allocation."""
    mgr = JsonStateManager(state_dir=tmp_path)

    # Verify methods exist and are callable.
    assert callable(mgr.get_last_backup_allocation)
    assert callable(mgr.set_last_backup_allocation)

    # Verify they work correctly.
    mgr.set_last_backup_allocation("/mnt/backup/test", "vda", 5000)
    assert mgr.get_last_backup_allocation("/mnt/backup/test", "vda") == 5000

    # Missing target returns None.
    assert mgr.get_last_backup_allocation("/nonexistent", "vda") is None


# ── clear_last_backup_allocation contract ───────────────────────────────


def test_istate_manager_clear_allocation_abstract():
    """clear_last_backup_allocation is abstract on IStateManager.

    A subclass missing only this method must fail to instantiate with
    TypeError because the ABC enforces all abstract methods.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "clear_last_backup_allocation" in abstract_methods

    # A subclass that implements everything EXCEPT clear_last_backup_allocation
    # must fail to instantiate.
    class _MissingClearBackupAlloc(IStateManager):
        def get_last_allocation(self, vm_name): ...
        def set_last_allocation(self, vm_name, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def remove_snapshot(self, vm_name, snapshot_name): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...
        def get_full_backups(self, target_path): ...
        def record_full_backup(self, target_path, name, timestamp, disk): ...
        def record_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_incremental_dependencies(self, target_path, full_name): ...
        def remove_full_backup(self, target_path, name): ...
        def remove_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_last_backup_allocation(self, target_path, disk): ...
        def set_last_backup_allocation(self, target_path, disk, alloc): ...
        def remove_all_incremental_dependencies(self, target_path, full_name): ...

    with pytest.raises(TypeError):
        _MissingClearBackupAlloc()


def test_istate_manager_remove_all_deps_abstract():
    """remove_all_incremental_dependencies is abstract on IStateManager.

    A subclass missing only this method must fail to instantiate with
    TypeError because the ABC enforces all abstract methods.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "remove_all_incremental_dependencies" in abstract_methods

    # A subclass that implements everything EXCEPT
    # remove_all_incremental_dependencies must fail to instantiate.
    class _MissingRemoveAllDeps(IStateManager):
        def get_last_allocation(self, vm_name): ...
        def set_last_allocation(self, vm_name, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def remove_snapshot(self, vm_name, snapshot_name): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...
        def get_full_backups(self, target_path): ...
        def record_full_backup(self, target_path, name, timestamp, disk): ...
        def record_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_incremental_dependencies(self, target_path, full_name): ...
        def remove_full_backup(self, target_path, name): ...
        def remove_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_last_backup_allocation(self, target_path, disk): ...
        def set_last_backup_allocation(self, target_path, disk, alloc): ...
        def clear_last_backup_allocation(self, target_path, disk): ...

    with pytest.raises(TypeError):
        _MissingRemoveAllDeps()


def test_inmemory_implements_clear_allocation():
    """InMemoryStateManager implements clear_last_backup_allocation, returns bool."""
    mgr = InMemoryStateManager()

    # Verify method exists and is callable.
    assert callable(mgr.clear_last_backup_allocation)

    # Verify it works correctly — clearing an existing entry returns True.
    mgr.set_last_backup_allocation("/mnt/backup/test", "vda", 5000)
    result = mgr.clear_last_backup_allocation("/mnt/backup/test", "vda")
    assert isinstance(result, bool)
    assert result is True

    # Verify the entry was actually removed.
    assert mgr.get_last_backup_allocation("/mnt/backup/test", "vda") is None

    # Clearing a non-existent entry returns False.
    result2 = mgr.clear_last_backup_allocation("/nonexistent", "vda")
    assert isinstance(result2, bool)
    assert result2 is False


def test_json_implements_clear_allocation(tmp_path):
    """JsonStateManager implements clear_last_backup_allocation, returns bool."""
    mgr = JsonStateManager(state_dir=tmp_path)

    # Verify method exists and is callable.
    assert callable(mgr.clear_last_backup_allocation)

    # Verify it works correctly — clearing an existing entry returns True.
    mgr.set_last_backup_allocation("/mnt/backup/test", "vda", 5000)
    result = mgr.clear_last_backup_allocation("/mnt/backup/test", "vda")
    assert isinstance(result, bool)
    assert result is True

    # Verify the entry was actually removed.
    assert mgr.get_last_backup_allocation("/mnt/backup/test", "vda") is None

    # Clearing a non-existent entry returns False.
    result2 = mgr.clear_last_backup_allocation("/nonexistent", "vda")
    assert isinstance(result2, bool)
    assert result2 is False


def test_inmemory_implements_remove_all_deps():
    """InMemoryStateManager implements remove_all_incremental_dependencies, returns int."""
    mgr = InMemoryStateManager()

    # Verify method exists and is callable.
    assert callable(mgr.remove_all_incremental_dependencies)

    # Record some dependencies and verify remove_all returns the correct count.
    mgr.record_incremental_dependency("/target", "inc1", "full1")
    mgr.record_incremental_dependency("/target", "inc2", "full1")
    mgr.record_incremental_dependency("/target", "inc3", "full1")

    result = mgr.remove_all_incremental_dependencies("/target", "full1")
    assert isinstance(result, int)
    assert result == 3

    # Verify dependencies were actually removed.
    remaining = mgr.get_incremental_dependencies("/target", "full1")
    assert remaining == []

    # Removing all deps for a non-existent full backup returns 0.
    result2 = mgr.remove_all_incremental_dependencies("/target", "nonexistent")
    assert isinstance(result2, int)
    assert result2 == 0

    # Removing all deps for a non-existent target returns 0.
    result3 = mgr.remove_all_incremental_dependencies("/nonexistent_tgt", "full1")
    assert isinstance(result3, int)
    assert result3 == 0


# ── per-target state independence contract ────────────────────────────


def test_inmemory_per_target_state_independent():
    """Per-target backup allocation is independent.

    Setting a baseline for one target does not affect baselines
    for other targets (independent-target-onchange spec).
    """
    mgr = InMemoryStateManager()

    mgr.set_last_backup_allocation("/mnt/backup/targetA", "vda", 1000)
    mgr.set_last_backup_allocation("/mnt/backup/targetB", "vda", 2000)

    assert mgr.get_last_backup_allocation("/mnt/backup/targetA", "vda") == 1000
    assert mgr.get_last_backup_allocation("/mnt/backup/targetB", "vda") == 2000

    # Overwrite targetA — targetB is unaffected.
    mgr.set_last_backup_allocation("/mnt/backup/targetA", "vda", 3000)
    assert mgr.get_last_backup_allocation("/mnt/backup/targetA", "vda") == 3000
    assert mgr.get_last_backup_allocation("/mnt/backup/targetB", "vda") == 2000


def test_json_per_target_state_independent(tmp_path):
    """JsonStateManager per-target backup allocation is independent.

    Verifies that persisted state for one target does not leak into
    another target's baseline (independent-target-onchange spec).
    """
    mgr = JsonStateManager(state_dir=tmp_path)

    mgr.set_last_backup_allocation("/mnt/backup/targetA", "vda", 1000)
    mgr.set_last_backup_allocation("/mnt/backup/targetB", "vda", 2000)

    assert mgr.get_last_backup_allocation("/mnt/backup/targetA", "vda") == 1000
    assert mgr.get_last_backup_allocation("/mnt/backup/targetB", "vda") == 2000

    # Overwrite targetA — targetB is unaffected.
    mgr.set_last_backup_allocation("/mnt/backup/targetA", "vda", 3000)
    assert mgr.get_last_backup_allocation("/mnt/backup/targetA", "vda") == 3000
    assert mgr.get_last_backup_allocation("/mnt/backup/targetB", "vda") == 2000


def test_json_implements_remove_all_deps(tmp_path):
    """JsonStateManager implements remove_all_incremental_dependencies, returns int."""
    manager = JsonStateManager(state_dir=tmp_path)

    # Verify method exists and is callable.
    assert callable(manager.remove_all_incremental_dependencies)

    # Record some dependencies and verify remove_all returns the correct count.
    manager.record_incremental_dependency("/target", "inc1", "full1")
    manager.record_incremental_dependency("/target", "inc2", "full1")
    manager.record_incremental_dependency("/target", "inc3", "full1")

    result = manager.remove_all_incremental_dependencies("/target", "full1")
    assert isinstance(result, int)
    assert result == 3

    # Verify dependencies were actually removed.
    remaining = manager.get_incremental_dependencies("/target", "full1")
    assert remaining == []

    # Removing all deps for a non-existent full backup returns 0.
    result2 = manager.remove_all_incremental_dependencies("/target", "nonexistent")
    assert isinstance(result2, int)
    assert result2 == 0

    # Removing all deps for a non-existent target returns 0.
    result3 = manager.remove_all_incremental_dependencies("/nonexistent_tgt", "full1")
    assert isinstance(result3, int)
    assert result3 == 0


# ── reset_vm_state / reset_target_state contract ──────────────────────────


def test_istate_manager_reset_methods_abstract():
    """reset_vm_state, reset_target_state, and per-disk resets are abstract on IStateManager.

    A subclass missing the VM-level reset methods must fail to instantiate
    with TypeError because the ABC enforces all abstract methods.  The
    per-disk counterparts (``reset_vm_disk_state``, ``reset_target_disk_state``)
    are also verified as abstract, completing the full reset-method contract.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "reset_vm_state" in abstract_methods
    assert "reset_target_state" in abstract_methods
    assert "reset_vm_disk_state" in abstract_methods
    assert "reset_target_disk_state" in abstract_methods

    # A subclass that implements everything EXCEPT the VM-level reset
    # methods must fail to instantiate.
    class _MissingResetMethods(IStateManager):
        def get_last_allocation(self, vm_name): ...
        def set_last_allocation(self, vm_name, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def remove_snapshot(self, vm_name, snapshot_name): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...
        def get_full_backups(self, target_path): ...
        def record_full_backup(self, target_path, name, timestamp, disk): ...
        def record_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_incremental_dependencies(self, target_path, full_name): ...
        def remove_full_backup(self, target_path, name): ...
        def remove_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_last_backup_allocation(self, target_path, disk): ...
        def set_last_backup_allocation(self, target_path, disk, alloc): ...
        def clear_last_backup_allocation(self, target_path, disk): ...
        def remove_all_incremental_dependencies(self, target_path, full_name): ...

    with pytest.raises(TypeError):
        _MissingResetMethods()


def test_istate_manager_concrete_implementations_have_reset_methods(tmp_path):
    """JsonStateManager and InMemoryStateManager implement reset_vm_state and reset_target_state."""
    json_mgr = JsonStateManager(state_dir=tmp_path)
    inmemory_mgr = InMemoryStateManager()

    for mgr, label in [
        (json_mgr, "JsonStateManager"),
        (inmemory_mgr, "InMemoryStateManager"),
    ]:
        assert callable(mgr.reset_vm_state), f"{label} missing reset_vm_state"
        assert callable(mgr.reset_target_state), f"{label} missing reset_target_state"


# ── per-disk state reset contract (design D4) ────────────────────────────


def test_istate_manager_per_disk_reset_methods_abstract():
    """reset_vm_disk_state and reset_target_disk_state are abstract on IStateManager.

    The per-disk reset methods declared by ``IStateManager`` for the
    fix-per-disk-isolation change (design D4) MUST be present in
    ``__abstractmethods__`` so that every concrete implementation is forced
    to provide them.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "reset_vm_disk_state" in abstract_methods
    assert "reset_target_disk_state" in abstract_methods


def test_concrete_implementations_have_per_disk_reset_methods(tmp_path):
    """JsonStateManager and InMemoryStateManager implement the per-disk reset methods.

    Both concrete managers provide callable ``reset_vm_disk_state`` and
    ``reset_target_disk_state`` implementations matching the signatures
    declared in ``IStateManager``.
    """
    json_mgr = JsonStateManager(state_dir=tmp_path)
    inmemory_mgr = InMemoryStateManager()

    for mgr, label in [
        (json_mgr, "JsonStateManager"),
        (inmemory_mgr, "InMemoryStateManager"),
    ]:
        assert callable(mgr.reset_vm_disk_state), f"{label} missing reset_vm_disk_state"
        assert callable(mgr.reset_target_disk_state), f"{label} missing reset_target_disk_state"


def test_missing_per_disk_reset_fails_instantiation():
    """A subclass missing per-disk reset methods raises TypeError on instantiation.

    When a concrete subclass of ``IStateManager`` provides every abstract
    method EXCEPT ``reset_vm_disk_state`` and ``reset_target_disk_state``,
    instantiation MUST raise ``TypeError`` because the ABC enforces all
    abstract methods.  This guarantees that no future implementation can
    silently omit the per-disk reset contract (design D4).
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "reset_vm_disk_state" in abstract_methods
    assert "reset_target_disk_state" in abstract_methods

    # A subclass that implements everything EXCEPT the two per-disk reset
    # methods — including reset_vm_state and reset_target_state.
    class _MissingPerDiskReset(IStateManager):
        def get_last_allocation(self, vm_name, disk): ...
        def set_last_allocation(self, vm_name, disk, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def remove_snapshot(self, vm_name, snapshot_name): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...
        def get_full_backups(self, target_path): ...
        def record_full_backup(self, target_path, name, timestamp, disk): ...
        def record_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_incremental_dependencies(self, target_path, full_name): ...
        def remove_full_backup(self, target_path, name): ...
        def remove_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_last_backup_allocation(self, target_path, disk): ...
        def set_last_backup_allocation(self, target_path, disk, alloc): ...
        def clear_last_backup_allocation(self, target_path, disk): ...
        def remove_all_incremental_dependencies(self, target_path, full_name): ...
        def reset_vm_state(self, vm_name): ...
        def reset_target_state(self, target_path): ...

    with pytest.raises(TypeError):
        _MissingPerDiskReset()


# ── deferred blockcommit enospc contract ──────────────────────────────────


def test_inmemory_add_deferred_blockcommit_accepts_enospc_reason():
    """InMemoryStateManager.add_deferred_blockcommit accepts reason='enospc'.

    The entry is stored and round-trips through get_deferred_operations
    with reason='enospc' intact (deferred-operations requirement:
    "Add deferred blockcommit with enospc reason").
    """
    mgr = InMemoryStateManager()
    mgr.add_deferred_blockcommit("testvm", "vda", ["snap1.qcow2"], "enospc")

    deferred = mgr.get_deferred_operations("testvm")
    assert len(deferred) == 1
    entry = deferred[0]
    assert entry.reason == "enospc"
    assert entry.disk == "vda"
    assert entry.snapshots == ["snap1.qcow2"]
    assert entry.last_warned_at is None

    # Round-trip: reading again returns the same entry with reason intact.
    deferred_again = mgr.get_deferred_operations("testvm")
    assert len(deferred_again) == 1
    assert deferred_again[0].reason == "enospc"


def test_json_add_deferred_blockcommit_accepts_enospc_reason(tmp_path):
    """JsonStateManager.add_deferred_blockcommit accepts reason='enospc'.

    The entry persists across a state round-trip: a fresh manager reading
    the same state directory returns the entry with reason='enospc' intact
    (deferred-operations requirement + state-recovery persistence).
    """
    mgr = JsonStateManager(state_dir=tmp_path)
    mgr.add_deferred_blockcommit("testvm", "vda", ["snap1.qcow2"], "enospc")

    # Round-trip through a fresh manager reading the persisted state file.
    mgr2 = JsonStateManager(state_dir=tmp_path)
    deferred = mgr2.get_deferred_operations("testvm")
    assert len(deferred) == 1
    entry = deferred[0]
    assert entry.reason == "enospc"
    assert entry.disk == "vda"
    assert entry.snapshots == ["snap1.qcow2"]
    assert entry.last_warned_at is None


# ── FULL backup .qcow2 name-invariant contract ──────────────────────────
# fix-full-backup-state-extension: every concrete IStateManager must
# normalize FULL backup names to extended form (.qcow2) on record, derive
# the stored path from the normalized name, and accept both stem and
# extended lookup names on remove.  Parametrized over BOTH implementations
# so mock/production divergence fails CI (design D4 — the gap that let
# the regression escape).


@pytest.mark.parametrize("mgr_cls", [JsonStateManager, InMemoryStateManager])
def test_contract_record_full_backup_normalizes_stem(mgr_cls, tmp_path):
    """record_full_backup with a stem name stores the extended name.

    Recording ``"vm.FULL.20260701T000000_vda_a1b2c3"`` (no extension) must
    persist the name with ``.qcow2`` appended, so the stored record always
    carries the extension invariant.
    """
    mgr = _make_state_manager(mgr_cls, tmp_path)
    target = "/mnt/backup/testvm"
    stem = "vm.FULL.20260701T000000_vda_a1b2c3"

    mgr.record_full_backup(target, stem, datetime(2026, 7, 1, 0, 0, 0), "vda")

    backups = mgr.get_full_backups(target)
    assert len(backups) == 1
    assert backups[0].name == stem + ".qcow2"
    assert backups[0].name.endswith(".qcow2")


@pytest.mark.parametrize("mgr_cls", [JsonStateManager, InMemoryStateManager])
def test_contract_record_full_backup_extended_no_double_append(mgr_cls, tmp_path):
    """Recording an already-extended name never double-appends.

    Passing ``"....qcow2"`` twice must store exactly that name — never
    ``"....qcow2.qcow2"``.  Note: JsonStateManager's load-time dedup may
    collapse identical duplicate records to one; only the name invariant
    is asserted, never the count.
    """
    mgr = _make_state_manager(mgr_cls, tmp_path)
    target = "/mnt/backup/testvm"
    extended = "vm.FULL.20260701T000000_vda_a1b2c3.qcow2"

    mgr.record_full_backup(target, extended, datetime(2026, 7, 1, 0, 0, 0), "vda")
    mgr.record_full_backup(target, extended, datetime(2026, 7, 1, 0, 0, 0), "vda")

    backups = mgr.get_full_backups(target)
    assert len(backups) >= 1
    for backup in backups:
        assert backup.name == extended
        assert ".qcow2.qcow2" not in backup.name


@pytest.mark.parametrize("mgr_cls", [JsonStateManager, InMemoryStateManager])
def test_contract_record_full_backup_derives_path_from_extended_name(mgr_cls, tmp_path):
    """record_full_backup derives the stored path from the normalized name.

    Recording a stem name must yield a ``FullBackupInfo.path`` of
    ``Path(target_path) / "<normalized>.qcow2"`` so existence-based
    consumers resolve the physical backup file on the target.
    """
    mgr = _make_state_manager(mgr_cls, tmp_path)
    target = "/mnt/backup/testvm"
    stem = "vm.FULL.20260701T000000_vda_a1b2c3"

    mgr.record_full_backup(target, stem, datetime(2026, 7, 1, 0, 0, 0), "vda")

    backups = mgr.get_full_backups(target)
    assert len(backups) == 1
    assert backups[0].name == stem + ".qcow2"
    assert backups[0].path == Path(target) / (stem + ".qcow2")


@pytest.mark.parametrize("mgr_cls", [JsonStateManager, InMemoryStateManager])
def test_contract_get_full_backups_returns_per_disk_fulls(mgr_cls, tmp_path):
    """get_full_backups returns every recorded FULL, per disk.

    Two FULLs recorded for the same target but different disks (``vda``,
    ``vdb``) must both be returned, each with its own ``disk`` value.
    """
    mgr = _make_state_manager(mgr_cls, tmp_path)
    target = "/mnt/backup/testvm"

    mgr.record_full_backup(
        target, "vm.FULL.20260701T000000_vda_a1b2c3", datetime(2026, 7, 1, 0, 0, 0), "vda"
    )
    mgr.record_full_backup(
        target, "vm.FULL.20260701T000000_vdb_c4d5e6", datetime(2026, 7, 1, 0, 0, 0), "vdb"
    )

    backups = mgr.get_full_backups(target)
    assert len(backups) == 2
    assert {b.disk for b in backups} == {"vda", "vdb"}


@pytest.mark.parametrize("mgr_cls", [JsonStateManager, InMemoryStateManager])
def test_contract_set_last_full_backup_roundtrip_with_disk(mgr_cls, tmp_path):
    """set_last_full_backup round-trips name, timestamp, and disk.

    After ``set_last_full_backup``, ``get_last_full_backup`` returns a
    ``FullBackupInfo`` whose ``name``, ``timestamp``, and ``disk`` match
    the recorded values and whose ``path`` resolves to the extended name.
    """
    mgr = _make_state_manager(mgr_cls, tmp_path)
    target = "/mnt/backup/testvm"
    extended = "vm.FULL.20260701T000000_vda_a1b2c3.qcow2"
    ts = datetime(2026, 7, 1, 0, 0, 0)

    mgr.set_last_full_backup(target, extended, ts, "vda")

    info = mgr.get_last_full_backup(target)
    assert info is not None
    assert info.name == extended
    assert info.timestamp == ts
    assert info.disk == "vda"
    assert info.path == Path(target) / extended


@pytest.mark.parametrize("mgr_cls", [JsonStateManager, InMemoryStateManager])
def test_contract_get_last_full_backup_empty_returns_none(mgr_cls, tmp_path):
    """get_last_full_backup returns None when nothing is recorded."""
    mgr = _make_state_manager(mgr_cls, tmp_path)

    assert mgr.get_last_full_backup("/mnt/backup/nonexistent") is None


@pytest.mark.parametrize("mgr_cls", [JsonStateManager, InMemoryStateManager])
def test_contract_remove_full_backup_stem_lookup(mgr_cls, tmp_path):
    """remove_full_backup with a stem name removes the extended record.

    ``Core._cleanup_backups`` passes ``BackupInfo.name`` from
    ``provider.list()`` — always a stem.  The tolerant lookup must still
    remove the stored extended record and return True.
    """
    mgr = _make_state_manager(mgr_cls, tmp_path)
    target = "/mnt/backup/testvm"
    stem = "vm.FULL.20260701T000000_vda_a1b2c3"
    extended = stem + ".qcow2"

    mgr.record_full_backup(target, extended, datetime(2026, 7, 1, 0, 0, 0), "vda")

    assert mgr.remove_full_backup(target, stem) is True
    assert mgr.get_full_backups(target) == []


@pytest.mark.parametrize("mgr_cls", [JsonStateManager, InMemoryStateManager])
def test_contract_remove_full_backup_extended_lookup(mgr_cls, tmp_path):
    """remove_full_backup with the extended name removes the record.

    Extended callers (which pass state-derived ``full.name``) must remove
    the same record and return True.
    """
    mgr = _make_state_manager(mgr_cls, tmp_path)
    target = "/mnt/backup/testvm"
    extended = "vm.FULL.20260701T000000_vda_a1b2c3.qcow2"

    mgr.record_full_backup(target, extended, datetime(2026, 7, 1, 0, 0, 0), "vda")

    assert mgr.remove_full_backup(target, extended) is True
    assert mgr.get_full_backups(target) == []


@pytest.mark.parametrize("mgr_cls", [JsonStateManager, InMemoryStateManager])
def test_contract_remove_full_backup_non_matching_returns_false(mgr_cls, tmp_path):
    """remove_full_backup for a non-matching name returns False.

    A lookup that matches no stored entry must leave state untouched and
    return False.
    """
    mgr = _make_state_manager(mgr_cls, tmp_path)
    target = "/mnt/backup/testvm"

    assert mgr.remove_full_backup(target, "nonexistent.qcow2") is False


# ── crash-evidence state fields contract (recover-lost-checkpoint-bitmaps) ──
# state-management spec: ``get_boot_id``/``set_boot_id`` (host boot
# identifier per VM) and ``get_last_commit_ts``/``set_last_commit_ts``
# (per-disk last-commit marker) are abstract on IStateManager.  Every
# concrete implementation must provide them; a subclass missing any of
# them fails to instantiate with TypeError (TESTING.md contract-test
# rule).


def test_istate_manager_boot_id_methods_abstract():
    """get_boot_id and set_boot_id are abstract on IStateManager.

    The host boot-id tracking methods declared by ``IStateManager`` for
    the recover-lost-checkpoint-bitmaps change MUST be present in
    ``__abstractmethods__`` so that every concrete implementation is
    forced to provide them.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_boot_id" in abstract_methods
    assert "set_boot_id" in abstract_methods


def test_istate_manager_last_commit_ts_methods_abstract():
    """get_last_commit_ts and set_last_commit_ts are abstract on IStateManager.

    The per-disk last-commit marker methods MUST be in
    ``__abstractmethods__`` so that every concrete implementation is
    forced to provide them.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_last_commit_ts" in abstract_methods
    assert "set_last_commit_ts" in abstract_methods


def test_concrete_implementations_have_crash_evidence_methods(tmp_path):
    """JsonStateManager and InMemoryStateManager implement the four new methods.

    Both concrete managers provide callable ``get_boot_id``,
    ``set_boot_id``, ``get_last_commit_ts``, and ``set_last_commit_ts``
    matching the ``IStateManager`` declarations.
    """
    json_mgr = JsonStateManager(state_dir=tmp_path)
    inmemory_mgr = InMemoryStateManager()

    for mgr, label in [
        (json_mgr, "JsonStateManager"),
        (inmemory_mgr, "InMemoryStateManager"),
    ]:
        assert callable(mgr.get_boot_id), f"{label} missing get_boot_id"
        assert callable(mgr.set_boot_id), f"{label} missing set_boot_id"
        assert callable(mgr.get_last_commit_ts), f"{label} missing get_last_commit_ts"
        assert callable(mgr.set_last_commit_ts), f"{label} missing set_last_commit_ts"


def test_crash_evidence_methods_work_in_both_implementations(tmp_path):
    """boot_id / last_commit_ts round-trip in both concrete implementations.

    A subclass implementing the interface is not enough — the concrete
    managers must actually persist the values (mock parity, TESTING.md
    paradigm table).
    """
    json_mgr = JsonStateManager(state_dir=tmp_path)
    inmemory_mgr = InMemoryStateManager()

    for mgr in (json_mgr, inmemory_mgr):
        assert mgr.get_boot_id("testvm") is None
        mgr.set_boot_id("testvm", "boot-A")
        assert mgr.get_boot_id("testvm") == "boot-A"

        assert mgr.get_last_commit_ts("testvm", "vda") is None
        mgr.set_last_commit_ts("testvm", "vda", "20260808T160000")
        assert mgr.get_last_commit_ts("testvm", "vda") == "20260808T160000"


def test_missing_crash_evidence_methods_fails_instantiation():
    """A subclass missing the crash-evidence methods raises TypeError.

    When a concrete subclass of ``IStateManager`` provides every abstract
    method EXCEPT ``get_boot_id``/``set_boot_id``/
    ``get_last_commit_ts``/``set_last_commit_ts``, instantiation MUST
    raise ``TypeError`` because the ABC enforces all abstract methods.
    This guarantees no future implementation can silently omit the
    crash-evidence contract (state-management spec).
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_boot_id" in abstract_methods
    assert "set_boot_id" in abstract_methods
    assert "get_last_commit_ts" in abstract_methods
    assert "set_last_commit_ts" in abstract_methods

    # A subclass that implements everything EXCEPT the four crash-evidence
    # methods — including the per-disk reset methods.
    class _MissingCrashEvidence(IStateManager):
        def get_last_allocation(self, vm_name, disk): ...
        def set_last_allocation(self, vm_name, disk, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def remove_snapshot(self, vm_name, snapshot_name): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...
        def get_full_backups(self, target_path): ...
        def record_full_backup(self, target_path, name, timestamp, disk): ...
        def record_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_incremental_dependencies(self, target_path, full_name): ...
        def remove_full_backup(self, target_path, name): ...
        def remove_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_last_backup_allocation(self, target_path, disk): ...
        def set_last_backup_allocation(self, target_path, disk, alloc): ...
        def clear_last_backup_allocation(self, target_path, disk): ...
        def remove_all_incremental_dependencies(self, target_path, full_name): ...
        def reset_vm_state(self, vm_name): ...
        def reset_target_state(self, target_path): ...
        def reset_vm_disk_state(self, vm_name, disk): ...
        def reset_target_disk_state(self, target_path, vm_name, disk): ...

    with pytest.raises(TypeError):
        _MissingCrashEvidence()


# ── commit intent journal contract (harden-blockcommit-races) ────────────
# commit-intent-journal spec: ``set_commit_in_progress`` /
# ``get_commit_in_progress`` / ``clear_commit_in_progress`` are abstract on
# IStateManager.  Behavior round-trips are parametrized over BOTH concrete
# implementations (JsonStateManager + InMemoryStateManager) so mock and
# production parity is enforced by the contract suite (TESTING.md §3).


def test_istate_manager_commit_intent_methods_abstract():
    """The three commit-intent journal methods are abstract on IStateManager.

    A subclass implementing every abstract method EXCEPT
    ``set_commit_in_progress`` / ``get_commit_in_progress`` /
    ``clear_commit_in_progress`` MUST fail to instantiate with TypeError.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "set_commit_in_progress" in abstract_methods
    assert "get_commit_in_progress" in abstract_methods
    assert "clear_commit_in_progress" in abstract_methods

    class _MissingIntentJournal(IStateManager):
        def get_last_allocation(self, vm_name, disk): ...
        def set_last_allocation(self, vm_name, disk, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def remove_snapshot(self, vm_name, snapshot_name): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...
        def get_full_backups(self, target_path): ...
        def record_full_backup(self, target_path, name, timestamp, disk): ...
        def record_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_incremental_dependencies(self, target_path, full_name): ...
        def remove_full_backup(self, target_path, name): ...
        def remove_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_last_backup_allocation(self, target_path, disk): ...
        def set_last_backup_allocation(self, target_path, disk, alloc): ...
        def clear_last_backup_allocation(self, target_path, disk): ...
        def remove_all_incremental_dependencies(self, target_path, full_name): ...
        def reset_vm_state(self, vm_name): ...
        def reset_target_state(self, target_path): ...
        def reset_vm_disk_state(self, vm_name, disk): ...
        def reset_target_disk_state(self, target_path, vm_name, disk): ...
        def get_boot_id(self, vm_name): ...
        def set_boot_id(self, vm_name, boot_id): ...
        def get_last_commit_ts(self, vm_name, disk): ...
        def set_last_commit_ts(self, vm_name, disk, timestamp): ...

    with pytest.raises(TypeError):
        _MissingIntentJournal()


@pytest.mark.parametrize("mgr_cls", [JsonStateManager, InMemoryStateManager])
def test_contract_commit_intent_set_get_clear(mgr_cls, tmp_path):
    """Commit intent set → read → clear round-trips in both implementations.

    Mirrors the commit-intent-journal scenario "Set, read, and clear an
    intent record" (tests/state/test_manager.py parity for the mock).
    """
    mgr = _make_state_manager(mgr_cls, tmp_path)

    assert mgr.get_commit_in_progress("testvm") == []

    mgr.set_commit_in_progress(
        "testvm",
        "vda",
        ["snap1.qcow2", "snap2.qcow2"],
        "/var/lib/libvirt/images/testvm.qcow2",
        "20260808T160000",
    )

    intents = mgr.get_commit_in_progress("testvm")
    assert len(intents) == 1
    intent = intents[0]
    assert intent.disk == "vda"
    assert intent.snapshots == ["snap1.qcow2", "snap2.qcow2"]
    assert intent.base == "/var/lib/libvirt/images/testvm.qcow2"
    assert intent.started_ts == "20260808T160000"

    mgr.clear_commit_in_progress("testvm", "vda")
    assert mgr.get_commit_in_progress("testvm") == []

    # Clearing an absent disk is a no-op, not an error.
    mgr.clear_commit_in_progress("testvm", "vdz")


@pytest.mark.parametrize("mgr_cls", [JsonStateManager, InMemoryStateManager])
def test_contract_commit_intent_upsert_same_disk(mgr_cls, tmp_path):
    """A second set for the same disk replaces the record (upsert semantics).

    Mirrors the commit-intent-journal scenario "Upsert replaces the record
    for the same disk".
    """
    mgr = _make_state_manager(mgr_cls, tmp_path)

    mgr.set_commit_in_progress(
        "testvm", "vda", ["snap1.qcow2"], "/base/testvm.qcow2", "20260808T160000"
    )
    mgr.set_commit_in_progress(
        "testvm", "vda", ["snap1.qcow2", "snap2.qcow2"], "/base/testvm.qcow2", "20260808T170000"
    )

    intents = mgr.get_commit_in_progress("testvm")
    assert len(intents) == 1, f"Upsert must keep one record, got {len(intents)}"
    assert intents[0].snapshots == ["snap1.qcow2", "snap2.qcow2"]
    assert intents[0].started_ts == "20260808T170000"


@pytest.mark.parametrize("mgr_cls", [JsonStateManager, InMemoryStateManager])
def test_contract_commit_intent_multiple_disks_independent(mgr_cls, tmp_path):
    """Multiple disks hold independent intent records for the same VM.

    Mirrors the commit-intent-journal scenario "Multiple disks hold
    independent intent records".
    """
    mgr = _make_state_manager(mgr_cls, tmp_path)

    mgr.set_commit_in_progress(
        "testvm", "vda", ["snap1.qcow2"], "/base/testvm.qcow2", "20260808T160000"
    )
    mgr.set_commit_in_progress(
        "testvm", "vdb", ["snap1-vdb.qcow2"], "/base/testvm-vdb.qcow2", "20260808T160000"
    )

    intents = mgr.get_commit_in_progress("testvm")
    assert len(intents) == 2
    assert {i.disk for i in intents} == {"vda", "vdb"}

    mgr.clear_commit_in_progress("testvm", "vda")
    remaining = mgr.get_commit_in_progress("testvm")
    assert len(remaining) == 1
    assert remaining[0].disk == "vdb"


# ── removed collapse-phase API: negative contract (bulk-collapse-blockcommit) ──
# bulk-collapse-blockcommit (state-management REMOVED requirement): the
# hysteresis collapse is now a single uncapped bulk blockcommit completed
# within one run, so the ``collapse_in_progress`` phase API
# (``get_collapse_in_progress`` / ``set_collapse_in_progress`` /
# ``clear_collapse_in_progress``) was removed from ``IStateManager``.
# Crash recovery is covered solely by the commit-intent journal.  This
# negative contract pins the removal so the methods can never reappear.


def test_istate_manager_has_no_collapse_phase_methods():
    """IStateManager no longer declares the collapse-phase methods.

    None of ``get_collapse_in_progress`` / ``set_collapse_in_progress`` /
    ``clear_collapse_in_progress`` may appear in
    ``IStateManager.__abstractmethods__`` (state-management REMOVED
    requirement: "remove set_collapse_in_progress,
    clear_collapse_in_progress, and the collapse-phase reader from
    IStateManager").
    """
    abstract_methods = IStateManager.__abstractmethods__
    for name in (
        "get_collapse_in_progress",
        "set_collapse_in_progress",
        "clear_collapse_in_progress",
    ):
        assert name not in abstract_methods, f"{name} must no longer be abstract on IStateManager"
