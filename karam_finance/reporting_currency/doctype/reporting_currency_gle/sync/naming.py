"""Source-derived names for synced RCGLE rows; DOE naming is independent."""

_GLE_NAME_MIN_PARTS = 4


def get_synced_rc_gle_name(gl_entry_name: str) -> str:
    """Preserve the existing series-name parsing and RC-prefixed fallback."""
    parts = gl_entry_name.split("-")
    if len(parts) >= _GLE_NAME_MIN_PARTS:
        return f"KE-RCGLE-{parts[2]}-{parts[3]}"
    return f"RC-{gl_entry_name}"
