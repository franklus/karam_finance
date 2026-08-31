"""DOE (Difference of Exchange) Computation Engine for Reporting Currency.

This module computes and creates DOE entries directly in RC GLE without creating actual
GL Entries. It creates balanced journal-entry-style records with reporting_doe=1 to
distinguish them from synced GL Entry records (reporting_doe=0).

Architecture: 5-Phase Fail-Fast System
--------------------------------------
Phase 1: Validation & Parameter Establishment
Phase 2: Filter RC GLE Records
Phase 3: Compute DOE per Account
Phase 4: Create 2 RC GLE Records per Account
Phase 5: Bulk Insert
"""

import hashlib
from typing import Any

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate, now

DOCTYPE_RC_GLE = "Reporting Currency GLE"
DOCTYPE_RC_SETTINGS = "Reporting Currency Settings"
DOCTYPE_ACCOUNT_EXCLUSIONS = "Account Exclusions"

# Threshold for considering amounts as effectively zero
DOE_ZERO_THRESHOLD = 0.01

# Minimum parts in DOE name (KE-RCDOE-GLE-{YYYY}-{#####})
DOE_NAME_MIN_PARTS = 4


def _get_rc_parameters_sorted_by_date(rc_parameters: list[Any]) -> list[Any]:
    """Return rc_parameters rows sorted by doe_posting_date ascending."""
    return sorted(rc_parameters, key=lambda row: getdate(row.doe_posting_date))


def _get_accounts_with_totals(
    company: str,
    reporting_currency: str,
    excluded_accounts_condition: str,
    until_date: str,
) -> list[dict[str, Any]]:
    """Return RC GLE accounts that require DOE along with aggregated totals.

    Aggregates all non-DOE RC GLE records from the beginning until until_date.

    Args:
        company: Company name
        reporting_currency: Target reporting currency
        excluded_accounts_condition: SQL condition for excluded accounts
        until_date: End date for filtering RC GLE records (inclusive)

    Returns:
        List of account dicts with aggregated totals
    """
    return frappe.db.sql(  # nosemgrep — parameterised values
        f"""
		SELECT
			account,
			account_currency,
			COALESCE(SUM(debit), 0) AS total_debit,
			COALESCE(SUM(credit), 0) AS total_credit,
			COALESCE(SUM(reporting_debit), 0) AS total_reporting_debit,
			COALESCE(SUM(reporting_credit), 0) AS total_reporting_credit
		FROM `tab{DOCTYPE_RC_GLE}`
		WHERE company = %s
			AND account_currency != %s
			AND posting_date <= %s
			{excluded_accounts_condition}
		GROUP BY account, account_currency
		ORDER BY account
		""",  # noqa: S608
        (company, reporting_currency, until_date),
        as_dict=True,
    )


def _get_profit_loss_currency_map(
    profit_account: str,
    loss_account: str,
    reporting_currency: str,
) -> dict[str, str | None]:
    """Fetch profit and loss account currencies.

    Args:
            profit_account: Profit account name
            loss_account: Loss account name
            reporting_currency: Fallback currency if account has none

    Returns:
            Dict mapping account name to currency
    """
    accounts = [a for a in (profit_account, loss_account) if a]
    if not accounts:
        return {}

    rows = frappe.get_all(
        "Account",
        filters={"name": ["in", accounts]},
        fields=["name", "account_currency"],
        limit=0,
    )
    return {row["name"]: row["account_currency"] or reporting_currency for row in rows}


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================


@frappe.whitelist()  # nosemgrep — RC module, UI-controlled access
def compute_doe(background: bool = True) -> dict[str, Any]:
    """Main entry point for DOE computation.

    Args:
            background: If True, run as background job (default)

    Returns:
            dict: Job ID if background=True, or result dict if background=False
    """
    # Get settings (Single DocType)
    settings = frappe.get_single(DOCTYPE_RC_SETTINGS)

    if not settings.reporting_currency:
        frappe.throw(_("Reporting Currency Settings not configured"))

    if background:
        # Generate unique job ID (md5 used for brevity, not security)
        job_id = hashlib.md5(f"doe-{now()}".encode()).hexdigest()[:12]  # noqa: S324

        # Enqueue background job
        frappe.enqueue(
            "karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.doe._compute_doe_background",
            queue="long",
            timeout=3600,  # 1 hour
            job_id=job_id,
        )

        return {"job_id": job_id, "message": _("DOE computation started in background")}
    # Run synchronously
    return _compute_doe_background()


