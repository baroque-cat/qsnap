# Design: fix-dry-run-predictions

## Context

Dry-run (`qsnap -n`) runs the same pipeline as a real run; every mutating step is individually guarded by `self._dry_run` checks in `Core` (`qsnap/core/__init__.py`). Prior audit found the guards architecturally sound except:

- `_check_deferred_operations()` (core:2979-3122) has **no guard** — it executes real blockcommits, removes state entries, rewrites the deferred queue, and refreshes domain XML during `qsnap -n run`.
- `_create_snapshot()` in dry-run returns `[]` (core:3136-3141), so all downstream steps — snapshot retention (core:3242), backup steps (core:4185), FULL decision and transfer list in `_backup_target` — evaluate against **pre-run state**, producing predictions that do not match what a real run would do.
- Incremental transfers are skipped silently (core:4620); no names, counts, or sizes are predicted.
- Dry-run logs for snapshot creation and blockcommit are per-VM relics of the single-disk era; the codebase is fully per-disk after the multi-disk refactor.

Existing assets to reuse:

- Fork's read-only chain-size estimation (`qemu-img info --force-share --backing-chain`, core:1408-1431) and `Core._format_bytes()` (core:1507).
- `IChangeDetector.has_changed()` returns `ChangeResult.current_allocation` — a read-only per-disk allocation query already abstracted behind the factory.
- The unit-test contract already permits read-only shell calls in dry-run (`qemu-img info`, `du`, `test`, `which`, `virsh dominfo`, `find`, `qemu-nbd` — `tests/core/test_pipeline.py:467-475`).
- `ActionRecord` already carries `disk`; `PipelineResult` already carries `dry_run`.
- Integration fixtures `test_vm` / `test_vm_multi_disk` (real libvirt VMs, `/var/tmp`).

Constraints: zero new dependencies (stdlib only); no ABC (`I`-prefix) interface changes; no state-file schema changes; dry-run is invoked infrequently, so extra read-only `qemu-img info` / `virsh domstate` calls are acceptable.

## Goals / Non-Goals

**Goals:**

- Dry-run performs **zero mutations**: no files created/deleted, no state writes, no XML changes, no blockcommits, no transaction log (fix the deferred-operations leak).
- Dry-run predicts the **post-run world**: retention, FULL decisions, and transfer lists are evaluated with the would-be-created snapshots included.
- Every predicted mutation is logged at INFO with **VM + disk context**, and with a **size estimate** wherever one can be computed read-only.
- Provide a **structured, testable predictions channel** (`PipelineResult.predictions`) and render it in the CLI summary per VM and per disk.
- Predictions stay honest: estimates are marked approximate (`~`), and conditional deletions are labeled as conditional.

**Non-Goals:**

- Changing the `qsnap estimate` command (spec `size-estimation` deliberately remains factual, no projections).
- Predicting exact NBD dirty-block transfer volumes (not knowable ahead of time; upper bounds only).
- Simulating failure paths (retry exhaustion, verification failures) in dry-run.
- Dry-run predictions for `restore` / `fork` / `reconcile` beyond their existing gates (restore already logs a 6-line plan; fork already logs chain-size estimate; reconcile has 11 guards). Pipeline dry-run only.
- Auto-discovery of VMs/disks not in the TOML config.

## Decisions

### D1 — In-memory simulation, never state-write-and-rollback

In dry-run, `_create_snapshot()` builds `SnapshotInfo` objects in memory and threads them through downstream steps. Nothing is written to `IStateManager`.

- **Alternative rejected**: record simulated snapshots into state and roll back at the end of the run. A crash mid-dry-run would leave phantom state; it violates the "dry-run writes nothing" invariant; it complicates concurrency with the lockfile.
- Simulated snapshots carry: predicted name from the real `_generate_snapshot_name()` (illustrative — a later real run produces a different timestamp/hex suffix), resolved path `snapshot_dir_for(disk) / f"{name}.qcow2"`, `timestamp=now`, `allocation=current disk allocation`, `disk=disk.target`.

