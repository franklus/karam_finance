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

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any, cast

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate, now

from karam_finance.reporting_currency.doctype.reporting_currency_settings.reporting_currency_settings import (
    ReportingCurrencySettings,
    validate_doe_exchange_rates,
)

from .doe_storage import bulk_insert_doe_records as _bulk_insert_doe_records
from .validation import get_reporting_company

DOCTYPE_RC_GLE = "Reporting Currency GLE"
DOCTYPE_RC_SETTINGS = "Reporting Currency Settings"

# Threshold for considering amounts as effectively zero
DOE_ZERO_THRESHOLD = 0.01

# Minimum parts in DOE name (KE-RCDOE-GLE-{YYYY}-{#####})
DOE_NAME_MIN_PARTS = 4


def _get_rc_parameters_sorted_by_date(rc_parameters: list[Any]) -> list[Any]:
    """Return rc_parameters rows sorted by doe_posting_date ascending."""
    return sorted(rc_parameters, key=_parameter_posting_date)


def _get_accounts_with_totals(
    company: str,
    reporting_currency: str,
    excluded_accounts_condition: str,
    *,
    until_date: str,
) -> list[dict[str, Any]]:
    """Return DOE account groups with their aggregated non-DOE RCGLE totals.

    Receivable and Payable accounts are split by party. All other accounts keep
    their existing account-level aggregation.

    Args:
        company: Company name
        reporting_currency: Target reporting currency
        excluded_accounts_condition: SQL condition for excluded accounts
        until_date: End date for filtering RC GLE records (inclusive)

    Returns:
        List of account-group dicts with aggregated totals
    """
    return frappe.db.sql(  # nosemgrep — parameterised values
        f"""
		SELECT
			rc.account,
			rc.account_currency,
			CASE
				WHEN account.account_type IN ('Receivable', 'Payable')
				THEN COALESCE(rc.party, '')
				ELSE ''
			END AS party,
			CASE
				WHEN account.account_type IN ('Receivable', 'Payable')
				THEN COALESCE(rc.party_type, '')
				ELSE ''
			END AS party_type,
			account.account_type,
			COALESCE(SUM(rc.debit), 0) AS total_debit,
			COALESCE(SUM(rc.credit), 0) AS total_credit,
			COALESCE(SUM(rc.reporting_debit), 0) AS total_reporting_debit,
			COALESCE(SUM(rc.reporting_credit), 0) AS total_reporting_credit
		FROM `tab{DOCTYPE_RC_GLE}` rc
		INNER JOIN `tabAccount` account ON account.name = rc.account
		WHERE rc.company = %s
			AND rc.reporting_doe = 0
            AND COALESCE(rc.is_cancelled, 0) = 0
			AND rc.account_currency != %s
			AND rc.posting_date <= %s
			{excluded_accounts_condition}
		GROUP BY
			rc.account,
			rc.account_currency,
			account.account_type,
			CASE
				WHEN account.account_type IN ('Receivable', 'Payable')
				THEN COALESCE(rc.party, '')
				ELSE ''
			END,
			CASE
				WHEN account.account_type IN ('Receivable', 'Payable')
				THEN COALESCE(rc.party_type, '')
				ELSE ''
			END
		ORDER BY rc.account, party_type, party
		""",  # noqa: S608
        (company, reporting_currency, until_date),
        as_dict=True,
    )


def _create_doe_records_for_groups(  # noqa: PLR0913, PLR0917
    account_groups: list[dict[str, Any]],
    company: str,
    reporting_currency: str,
    exchange_rate: float,
    doe_posting_date: str,
    profit_account: str,
    loss_account: str,
    fiscal_year: str,
    name_counter: dict[str, Any],
    profit_loss_currency_map: dict[str, str | None],
    prior_doe_by_group: dict[tuple[str, str, str | None, str | None], dict[str, float]],
) -> tuple[list[dict[str, Any]], int]:
    """Create DOE pairs for account-level or account-and-party-level groups."""
    records: list[dict[str, Any]] = []
    processed_count = 0

    for account_info in account_groups:
        account = account_info["account"]
        account_currency = account_info["account_currency"]
        party = account_info.get("party") or None
        party_type = account_info.get("party_type") or None
        group_key = (account, account_currency, party_type, party)

        # Earlier DOE parameter rows must affect only the same account/party.
        adjusted_totals = dict(account_info)
        if group_key in prior_doe_by_group:
            prior = prior_doe_by_group[group_key]
            adjusted_totals["total_reporting_debit"] = (
                flt(adjusted_totals["total_reporting_debit"]) + prior["reporting_debit"]
            )
            adjusted_totals["total_reporting_credit"] = (
                flt(adjusted_totals["total_reporting_credit"])
                + prior["reporting_credit"]
            )

        computed_data = _compute_doe_for_account(
            company=company,
            account=account,
            _account_currency=account_currency,
            _reporting_currency=reporting_currency,
            exchange_rate=exchange_rate,
            account_totals=adjusted_totals,
        )

        if (
            computed_data is None
            or abs(computed_data.get("final_amount", 0)) < DOE_ZERO_THRESHOLD
        ):
            continue

        group_records = _create_doe_records(
            account=account,
            account_currency=account_currency,
            party=party,
            party_type=party_type,
            computed_data=computed_data,
            doe_posting_date=doe_posting_date,
            profit_account=profit_account,
            loss_account=loss_account,
            reporting_currency=reporting_currency,
            fiscal_year=fiscal_year,
            company=company,
            name_counter=name_counter,
            profit_loss_currency_map=profit_loss_currency_map,
        )
        records.extend(group_records)
        processed_count += 1

        # Record 0 is always the revalued account leg, not the P&L offset leg.
        prior = prior_doe_by_group.setdefault(
            group_key, {"reporting_debit": 0.0, "reporting_credit": 0.0}
        )
        prior["reporting_debit"] += flt(group_records[0].get("reporting_debit", 0))
        prior["reporting_credit"] += flt(group_records[0].get("reporting_credit", 0))

    return records, processed_count


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