def _compute_doe_background() -> dict[str, Any]:  # noqa: PLR0915
    """Background job for DOE computation.

    This is the main orchestrator that runs all 5 phases.
    Processes rc_parameters rows sorted by doe_posting_date ascending, computing DOE
    cumulatively from the beginning until each row's doe_posting_date. DOE records
    accumulate across rows.
    """
    from erpnext.accounts.utils import get_fiscal_year  # noqa: PLC0415

    frappe.db.commit()  # nosemgrep — background job
    company = None

    try:
        _publish_progress(0, "Starting DOE computation...")

        # ====================================================================
        # PHASE 1: VALIDATION & PARAMETER ESTABLISHMENT
        # ====================================================================
        _publish_progress(10, "Phase 1: Validating configuration...")

        settings = frappe.get_single(DOCTYPE_RC_SETTINGS)

        company = frappe.db.get_value(DOCTYPE_RC_GLE, {"reporting_doe": 0}, "company")

        if not company:
            frappe.throw(_("No RC GLE records found. Please run sync first."))

        if not settings.reporting_currency:
            frappe.throw(_("Reporting Currency is not configured in settings"))

        if not settings.rc_parameters or len(settings.rc_parameters) == 0:
            frappe.throw(_("No RC Parameters defined. Please add at least one row."))

        rc_gle_count = frappe.db.count(
            DOCTYPE_RC_GLE, {"company": company, "reporting_doe": 0}
        )
        if rc_gle_count == 0:
            frappe.throw(_("No RC GLE records found. Please run sync first."))

        _publish_progress(20, f"Validated. Found {rc_gle_count} RC GLE records.")

        # ====================================================================
        # PHASE 2: FILTER RC GLE RECORDS & SETUP
        # ====================================================================
        _publish_progress(25, "Phase 2: Filtering RC GLE records...")

        excluded_accounts = frappe.db.sql_list(
            """
			SELECT account
			FROM `tabAccount Exclusions`
			WHERE parent = %s
		""",
            DOCTYPE_RC_SETTINGS,
        )

        excluded_accounts_condition = ""
        if excluded_accounts:
            excluded_accounts_str = ", ".join(
                [frappe.db.escape(acc) for acc in excluded_accounts]
            )
            excluded_accounts_condition = (
                f"AND account NOT IN ({excluded_accounts_str})"
            )

        # Sort rc_parameters by doe_posting_date ascending
        sorted_params = _get_rc_parameters_sorted_by_date(settings.rc_parameters)

        _publish_progress(30, f"Found {len(sorted_params)} row(s) to process")

        # ====================================================================
        # PHASE 3: DELETE EXISTING DOE RECORDS
        # ====================================================================
        _publish_progress(32, "Phase 3: Deleting existing DOE records...")

        frappe.db.sql(  # nosemgrep — parameterised values
            f"""
			DELETE FROM `tab{DOCTYPE_RC_GLE}`
			WHERE company = %s AND reporting_doe = 1
		""",  # noqa: S608
            company,
        )

        _publish_progress(35, "Deleted existing DOE records")

        # ====================================================================
        # PHASE 4: COMPUTE DOE & CREATE RECORDS
        # ====================================================================
        _publish_progress(40, "Phase 4: Computing DOE and creating records...")

        doe_records: list[dict[str, Any]] = []
        total_processed_count = 0

        name_counters_by_year: dict[int, dict[str, Any]] = {}

        # Track prior DOE amounts per account for subsequent row calculations
        # This is needed because DOE records are bulk inserted at the end,
        # so subsequent rows can't see prior DOE via database queries
        prior_doe_by_account: dict[str, dict[str, float]] = {}

        for row_idx, row in enumerate(sorted_params):
            exchange_rate = flt(row.exchange_rate)
            doe_posting_date = getdate(row.doe_posting_date)
            profit_account = row.profit_account
            loss_account = row.loss_account

            if not exchange_rate:
                frappe.log_error(
                    title=f"DOE Skipped for row {row.idx}",
                    message="Exchange rate not configured",
                )
                continue

            if not profit_account or not loss_account:
                frappe.log_error(
                    title=f"DOE Skipped for row {row.idx}",
                    message="Profit/Loss accounts not configured",
                )
                continue

            fiscal_year = get_fiscal_year(doe_posting_date, company=company)[0]

            profit_loss_currency_map = _get_profit_loss_currency_map(
                profit_account, loss_account, settings.reporting_currency
            )

            doe_year = doe_posting_date.year
            if doe_year not in name_counters_by_year:
                name_counters_by_year[doe_year] = _get_starting_doe_number(
                    doe_posting_date
                )
            name_counter = name_counters_by_year[doe_year]

            # Get accounts with cumulative totals from beginning until doe_posting_date
            accounts = _get_accounts_with_totals(
                company=company,
                reporting_currency=settings.reporting_currency,
                excluded_accounts_condition=excluded_accounts_condition,
                until_date=doe_posting_date.isoformat(),
            )

            if not accounts:
                msg = f"Row {row.idx} (until {doe_posting_date}): No accounts"
                _publish_progress(
                    40 + int((row_idx + 1) / len(sorted_params) * 45), msg
                )
                continue

            for account_info in accounts:
                account = account_info.account
                account_currency = account_info.account_currency

                # Adjust totals with prior DOE amounts (not yet in database)
                adjusted_totals = dict(account_info)
                if account in prior_doe_by_account:
                    prior = prior_doe_by_account[account]
                    adjusted_totals["total_reporting_debit"] = (
                        flt(adjusted_totals["total_reporting_debit"])
                        + prior["reporting_debit"]
                    )
                    adjusted_totals["total_reporting_credit"] = (
                        flt(adjusted_totals["total_reporting_credit"])
                        + prior["reporting_credit"]
                    )

                computed_data = _compute_doe_for_account(
                    company=company,
                    account=account,
                    _account_currency=account_currency,
                    _reporting_currency=settings.reporting_currency,
                    exchange_rate=exchange_rate,
                    account_totals=adjusted_totals,
                )

                if computed_data is None:
                    continue

                if abs(computed_data.get("final_amount", 0)) < DOE_ZERO_THRESHOLD:
                    continue

                records = _create_doe_records(
                    account=account,
                    account_currency=account_currency,
                    computed_data=computed_data,
                    doe_posting_date=doe_posting_date,
                    profit_account=profit_account,
                    loss_account=loss_account,
                    reporting_currency=settings.reporting_currency,
                    fiscal_year=fiscal_year,
                    company=company,
                    name_counter=name_counter,
                    profit_loss_currency_map=profit_loss_currency_map,
                )

                doe_records.extend(records)
                total_processed_count += 1

                # Track this DOE for subsequent rows
                # Record 0 is always for the main account (not profit/loss)
                if account not in prior_doe_by_account:
                    prior_doe_by_account[account] = {
                        "reporting_debit": 0.0,
                        "reporting_credit": 0.0,
                    }
                prior_doe_by_account[account]["reporting_debit"] += flt(
                    records[0].get("reporting_debit")
                )
                prior_doe_by_account[account]["reporting_credit"] += flt(
                    records[0].get("reporting_credit")
                )

            progress = 40 + int((row_idx + 1) / len(sorted_params) * 45)
            msg = f"Completed row {row.idx} (until {doe_posting_date})"
            _publish_progress(progress, msg)

        _publish_progress(85, f"Created {len(doe_records)} DOE records")

        # ====================================================================
        # PHASE 5: BULK INSERT
        # ====================================================================
        _publish_progress(90, "Phase 5: Inserting DOE records...")

        if doe_records:
            _bulk_insert_doe_records(doe_records)

        _publish_progress(95, f"Inserted {len(doe_records)} DOE records")

        settings.db_set("last_sync_timestamp", now())
        frappe.db.commit()  # nosemgrep — background job

        _publish_progress(100, "DOE computation completed successfully!")

        msg = (
            f"DOE computation completed. Processed {total_processed_count} "
            f"accounts, created {len(doe_records)} records."
        )
        result = {
            "success": True,
            "message": msg,
            "accounts_processed": total_processed_count,
            "records_created": len(doe_records),
        }

        frappe.msgprint(result["message"], alert=True, indicator="green")

    except Exception as e:
        frappe.db.rollback()

        frappe.log_error(
            title=f"DOE Computation Failed for {company or 'unknown'}",
            message=frappe.get_traceback(),
        )

        _publish_progress(-1, f"Error: {e!s}")
        raise

    else:
        return result


