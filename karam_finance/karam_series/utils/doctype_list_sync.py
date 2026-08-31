"""Helpers to reconcile the curated Karam Series doctype list."""

from __future__ import annotations


def reconcile_doctype_rows(
    curated: list[str],
    existing_flags: dict[str, int],
) -> list[dict[str, str | int]]:
    """Return canonical child-table rows for the curated doctypes.

    - Preserves existing mandatory flags where present.
    - Ensures alphabetical order by doctype name.
    - Does not include doctypes outside the curated list.
    """
    curated_sorted = sorted(curated)
    return [
        {
            "doctype_name": dt,
            "karam_series_mandatory": existing_flags.get(dt, 0),
        }
        for dt in curated_sorted
    ]