@frappe.whitelist()
def compute_doe(background: bool = True) -> dict[str, Any]:
    """Main entry point for DOE computation.

    Args:
            background: If True, run as background job (default)

    Returns:
            dict: Job ID if background=True, or result dict if background=False
    """
    frappe.only_for("System Manager")
    # Get settings (Single DocType)
    settings = cast("ReportingCurrencySettings", frappe.get_single(DOCTYPE_RC_SETTINGS))

    if not settings.reporting_currency:
        frappe.throw(_("Reporting Currency Settings not configured"))

    validate_doe_exchange_rates(settings.rc_parameters or [])

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


def _compute_doe_background() -> dict[str, Any]:
    """Background job for DOE computation.

    This is the main orchestrator that runs all 5 phases.
    Processes rc_parameters rows sorted by doe_posting_date ascending, computing DOE
    cumulatively from the beginning until each row's doe_posting_date. DOE records
    accumulate across rows.
    """
    frappe.db.commit()  # nosemgrep — background job
    company = None

    try:
        _publish_progress(0, "Starting DOE computation...")

        # ====================================================================
        # PHASE 1: VALIDATION & PARAMETER ESTABLISHMENT
        # ====================================================================
        _publish_progress(10, "Phase 1: Validating configuration...")

        settings = cast(
            "ReportingCurrencySettings", frappe.get_single(DOCTYPE_RC_SETTINGS)
        )

        company = get_reporting_company()

        if not company:
            frappe.throw(_("No RC GLE records found. Please run sync first."))

        if not settings.reporting_currency:
            frappe.throw(_("Reporting Currency is not configured in settings"))

        if not settings.rc_parameters or len(settings.rc_parameters) == 0:
            frappe.throw(_("No RC Parameters defined. Please add at least one row."))

        validate_doe_exchange_rates(settings.rc_parameters)

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

        excluded_accounts_condition = _get_excluded_accounts_condition()

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

        doe_records, total_processed_count = _collect_doe_records(
            sorted_params,
            cast("str", company),
            cast("str", settings.reporting_currency),
            excluded_accounts_condition=excluded_accounts_condition,
            progress=True,
        )

        result = _finish_background_doe(settings, doe_records, total_processed_count)

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
    savepoint_name: str | None = None
    try:
        settings = cast(
            "ReportingCurrencySettings", frappe.get_single(DOCTYPE_RC_SETTINGS)
        )

        company = get_reporting_company()

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

        validate_doe_exchange_rates(settings.rc_parameters)

        # Get excluded accounts
        excluded_accounts_condition = _get_excluded_accounts_condition()

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

        doe_records, total_processed_count = _collect_doe_records(
            sorted_params,
            company,
            settings.reporting_currency,
            excluded_accounts_condition=excluded_accounts_condition,
        )

        # Bulk insert all accumulated DOE records
        if doe_records:
            _bulk_insert_doe_records(doe_records)

        return _doe_result(doe_records, total_processed_count)

    except Exception:
        if savepoint_name:
            frappe.db.rollback(save_point=savepoint_name)
        else:
            frappe.db.rollback()

        frappe.log_error(
            title="DOE Computation Failed (Inline)", message=frappe.get_traceback()
        )

        raise


def _get_excluded_accounts_condition() -> str:
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
        excluded_accounts_condition = f"AND rc.account NOT IN ({excluded_accounts_str})"

    return excluded_accounts_condition


def _finish_background_doe(
    settings: ReportingCurrencySettings,
    doe_records: list[dict[str, Any]],
    total_processed_count: int,
) -> dict[str, Any]:
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

    result = _doe_result(doe_records, total_processed_count)

    frappe.msgprint(result["message"], alert=True, indicator="green")

    return result


def _doe_result(records: list[dict[str, Any]], count: int) -> dict[str, Any]:
    return {
        "success": True,
        "message": f"DOE computation completed. Processed {count} accounts, created {len(records)} records.",
        "accounts_processed": count,
        "records_created": len(records),
    }


def _parameter_posting_date(row: Any) -> date:
    return _required_doe_date(row.doe_posting_date)


def _required_doe_date(value: str | date) -> date:
    posting_date = getdate(value)
    if posting_date is None:
        raise frappe.ValidationError(_("Invalid DOE posting date"))
    return posting_date


