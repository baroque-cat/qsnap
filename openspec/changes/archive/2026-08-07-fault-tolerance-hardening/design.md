## Context

qsnap is a QEMU/KVM snapshot and backup orchestrator built on strict DI (ABC interfaces,
result objects, frozen config dataclasses, `IShell` for all external commands). A deep
code-vs-spec audit (this repo, all 64 specs) plus targeted code verification found three
fault-tolerance gaps that contradict the project's conservative preserve-over-delete
philosophy:

1. **ENOSPC is only handled passively.** Verified current behavior:
   - Backup transfers write `.tmp` → atomic `mv` (leftovers cleaned by next-run pre-flight).
   - ENOSPC is classified non-retryable (`qsnap/utils/retry.py:31-34`) → fast fail.
   - FULL verify/create failure raises `BackupAbortError` (`core:4891-4903`); incremental
     transfer failure also aborts (`core:4994-4999`). Both abort the **whole VM** before
     retention/cleanup (`core:5049-5058`) — a full target A prevents target B (different
     storage) from being backed up at all.
   - `JsonStateManager._save` (`qsnap/state/json_manager.py:94-111`) does **not** catch
     `OSError` — ENOSPC in the state directory crashes the entire process, killing
     remaining VMs (while `_load` does catch).
   - Blockcommit ENOSPC surfaces as `RuntimeError` (VM abort) instead of deferral.
   - No proactive free-space check exists anywhere (no `statvfs`/`disk_usage`).
   - Auto-resume prerequisites already hold: checkpoints rotate only after success +
     verify (`bitmap.py:1918-1944`), onchange baselines update only after success
     (`core:5038-5042`), state records only after success (`core:4333-4337`).
2. **Quiesce covers only the first disk.** `quiesce = vm_config.snapshot_quiesce and
   index == 0` (`core:3545`); each disk is a separate `virsh snapshot-create-as` call, so
   disks 2..N are snapshotted after the guest thawed — no cross-disk consistency, no
   cross-disk atomicity (a failed disk N leaves disks 1..N-1 recorded).
3. **`snapshot_preserve_min` defaults to 0 (inactive)** (`models/config.py:57`), so
   default retention can commit local history down to the active layer.

Constraints: zero new runtime dependencies (stdlib only); every external command through
`IShell`; expected failures as result objects, never exceptions; modules stateless; Core
the only coordinator; all code/comments/docs in English.

Stakeholders: operators running qsnap via systemd timers (hourly `qsnap.timer` with
`Persistent=true`; weekly `qsnap-check.timer`).

## Goals / Non-Goals

**Goals:**
- ENOSPC anywhere (target, state dir, snapshot dir) never causes data deletion, never
  crashes the process, never blocks unaffected targets/VMs, and the next scheduled run
  auto-resumes without operator action.
- First-class space-error classification at one pure helper; distinct CLI exit code.
- Proactive free-space gate before every FULL/incremental transfer (configurable
  strict/warn/off).
- One guest-agent freeze covering ALL disks of a VM; all-or-nothing multi-disk snapshot
  creation via a single `virsh snapshot-create-as` with multiple `--diskspec`.
- Safe default `snapshot_preserve_min = 48` keeping ~2 days of hourly snapshots
  uncommitted.

**Non-Goals:**
- Synthetic FULL construction from old FULL + increments (explicitly rejected: breaks the
  independent-verification trust model — M3 `qemu-img compare` against the live source is
  the ground truth; re-pull via `force_full` is already the recovery path).
- Calendar-based FULL triggers, rate limiting, or any retention-model changes beyond the
  preserve_min default.
- Auto-rebase or automatic repair of broken backing chains (remains operator-only).
- Changing retry classification (`no space left on device` stays non-retryable).
- Rewriting the single-disk `ISnapshotProvider.create()` (kept for compatibility/tests).

## Decisions

### Task 1 — ENOSPC handling

**D1. Classification: pure string-matching helper, not exception types.**
New pure function `is_space_error(error: str | None) -> bool` in `qsnap/utils/retry.py`
(next to `is_retryable`), matching case-insensitively `no space left on device` and
`disk quota exceeded` (EDQUOT). Rationale: errors surface as stderr text inside
`ShellResult`/`*Result.error` strings (result-object paradigm — expected failures are not
exceptions), so errno-based `OSError` matching would miss most paths. The one place an
`OSError` object exists (state writes) converts via `str(e)` before classification.
Alternative considered: dedicated `SpaceError` exception hierarchy — rejected: violates
"never raise for expected failures" and cannot cross the `IShell` string boundary.

