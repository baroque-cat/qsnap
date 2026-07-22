"""Pure extent-processing functions for NBD dirty-block transfer.

Semantics ported from virtnbdbackup's extent handler
(``examples/virtnbdbackup/libvirtnbdbackup/extenthandler/extenthandler.py``:
``_unifyExtents`` and ``overlap``).  Both functions are pure,
deterministic, and I/O-free (IRetentionEngine-style): no global state,
no wall-clock, no side effects.
"""

from __future__ import annotations

from qsnap.models.results import NbdExtent


def unify_extents(extents: list[NbdExtent]) -> list[NbdExtent]:
    """Merge consecutive extents of the same kind into larger blocks.

    "Same kind" means the same ``data`` flag, and "consecutive" means
    both list-consecutive AND disk-adjacent (``next.offset ==
    current.offset + current.length``) — the input is expected to come
    from a single meta-context query, where extents are adjacent by
    construction.  The adjacency condition is defensive: two same-kind
    extents separated by a gap on disk are never merged, so a
    non-contiguous input can never fabricate coverage of the gap.

    Ported from virtnbdbackup ``_unifyExtents``.  Returns a new list;
    the input is not mutated.  Empty input yields empty output.
    """
    unified: list[NbdExtent] = []
    current: NbdExtent | None = None
    for extent in extents:
        if current is None:
            current = extent
        elif current.data == extent.data and current.offset + current.length == extent.offset:
            current = NbdExtent(
                offset=current.offset,
                length=current.length + extent.length,
                data=current.data,
            )
        else:
            unified.append(current)
            current = extent
    if current is not None:
        unified.append(current)
    return unified


def overlap_with_allocation(
    dirty: list[NbdExtent],
    allocated: list[NbdExtent],
) -> list[NbdExtent]:
    """Intersect dirty extents with allocated extents (sparse filtering).

    *dirty* is the unified extent list from the
    ``qemu:dirty-bitmap:<name>`` meta-context; *allocated* is the
    unified extent list from ``base:allocation``.  Only sub-ranges that
    are both dirty (``data=True`` in *dirty*) and allocated
    (``data=True`` in *allocated*) are returned — regions the guest
    dirtied but later discarded (fstrim) or never allocated are
    filtered out, so the copy loop reads no hole/zero data.

    Both inputs must be sorted by offset (block-status query order).
    Ported from virtnbdbackup ``overlap``: a two-pointer sweep that
    skips non-data and non-intersecting extents.  Returned extents all
    have ``data=True``.
    """
    result: list[NbdExtent] = []
    i = 0  # index into allocated
    j = 0  # index into dirty
    while i < len(allocated) and j < len(dirty):
        base = allocated[i]
        backup = dirty[j]

        # Skip unallocated base extents and base extents that end at or
        # before the dirty extent starts (no intersection).
        if not base.data or base.offset + base.length <= backup.offset:
            i += 1
            continue
        # Skip clean dirty extents and dirty extents that end at or
        # before the base extent starts (no intersection).
        if not backup.data or backup.offset + backup.length <= base.offset:
            j += 1
            continue

        offset = max(base.offset, backup.offset)
        end = min(backup.offset + backup.length, base.offset + base.length)
        result.append(NbdExtent(offset=offset, length=end - offset, data=True))

        # Advance the pointer(s) whose extent ends at the intersection end.
        if end == base.offset + base.length:
            i += 1
        if end == backup.offset + backup.length:
            j += 1

    return result
