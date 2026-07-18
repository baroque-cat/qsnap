"""Btrbk-compatible transaction log writer.

Appends one line per :class:`ActionRecord` to a transaction log file
in the format::

    localtime type status target_url source_url parent_url

Fields are separated by a single space.  Undefined fields use ``-`` as
placeholder.  This module is a **stateless utility** — it has no
knowledge of Core, pipeline, or config.  It accepts only ``Path`` and
``ActionRecord``.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path

from qsnap.models.results import ActionRecord

# ── Action type → btrbk-compatible type mapping ───────────────────────────

_TYPE_MAP: dict[str, str] = {
    "snapshot_create": "snapshot",
    "snapshot_delete": "delete_snapshot",
    "backup_transfer": "backup",
    "backup_full": "backup_full",
    "backup_delete": "delete_backup",
}

# Actions that store the path in ``source_url`` (snapshot operations).
_SOURCE_URL_ACTIONS = frozenset({"snapshot_create", "snapshot_delete"})

# Actions that store the path in ``target_url`` (backup operations).
_TARGET_URL_ACTIONS = frozenset(
    {
        "backup_transfer",
        "backup_full",
        "backup_delete",
    }
)


class TransactionWriter:
    """Stateless utility for appending btrbk-compatible log lines.

    Each call to :meth:`write` appends exactly one line to *path*.
    No buffering — the file is opened, written, and closed on each
    call.  This ensures durability even if the process crashes between
    actions.
    """

    @staticmethod
    def write(path: Path, record: ActionRecord) -> None:
        """Append a single transaction log line to *path*.

        The line format is::

            localtime type status target_url source_url parent_url

        - ``localtime``: ``YYYY-MM-DDTHH:MM:SS`` (ISO 8601 local time)
        - ``type``: btrbk-compatible action type (see ``_TYPE_MAP``)
        - ``status``: ``"success"`` or ``"ERROR"``
        - ``target_url``: target path (for backups) or ``-``
        - ``source_url``: source snapshot path or ``-``
        - ``parent_url``: parent/backing path or ``-``;
          for errors, ``# <error_message>``

        For ``error`` actions, ``type`` is set to ``"error"`` and
        ``status`` is ``"ERROR"``.
        """
        localtime = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        # Determine type and status.
        if record.action == "error":
            log_type = "error"
            status = "ERROR"
        else:
            log_type = _TYPE_MAP.get(record.action, "unknown")
            status = "success"

        # Determine target_url, source_url, parent_url based on action.
        target_url = "-"
        source_url = "-"
        parent_url = "-"

        if record.action in _SOURCE_URL_ACTIONS:
            source_url = str(record.path) if record.path else "-"
        elif record.action in _TARGET_URL_ACTIONS:
            target_url = str(record.path) if record.path else "-"
        elif record.action == "error" and record.error:
            parent_url = f"# {record.error}"

        line = f"{localtime} {log_type} {status} {target_url} {source_url} {parent_url}\n"

        # Append to file (create if it doesn't exist).
        # Ensure parent directory exists.
        parent_dir = path.parent
        with contextlib.suppress(OSError):
            parent_dir.mkdir(parents=True, exist_ok=True)

        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)

    @staticmethod
    def write_finished(path: Path) -> None:
        """Append a final ``finished success`` line to *path*.

        Format: ``<localtime> finished success - - -``
        """
        localtime = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        line = f"{localtime} finished success - - -\n"

        parent_dir = path.parent
        with contextlib.suppress(OSError):
            parent_dir.mkdir(parents=True, exist_ok=True)

        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)


__all__ = ["TransactionWriter"]