**D2. Per-target suspension, not VM abort, for space errors in backup steps.**
In `Core._backup_target`, transfer failures classified by `is_space_error` suspend ONLY
that target: remaining disks/transfers of the target are skipped, a CRITICAL is logged,
and control returns to `_execute_backup_steps`, which continues with the next target.
Retention and cleanup STILL run for the suspended target (deletion frees space —
self-heal). Non-space failures keep the existing `BackupAbortError` VM-abort behavior
unchanged — this is critical: the verify-before-delete gate (verification failures abort
before cleanup) must not be weakened. Core tracks space-limited targets in a run-scoped
set to drive the exit code. Alternative: per-target `BackupAbortError` with a flag —
rejected: `BackupAbortError` semantics are "abort VM before cleanup"; overloading it
endangers the verify-before-delete invariant.

**D3. State-write resilience: catch `OSError` in `JsonStateManager._save`.**
`_save` wraps write + `os.replace` in `try/except OSError` → CRITICAL log (path + errno)
→ re-raise as `RuntimeError`. The per-VM `try/except` in `Core._run_pipeline`
(`core:2370-2392`) then contains the failure to one VM; remaining VMs continue. Losing
the in-flight record is safe: state only advances after successful operations, so the
worst case is redone (idempotent) work on the next run. Alternative: swallow and continue
— rejected: silent state loss is worse than a loud per-VM abort.

**D4. Blockcommit ENOSPC → deferred queue, reason `enospc`.**
In `Core._blockcommit_one_disk`, commit failures whose error classifies as space are
recorded via `add_deferred_blockcommit(..., reason="enospc")` instead of raising
`RuntimeError`. The existing drain (step 0 of next run, `_check_deferred_operations`) and
threshold monitoring (`deferred_warn_count/crit_count/warn_age/crit_age`) apply unchanged.
Snapshot state records remain (they were never removed — removal happens only after
successful commit), so the merge is retried intact.

**D5. Proactive free-space gate before transfers.**
New `qsnap/utils/space.py` with pure-ish helpers (IShell for `qemu-img info`, stdlib
`shutil.disk_usage` for the filesystem):
- `estimate_full_size(shell, source_path) -> int | None` — sum of `actual-size` over the
  backing chain of the source (worst-case standalone copy size); `None` when undecidable.
- `estimate_incremental_size(shell, source_path) -> int | None` — `actual-size` of the
  active layer (upper bound for a dirty-block delta).
- `check_free_space(target_dir, required, reserve, factor) -> SpaceCheckResult` —
  `shutil.disk_usage(target_dir).free >= required * factor + reserve`.
Core invokes the gate before each FULL/incremental transfer. New `GlobalConfig` options:
`free_space_check: str = "strict"` (`strict` | `warn` | `off`),
`free_space_reserve: int = 0` (bytes), `free_space_factor: float = 1.0`.
Semantics: `strict` — insufficient space is treated exactly like a reactive space error
(target suspension path, transfer not attempted); `warn` — log WARNING and proceed;
`off` — no check. If estimation returns `None`, proceed with a WARNING (never block on an
undecidable estimate — availability direction). Rationale for gate-before-transfer:
avoid creating checkpoints/exports for transfers doomed to ENOSPC.

**D6. Exit code 4 (`EXIT_DISKFULL`).**
`qsnap/errors.py` gains `EXIT_DISKFULL = 4`; `PipelineResult` gains an additive frozen
field `space_limited: bool = False`; CLI maps it to exit 4 when any target/VM was limited
by a space error (reactive or proactive-strict). Existing codes 0/1/2/3/10 unchanged.
Monitoring scripts must treat 4 as "needs space, auto-resumes" — documented.

**D7. Auto-resume is a contract, not new machinery.**
Spec requirement only: after a space-limited run, the next run resumes from the last good
checkpoint/baseline/state and completes without operator intervention. This already holds
by construction (success-only advancement); the spec pins it against regressions.

### Task 2 — atomic multi-disk snapshots

**D8. Additive ABC method `create_multi` (BREAKING for implementers/mocks).**
`ISnapshotProvider.create_multi(vm_config: VMConfig, specs: Sequence[SnapshotSpec],
quiesce: bool) -> list[SnapshotResult]` where `SnapshotSpec` is a new frozen dataclass
(`disk`, `name`, `path`) in `qsnap/models/`. Single-disk `create()` remains (tests,
compat). All implementations and mocks (`MockSnapshotProvider`) must implement it —
hence BREAKING. The factory needs no new branch (same concrete class).

**D9. One virsh call with N `--diskspec`.**
`ExternalSnapshotProvider.create_multi` builds ONE command:
`virsh snapshot-create-as --domain <vm> --name <batch_name> --diskspec <disk>,file=<path>,snapshot=external`
(repeated per disk) `--disk-only --atomic --no-metadata [--quiesce]`.
`batch_name` = `{vm}.{timestamp}_{6hex}` (domain-unique; per-file names keep the
`_{disk}_` segment). One guest-agent freeze/thaw wraps all disks; `--atomic` gives
all-or-nothing at the libvirt level. The lock-retry loop (3 attempts, backoff 2s/4s on
"cannot acquire state change lock") wraps the whole call. Post-creation validation reuses
the existing per-file checks (exists/qcow2/virtual-size/actual-size≤50%/corrupt/
backing-filename) plus ONE `domblklist` pivot check covering all disks.
Alternative considered: manual `guest-fsfreeze-freeze`/`thaw` around per-disk calls —
rejected: thaw-on-crash is fragile, agent error handling duplicates libvirt, and libvirt
already performs the freeze atomically with `--quiesce`.