def compute_doe_inline(
    _progress_event: str | None = None, _user: str | None = None
) -> dict[str, Any]:
    """Inline DOE computation for sync workflow (non-background).

    This function is called directly from the sync job after RC GLE records are updated.
    It computes DOE synchronously without queuing a background job.

    Processing logic:
    - Sort rc_parameters rows by doe_posting_date ascending
    - Delete all existing DOE records once at start
    - For each row: aggregate data from beginning until doe_posting_date, compute DOE
    - DOE records accumulate across rows (2 per account per row)

    Args:
        _progress_event: Event name for progress updates (reserved for future use)
        _user: User to send realtime updates to (reserved for future use)

    Returns:
        dict: Computation result with accounts_processed and records_created
    """
    from erpnext.accounts.utils import get_fiscal_year  # noqa: PLC0415

    savepoint_name: str | None = None
    try:
        settings = frappe.get_single(DOCTYPE_RC_SETTINGS)

        company = frappe.db.get_value(DOCTYPE_RC_GLE, {"reporting_doe": 0}, "company")

        if not company:
            return {
                "success": False,
                "message": "No RC GLE records found",
                "accounts_processed": 0,
                "records_created": 0,
            }

        if not settings.reporting_currency:
            return {"success": False, "message": "Reporting Currency not configured"}

        if not settings.rc_parameters or len(settings.rc_parameters) == 0:
            return {"success": False, "message": "No RC Parameters defined"}

        # Get excluded accounts
        excluded_accounts = frappe.db.sql_list(
            """
			SELECT account
			FROM `tabAccount Exclusions`
			WHERE parent = %s
		""",
            DOCTYPE_RC_SETTINGS,
        )

        excluded_accounts_condition = ""
        if excluded_accounts:
            excluded_accounts_str = ", ".join(
                [frappe.db.escape(acc) for acc in excluded_accounts]
            )
            excluded_accounts_condition = (
                f"AND account NOT IN ({excluded_accounts_str})"
            )

        # Sort rc_parameters by doe_posting_date ascending
        sorted_params = _get_rc_parameters_sorted_by_date(settings.rc_parameters)

        savepoint_name = "inline_doe_compute"
        frappe.db.savepoint(savepoint_name)

        # Delete all existing DOE records ONCE at the start
        frappe.db.sql(  # nosemgrep — parameterised values
            f"""
			DELETE FROM `tab{DOCTYPE_RC_GLE}`
			WHERE company = %s AND reporting_doe = 1
		""",  # noqa: S608
            company,
        )

        doe_records: list[dict[str, Any]] = []
        total_processed_count = 0

        # Track name counters per doe_posting_date year to avoid collisions
        name_counters_by_year: dict[int, dict[str, Any]] = {}

        # Track prior DOE amounts per account for subsequent row calculations
        # This is needed because DOE records are bulk inserted at the end,
        # so subsequent rows can't see prior DOE via database queries
        prior_doe_by_account: dict[str, dict[str, float]] = {}

        # Process each rc_parameters row (oldest to newest by doe_posting_date)
        for row in sorted_params:
            exchange_rate = flt(row.exchange_rate)
            doe_posting_date = getdate(row.doe_posting_date)
            profit_account = row.profit_account
            loss_account = row.loss_account

            if not exchange_rate:
                frappe.log_error(
                    title=f"DOE Skipped for row {row.idx}",
                    message="Exchange rate not configured",
                )
                continue

            if not profit_account or not loss_account:
                frappe.log_error(
                    title=f"DOE Skipped for row {row.idx}",
                    message="Profit/Loss accounts not configured",
                )
                continue

            fiscal_year = get_fiscal_year(doe_posting_date, company=company)[0]

            profit_loss_currency_map = _get_profit_loss_currency_map(
                profit_account, loss_account, settings.reporting_currency
            )

            doe_year = doe_posting_date.year
            if doe_year not in name_counters_by_year:
                name_counters_by_year[doe_year] = _get_starting_doe_number(
                    doe_posting_date
                )
            name_counter = name_counters_by_year[doe_year]

            # Get accounts with cumulative totals from beginning until doe_posting_date
            accounts = _get_accounts_with_totals(
                company=company,
                reporting_currency=settings.reporting_currency,
                excluded_accounts_condition=excluded_accounts_condition,
                until_date=doe_posting_date.isoformat(),
            )

            if not accounts:
                continue

            for account_info in accounts:
                account = account_info.account
                account_currency = account_info.account_currency

                # Adjust totals with prior DOE amounts (not yet in database)
                adjusted_totals = dict(account_info)
                if account in prior_doe_by_account:
                    prior = prior_doe_by_account[account]
                    adjusted_totals["total_reporting_debit"] = (
                        flt(adjusted_totals["total_reporting_debit"])
                        + prior["reporting_debit"]
                    )
                    adjusted_totals["total_reporting_credit"] = (
                        flt(adjusted_totals["total_reporting_credit"])
                        + prior["reporting_credit"]
                    )

                computed_data = _compute_doe_for_account(
                    company=company,
                    account=account,
                    _account_currency=account_currency,
                    _reporting_currency=settings.reporting_currency,
                    exchange_rate=exchange_rate,
                    account_totals=adjusted_totals,
                )

                if computed_data is None:
                    continue

                if abs(computed_data.get("final_amount", 0)) < DOE_ZERO_THRESHOLD:
                    continue

                records = _create_doe_records(
                    account=account,
                    account_currency=account_currency,
                    computed_data=computed_data,
                    doe_posting_date=doe_posting_date,
                    profit_account=profit_account,
                    loss_account=loss_account,
                    reporting_currency=settings.reporting_currency,
                    fiscal_year=fiscal_year,
                    company=company,
                    name_counter=name_counter,
                    profit_loss_currency_map=profit_loss_currency_map,
                )

                doe_records.extend(records)
                total_processed_count += 1

                # Track this DOE for subsequent rows
                # Record 0 is always for the main account (not profit/loss)
                if account not in prior_doe_by_account:
                    prior_doe_by_account[account] = {
                        "reporting_debit": 0.0,
                        "reporting_credit": 0.0,
                    }
                prior_doe_by_account[account]["reporting_debit"] += flt(
                    records[0].get("reporting_debit")
                )
                prior_doe_by_account[account]["reporting_credit"] += flt(
                    records[0].get("reporting_credit")
                )

        # Bulk insert all accumulated DOE records
        if doe_records:
            _bulk_insert_doe_records(doe_records)

        msg = (
            f"DOE computation completed. Processed {total_processed_count} "
            f"accounts, created {len(doe_records)} records."
        )
        return {
            "success": True,
            "message": msg,
            "accounts_processed": total_processed_count,
            "records_created": len(doe_records),
        }

    except Exception:
        if savepoint_name:
            frappe.db.rollback(save_point=savepoint_name)
        else:
            frappe.db.rollback()

        frappe.log_error(
            title="DOE Computation Failed (Inline)", message=frappe.get_traceback()
        )

        raise


