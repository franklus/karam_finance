"""Consolidate equivalent standard reports left by a v15 restore."""

import frappe

_IGNORED_FIELDS = {
    "name",
    "report_name",
    "creation",
    "modified",
    "owner",
    "modified_by",
    "parent",
    "roles",
}


def _definition(value: object) -> object:
    if isinstance(value, dict):
        return {k: _definition(v) for k, v in value.items() if k not in _IGNORED_FIELDS}
    if isinstance(value, list):
        return [_definition(row) for row in value]
    return value


def rename_report(old_name: str, new_name: str, module: str) -> None:
    """Move links to the canonical report without deleting source files."""
    merge = merge_if_equivalent(old_name, new_name, module)
    developer_mode = frappe.conf.get("developer_mode")
    try:
        # Report.on_trash otherwise schedules deletion of the checked-out folder.
        frappe.conf.update(developer_mode=0)
        frappe.rename_doc("Report", old_name, new_name, force=True, merge=merge)
    finally:
        frappe.conf.update(developer_mode=developer_mode)


def merge_if_equivalent(old_name: str, new_name: str, module: str) -> bool:
    """Allow a merge only when the target retains the source definition and roles."""
    if not frappe.db.exists("Report", new_name):
        return False

    old = frappe.get_doc("Report", old_name).as_dict()
    new = frappe.get_doc("Report", new_name).as_dict()

    if (
        old["is_standard"] != "Yes"
        or new["is_standard"] != "Yes"
        or old["module"] != module
        or new["module"] != module
        or old["report_type"] != "Script Report"
        or _definition(old) != _definition(new)
        or not {row["role"] for row in old["roles"]}.issubset(
            row["role"] for row in new["roles"]
        )
    ):
        frappe.throw(
            f"Cannot consolidate Report {old_name} into {new_name}: "
            "the definitions or permissions differ. Preserve both reports and "
            "resolve the customisation before retrying migration."
        )
    return True