**D10. All-or-nothing state recording in Core.**
`Core._create_snapshot`: generate all names/paths first → single `create_multi` call →
on full success record all snapshots + `set_last_allocation`; on ANY failure record
NOTHING, best-effort `rm -f` created files, raise `RuntimeError` (VM-level abort as
today; other VMs unaffected). Pre-flight orphan detection catches any leftover on the
next run. The `index == 0` quiesce hack is deleted: `quiesce = vm_config.snapshot_quiesce`.
Single-disk VMs are the degenerate case of the same path (one `--diskspec`).

**D11. Timeouts.** Quiesce: 180s regardless of disk count (freeze dominates). Non-quiesce:
`120 + 30 × (N − 1)` seconds. Both constants live in `ExternalSnapshotProvider`.

**D12. Dry-run unaffected.** `_simulate_snapshots` stays per-disk; batching is a creation
mechanic, predictions unchanged.

### Task 3 — preserve_min default

**D13. Default 0 → 48 as an explicit constant.** `GlobalConfig.snapshot_preserve_min:
int = 48`. With default `snapshot_chain_length = 24` the floor dominates: effective
default retention = keep-newest-48 per disk (~2 days hourly). Explicit
`snapshot_preserve_min = 0` still disables the floor (validation `>= 0` unchanged).
Alternative considered: derive default as `2 × snapshot_chain_length` — rejected:
implicit coupling, harder to spec and to reason about.

**D14. Test-audit as an explicit work item.** Fixtures/tests constructing `GlobalConfig()`
or full configs without explicit `snapshot_preserve_min` relied on default 0; the tester
agent audits and either pins `preserve_min=0` where old semantics are intended or updates
expectations. `qsnap.toml.example` documents the new default with the interaction note.

### Cross-cutting

**D15. Implementation order:** Task 1 (state protection → classification → per-target
isolation → proactive gate → blockcommit deferral → exit code), then Task 2, then Task 3.
Each task independently testable; commit per task.

**D16. Config values are plain numbers.** `free_space_reserve` is integer bytes;
`free_space_factor` is float. No human-readable size parsing in v1 (consistent with
existing numeric config fields); examples in `qsnap.toml.example`.

## Risks / Trade-offs

- [libvirt version variance for multi `--diskspec` + `--atomic` + `--quiesce`] → qsnap
  already requires checkpoint-capable libvirt (newer than multi-diskspec support);
  integration tests on a real VM verify the exact command; env-validation error message
  guides the operator.
- [Size estimation inaccuracy (compression, sparse allocation)] → configurable
  `free_space_factor`/`free_space_reserve`; `warn` mode for nervous operators; undecidable
  estimates proceed with WARNING rather than block.
- [Default snapshot-dir usage roughly doubles (24 → 48 kept)] → documented in proposal
  Impact, `qsnap.toml.example`, and the spec; opt-out is one explicit line.
- [Per-target isolation accidentally weakening verify-before-delete] → isolation applies
  ONLY to space-classified errors; verification failures still raise `BackupAbortError`
  aborting before cleanup; pinned by explicit spec scenario and tests.
- [Exit code 4 surprises monitoring] → new code only; documented in cli-interface spec,
  `--help` epilog, and the change notes.
- [Test-suite churn from the default change] → dedicated audit work item for the tester
  agent (list of tests to update/remove produced before implementation).
- [`--atomic` rollback may still leave files on some libvirt versions] → best-effort
  `rm -f` on batch failure + existing pre-flight orphan cleanup as second net.

## Migration Plan

1. No state-file migration: deferred queue reuses the existing string `reason` field
   (new value `enospc`); `PipelineResult.space_limited` is additive with default.
2. Config backward compatible: all existing explicit values honored; only installations
   WITHOUT explicit `snapshot_preserve_min` change behavior (keep 48 instead of 0).
3. Deploy order: ship code + specs + updated `qsnap.toml.example`; operators review
   snapshot-dir capacity before/after upgrade.
4. Rollback: revert the change; setting `snapshot_preserve_min = 0` restores old
   retention behavior without reverting code.

## Open Questions

- Non-quiesce batch timeout formula `120 + 30 × (N − 1)`: validate against integration
  runs on 3+ disk VMs; adjust constant if needed.
- Whether `free_space_reserve` should later accept human-readable sizes ("10G") — deferred
  to a follow-up; v1 uses bytes.
