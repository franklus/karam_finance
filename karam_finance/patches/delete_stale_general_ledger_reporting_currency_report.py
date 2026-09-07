"""Retain the obsolete cleanup path for migration compatibility."""

from __future__ import annotations


# Keep this module and its dotted path callable: sites may already have this
# patch in Patch Log, while other sites may still have it pending.  The rename
# patch owns the transition, so this obsolete cleanup must not delete reports.
def execute() -> None:
    """Do nothing; the pre-sync rename patch owns report cleanup and links."""
