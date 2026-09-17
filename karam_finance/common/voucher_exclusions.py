"""Internal Journal Entry exclusions use both parts of voucher identity."""

from typing import Any

from frappe.query_builder import Criterion
from pypika.queries import Table

JOURNAL_EXCLUSION_SQL = (
    "(gl.voucher_type is null or gl.voucher_type != 'Journal Entry' "
    "or gl.voucher_no is null or gl.voucher_no not in %(voucher_no_not_in)s)"
)


def exclude_journal_entries(ledger: Table, names: Any) -> Criterion:
    """Keep unrelated voucher types, including names shared with an excluded JE."""
    return (
        ledger.voucher_type.isnull()
        | (ledger.voucher_type != "Journal Entry")
        | ledger.voucher_no.isnull()
        | ~ledger.voucher_no.isin(names)
    )
