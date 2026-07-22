## Context

qsnap currently ships two `IBackupProvider` implementations: `FileCopyBackupProvider` (rsync whole-file copy + `qemu-img rebase -u`, the original mechanism from change `2026-07-13-implement-domain-modules`) and `BitmapBackupProvider` (NBD pull-model + libnbd dirty-block copy loop, backing-chained deltas since `2026-07-22-bitmap-dirty-block-transfer`). Codebase-wide inventory confirmed `rsync` has exactly one functional call site — `FileCopyBackupProvider.transfer_missing()` (`qsnap/modules/backup/file_copy.py:154-196`, two command variants with/without `--bwlimit`). Everything else that mentions rsync is config plumbing (`rate_limit`, `copy_base`, `incremental_mode`), env-validation (`which rsync` hard check in `Core._validate_environment`), docs, specs, and tests.

Since `2026-07-22-bitmap-dirty-block-transfer`, bitmap incrementals are backing-chained qcow2 deltas, so file-copy's last unique advantage (chain-based restore) is gone. Gap analysis of file-copy's remaining unique capabilities: offline-VM FULL (direct `qemu-img convert`), offline-VM increments (rsync of snapshot files), libvirt < 7.2 fallback (factory), raw-disk copies, multi-disk file iteration, `rsync --bwlimit` throttling. Per product decision, **none of these are preserved** in this change — no fallback, no legacy libvirt/qemu support; breaking current configurations is accepted to minimize long-term maintenance surface.

Constraints: AGENTS.md paradigm (DI + ABC interfaces, factory-only module instantiation, frozen config dataclasses, result objects — all preserved untouched), zero runtime PyPI dependencies, all code/comments/docs in English, tests mirror the production hierarchy (TESTING.md).

## Goals / Non-Goals

**Goals:**

- One backup provider (`BitmapBackupProvider`), one transfer mechanism (NBD/libnbd), one config surface.
- Delete `FileCopyBackupProvider`, the `rsync` invocation, and every config field, env check, spec, test, and doc reference that exists solely for file-copy/rsync.
- Interface hygiene: `IBackupProvider.transfer_missing()` loses the dead `rate_limit` parameter; factory fallback branches removed.
- Removed config fields degrade gracefully: deprecation WARNING + ignore (existing precedent).
- Repo ends in a consistent state: `ruff`, `pyright --strict`, and the remaining pytest suite green; `rg -i "rsync"` over `qsnap/` returns zero hits.

**Non-Goals:**

- No replacement fallback provider of any kind; no libvirt < 7.2 support; no host-without-libnbd support (already hard errors).
- No offline-VM backup support (FULL or incremental) — a future change will re-add it via qemu-nbd-served bitmaps (virtnbdbackup pattern).
- No rate-limiting substitute (cgroups/io.max is a possible future capability, not this change).
- No changes to the bitmap FULL data path (`qemu-img convert` over NBD stays — separate later change), no restore-path changes, no multi-disk work, no `IStateManager` schema changes.

## Decisions

- **D1 — Delete `FileCopyBackupProvider` wholesale (no FULL-only remnant).** Its only unique surviving capability is offline FULL via direct convert; keeping a FULL-only provider would preserve the factory branch, config fields, spec requirements, and ~35 tests — precisely the maintenance surface this change eliminates. Offline support returns later in a libnbd-native form. *Alternatives rejected:* (a) keep provider FULL-only — retains most complexity for a scenario explicitly accepted as broken; (b) convert file_copy increments to libnbd — that is the later transport-unification change, and it targets the bitmap provider, not this class.

- **D2 — libvirt < 7.2 is a hard `RuntimeError` in `DefaultFactory`, identical in posture to missing libnbd (design R4).** *Alternative rejected:* silent fallback to any other behavior — the user explicitly selected NBD mode; a missing platform dependency is an actionable error, not a mode switch.

- **D3 — Removed TOML fields (`incremental_mode`, `rate_limit`, `copy_base`) log a deprecation WARNING and are ignored.** Mirrors the existing `full_every` precedent (`config-parsing`). *Alternative rejected:* hard config error — breaks otherwise-valid user configs over dead keys; warning + ignore is the established project convention.

