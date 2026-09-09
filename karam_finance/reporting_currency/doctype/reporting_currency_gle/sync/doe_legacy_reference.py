"""Legacy pair references used only by metadata repair."""

import hashlib

from frappe.utils import getdate

VOUCHER_NO_MAX_LENGTH = 140


def build_doe_voucher_no(
    account_number: str,
    party_type: str | None,
    party: str | None,
    *,
    posting_date: str,
    pair_name: str,
) -> str:
    """Build the legacy reference for the metadata-only repair utility.

    The primary record's sequence distinguishes repeated parameters on a date.
    Both sides use the primary record name, including during metadata repair.
    """
    date = getdate(posting_date)
    if date is None:
        message = "Invalid DOE posting date"
        raise ValueError(message)
    parts = ["DOE", str(date.year), account_number]
    if party:
        parts.extend([party_type or "Party", party])
    parts.extend([date.strftime("%Y%m%d"), pair_name.rsplit("-", 1)[-1]])
    reference = "-".join(parts)
    if len(reference) > VOUCHER_NO_MAX_LENGTH:
        digest = hashlib.sha256(reference.encode()).hexdigest()[:16]
        prefix_length = VOUCHER_NO_MAX_LENGTH - len(digest) - 1
        return f"{reference[:prefix_length]}-{digest}"
    return reference