### D2 — Allocation estimate via `IChangeDetector`

The simulated snapshot's `allocation` is `detector.has_changed(vm_config, disk).current_allocation` — reuses existing read-only infrastructure (works for running and stopped VMs, both detection modes). In `onchange` mode the detector is already called by the gate; the duplicate read-only query in dry-run is accepted (dry-run is rare; no API distortion to avoid it).

### D3 — Threading via optional parameters (no ABC changes)

- `_evaluate_snapshot_retention(vm_config, extra_snapshots: list[SnapshotInfo] | None = None)` merges state snapshots + extras before per-disk grouping.
- `_execute_backup_steps(vm_config, extra_snapshots=None)` builds `snapshots = state + extras` before iterating targets.
- Non-dry-run callers pass nothing; behavior is byte-for-byte unchanged for real runs. Both are private Core methods — no interface breakage.

### D4 — Incremental transfer prediction with upper-bound sizes

In dry-run, instead of silently skipping the transfer block (core:4620), `_backup_target()` computes the would-be transfer list: `snapshots − full_source_names − already present on target` (presence via the read-only `provider.list(target)` glob). For each snapshot:

- Existing file → estimate = file `actual-size` via `qemu-img info --force-share` (upper bound; real NBD transfer copies dirty blocks only).
- Simulated (file does not exist yet) → estimate = simulated `allocation` (D2).
- All estimates logged and recorded with `~` semantics; no false precision.

### D5 — Reusable chain-size helper for FULL prediction

Extract fork's estimation (core:1408-1431) into `Core._estimate_chain_size(path) -> int` (sum of `actual-size` over `qemu-img info --force-share --backing-chain --output=json`). `fork()` calls the helper (behavior unchanged); dry-run FULL prediction logs `Would create FULL backup for disk <d> (~<size>, method=NBD, VM=<state>)`. For a simulated FULL source the chain exists on disk (snapshot overlays chain back to base), so the estimate is real.

### D6 — Predicted FULLs are simulated into backup retention

When dry-run predicts a new FULL for a disk, `_evaluate_backup_retention()` receives the predicted FULL as an extra chain (timestamp = now) so `keep_generations` rollover is predicted correctly. Consequences:

- Cleanup prediction may include old generations that a real run would delete — each such deletion is logged with the explicit condition "after new FULL verification" (verify-before-delete gate cannot pass in dry-run).
- **Alternative rejected**: leave retention on live files only — it systematically under-predicts deletions exactly when FULL rollover happens, which is the common case.

### D7 — Structured predictions channel

- `Core._predictions: list[ActionRecord]` accumulates only in dry-run, in parallel with `_actions` (which stays empty in dry-run, per spec `action-audit-trail`).
- `PipelineResult` gains `predictions: list[ActionRecord]` (default empty — backward compatible).
- Action vocabulary: existing values (`snapshot_create`, `backup_transfer`, `backup_full`, `backup_delete`) plus new `blockcommit` (predicted overlay merges) and `snapshot_delete` where overlays would be removed. Predictions are **never** written to the transaction log.
- **Alternative rejected**: log-only predictions — untestable without caplog parsing and unusable for the summary table.

### D8 — Deferred-operations guard with state-accurate prediction

New guard at the top of `_check_deferred_operations()`: in dry-run, for each queued entry, run the read-only `_plan_blockcommit()` (one `virsh domstate` per disk — already in the permitted dry-run shell-call allowlist) to predict the committable/deferrable split, log it per disk, record a `blockcommit` prediction, and return without any state write or blockcommit execution. If domstate fails (plan is None), log "would attempt to drain N deferred blockcommit(s) (VM state unknown)".

### D9 — Per-disk logging convention

All new/changed dry-run log lines follow `[dry-run] <vm>/<disk>: <what would happen> (~<size>)` or the existing `[dry-run] Would ... for disk %s ...` style where a message is inherently per-disk. The per-VM counter messages (core:3138, 3700-3704) are replaced by per-disk messages listing names.

