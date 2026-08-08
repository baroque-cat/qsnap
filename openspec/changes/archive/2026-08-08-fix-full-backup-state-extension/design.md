# Design: fix-full-backup-state-extension

## Context

`_full_backups.json` records FULL backups as `{name, path, timestamp, disk}` per target.
`JsonStateManager.record_full_backup()` derives `path = str(Path(target_path) / name)`, and
every existence-based consumer (`_detect_phantom_fulls` at `core/__init__.py:1770`, startup
validation at `:3081`, the `_backup_target` phantom filter at `:4938`, target consistency
check at `:963-977`, `reconcile` at `:2284-2305`) compares `FullBackupInfo.path` against
the filesystem. The archived design decision D3 (`2026-07-27-fix-broken-backing-chain`)
fixed the storage contract: `_full_backups.json` names carry the `.qcow2` extension;
`_dependencies.json` keys are stems, normalized via `_normalize_full_name()` at lookup.
Commit `0811599` broke the first half by forwarding `BackupResult.snapshot_name` (a stem)
into `record_full_backup()`.

Constraints:

- Zero runtime dependencies, pure stdlib; state files are plain JSON under
  `/var/lib/qsnap/state/`.
- No ABC signature changes (non-breaking fix).
- Production installs may hold any of three state shapes: pre-regression extended names,
  buggy-version stem names, or a mix (downgrade/upgrade oscillation).
- The DI paradigm requires the test mock (`InMemoryStateManager`) to implement the same
  contract as `JsonStateManager`; contract tests are parametrized over both.

## Goals / Non-Goals

**Goals:**

- Restore the `.qcow2` name invariant for `_full_backups.json` entries (`name` and the
  derived `path`).
- Make the state layer self-healing: idempotent load-time normalization of stem-format
  entries left by the buggy version.
- Make `remove_full_backup()` tolerant to both name forms so every existing call site
  (stem from `provider.list()`, extended from state reads) keeps working unchanged.
- Eliminate the test workarounds that mask the bug and turn them into assertions of the
  corrected behavior.

**Non-Goals:**

- Changing `BackupResult.snapshot_name` semantics (stays a stem — it is the provider's
  backup identifier, not a filename).
- Touching `_dependencies.json`, `_target_state.json`, per-VM `{vm}.json`, libvirt
  checkpoints, snapshot world, restore flow (verified orthogonal — restore never reads
  `_full_backups.json`).
- Fixing the pre-existing crash window between the atomic `mv` of the backup file and
  `record_full_backup()` (unrecorded FULLs are deleted as orphans by `reconcile` today;
  this change neither worsens nor repairs that window).
- Adding production-VM state fixtures; migration behavior is covered by synthetic
  unit-level state dicts (explicit product-owner decision).

## Decisions

### D1 — Defense in depth: fix the call site AND enforce the invariant in the state manager

`Core._backup_target()` records `f"{result.snapshot_name}.qcow2"` (restoring the
pre-regression call shape and the archived D3 contract), and
`JsonStateManager.record_full_backup()` additionally appends `.qcow2` to `name` when
missing before deriving `path`.

- **Why both:** the call-site fix restores the documented contract at the only production
  producer; the state-manager guard makes the invariant caller-independent, so no future
  call site (including the currently dead `set_last_full_backup()` wrapper, which
  delegates) can regress it.
- **Alternative rejected — call site only:** leaves a silent exact-format trap; the same
  regression class would recur on the next refactor (it already did once).
- **Alternative rejected — state manager only:** hides the producer's contract violation;
  the call site would still pass a semantically wrong argument.

### D2 — Load-time idempotent migration in `_load_full_backups()`, ordered BEFORE dedup

`_load_full_backups()` normalizes every entry to the extended form before the existing
dedup pass runs, then persists the repaired data (same write-back pattern as the existing
dedup migration). Normalization checks `name` and `path` **independently** (append
`.qcow2` only if the respective field lacks it), guarding against double-append and
against asymmetric entries.

