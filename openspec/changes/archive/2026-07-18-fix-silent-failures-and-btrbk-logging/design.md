## Context

qsnap currently has a visibility problem. When `backup_failed = True`, no WARNING is logged — the operator sees exit code 10 in systemd with zero context. When post-commit chain length verification is skipped (`chain_length_before is None`), a false "passed" message is emitted. Five failure paths in `FileCopyBackupProvider.transfer_missing()` silently return `BackupResult(success=False)` without WARNING. `BitmapBackupProvider` lacks parity with `FileCopyBackupProvider` in two areas: no `domjobabort` after NBD incremental backups, and no `IStateManager` injection for FULL recording.

Beyond bug fixes, qsnap's logging is sparse — it was designed as a cron/systemd program providing pass/fail output, but btrbk (its spiritual predecessor) provides per-operation INFO messages and a symbolic summary table on stdout. Adding these would make qsnap more debuggable and operator-friendly.

## Goals / Non-Goals

**Goals:**
- Ensure every `backup_failed = True` assignment emits a WARNING with the specific failure reason
- Fix the false "Post-commit chain verification passed" log
- Add WARNING logs to all silent failure paths in `transfer_missing()`
- Add `virsh domjobabort` to `BitmapBackupProvider.transfer_missing()` finally block
- Add `IStateManager` injection and `record_full_backup()` call to `BitmapBackupProvider`
- Introduce `ActionRecord` dataclass as the audit trail data structure
- Add per-operation INFO messages (btrbk-style: `[snapshot]`, `[blockcommit]`, `[backup]`, `[delete]`)
- Add summary table on stdout after each run
- Add `transaction_log` config option for structured machine-readable log

**Non-Goals:**
- Changing `PipelineResult.success` semantics (it already correctly ignores `backup_failed`)
- Changing exit code 10 behavior (by design, btrbk-compatible)
- Adding a new CLI subcommand or changing existing subcommand interfaces
- Changing the btrbk summary format (legend symbols, per-VM blocks)
- Implementing transaction log rotation or compression

## Decisions

### D1: ActionRecord accumulation in Core, not in modules

**Decision:** Core accumulates `ActionRecord` instances in `self._actions: list[ActionRecord]` during pipeline execution. Modules return their existing `*Result` types unchanged; they do not know about `ActionRecord`.

**Rationale:** Modules are stateless workers per AGENTS.md. Adding audit trail awareness to modules would violate the module contract. Core already coordinates all pipeline steps and has full visibility into what happened. After each pipeline step, Core appends the appropriate `ActionRecord`.

**Alternatives considered:**
- **A: Modules return ActionRecord alongside their result.** Rejected — would change all ABC interfaces (`ISnapshotProvider.create()`, etc.) and break contract tests.
- **B: Separate audit-trail listener/observer.** Over-engineering for a single-consumer pattern.

### D2: Summary formatter as pure function in CLI layer

**Decision:** `qsnap/cli/summary.py` contains a pure function `format_summary(result: PipelineResult) -> str`. It accepts the pipeline result (including `actions: list[ActionRecord]`) and returns a formatted string. It does NOT access filesystem, state, or config.

**Rationale:** AGENTS.md rule: "CLI layer is a thin translation layer: CLI args → Core call → formatted output." The formatter translates `PipelineResult` → formatted string. All data comes from Core via `PipelineResult.actions`.

### D3: Transaction log writer as stateless utility

**Decision:** `qsnap/utils/transaction.py` provides a `TransactionWriter` class with a static method `write(path: Path, record: ActionRecord) -> None`. It appends a single line in btrbk-compatible format. It has no knowledge of Core, pipeline, or config.

**Rationale:** Stateless cross-cutting utility per AGENTS.md — belongs in `qsnap.utils/`. The Core calls it after each `ActionRecord` if `transaction_log` is configured.

### D4: BitmapBackupProvider IStateManager injection mirrors FileCopyBackupProvider

**Decision:** `BitmapBackupProvider.__init__()` gains an optional `state: IStateManager | None = None` parameter, identical to `FileCopyBackupProvider.__init__()`. The factory (`DefaultFactory`) passes `self._state` when constructing `BitmapBackupProvider`. `create_full_backup()` calls `self._state.record_full_backup(...)` on success when `self._state is not None`.

**Rationale:** Parity between the two IBackupProvider implementations. The FULL recording was already specified (spec requirement existed) but never implemented due to missing constructor parameter.

### D5: Domjobabort in bitmap.py mirrors nbd.py pattern exactly

**Decision:** Copy the `finally` block pattern from `qsnap/utils/nbd.py:215-232` into `BitmapBackupProvider.transfer_missing()` at the equivalent position after `qemu-img convert`.

**Rationale:** The archived delta spec already requires this. Both NBD paths (FULL export and incremental bitmap export) need the same abort logic.

## Risks / Trade-offs

- **[Risk] ActionRecord list grows unbounded for long chains** → Mitigation: `ActionRecord` is ephemeral — only accumulated during a single `_run_pipeline()` call and discarded after. Max entries ≈ number of snapshots × targets per run, unlikely to exceed hundreds.
- **[Risk] Summary table could be confusing if `backup_failed` is True but individual items show as transferred** → Mitigation: Items that failed have `action="error"` or `error=...` in their ActionRecord, and `!!!` symbol in summary table.
- **[Risk] Transaction log may contain sensitive paths** → Mitigation: Paths are already logged at INFO level throughout the pipeline. Transaction log is opt-in via config.
- **[Trade-off] stdout summary goes to stdout, stderr per-op logs to stderr** → This mirrors btrbk's separation: summary on stdout for `--quiet` mode, per-op details on stderr for `-v` mode. Operators can redirect independently.