### D10 — Summary rendering

`format_summary()` renders, when `result.dry_run` and predictions exist, a "Planned actions (dry-run)" section: per-VM blocks with per-disk rows reusing the existing symbol/prefix formatting (`+++ [vda] ...`). The `Dryrun: YES` header and the existing footer disclaimer stay. `actions` rendering is untouched.

### D11 — State-hygiene self-healing in dry-run: predict, never write

Post-verification follow-up (verify report WARNING #1). Two self-healing sites wrote to `IStateManager` even in dry-run, violating the zero-mutation invariant: `Core._validate_state_at_startup()` (stale-baseline clear when no FULLs; phantom-FULL removal with dependency cascade; post-cascade baseline re-check) and the phantom-FULL filter inside `Core._backup_target()` (a third copy of the same logic, reachable before the per-disk FULL decision).

Decisions:

- **Gate only the writes.** Detection stays active in dry-run: the in-memory `filtered_fulls` list still excludes phantom FULLs so the FULL decision and its predictions are computed against the real post-cleanup world. Only the `IStateManager` calls (`remove_full_backup`, `remove_all_incremental_dependencies`, `clear_last_backup_allocation`) are skipped behind `if not self._dry_run`, each replaced by a `[dry-run] Would ...` log (D9 convention). Real-run branches are byte-for-byte unchanged.
- **Cascade count via read-only getter.** The dry-run phantom log obtains the dependency count from `IStateManager.get_incremental_dependencies()` (read-only) instead of the mutating `remove_all_incremental_dependencies()` return value. Count failure degrades to 0 with a warning, never aborts.
- **Post-cascade baseline decision is computed in memory in dry-run.** Because dry-run does not remove phantom records, the real-run `get_full_backups()` re-check would still see them; dry-run instead derives "FULLs that would remain" from the already-detected phantom set (`os.path.exists` filter) and logs the baseline cleanup without executing it.
- **Log-only, no ActionRecord.** State hygiene is internal metadata repair, not a file-level backup action. The ActionRecord vocabulary is fixed by the `action-audit-trail` spec; adding a hygiene action type would pollute the "Planned actions (dry-run)" summary. The zero-mutation invariant is satisfied by skipping the writes themselves.
- **Per-run log dedupe.** `_validate_state_at_startup()` runs twice per pipeline (snapshot steps + backup steps) and `_backup_target()` sees the same phantoms again; in dry-run the state is never cleaned, so all three sites would log the same prediction. `Core._healing_logged: set[str]` (keys `phantom:<target>:<name>`, `baseline:<target>:<disk>`, `baseline-after-phantom:<target>`), reset in `_run_pipeline()`, suppresses repeats. Dedupe applies only in dry-run; real-run logging is untouched.

## Risks / Trade-offs

- [Predicted names differ from a later real run (timestamp/hex)] → documented as illustrative; spec states names are predicted at dry-run time.
- [Extra read-only shell calls per dry-run (qemu-img info per snapshot, domstate per deferred disk)] → dry-run is rare; every call has a timeout; failures degrade to size 0 / "unknown" without failing the pipeline.
- [Upper-bound sizes misread as exact] → all estimates carry `~` and spec language says "upper bound".
- [Conditional generation deletions (D6) could alarm operators] → deletion lines carry the explicit "after new FULL verification" condition.
- [Simulated snapshots accidentally persisted] → unit test asserts full state-dump equality before/after dry-run; integration test asserts state files are byte-identical and no new files appear in snapshot dirs or targets.
- [Test churn breaks unrelated suites] → test-plan.md carries an explicit inventory of tests to delete/update with reasons; reviewer confirms before implementation merges.

## Migration Plan

No data or config migration. Deploy = code update. Rollback = revert; dry-run behavior returns to the previous (less accurate) predictions; the deferred-operations bug returns with it, so rollback is discouraged.

## Open Questions

None blocking. (Precision of incremental size estimates is settled by D4: upper bounds, marked approximate — "as accurate as reasonable".)
