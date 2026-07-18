"""Shared file hashing utility.

Provides :func:`file_sha256` — a cross-cutting SHA-256 hashing function
used by multiple domains (snapshot content hashing, backup verification).

This is a stateless pure function that does not implement any ABC and
does not belong in any domain module sub-package.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB


def file_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file, reading 8 MiB chunks.

    Args:
        path: Filesystem path to the file to hash.

    Returns:
        The SHA-256 hex digest string.
    """
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()
