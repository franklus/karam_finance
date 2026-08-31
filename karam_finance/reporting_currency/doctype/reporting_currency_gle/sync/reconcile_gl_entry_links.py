"""Reconciliation Script: Fix Broken GL Entry Links in RC GLE Records.

This script fixes RC GLE records that reference non-existent GL Entries due to
ERPNext's GL Entry renaming process (hash names → naming series).

Usage::

    bench console
    >>> from karam_finance.reporting_currency.doctype. (
    ...     reporting_currency_gle.sync.reconcile_gl_entry_links
    ... ) import reconcile_gl_entry_links
    >>> reconcile_gl_entry_links()

Or run directly via bench execute with the full module path.
"""

import frappe
from frappe.utils import now

from .utils import get_gl_entry_stable_hash

_logger = frappe.logger("karam_finance.reconcile_gl_entry_links", allow_site=True)


def reconcile_gl_entry_links(
    commit: bool = True, dry_run: bool = False
) -> dict[str, str | int]:
    """Reconcile orphaned RC GLE records with their actual GL Entries.

    Process:
    1. Find RC GLE records with broken gl_entry links
    2. Calculate hash from current GL Entry data
    3. Store hash in RC GLE record
    4. Match hash to find correct current GL Entry name
    5. Update RC GLE link

    Args:
            commit: Whether to commit changes (default True)
            dry_run: If True, only report without making changes

    Returns:
            dict: Status with fixed and error counts
    """
    if dry_run:
        _logger.info("Running in DRY-RUN mode - no changes will be committed")

    # Step 1: Find orphaned RC GLE records
    orphaned = frappe.db.sql(
        """
		SELECT
			rc.name as rc_name,
			rc.gl_entry as old_gl_name,
			rc.gl_entry_hash,
			rc.voucher_type,
			rc.voucher_no,
			rc.account,
			rc.posting_date,
			rc.debit,
			rc.credit
		FROM `tabReporting Currency GLE` rc
		LEFT JOIN `tabGL Entry` gle ON rc.gl_entry = gle.name
		WHERE gle.name IS NULL
	""",
        as_dict=True,
    )

    if not orphaned:
        return {"status": "success", "fixed": 0, "errors": 0}

    # Step 2: Load all GL Entries (for matching)
    gl_entries = frappe.db.sql(
        """
        SELECT name, voucher_type, voucher_no, account,
               posting_date, debit, credit
        FROM `tabGL Entry`
        """,
        as_dict=True,
    )

    # Step 3: Build hash lookup table for fast matching
    hash_to_gl_entries = {}  # Hash -> list of GL entry names (handles 1:many)
    for gle in gl_entries:
        hash_value = get_gl_entry_stable_hash(gle)
        if hash_value not in hash_to_gl_entries:
            hash_to_gl_entries[hash_value] = []
        hash_to_gl_entries[hash_value].append(gle.name)

    # Report hash collisions (legitimate duplicates)
    collisions = {h: names for h, names in hash_to_gl_entries.items() if len(names) > 1}
    if collisions:
        affected_count = sum(len(names) for names in collisions.values())
        _logger.warning(
            "Found %d hash collision(s) affecting %d GL entries. "
            "These may require manual resolution.",
            len(collisions),
            affected_count,
        )
        for hash_val, names in list(collisions.items())[:5]:  # Log first 5
            _logger.warning("  Hash %s: %s", hash_val, names)

    # Step 4: Reconcile each orphaned record

    fixed = 0
    errors = 0

    for rc in orphaned:
        try:
            # Calculate hash if not already present
            if rc.gl_entry_hash:
                hash_value = rc.gl_entry_hash
            else:
                # Build pseudo-GL Entry dict from RC GLE data
                pseudo_gle = {
                    "voucher_type": rc.voucher_type,
                    "voucher_no": rc.voucher_no,
                    "account": rc.account,
                    "posting_date": rc.posting_date,
                    "debit": rc.debit,
                    "credit": rc.credit,
                }
                hash_value = get_gl_entry_stable_hash(pseudo_gle)

            # Find matching GL Entry (or entries)
            matching_gl_names = hash_to_gl_entries.get(hash_value, [])

            if len(matching_gl_names) == 0:
                # No match found
                _logger.warning(
                    "No matching GL Entry for RC GLE %s (voucher: %s/%s, hash: %s)",
                    rc.rc_name,
                    rc.voucher_type,
                    rc.voucher_no,
                    hash_value,
                )
                errors += 1

            elif len(matching_gl_names) == 1:
                # Unique match - safe to link
                matching_gl_name = matching_gl_names[0]

                if not dry_run:
                    # Update RC GLE record
                    frappe.db.sql(
                        """
						UPDATE `tabReporting Currency GLE`
						SET gl_entry = %s, gl_entry_hash = %s, modified = %s
						WHERE name = %s
					""",
                        (matching_gl_name, hash_value, now(), rc.rc_name),
                    )

                fixed += 1

            else:
                # Multiple matches - ambiguous, needs manual resolution
                _logger.warning(
                    "Ambiguous match for RC GLE %s: found %d GL entries "
                    "with same hash. Candidates: %s",
                    rc.rc_name,
                    len(matching_gl_names),
                    matching_gl_names[:5],  # Show first 5
                )
                errors += 1

        except Exception:
            errors += 1

    # Step 5: Commit changes and log results
    mode_str = "[DRY-RUN] " if dry_run else ""
    _logger.info(
        "%sReconciliation complete: %d fixed, %d errors, %d total orphaned",
        mode_str,
        fixed,
        errors,
        len(orphaned),
    )

    if not dry_run and commit:
        frappe.db.commit()  # nosemgrep — background job
        _logger.info("Changes committed to database")

    return {
        "status": "success" if errors == 0 else "partial",
        "fixed": fixed,
        "errors": errors,
        "total": len(orphaned),
    }


if __name__ == "__main__":
    # Allow running directly via: bench execute path.to.this.file
    reconcile_gl_entry_links()
