## Why

The dry-run zero-mutation invariant (`dry-run-prediction`, requirement "Zero-mutation invariant for the dry-run pipeline") was violated in four places discovered by analysis:

- **L1** `_preflight_cleanup` deleted stale `*.tmp`/`*.partial` files, NBD sockets, and truncated qcow2 files even in dry-run;
- **L2** `_check_deferred_thresholds` wrote `update_deferred_warning()` to state in dry-run;
- **L3** `_blockcommit_snapshots` stale-state self-healing called `remove_snapshot()` in dry-run;
- **L4** `check --deep` wrote the `_last_deep_check` timestamp in dry-run.

Additionally, `qsnap --dry-run restore SNAP` silently ran a REAL restore: the restore subparser declared its own `--dry-run` with `default=False`, and argparse subparsers write their argument defaults over the values parsed by the global parser, clobbering the global `True`.

## What Changes

- **F1 (L1)**: in dry-run, each preflight `rm -f` is replaced by a `[dry-run] Would remove stale file/socket/partial transfer: ...` log; read-only `find`/`qemu-img info` detection stays. By decision, no prediction records are added — logs only.
- **F2 (L2)**: `update_deferred_warning()` gated by `if not self._dry_run` (WARNING/CRITICAL logs still emitted).
- **F3 (L3)**: stale-entry self-healing in dry-run logs `[dry-run] Would remove stale state entry: ...` and excludes the entry from `to_merge` without calling `remove_snapshot()`.
- **F4 (L4)**: `_set_last_deep_check_time()` gated by `if deep and not self._dry_run`.
- **F5 (CLI)**: restore's `--dry-run` uses `default=argparse.SUPPRESS` (fork/reconcile pattern) and gains the `-n` alias; the action subcommands `run`/`snapshot`/`backup`/`prune` gain a local `--dry-run`/`-n` with `SUPPRESS`, so the flag works both before and after the subcommand uniformly.
- **F6 (docs)**: `PipelineResult.actions` docstring and the specs now state that in dry-run `actions` contains only `error` records (a failed VM is reported regardless of mode); mutation records are never appended.

## Capabilities

### Modified Capabilities

- `dry-run-prediction`: four explicit zero-mutation requirements for preflight cleanup, deferred thresholds, stale-entry healing, and deep-check timestamp.
- `action-audit-trail`: error records permitted in dry-run `actions`; mutation records still forbidden.
- `cli-interface`: `--dry-run` accepted before and after the subcommand; SUPPRESS pattern documented.
- `core-orchestrator`: "Dry-run mode" requirement aligned with the error-records-in-actions behavior.

## Impact

- **Code**: `qsnap/core/__init__.py` (4 gates + docstring), `qsnap/cli/app.py` (SUPPRESS defaults, local flags).
- **Tests**: argparse regression tests for both flag positions and restore; dry-run regression tests for L1–L4.
- **Behavior**: `qsnap --dry-run restore SNAP` no longer performs a real restore; dry-run runs leave state and filesystem untouched.
