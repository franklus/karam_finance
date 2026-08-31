# ruff: noqa: D100, D103, E501, S608

import frappe

from karam_finance.common.sql_utils import safe_int

from .glrc_conditions import chunked, get_conditions, get_order_by_clause

PARTY_LOOKUP_BATCH_SIZE = 1000


def get_gl_entries(filters: dict) -> list[frappe._dict]:
    fields = [
        "rc.name as gl_entry",
        "rc.posting_date",
        "rc.account",
        "rc.party_type",
        "rc.party",
        "rc.voucher_type",
        "rc.voucher_subtype",
        "rc.voucher_no",
        "rc.cost_center",
        "rc.project",
        "rc.against_voucher_type",
        "rc.against_voucher",
        "rc.account_currency",
        "rc.against",
        "rc.is_opening",
        "rc.creation",
        "rc.reporting_debit",
        "rc.reporting_credit",
        "rc.reporting_currency",
        "rc.reporting_doe",
        "rc.manual_entry",
    ]

    if filters.get("show_remarks"):
        remarks_length = safe_int(filters.get("_remarks_length", 0))
        if remarks_length:
            fields.append(f"substr(rc.remarks, 1, {remarks_length}) as remarks")
        else:
            fields.append("rc.remarks")

    order_by = get_order_by_clause(filters)

    gl_entries = frappe.db.sql(  # nosemgrep: frappe-sql-injection-format-string, frappe-prefer-query-builder
        f"""
        select {", ".join(fields)}
        from `tabReporting Currency GLE` rc
        where rc.company=%(company)s {get_conditions(filters)}
        {order_by}
        """,
        filters,
        as_dict=1,
    )

    party_name_map = get_party_name_map(gl_entries)
    for gl_entry in gl_entries:
        if gl_entry.party_type and gl_entry.party:
            gl_entry.party_name = party_name_map.get(gl_entry.party_type, {}).get(
                gl_entry.party
            )
        gl_entry.debit = gl_entry.reporting_debit or 0
        gl_entry.credit = gl_entry.reporting_credit or 0
        gl_entry.debit_in_account_currency = gl_entry.debit
        gl_entry.credit_in_account_currency = gl_entry.credit

    return gl_entries


def get_party_name_map(gl_entries: list[frappe._dict]) -> dict[str, dict[str, str]]:
    party_map: dict[str, dict[str, str]] = {}
    if not gl_entries:
        return party_map

    party_fields = {
        "Customer": "customer_name",
        "Supplier": "supplier_name",
        "Employee": "employee_name",
    }

    targets: dict[str, set[str]] = {}
    for entry in gl_entries:
        party_type = entry.get("party_type")
        party = entry.get("party")
        if party_type and party:
            targets.setdefault(party_type, set()).add(party)

    for party_type, fieldname in party_fields.items():
        names = targets.get(party_type)
        if not names:
            continue

        records = []
        for bucket in chunked(
            list(names), PARTY_LOOKUP_BATCH_SIZE, PARTY_LOOKUP_BATCH_SIZE
        ):
            rows = frappe.get_all(
                party_type,
                filters={"name": ["in", bucket]},
                fields=["name", fieldname],
                limit=0,
            )
            records.extend(rows)

        if records:
            party_map[party_type] = {
                row["name"]: row.get(fieldname) or ""
                for row in records
                if row.get("name")
            }

    return party_map
