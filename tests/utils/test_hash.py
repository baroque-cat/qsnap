"""Unit tests for qsnap.utils.hash — shared file hashing utility.

Tests verify the ``file_sha256`` function produces correct SHA-256 hex
digests for known content using 8 MiB chunked reading.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from qsnap.utils.hash import file_sha256


def test_file_sha256_hex_result(tmp_path: Path) -> None:
    """``file_sha256`` returns a 64-character lowercase hex SHA-256 digest
    matching ``hashlib.sha256`` for known content.
    """
    content = b"hello world"
    expected_hash = hashlib.sha256(content).hexdigest()

    # Create a temp file with known content
    test_file = tmp_path / "test.bin"
    test_file.write_bytes(content)

    result = file_sha256(test_file)

    # Must be a 64-character lowercase hex string
    assert isinstance(result, str)
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)
    assert result == expected_hash
