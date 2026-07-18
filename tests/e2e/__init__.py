"""End-to-end tests — full pipeline from config file to restored VM.

Marked with ``@pytest.mark.e2e`` and excluded from normal test runs via
``-m "not e2e"``.  These tests exercise the complete qsnap workflow:
config parsing → snapshot creation → backup transfer → retention →
restore.
"""
