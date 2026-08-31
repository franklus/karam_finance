import frappe


def parse_multiselect(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [c for c in (str(v).strip() for v in value) if c]
    if isinstance(value, str):
        try:
            parsed = frappe.parse_json(value)
        except Exception:
            return [c for c in (part.strip() for part in value.split(",")) if c]
        if isinstance(parsed, (list, tuple, set)):
            return [c for c in (str(v).strip() for v in parsed) if c]
        parsed_str = str(parsed).strip()
        return [parsed_str] if parsed_str else []
    parsed_str = str(value).strip()
    return [parsed_str] if parsed_str else []


def normalise_scalar(value: object) -> str | None:
    def first_str(val: object) -> str | None:
        if isinstance(val, (list, tuple, set)):
            first = next(iter(val), None)
            return str(first).strip() if first is not None else None
        return str(val).strip() or None

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
        except Exception:
            return stripped
        return first_str(parsed)
    return str(value).strip() or None
