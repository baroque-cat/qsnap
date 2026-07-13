"""Domain modules — concrete implementations of all domain ABCs.

Each module implements exactly one ABC interface and accepts all
dependencies as constructor parameters.  Modules do NOT inherit from
Core (design D1) — they are standalone workers coordinated by Core.
"""