# ============================================================================
# PHASE 3: COMPUTE DOE FOR ACCOUNT
# ============================================================================


def _compute_doe_for_account(
    company: str,
    account: str,
    _account_currency: str,
    _reporting_currency: str,
    exchange_rate: float,
    account_totals: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Compute DOE values for a specific account.

    Args:
        company: Company name
        account: Account name
        _account_currency: Account currency (reserved for future use)
        _reporting_currency: Reporting currency (reserved for future use)
        exchange_rate: Exchange rate to use for DOE calculation
        account_totals: Pre-computed account totals (optional)

    Returns:
        dict: Computed values including final_amount
    """
    if account_totals is not None:
        record = account_totals
    else:
        records = frappe.db.sql(  # nosemgrep — parameterised values
            f"""
			SELECT
				COALESCE(SUM(debit), 0) as total_debit,
				COALESCE(SUM(credit), 0) as total_credit,
				COALESCE(SUM(reporting_debit), 0) as total_reporting_debit,
				COALESCE(SUM(reporting_credit), 0) as total_reporting_credit
			FROM `tab{DOCTYPE_RC_GLE}`
			WHERE company = %s
				AND account = %s
		""",  # noqa: S608
            (company, account),
            as_dict=True,
        )

        if not records:
            return None

        record = records[0]

    # Step 1: Default currency totals
    total_debit_default_currency = flt(record.get("total_debit"), 9)
    total_credit_default_currency = flt(record.get("total_credit"), 9)
    difference_default_currency = (
        total_debit_default_currency - total_credit_default_currency
    )

    # Step 2: Reporting currency totals
    reporting_debit_total = flt(record.get("total_reporting_debit"), 9)
    reporting_credit_total = flt(record.get("total_reporting_credit"), 9)
    difference_reporting_currency = reporting_debit_total - reporting_credit_total

    # Step 3: DOE difference
    # difference_default_currency ÷ exchange_rate
    reporting_doe_difference = (
        flt(difference_default_currency / exchange_rate, 9) if exchange_rate else 0
    )

    # Step 4: Final amount
    # reporting_doe_difference - difference_reporting_currency
    final_amount = flt(reporting_doe_difference - difference_reporting_currency, 9)

    return {
        "total_debit_default_currency": total_debit_default_currency,
        "total_credit_default_currency": total_credit_default_currency,
        "difference_default_currency": difference_default_currency,
        "reporting_debit_total": reporting_debit_total,
        "reporting_credit_total": reporting_credit_total,
        "difference_reporting_currency": difference_reporting_currency,
        "reporting_doe_difference": reporting_doe_difference,
        "final_amount": final_amount,
    }


# ============================================================================
# PHASE 4: CREATE DOE RECORDS
# ============================================================================


def _create_doe_records(  # noqa: PLR0913
    account: str,
    account_currency: str,
    computed_data: dict[str, Any],
    doe_posting_date: str,
    profit_account: str,
    loss_account: str,
    reporting_currency: str,
    fiscal_year: str,
    company: str,
    name_counter: dict[str, Any],
    profit_loss_currency_map: dict[str, str | None],
) -> list[dict[str, Any]]:
    """Create 2 balanced RC GLE records for the account.

    Logic:
    - If final_amount > 0: Debit account, Credit profit_account
    - If final_amount < 0: Credit account, Debit loss_account

    Args:
            account: Account name
            account_currency: Account's currency
            computed_data: Dict with computed DOE values
            doe_posting_date: Posting date for DOE entries (from rc_parameters row)
            profit_account: Profit account (from rc_parameters row)
            loss_account: Loss account (from rc_parameters row)
            reporting_currency: Target reporting currency
            fiscal_year: Fiscal year for the entries
            company: Company name
            name_counter: Dict with year and counter for name generation
            profit_loss_currency_map: Cached currency map for profit/loss accounts

    Returns:
            list: 2 RC GLE record dicts
    """
    final_amount = computed_data["final_amount"]

    # Determine accounts and amounts based on sign
    if final_amount > 0:
        # Positive difference means profit entry
        account_1 = account
        against_1 = profit_account
        reporting_debit_1 = abs(final_amount)
        reporting_credit_1 = 0

        account_2 = profit_account
        against_2 = account
        reporting_debit_2 = 0
        reporting_credit_2 = abs(final_amount)
    else:
        # Negative difference means loss entry
        account_1 = account
        against_1 = loss_account
        reporting_debit_1 = 0
        reporting_credit_1 = abs(final_amount)

        account_2 = loss_account
        against_2 = account
        reporting_debit_2 = abs(final_amount)
        reporting_credit_2 = 0

    # Get account currency for profit/loss account
    profit_loss_acct = profit_account if final_amount > 0 else loss_account
    profit_loss_currency = profit_loss_currency_map.get(profit_loss_acct)
    if not profit_loss_currency:
        profit_loss_currency = (
            frappe.db.get_value("Account", profit_loss_acct, "account_currency")
            or reporting_currency
        )
        profit_loss_currency_map[profit_loss_acct] = profit_loss_currency

    # Generate voucher_no for DOE pair identification (e.g. DOE-2017-40110020001)
    doe_year = getdate(doe_posting_date).year
    account_number = (
        frappe.db.get_value("Account", account, "account_number") or account
    )
    voucher_no = f"DOE-{doe_year}-{account_number}"

    # Generate names using counter
    name_1 = _generate_doe_name_from_counter(doe_posting_date, name_counter)
    name_2 = _generate_doe_name_from_counter(doe_posting_date, name_counter)

    # Common fields (excluding meta fields like 'doctype')
    common_fields = {
        "reporting_doe": 1,
        "docstatus": 1,
        "posting_date": doe_posting_date,
        "fiscal_year": fiscal_year,
        "voucher_type": "Exchange Rate Revaluation",
        "voucher_no": voucher_no,
        "reporting_currency": reporting_currency,
        "company": company,
        # Company currency amounts (0 for DOE entries)
        "debit": 0,
        "credit": 0,
        "debit_amount_in_account_currency": 0,
        "credit_amount_in_account_currency": 0,
        # Computed totals (same for both records)
        "total_debit_default_currency": computed_data["total_debit_default_currency"],
        "total_credit_default_currency": computed_data["total_credit_default_currency"],
        "difference_default_currency": computed_data["difference_default_currency"],
        "reporting_debit_total": computed_data["reporting_debit_total"],
        "reporting_credit_total": computed_data["reporting_credit_total"],
        "difference_reporting_currency": computed_data["difference_reporting_currency"],
        "reporting_doe_difference": computed_data["reporting_doe_difference"],
    }

    # Record 1
    record_1 = {
        **common_fields,
        "name": name_1,
        "account": account_1,
        "against": against_1,
        "account_currency": account_currency,
        "reporting_debit": reporting_debit_1,
        "reporting_credit": reporting_credit_1,
    }

    # Record 2
    record_2 = {
        **common_fields,
        "name": name_2,
        "account": account_2,
        "against": against_2,
        "account_currency": profit_loss_currency,
        "reporting_debit": reporting_debit_2,
        "reporting_credit": reporting_credit_2,
    }

    return [record_1, record_2]


def _get_starting_doe_number(posting_date: str) -> dict[str, int]:
    """Get the starting number for DOE naming series.

    Returns a dict with 'year' and 'counter' that can be incremented.
    """
    posting_datetime = get_datetime(posting_date)
    year = posting_datetime.year

    # Get the last number used for this year
    last_name = frappe.db.sql(  # nosemgrep — no user input
        f"""
		SELECT name
		FROM `tab{DOCTYPE_RC_GLE}`
		WHERE name LIKE 'KE-RCDOE-GLE-{year}-%'
		ORDER BY name DESC
		LIMIT 1
	"""  # noqa: S608
    )

    if last_name and last_name[0][0]:
        # Extract number and increment
        parts = last_name[0][0].split("-")
        if len(parts) >= DOE_NAME_MIN_PARTS:
            try:
                last_number = int(parts[3])
                next_number = last_number + 1
            except (ValueError, IndexError):
                next_number = 1
        else:
            next_number = 1
    else:
        next_number = 1

    return {"year": year, "counter": next_number}


def _generate_doe_name_from_counter(
    _posting_date: str, name_counter: dict[str, Any]
) -> str:
    """Generate a unique name using an incrementing counter.

    Args:
            _posting_date: Date for the DOE entry (reserved, uses name_counter year)
            name_counter: Dict with 'year' and 'counter' keys (will be incremented)

    Returns:
            str: Unique name like KE-RCDOE-GLE-2025-00001
    """
    year = name_counter["year"]
    number = name_counter["counter"]

    # Increment counter for next call
    name_counter["counter"] += 1

    return f"KE-RCDOE-GLE-{year}-{number:05d}"


def _generate_doe_name(_company: str, posting_date: str) -> str:
    """Generate a unique name for DOE entry.

    Pattern: KE-RCDOE-GLE-{YYYY}-{#####}

    Args:
        _company: Company name (reserved for future use)
        posting_date: Posting date for the DOE entry
    """
    posting_datetime = get_datetime(posting_date)
    year = posting_datetime.year

    # Get next number for this year
    last_name = frappe.db.sql(  # nosemgrep — no user input
        f"""
		SELECT name
		FROM `tab{DOCTYPE_RC_GLE}`
		WHERE name LIKE 'KE-RCDOE-GLE-{year}-%'
		ORDER BY name DESC
		LIMIT 1
	"""  # noqa: S608
    )

    if last_name and last_name[0][0]:
        # Extract number and increment
        parts = last_name[0][0].split("-")
        if len(parts) >= DOE_NAME_MIN_PARTS:
            try:
                last_number = int(parts[3])
                next_number = last_number + 1
            except (ValueError, IndexError):
                next_number = 1
        else:
            next_number = 1
    else:
        next_number = 1

    return f"KE-RCDOE-GLE-{year}-{next_number:05d}"


# ============================================================================
# PHASE 5: BULK INSERT
# ============================================================================


def _bulk_insert_doe_records(records: list[dict[str, Any]]) -> None:
    """Bulk insert DOE records into RC GLE table."""
    # Define fields to insert (excluding 'doctype' as it's a meta field)
    fields = [
        "name",
        "reporting_doe",
        "posting_date",
        "fiscal_year",
        "account",
        "account_currency",
        "against",
        "voucher_type",
        "voucher_no",
        "reporting_currency",
        "reporting_debit",
        "reporting_credit",
        "debit",
        "credit",
        "debit_amount_in_account_currency",
        "credit_amount_in_account_currency",
        "total_debit_default_currency",
        "total_credit_default_currency",
        "difference_default_currency",
        "reporting_debit_total",
        "reporting_credit_total",
        "difference_reporting_currency",
        "reporting_doe_difference",
        "company",
        "docstatus",
        "manual_entry",
        "creation",
        "modified",
        "owner",
        "modified_by",
    ]

    # Prepare values
    values = []
    for record in records:
        row = [
            record.get("name"),
            record.get("reporting_doe"),
            record.get("posting_date"),
            record.get("fiscal_year"),
            record.get("account"),
            record.get("account_currency"),
            record.get("against"),
            record.get("voucher_type"),
            record.get("voucher_no"),
            record.get("reporting_currency"),
            flt(record.get("reporting_debit"), 9),
            flt(record.get("reporting_credit"), 9),
            flt(record.get("debit"), 9),
            flt(record.get("credit"), 9),
            flt(record.get("debit_amount_in_account_currency"), 9),
            flt(record.get("credit_amount_in_account_currency"), 9),
            flt(record.get("total_debit_default_currency"), 9),
            flt(record.get("total_credit_default_currency"), 9),
            flt(record.get("difference_default_currency"), 9),
            flt(record.get("reporting_debit_total"), 9),
            flt(record.get("reporting_credit_total"), 9),
            flt(record.get("difference_reporting_currency"), 9),
            flt(record.get("reporting_doe_difference"), 9),
            record.get("company"),
            record.get("docstatus", 0),
            0,  # manual_entry - DOE records are never manual
            now(),  # creation
            now(),  # modified
            frappe.session.user,  # owner
            frappe.session.user,  # modified_by
        ]
        values.append(row)

    # Bulk insert in chunks of 1000
    chunk_size = 1000
    for i in range(0, len(values), chunk_size):
        chunk = values[i : i + chunk_size]
        frappe.db.bulk_insert(DOCTYPE_RC_GLE, fields, chunk)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


def _publish_progress(progress: int, message: str) -> None:
    """Publish real-time progress event via WebSocket.

    Args:
            progress: Progress percentage (0-100), -1 for error
            message: Status message
    """
    frappe.publish_realtime(
        "doe_progress",
        {"progress": progress, "message": message, "timestamp": now()},
        user=frappe.session.user,
    )
