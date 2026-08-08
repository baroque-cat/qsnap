"""Unit tests for pure extent-processing functions.

Tests cover ``unify_extents`` and ``overlap_with_allocation`` from
``qsnap.utils.extents``.  Both functions are pure and I/O-free — no
mocks required.  Deterministic given the same input.
"""

from __future__ import annotations

import pytest

from qsnap.models.results import NbdExtent
from qsnap.utils.extents import overlap_with_allocation, unify_extents

# ──────────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────────


def _e(offset: int, length: int, data: bool) -> NbdExtent:
    """Shorthand for building an NbdExtent."""
    return NbdExtent(offset=offset, length=length, data=data)


# ══════════════════════════════════════════════════════════════════════════════
# unify_extents
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_unify_adjacent_dirty_extents() -> None:
    """Consecutive adjacent same-kind extents merge into one covering the
    combined range (spec scenario: "Consecutive same-kind extents are
    unified")."""
    inp = [
        _e(0, 65536, True),
        _e(65536, 65536, True),
        _e(131072, 65536, True),
    ]
    result = unify_extents(inp)
    assert result == [_e(0, 196608, True)]


@pytest.mark.unit
def test_unify_single_extent_noop() -> None:
    """A single-extent list is returned as a new list with the same content."""
    inp = [_e(0, 1048576, True)]
    result = unify_extents(inp)
    assert result == inp
    assert result is not inp  # returns new list


@pytest.mark.unit
def test_unify_empty_input() -> None:
    """Empty input yields empty output."""
    assert unify_extents([]) == []


@pytest.mark.unit
def test_unify_different_data_flags_not_merged() -> None:
    """Adjacent extents with different ``data`` flags are NOT merged."""
    inp = [
        _e(0, 65536, True),
        _e(65536, 65536, False),
    ]
    result = unify_extents(inp)
    assert result == inp  # both preserved separately


@pytest.mark.unit
def test_unify_input_not_mutated() -> None:
    """The original input list is not modified by the call."""
    inp = [
        _e(0, 65536, True),
        _e(65536, 65536, True),
        _e(131072, 65536, False),
    ]
    original = list(inp)  # shallow copy (NbdExtent is frozen, so safe)
    unify_extents(inp)
    assert inp == original


@pytest.mark.unit
def test_unify_mixed_sequence() -> None:
    """Alternating data/hole extents are not merged across flag boundaries."""
    inp = [
        _e(0, 65536, True),
        _e(65536, 65536, True),
        _e(131072, 65536, False),
        _e(196608, 65536, False),
        _e(262144, 65536, True),
    ]
    result = unify_extents(inp)
    assert result == [
        _e(0, 131072, True),  # first two merged
        _e(131072, 131072, False),  # next two merged
        _e(262144, 65536, True),  # last one alone
    ]


@pytest.mark.unit
def test_unify_gap_not_merged() -> None:
    """Two same-kind extents separated by a disk-offset gap are NOT merged.

    The gap means the extents are not truly adjacent on disk, so merging
    them would fabricate coverage of the hole region — a correctness bug.
    """
    inp = [
        _e(0, 65536, True),
        _e(131072, 65536, True),  # gap at [65536, 131072)
    ]
    result = unify_extents(inp)
    # Each extent preserved separately; no merge across the gap.
    assert result == inp


@pytest.mark.unit
def test_unify_gap_free_neighbor_still_merges() -> None:
    """Regression guard: gap-free disk-adjacent same-kind extents still
    merge into one (the adjacency check must not reject the correct case).
    """
    inp = [
        _e(0, 65536, True),
        _e(65536, 65536, True),  # exactly adjacent — no gap
    ]
    result = unify_extents(inp)
    assert result == [_e(0, 131072, True)]


# ══════════════════════════════════════════════════════════════════════════════
# overlap_with_allocation
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_overlap_filters_unallocated_regions() -> None:
    """Dirty extent overlapping a region that ``base:allocation`` reports as
    hole/zero is excluded; only dirty-and-allocated sub-ranges remain
    (spec scenario: "Dirty-but-unallocated regions are filtered")."""
    dirty = [_e(0, 1000000, True)]
    allocated = [
        _e(0, 400000, True),
        _e(400000, 600000, False),  # hole / discarded region
    ]
    result = overlap_with_allocation(dirty, allocated)
    assert result == [_e(0, 400000, True)]


