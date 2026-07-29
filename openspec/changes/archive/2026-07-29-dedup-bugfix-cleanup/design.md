## Context

A deep code audit of the qsnap codebase (4200+ lines in `qsnap/core/__init__.py`, plus modules, utils, tests) identified systematic issues: duplicated verification logic across lifecycle managers and Core, duplicated retry patterns, and 4 independent backing-chain verification implementations. These duplications arose from organic growth — each feature was added independently, often copy-pasting similar logic. Additionally, 2 active bugs affect production behavior: `target_chain_length or 0` triggers unnecessary FULL backups, and `deep_verify` is silently skipped in the main blockcommit path.

The project follows strict architectural rules (DI, ABC interfaces, frozen dataclasses). Any changes must preserve these invariants.

## Goals / Non-Goals

**Goals:**
- Eliminate code duplication (6 areas) by extracting shared functions/methods into reusable components
- Fix all 4 identified bugs (A1–A4 from the audit plan)
- Remove dead code (config fields, dead method, dead test elements) without breaking existing functionality
- Fix documentation drift (qsnap.toml.example, 4+ spec files)
- Clean up test suite (centralize helpers, split monolithic test file, fix tautological tests)
- Maintain backward compatibility for all valid configuration files

**Non-Goals:**
- Do NOT implement the `deep_check_targets` feature (field is removed as dead config — future implementation is out of scope)
- Do NOT fix the unresolved questions from the audit report (ExternalSnapshotProvider post-creation validation, transfer_missing Path B reachability)
- Do NOT change any ABC interface signatures (except removing dead fields from config dataclasses)
- Do NOT add new runtime dependencies
- Do NOT change the pipeline execution order or snapshot/backup format

## Decisions

### D1: New verification helpers live in `qsnap/utils/verification.py`

**Decision:** Place `deep_verify_base_image()` and `scan_backing_chain()` in the existing `qsnap/utils/verification.py`, which already hosts `verify_full_backup()` and `verify_bitmap_incremental()`.

**Rationale:** These are pure verification functions with no knowledge of Core or lifecycle modules. They accept `IShell` and file paths, return result dataclasses. Keeping them with existing verifiers avoids creating a new module and follows the established pattern.

**Alternatives considered:**
- New module `qsnap/utils/chain.py` — rejected: adds unnecessary file, existing verifications already in verification.py
- Make them static methods on a class — rejected: violates project's preference for pure functions; retention engine is already a pure-function pattern

### D2: `scan_backing_chain()` returns a `ChainScanResult` dataclass

**Decision:** Create `ChainScanResult` (frozen dataclass) with fields: `paths: set[str]`, `broken_files: list[str]`, `success: bool`, `error: str | None`.

**Rationale:** The 4 existing call sites have different needs:
- `_verify_backing_chain()` needs a `ChainVerifyResult` with `broken_file: Path | None` → converts `ChainScanResult.broken_files[0]` if any
- `_check_snapshot_chain()` needs `set[str]` of paths + `broken: list` side-effect → uses `.paths` and `.broken_files`
- `_check_target_consistency()` needs success/failure → checks `.success`
- Post-cleanup in `_cleanup_backups()` needs CRITICAL log on failure → checks `.success`

A single dataclass covers all cases. Each call site converts/extracts what it needs.

**Alternatives considered:**
- Return `tuple[set[str], list[str], bool, str | None]` — rejected: fragile, type-unclear
- Multiple specialized functions — rejected: defeats deduplication purpose

### D3: `_execute_with_retry()` is a Core private method, not a utility

**Decision:** Make `_execute_with_retry()` a private method on `Core`, not a standalone function.

**Rationale:** The retry logic depends on `target.backup_retry_max`, `target.backup_retry_base`, `parse_retry_duration()`, `compute_backoff()`, and `is_retryable()` — all available to Core. The method accepts a `Callable[[], ResultT]` operation and a `TargetConfig`. Keeping it on Core avoids threading too many parameters through a utility function.

**Alternatives considered:**
- Standalone function in `qsnap/utils/retry.py` — would need `is_retryable_fn` parameter and `parse_retry_duration` import, making it less cohesive
- Decorator pattern — rejected: operations have different signatures and error handling

