"""Exit code constants for the qsnap CLI.

Mirrors btrbk exit codes:
  0  — success
  1  — generic error
  2  — parse error (CLI args or config file)
  3  — lockfile error (another instance running)
  4  — disk-full (at least one target/VM was limited by a space error;
       auto-resumes from last good checkpoint on the next run)
  10 — backup abort (at least one backup task failed)
"""

from __future__ import annotations

EXIT_SUCCESS = 0
EXIT_GENERIC = 1
EXIT_PARSE = 2
EXIT_LOCKFILE = 3
EXIT_DISKFULL = 4
EXIT_BACKUP_ABORT = 10