@pytest.mark.unit
def test_overlap_fully_allocated_dirty_preserved() -> None:
    """A dirty extent that is fully covered by an allocated extent is
    returned unchanged."""
    dirty = [_e(0, 1048576, True)]
    allocated = [_e(0, 1048576, True)]
    result = overlap_with_allocation(dirty, allocated)
    assert result == dirty


@pytest.mark.unit
def test_overlap_zero_overlap_empty() -> None:
    """Non-overlapping extents produce an empty result."""
    # dirty completely before allocated
    result = overlap_with_allocation(
        [_e(0, 500, True)],
        [_e(500, 500, True)],
    )
    assert result == []


@pytest.mark.unit
def test_overlap_partial_boundaries() -> None:
    """Dirty extent that starts before the allocated region and ends after
    it — only the intersection is returned."""
    dirty = [_e(100, 600, True)]  # [100, 700)
    allocated = [_e(200, 400, True)]  # [200, 600)
    result = overlap_with_allocation(dirty, allocated)
    # intersection: [max(100,200), min(700,600)) = [200, 600) → length 400
    assert result == [_e(200, 400, True)]


@pytest.mark.unit
def test_overlap_dirty_starts_after_allocation() -> None:
    """Zero overlap when dirty starts at or after allocation ends."""
    dirty = [_e(1000, 500, True)]  # [1000, 1500)
    allocated = [_e(0, 1000, True)]  # [0, 1000)
    result = overlap_with_allocation(dirty, allocated)
    assert result == []


@pytest.mark.unit
def test_overlap_two_allocated_islands() -> None:
    """Dirty extent spanning two allocated islands with an unallocated hole
    in between — two output extents returned."""
    dirty = [_e(0, 1000, True)]  # [0, 1000)
    allocated = [
        _e(0, 300, True),  # first island  [0, 300)
        _e(300, 200, False),  # hole          [300, 500)
        _e(500, 500, True),  # second island [500, 1000)
    ]
    result = overlap_with_allocation(dirty, allocated)
    assert result == [
        _e(0, 300, True),
        _e(500, 500, True),
    ]


@pytest.mark.unit
def test_overlap_multiple_vs_multiple() -> None:
    """Multiple dirty extents against multiple allocated extents."""
    dirty = [
        _e(0, 200, True),  # [0, 200)
        _e(300, 400, True),  # [300, 700)
        _e(800, 200, False),  # clean → skipped
        _e(1000, 200, True),  # [1000, 1200)
    ]
    allocated = [
        _e(0, 500, True),  # [0, 500)
        _e(500, 300, False),  # hole [500, 800)
        _e(800, 500, True),  # [800, 1300)
    ]
    result = overlap_with_allocation(dirty, allocated)
    assert result == [
        _e(0, 200, True),  # dirty[0] ∩ allocated[0]
        _e(300, 200, True),  # dirty[1] ∩ allocated[0] → [300, 500)
        # gap [500, 800): dirty[1] ends at 700 < allocated[1].offset=800, j advances
        # dirty[2] skipped (data=False)
        _e(1000, 200, True),  # dirty[3] ∩ allocated[2] → [1000, 1200)
    ]


@pytest.mark.unit
def test_overlap_empty_inputs() -> None:
    """Empty inputs on either side yield empty output."""
    assert overlap_with_allocation([], []) == []
    assert overlap_with_allocation([], [_e(0, 100, True)]) == []
    assert overlap_with_allocation([_e(0, 100, True)], []) == []


@pytest.mark.unit
def test_overlap_skips_unallocated_base_then_finds_allocated() -> None:
    """Leading unallocated base extents are skipped until an allocated one
    is reached."""
    dirty = [_e(500, 500, True)]
    allocated = [
        _e(0, 200, False),
        _e(200, 300, False),
        _e(500, 500, True),
    ]
    result = overlap_with_allocation(dirty, allocated)
    assert result == [_e(500, 500, True)]


