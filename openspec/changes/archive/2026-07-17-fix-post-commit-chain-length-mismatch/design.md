## Context

The post-commit chain length verification in `Core._blockcommit_snapshots()` (lines 2121–2144) uses `_get_chain_length(vm_config, use_base_image=True)` to measure the chain after blockcommit. This queries `vm_config.base_image` with `qemu-img info --backing-chain`. Because the base image is the **root** of the backing chain (it has `backing-filename = NULL`), the output always contains exactly 1 entry — the base image itself. This makes the comparison `chain_length_after != expected_length` always fail whenever any snapshots were merged, producing a false CRITICAL error.

The `use_base_image` parameter was introduced solely for this purpose (see `_get_chain_length` docstring at line 1993). It was based on the (incorrect) assumption in the `chain-integrity-verification` spec (line 46) that `qemu-img info --backing-chain` on the base image returns the full remaining chain. The backing chain is a directed linked list from top to bottom — the base image does not know about layers stacked on top of it.

Additionally, the `expected_length` calculation `chain_length_before - len(to_merge)` assumes each merged snapshot removes exactly 1 entry from the chain. But `virsh blockcommit --top X --base Y --delete` removes **all** intermediate files between X and Y, which may be more than just X.

## Goals / Non-Goals

**Goals:**
- Fix post-commit chain length measurement to reflect the actual remaining chain length
- Remove the now-unnecessary `use_base_image` parameter from `_get_chain_length()`
- Update the spec to correctly describe post-commit verification
- Replace mocked tests with fixture-based tests that exercise realistic `qemu-img info --backing-chain` outputs

**Non-Goals:**
- Do NOT change the blockcommit execution logic (BlockCommitManager, QemuImgCommitManager) — they work correctly
- Do NOT change the pre-commit verification (`_verify_backing_chain()`) — it is correct
- Do NOT change `ILifecycleManager` or any other ABC interface
- Do NOT change the `expected_length` calculation to predict intermediate-file removal — accept the actual post-commit chain length as truth

## Decisions

### Decision 1: Use snapshots from updated IStateManager for post-commit measurement

**Rationale:** After a successful blockcommit, the merged snapshots are no longer on disk. The most reliable way to find the current active layer is to query `IStateManager` for the most recent snapshot that was NOT merged. The flow:

1. Before blockcommit: measure `chain_length_before` from current active snapshot (unchanged)
2. Execute blockcommit
3. Remove merged snapshots from `IStateManager`
4. Measure `chain_length_after` by calling `_get_chain_length(vm_config)` **without** `use_base_image` — it now finds the most recent surviving snapshot

**Alternatives considered:**
- **A: Save the pre-commit active snapshot path and reuse it post-commit.** Unsafe — the file may have been deleted by `virsh blockcommit --delete`.
- **B: Query the base image but expect `chain_length_after == 1`.** Not useful — does not verify anything meaningful.
- **C: Walk the chain upward from the base using `backing-filename` references tracked in state.** Over-engineered when the simpler approach works.

### Decision 2: Accept actual post-commit chain length as truth

**Rationale:** The pre-commit `expected_length = chain_length_before - len(to_merge)` cannot predict how many intermediate files `virsh blockcommit --delete` will remove. Instead, verify that:

1. The blockcommit succeeded (result.success is True)
2. The post-commit chain length is **less** than the pre-commit chain length
3. If `chain_length_after == chain_length_before`, log CRITICAL

This is a simpler, more robust check. It correctly flags the case where blockcommit silently failed (chain unchanged) while accepting any actual reduction.

**Alternatives considered:**
- **A: Compute the exact expected length by counting intermediate files.** Requires querying the backing chain for each merged snapshot to discover what's between it and base. Complex, error-prone, and unnecessary — the blockcommit result already tells us if the operation succeeded.
- **B: Remove post-commit verification entirely.** Loses the safety net that catches silent blockcommit failures.

### Decision 3: Remove `use_base_image` parameter from `_get_chain_length()`

**Rationale:** This parameter was introduced exclusively for the broken post-commit path. With the fix, it has no callers. Removing it simplifies the API.

**Impact on callers:**
| Caller | Line | Change |
|---|---|---|
| `_blockcommit_snapshots()` pre-commit | 2087 | No change (uses default) |
| `_blockcommit_snapshots()` post-commit | 2123 | Remove `use_base_image=True`, call AFTER state cleanup |
| `check_integrity()` | (elsewhere) | No change |

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| Post-commit `qemu-img info` fails because the active layer is locked | `_get_chain_length()` returns `None` on failure; verification is skipped with a WARNING. The blockcommit itself succeeded, so this is acceptable. |
| Merged snapshots are removed from state BEFORE post-commit measurement | If the VM crashes between state removal and measurement, snapshots are gone from state but the chain is healthy. This is acceptable — the next run will see the correct state. The CRITICAL log path (snapshots preserved in state) only fires when the measurement mismatches, which now only happens when the chain is genuinely unchanged. |
| State removal happens before measurement, making pre/post comparison asymmetric | By design — we want to measure the same logical starting point (the most recent surviving snapshot) both before and after. The "before" measurement happened while the merged snapshot was still the active layer; the "after" measurement happens with the next surviving snapshot as the active layer. Both measure from the top of the current chain. |

## Open Questions

None — the root cause is well-understood and the fix is straightforward.
