from collections.abc import Iterable

import frappe


def parse_multiselect(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = frappe.parse_json(value)
        except ValueError:
            return _string_values(value.split(","))
    if isinstance(value, (list, tuple, set)):
        return _string_values(value)
    parsed = str(value).strip()
    return [parsed] if parsed else []


def _string_values(values: Iterable[object]) -> list[str]:
    return [cleaned for value in values if (cleaned := str(value).strip())]


def first_str(val: object) -> str | None:
    if isinstance(val, (list, tuple, set)):
        first = next(iter(val), None)
        return str(first).strip() if first is not None else None
    return str(val).strip() or None


def normalise_scalar(value: object) -> str | None:

    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        return first_str(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = frappe.parse_json(stripped)
        except ValueError:
            return stripped
        return first_str(parsed)
    return str(value).strip() or None