@pytest.mark.unit
def test_overlap_all_dirty_data_false_returns_empty() -> None:
    """When all dirty extents have data=False, nothing is returned."""
    dirty = [_e(0, 1000, False)]
    allocated = [_e(0, 1000, True)]
    result = overlap_with_allocation(dirty, allocated)
    assert result == []


@pytest.mark.unit
def test_overlap_all_base_data_false_returns_empty() -> None:
    """When all allocated extents have data=False, nothing is returned."""
    dirty = [_e(0, 1000, True)]
    allocated = [_e(0, 1000, False)]
    result = overlap_with_allocation(dirty, allocated)
    assert result == []


# ══════════════════════════════════════════════════════════════════════════════
# Recovered-delta copy-set semantics (bitmap-loss-recovery, D5)
# ══════════════════════════════════════════════════════════════════════════════
#
# The recovered-delta copy loop iterates ONLY the ``base:allocation``
# meta-context (no dirty-bitmap).  Per NBD semantics, zero clusters
# (guest discards) are reported as data=True and MUST be copied
# explicitly — skipping them would expose stale backing data.  Holes
# (data=False) are the only extents skipped.


@pytest.mark.unit
def test_recovered_delta_copy_set_includes_zero_extents_skips_holes() -> None:
    """The recovered-delta copy set from ``base:allocation`` keeps data and
    zero extents (both ``data=True``) and drops holes (``data=False``).

    This is the pure-function basis of the D5 rule: "copy ALL data and
    zero extents into <tmp>, skipping only holes" (spec scenario "Zero
    extents are copied").
    """
    alloc_raw = [
        _e(0, 65536, True),  # allocated data
        _e(65536, 65536, True),  # zero cluster (guest discard — data=True)
        _e(131072, 65536, False),  # hole — the ONLY skipped extent
        _e(196608, 65536, True),  # allocated data
    ]
    unified = unify_extents(alloc_raw)
    copy_set = [e for e in unified if e.data]

    assert copy_set == [
        _e(0, 131072, True),  # data + zero merged (same flag, adjacent)
        _e(196608, 65536, True),
    ]
    # The hole region [131072, 196608) is absent from the copy set.
    assert all(e.data for e in copy_set)
    assert sum(e.length for e in copy_set) == 131072 + 65536


@pytest.mark.unit
def test_recovered_delta_copy_set_preserves_zero_regions_after_unify() -> None:
    """Unification never merges a zero extent into a hole: the data flag
    boundary is preserved, so the copy loop can still distinguish
    "copy explicitly" (data=True) from "skip" (data=False)."""
    alloc_raw = [
        _e(0, 65536, True),  # zero cluster
        _e(65536, 65536, False),  # hole
        _e(131072, 65536, True),  # data
    ]
    unified = unify_extents(alloc_raw)
    assert unified == alloc_raw  # flags differ → no merge across boundary

    copy_set = [e for e in unified if e.data]
    assert copy_set == [_e(0, 65536, True), _e(131072, 65536, True)]


@pytest.mark.unit
def test_recovered_delta_copy_set_is_allocation_only() -> None:
    """The recovered delta copies extents straight from ``base:allocation``
    — it must NOT be intersected with a dirty-bitmap context (there is no
    usable bitmap: that is the whole incident).  Filtering the unified
    allocation list by ``data`` yields the full copy set."""
    alloc_raw = [
        _e(0, 262144, True),
        _e(262144, 65536, False),  # hole
        _e(327680, 131072, True),
    ]
    copy_set = [e for e in unify_extents(alloc_raw) if e.data]

    # Same shape as the zero_skip FULL loop: all non-hole extents.
    assert copy_set == [_e(0, 262144, True), _e(327680, 131072, True)]
    # No extent of the copy set lies inside a hole.
    hole = _e(262144, 65536, False)
    for extent in copy_set:
        assert not (
            extent.offset < hole.offset + hole.length
            and extent.offset + extent.length > hole.offset
        )


@pytest.mark.unit
def test_recovered_delta_hole_only_layer_produces_empty_copy_set() -> None:
    """A layer whose extents are all holes contributes nothing to the
    recovered delta — holes are never copied."""
    alloc_raw = [
        _e(0, 65536, False),
        _e(65536, 65536, False),
    ]
    copy_set = [e for e in unify_extents(alloc_raw) if e.data]
    assert copy_set == []
