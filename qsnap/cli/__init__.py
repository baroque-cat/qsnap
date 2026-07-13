"""qsnap CLI package — thin translation layer from CLI args to Core calls."""

from __future__ import annotations

from qsnap.cli.app import build_argparser, main

__all__ = ["build_argparser", "main"]
