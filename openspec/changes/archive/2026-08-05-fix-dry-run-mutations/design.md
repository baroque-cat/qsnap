# Design: fix-dry-run-mutations

## D1 — Four mutation gates

Each leak gets a minimal gate; the surrounding read-only detection stays active so dry-run output remains informative:

- **L1** `_preflight_cleanup`: each of the three `rm -f` sites (stale `*.tmp`/`*.partial`, NBD sockets `qsnap-backup-*.sock`, truncated qcow2) logs `[dry-run] Would remove stale file/socket/partial transfer: <path>` in dry-run and skips the `rm`; `removed_count` is not incremented. `find` and `qemu-img info` remain (read-only).
- **L2** `_check_deferred_thresholds`: `update_deferred_warning()` wrapped in `if not self._dry_run`; the WARNING/CRITICAL threshold logs are still emitted.
- **L3** `_blockcommit_snapshots` stale-entry self-healing: dry-run logs `[dry-run] Would remove stale state entry: snapshot <name> file not found on disk` and filters the entry out of `to_merge` without `remove_snapshot()`.
- **L4** `check --deep`: `_set_last_deep_check_time()` gated by `if deep and not self._dry_run`.

By decision, L1 produces logs only — no new prediction record type is introduced.

## D2 — argparse SUPPRESS pattern

argparse subparsers write their argument DEFAULTS over values already parsed by the parent parser into the shared `Namespace`. A subcommand-local `--dry-run` with `action="store_true"` (default `False`) therefore clobbers a global `--dry-run` parsed before the subcommand. `default=argparse.SUPPRESS` makes argparse write nothing when the flag is absent, preserving the global value; when present, it sets `True`. Applied to:

- `restore` (was a plain `store_true` — the silent-disable bug), now also with `-n` alias;
- `run`/`snapshot`/`backup`/`prune` gain a local `--dry-run`/`-n` with SUPPRESS so the flag works after the subcommand too (previously only the global position worked, and `qsnap run --dry-run` was a parse error).

`fork` and `reconcile` already used the pattern.

## D3 — Error records in dry-run actions

`_run_pipeline`'s per-VM except handler appends `ActionRecord(action="error", ...)` regardless of mode — a failed VM must be visible in the audit trail even in dry-run. The `PipelineResult.actions` docstring and the specs are corrected: in dry-run, `actions` contains ONLY error records; mutation records are never appended. No code change needed for this item — documentation only.