- **D4 — `rate_limit` is removed from the `IBackupProvider.transfer_missing()` signature, not kept-and-ignored.** A dead ABC parameter invites misuse and perpetuates the concept; pyright strict + contract tests make the signature change exhaustive across implementations and mocks. *Alternative rejected:* keep the parameter documented-as-ignored (current bitmap practice) — dead weight in the interface.

- **D5 — The `which rsync` env-validation check is deleted unconditionally.** With a single backup mode there is no condition to key it on. The libnbd check (bitmap targets) already exists and now covers every target.

- **D6 — `verify_backup()` (file-copy-oriented helper in `qsnap/utils/verification.py`) is deleted after a grep-verification step proves no remaining callers.** `verify_full_backup()` and `verify_bitmap_incremental()` are untouched; verification tiers for bitmap chains are unchanged.

- **D7 — The `rate-limit` capability spec is deleted entirely, not kept as a placeholder.** No NBD throttling mechanism exists post-change; a future throttling mechanism would be specified as a new capability.

- **D8 — Pre-flight truncated-qcow2 cleanup is kept and reworded ("truncated transfer artifact").** The `qemu-img info` probe defends against any interrupted transfer (NBD .tmp leftovers included), not just rsync partials; behavior unchanged.

- **D9 — Existing file-copy backup chains on target storage remain valid and restorable.** Restore is format-based (qcow2 chain + `rebase -u`), not provider-based; no data migration, no state-schema change. Deletion of the provider does not orphan user data.

- **D10 — Project-wide reference sweep is part of the change, verified by `rg`.** Code, docstrings, comments, `__all__` re-exports, `AGENTS.md` (pipeline pseudocode D3, architecture diagram, shell-abstraction wording), `README.md` (including removal of the completed "Migration from rsync to NBD" section), `TESTING.md`, `qsnap.toml.example`, and TOML fixtures are all updated in the same change — spec/code/doc consistency is a single atomic unit.

## Risks / Trade-offs

- [Offline VMs lose all backup coverage (FULL and incremental)] → Accepted and documented as **BREAKING** in proposal + README; error surfaces clearly (`virsh backup-begin` failure on inactive domain). Future change restores it libnbd-natively (`qemu-nbd -r --bitmap` on offline qcow2).
- [Hosts with libvirt < 7.2 or without python3-libnbd stop working] → Accepted; hard `RuntimeError` with actionable install/upgrade message at factory/env-validation time, consistent with design R4.
- [User TOMLs containing removed fields] → Deprecation WARNING + ignore (D3); documented in README migration notes.
- [Mass test deletion (~85 tests) may incidentally drop coverage of behaviors that survive (FULL verification, `nbd_full_export` for bitmap FULL and restore)] → test-plan.md pins the keep-list: surviving behaviors live in `test_bitmap.py`, `test_full_verification.py`, `test_verification.py`, `test_nbd_full_backup.py` (rewritten onto `BitmapBackupProvider`), `tests/utils/test_nbd.py`; coverage delta is reviewed before closing the change.
- [Stray callers of deleted helpers (`verify_backup`, `parse_rate_limit`, `rate_limit_to_kib`) surface late] → tasks include `rg`-verification steps before deletion; `ruff` + `pyright --strict` are the compile-time net.
- [Spec drift: 16 modified + 1 deleted capability is wide] → All deltas are small removals/rewordings derived from a single inventory (this design + proposal); `openspec validate` and a post-sync `rg -i "rsync|file-copy" openspec/specs/` gate archiving.
- [Restore of historical file-copy chains must not regress] → D9: restore path untouched; existing `restore-command` tests stay green (scenario renamed only).

## Migration Plan

1. Land the change atomically on the main branch (code + tests + specs + docs in one commit series); no staged rollout, no feature flag — removal is the feature.
2. User-facing migration (documented in README): hosts need libvirt ≥ 7.2 and `python3-libnbd` (already required for the default bitmap mode); `rsync` may be uninstalled; removed TOML keys are warned-and-ignored, so existing configs keep loading.
3. Rollback: `git revert` the change; no persistent-state or on-disk-format changes exist to roll back.

## Open Questions

None blocking. Deferred by explicit product decision to later changes: FULL-path transport unification (libnbd copy loop for FULL), offline-VM bitmap increments, multi-disk bitmap transfer, throttling mechanism, hardening bundle (fs freeze/thaw, checkpoint redefine-validate, NBD connect-retry, write-side flush).