### D4: `check_state()` and `reconcile()` share detectors, not actions

**Decision:** Extract 4 private detector methods on Core: `_detect_phantom_snapshots()`, `_detect_phantom_fulls()`, `_detect_stale_deps()`, `_detect_broken_chains()`. These return data (lists/dicts). `check_state()` calls them and formats `StateCheckResult`. `reconcile()` calls them and performs repair actions (state mutation, XML refresh, file deletion).

**Rationale:** The detection logic is identical between the two methods, but the action logic differs significantly (reconcile has XML cross-check, dry-run gating, state mutation). By extracting only detection, we avoid coupling the repair logic. The detector methods are pure — no side effects, no state mutation, no logging of severity.

**Alternatives considered:**
- Make `reconcile()` call `check_state()` first then act on results — rejected: `check_state()` formats a `StateCheckResult` for human display, not structured enough for programmatic repair
- Extract a `ConsistencyScanner` class — rejected: over-engineering for 4 simple methods

### D5: Config field removal uses deprecation WARNING

**Decision:** When removing `incremental` from `TargetConfig`, the facade will detect the key in TOML and log a WARNING: `"incremental is deprecated and ignored — all backups are now bitmap-based"`. The field is not stored. `deep_check_targets` is simply removed (it was never documented as a user-facing config key in qsnap.toml.example).

**Rationale:** `deep_check_targets` was an internal-only field (not in qsnap.toml.example), safe to remove silently. `incremental` is documented in the example file and users may have it configured — a deprecation period is appropriate.

**Alternatives considered:**
- Hard error on `incremental` — rejected: unnecessarily breaks existing configs for a field that has no effect
- Keep the field but mark it deprecated — rejected: dead fields accumulate; config dataclasses should only contain consumed data

### D6: `parse_duration` / `parse_stall_timeout` move to `qsnap/utils/time.py`

**Decision:** Move from `qsnap/retention/time_based.py` to `qsnap/utils/time.py`. Update imports in `qsnap/core/__init__.py` and `qsnap/retention/time_based.py`.

**Rationale:** These are general-purpose time-parsing utilities used by Core, not retention-specific logic. `time_based.py` should contain only `TimeBasedRetention` and its helper `_keep_count`. This is a pure relocation — no behavior change.

### D7: Test helpers centralized in conftest.py

**Decision:** Add `success_result` and `failure_result` factory fixtures to `conftest.py`. Add `_add_deferred_with_since` as a helper (not fixture) in `tests/helpers.py` or extend `make_vm_config`. Add `clean_shell` fixture for tests needing MockShell without validation expectations.

**Rationale:** 16+ identical helper functions across 8+ files is a maintenance hazard. Fixtures are the pytest-idiomatic way to provide test dependencies. The `clean_shell` fixture addresses the 63+ direct `MockShell()` instantiations.

## Risks / Trade-offs

- **[B2: Chain-verify unification risk]** The 4 existing implementations have subtly different JSON parsing strategies. Unifying them could introduce edge-case regressions. → **Mitigation**: extensive unit tests for `scan_backing_chain()` covering all 4 call-site usage patterns. Keep existing tests and add new ones.
- **[B5: Check/reconcile detector risk]** `reconcile()` has additional logic (XML cross-check, `_parse_domain_xml_source_paths`) that check_state doesn't use. The detectors must return raw data without including reconcile-specific state. → **Mitigation**: detectors return pure lists, no `xml_paths` prepopulation. `reconcile()` does its own XML parsing before calling detectors.
- **[C3: incremental field removal]** Users with `incremental = false` in config may be alarmed by the WARNING. → **Mitigation**: WARNING message explicitly says "all backups are now bitmap-based" so users understand the change is safe.
- **[Spec drift fixes]** Spec updates must be consistent with code changes. If specs and code diverge further, future audits will flag false positives. → **Mitigation**: all spec deltas in this change reference exact code locations.

## Migration Plan

1. No data migration needed (IStateManager schema unchanged)
2. Users with `incremental = false` in config see a deprecation WARNING, no action required
3. Users with `deep_check_targets = true` — field is silently removed; no action required (never consumed)
4. No systemd unit changes
5. No backup/snapshot format changes

## Open Questions

None — all design decisions resolved.
