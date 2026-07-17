## Why

Post-commit chain length verification in `Core._blockcommit_snapshots()` produces **false-positive CRITICAL errors** because it measures the post-commit chain length from `vm_config.base_image`, whose `qemu-img info --backing-chain` always returns 1 entry (the base image has no backing file). A successful blockcommit is incorrectly flagged as failed, and merged snapshots are not removed from `IStateManager`, causing cascading failures on subsequent runs. This is a high-priority bug — it is enabled by default (`chain_verify_after_commit = true`) and affects every user who runs blockcommit (i.e., every user with retention policies that expire snapshots).

## What Changes

- Fix `_get_chain_length()` post-commit measurement to query the **current active layer** (the most recent surviving snapshot) instead of the base image
- Fix `expected_length` calculation to account for the fact that `virsh blockcommit --top X --base Y --delete` removes ALL intermediate files between X and Y, not just X
- Update the `chain-integrity-verification` spec: change the post-commit requirement from "query the base image" to "query the current active layer"
- Replace the broken unit tests that mock `_get_chain_length` with integration-style tests that exercise the real `qemu-img info --backing-chain` flow (using fixture shell outputs)
- Remove or obsolete the `use_base_image` parameter from `_get_chain_length()` since it was introduced solely for the broken post-commit path

## Capabilities

### New Capabilities
<!-- None — this is a bug fix, not a new capability -->

### Modified Capabilities
- `chain-integrity-verification`: Post-commit chain length verification MUST query the **current active layer** (the most recent snapshot that survived blockcommit), not the base image. The `use_base_image` parameter SHALL be removed. The `expected_length` calculation SHALL account for all intermediate files removed by `virsh blockcommit --delete`.

## Impact

| Area | Impact |
|---|---|
| `qsnap/core/__init__.py` | `_get_chain_length()` — remove `use_base_image` parameter; `_blockcommit_snapshots()` — fix post-commit measurement and expected_length calculation (lines 1985–2030, 2121–2144) |
| `openspec/specs/chain-integrity-verification/spec.md` | Delta spec: change Requirement "Post-commit chain length verification" (lines 45–61) |
| `tests/core/test_pipeline.py` | Tests `test_post_commit_chain_shortened_as_expected`, `test_post_commit_chain_length_unchanged_critical`, `test_post_commit_verification_fails_snapshots_preserved` (lines 1801–1921) — replace mocked approach with fixture-based `qemu-img info --backing-chain` outputs |
| `tests/mocks/mock_shell.py` | May need new fixture entries for post-commit chain queries |
| **No ABC interface changes** | `ILifecycleManager`, `IConfigFacade`, `IStateManager` are unaffected |