- **Why load-time:** mirrors the proven `_load_dependencies()` legacy-key migration —
  zero operational steps, idempotent, lazy. Pre-regression production state (already
  extended) passes through unchanged: zero migration cost for the common case.
- **Why before dedup:** the dedup pass keys on the raw `name`. Running normalization
  after dedup would let a stem entry and its extended twin survive as "distinct", then
  collapse into duplicates after normalization.
- **Alternative rejected — one-shot migration CLI command:** requires operator action,
  ordering discipline on upgrade, and a new CLI surface for a self-healable problem.

### D3 — Name-format tolerance inside `remove_full_backup()`, not at call sites

`remove_full_backup()` normalizes the lookup name to the extended form before matching
(the inverse direction of the existing `_normalize_full_name()`, which strips the
extension for the dependency world). A new private helper `_to_extended_name(name)`
serves both `record_full_backup()` and `remove_full_backup()`.

- **Why:** `_cleanup_backups()` (`core/__init__.py:5681`) passes `backup.name` from
  `provider.list()` — always a stem (`file.stem` at `bitmap.py:1296`) — while
  startup-validation and reconcile pass state-derived extended names. Tolerance inside
  the manager makes ALL call sites correct with zero call-site changes and removes the
  exact-match trap permanently.
- **Alternative rejected — patching `core:5681` to append `.qcow2`:** fixes one caller,
  keeps the trap for the others, and couples Core to the storage name format (the
  dependency world already proves lookup-time normalization is the house pattern, D3 of
  the archived change).

### D4 — `InMemoryStateManager` mirrors the contract

The test mock implements the same normalization in `record_full_backup`,
`remove_full_backup`, and read-back. Contract tests (`tests/interfaces/`) are
parametrized over `JsonStateManager` and `InMemoryStateManager` so behavioral divergence
fails CI. The mock mirrored the bug today (`path = Path(target_path) / name` with no
invariant) — that is precisely why unit tests missed the regression.

### D5 — Provider contract unchanged

`BitmapBackupProvider.run_backup()` keeps returning the stem in
`BackupResult.snapshot_name`; `provider.list()` keeps returning `name=stem`,
`path=with .qcow2`. Core owns the stem→filename derivation at the state boundary. This
keeps the fix confined to `core` + `state` + tests.

## Risks / Trade-offs

- **[Mixed-format duplicate entries during migration]** → normalization runs before dedup
  (D2); covered by a unit test with a mixed stem/extended state dict.
- **[Double `.qcow2` append on already-correct entries]** → per-field `endswith(".qcow2")`
  guard; covered by idempotency unit tests (record twice, load twice).
- **[Mock/production divergence hides regressions again]** → contract tests parametrized
  over both implementations (D4); the `.qcow2` invariant becomes an asserted contract,
  which is the gap that let the regression escape CI.
- **[Downgrade to the buggy binary re-contaminates state]** → accepted: reads remain safe
  (extended paths validate correctly even under the buggy binary), and re-upgrading
  self-heals via D2. No data loss in either direction.
- **[Integration tests that relied on the workarounds change meaning]** → the workarounds
  are deleted and their call sites converted into assertions of the corrected behavior
  (see test-plan); no test is silently weakened.

## Migration Plan

1. **Deploy:** no operational steps. First run after upgrade: `_load_full_backups()`
   repairs transitional stem entries in memory and persists them on the next state write;
   new records are written extended.
2. **Verify:** `qsnap check` reports no phantom FULLs for real on-disk files; a second
   `qsnap run` creates a delta (not a new FULL) when the chain is within
   `target_chain_length`.
3. **Rollback:** rolling back to the fixed or pre-regression binary is byte-compatible.
   Rolling back to the buggy binary is read-safe but re-introduces stem records for new
   FULLs (re-healed on the next upgrade).

## Open Questions

None — all architectural questions were resolved during the deep-explore verification
phase (see proposal for the evidence trail).