@dataclass(kw_only=True)
class _DOEComputation:
    excluded_accounts_condition: str
    name_counters_by_year: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Records are inserted after all rows, so carry each group's earlier DOE in memory.
    prior_doe_by_group: dict[
        tuple[str, str, str | None, str | None], dict[str, float]
    ] = field(default_factory=dict)


def _collect_doe_records(
    sorted_params: list[Any],
    company: str,
    reporting_currency: str,
    *,
    excluded_accounts_condition: str,
    progress: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    context = _DOEComputation(excluded_accounts_condition=excluded_accounts_condition)
    doe_records: list[dict[str, Any]] = []
    total_processed_count = 0
    for row_idx, row in enumerate(sorted_params):
        records, count, message = _process_doe_parameter(
            row,
            company,
            reporting_currency,
            context=context,
        )
        doe_records.extend(records)
        total_processed_count += count
        if progress and message:
            _publish_progress(
                40 + int((row_idx + 1) / len(sorted_params) * 45), message
            )
    return doe_records, total_processed_count


def _process_doe_parameter(
    row: Any,
    company: str,
    reporting_currency: str,
    *,
    context: _DOEComputation,
) -> tuple[list[dict[str, Any]], int, str | None]:
    from erpnext.accounts.utils import get_fiscal_year  # noqa: PLC0415

    exchange_rate = flt(row.exchange_rate)
    doe_posting_date = _parameter_posting_date(row)
    profit_account, loss_account = row.profit_account, row.loss_account
    if not profit_account or not loss_account:
        frappe.log_error(
            title=f"DOE Skipped for row {row.idx}",
            message="Profit/Loss accounts not configured",
        )
        return [], 0, None
    fiscal_year = get_fiscal_year(doe_posting_date, company=company)[0]
    profit_loss_currency_map = _get_profit_loss_currency_map(
        profit_account, loss_account, reporting_currency
    )
    doe_year = doe_posting_date.year
    if doe_year not in context.name_counters_by_year:
        context.name_counters_by_year[doe_year] = _get_starting_doe_number(
            doe_posting_date
        )
    account_groups = _get_accounts_with_totals(
        company=company,
        reporting_currency=reporting_currency,
        excluded_accounts_condition=context.excluded_accounts_condition,
        until_date=doe_posting_date.isoformat(),
    )
    if not account_groups:
        return [], 0, f"Row {row.idx} (until {doe_posting_date}): No accounts"
    records, count = _create_doe_records_for_groups(
        account_groups=account_groups,
        company=company,
        reporting_currency=reporting_currency,
        exchange_rate=exchange_rate,
        doe_posting_date=doe_posting_date.isoformat(),
        profit_account=profit_account,
        loss_account=loss_account,
        fiscal_year=fiscal_year,
        name_counter=context.name_counters_by_year[doe_year],
        profit_loss_currency_map=profit_loss_currency_map,
        prior_doe_by_group=context.prior_doe_by_group,
    )
    return records, count, f"Completed row {row.idx} (until {doe_posting_date})"


# ============================================================================
# PHASE 3: COMPUTE DOE FOR ACCOUNT
# ============================================================================


def _compute_doe_for_account(
    company: str,
    account: str,
    *,
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
    total_debit_default_currency = flt(record.get("total_debit") or 0, 9)
    total_credit_default_currency = flt(record.get("total_credit") or 0, 9)
    difference_default_currency = (
        total_debit_default_currency - total_credit_default_currency
    )

    # Step 2: Reporting currency totals
    reporting_debit_total = flt(record.get("total_reporting_debit") or 0, 9)
    reporting_credit_total = flt(record.get("total_reporting_credit") or 0, 9)
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


def _create_doe_records(  # noqa: PLR0913, PLR0917
    account: str,
    account_currency: str,
    party: str | None,
    party_type: str | None,
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
    """Create 2 balanced RC GLE records for an account or account/party group.

    Logic:
    - If final_amount > 0: Debit account, Credit profit_account
    - If final_amount < 0: Credit account, Debit loss_account

    Args:
            account: Account name
            account_currency: Account's currency
            party: Party for a Receivable/Payable DOE group, when applicable
            party_type: Party DocType for a Receivable/Payable DOE group
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
    doe_year = _required_doe_date(doe_posting_date).year
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
        "is_opening": "No",
        "docstatus": 1,
        "posting_date": doe_posting_date,
        "fiscal_year": fiscal_year,
        "voucher_type": "Exchange Rate Revaluation",
        "voucher_no": voucher_no,
        "reporting_currency": reporting_currency,
        "company": company,
        "party": party,
        "party_type": party_type,
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


def _get_starting_doe_number(posting_date: str | date) -> dict[str, int]:
    """Get the starting number for DOE naming series.

    Returns a dict with 'year' and 'counter' that can be incremented.
    """
    posting_datetime = get_datetime(posting_date)
    if posting_datetime is None:
        raise frappe.ValidationError(_("Invalid DOE posting date"))
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
            except ValueError, IndexError:
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


# ============================================================================
# PHASE 5: BULK INSERT
# ============================================================================


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
