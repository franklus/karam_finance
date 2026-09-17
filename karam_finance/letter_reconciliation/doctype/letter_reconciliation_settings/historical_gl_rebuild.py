"""Historical GL rebuild helpers for Letter Reconciliation Settings."""

from __future__ import annotations

import re
from contextlib import suppress
from typing import TYPE_CHECKING, NotRequired, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    from erpnext.accounts.doctype.journal_entry.journal_entry import (  # noqa: V104 - quoted cast type.
        JournalEntry,
    )

    from karam_finance.letter_reconciliation.utils.doc_events import _JournalEntryDoc

    from .letter_reconciliation_settings import (  # noqa: V104 - quoted cast type.
        LetterReconciliationSettings,
    )

import frappe
from erpnext.accounts import party as erpnext_party
from erpnext.accounts.doctype.gl_entry import gl_entry as erpnext_gl_entry
from frappe import _
from frappe.utils import getdate

from karam_finance.letter_reconciliation.utils.doc_events import (
    sync_journal_entry_gl_letters,
)

from .rebuild_permissions import check_rebuild_journals, check_rebuild_voucher

_PREVIEW_SAMPLE_LIMIT = 10
_GROUP_SAMPLE_LIMIT = 5
_DATE_FILTER_PLACEHOLDER = "/*date_filter_clause*/"
_DATE_RANGE_FILTER_SQL = (
    "AND je.posting_date BETWEEN %(from_posting_date)s AND %(to_posting_date)s"
)
_HTML_BREAK_PATTERN = re.compile(r"<br\s*/?>", re.IGNORECASE)
_BALANCE_ACCOUNT_PATTERN = re.compile(
    r"Balance for Account\s+(.+?)\s+must always be\s+(Debit|Credit)"
)
_ACCOUNT_LINE_PATTERN = re.compile(r"^\d[\dA-Za-z %.,()'/+-]*(?:\s-\s.+)?$")
_VALIDATION_ERROR_LINE_PATTERN = re.compile(
    r"frappe\.exceptions\.[^:]+:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


class RebuildFilters(TypedDict):
    """Subset filters selected in Letter Reconciliation Settings."""

    company: str
    whole_history: bool
    from_posting_date: str | None
    to_posting_date: str | None


class VoucherSnapshot(TypedDict):
    """Summarised Journal Entry voucher state for historical rebuild."""

    voucher_no: str
    posting_date: str
    missing_detail_rows: int  # noqa: V107 - serialised payload key, consumed through dictionary lookups.
    active_gl_rows: int  # noqa: V107 - serialised payload key, consumed through dictionary lookups.


class ClassifiedVoucher(TypedDict):
    """Voucher classification payload returned to the UI/job layer."""

    voucher_no: str
    posting_date: str
    reason: str
    accounts: NotRequired[list[str]]


class PreviewBucket(TypedDict):
    """Grouped preview bucket for one classification outcome."""

    count: int
    summary_reason: str
    samples: list[str]
    groups: list[ReasonSummaryGroup]
    items: list[ClassifiedVoucher]


class ReasonSummaryGroup(TypedDict):
    """Grouped summary for repeated rebuild reasons."""

    reason: str
    voucher_count: int  # noqa: V107 - serialised payload key, consumed through dictionary lookups.
    unique_account_count: int  # noqa: V107 - serialised payload key, consumed through dictionary lookups.
    account_samples: list[str]  # noqa: V107 - serialised payload key, consumed through dictionary lookups.
    voucher_samples: list[str]
    accounts: NotRequired[list[str]]
    vouchers: NotRequired[list[str]]


class _ReasonGroupAccumulator(TypedDict):
    """Mutable accumulator used while grouping repeated reasons."""

    reason: str
    voucher_count: int  # noqa: V107 - serialised payload key, consumed through dictionary lookups.
    accounts: set[str]
    voucher_samples: list[str]


class RebuildPreview(TypedDict):
    """Full server-side preview payload for one subset selection."""

    filters: RebuildFilters
    total_submitted_vouchers: int  # noqa: V107 - serialised payload key, consumed through dictionary lookups.
    eligible: PreviewBucket
    blocked: PreviewBucket
    already_correct: PreviewBucket


def get_validated_rebuild_filters() -> RebuildFilters:
    """Return validated subset filters from Letter Reconciliation Settings."""
    settings = cast(
        "LetterReconciliationSettings",
        frappe.get_single("Letter Reconciliation Settings"),
    )
    company = (settings.rebuild_company or "").strip()
    whole_history = bool(settings.rebuild_whole_history)
    from_posting_date = settings.rebuild_from_posting_date
    to_posting_date = settings.rebuild_to_posting_date

    if not company:
        frappe.throw(
            _("Select a Company before previewing or running the historical rebuild.")
        )
    from_date, to_date = _validated_date_range(
        from_posting_date, to_posting_date, whole_history=whole_history
    )
    return {
        "company": company,
        "whole_history": whole_history,
        "from_posting_date": from_date,
        "to_posting_date": to_date,
    }


def _validated_date_range(
    start: str | date | None, end: str | date | None, *, whole_history: bool
) -> tuple[str | None, str | None]:
    if whole_history:
        return None, None
    if not start or not end:
        frappe.throw(
            _("Set both From Posting Date and To Posting Date before continuing.")
        )
        return None, None
    from_date, to_date = _required_date(start), _required_date(end)
    if from_date > to_date:
        frappe.throw(_("From Posting Date cannot be after To Posting Date."))
    return str(from_date), str(to_date)


def _required_date(value: str | date) -> date:
    parsed = getdate(value)
    if parsed is None:
        frappe.throw(_("Invalid posting date: {0}").format(value))
        raise AssertionError
    return parsed


def build_rebuild_preview(filters: RebuildFilters) -> RebuildPreview:
    """Classify the selected historical subset for preview and execution."""
    voucher_rows = _get_subset_voucher_rows(filters)
    check_rebuild_journals([row["voucher_no"] for row in voucher_rows])
    voucher_account_map = _get_subset_voucher_account_map(filters)
    latest_closed_period = _get_latest_closed_period_end(filters["company"])
    repost_allowed = _is_journal_entry_repost_allowed()

    eligible: list[ClassifiedVoucher] = []
    blocked: list[ClassifiedVoucher] = []
    already_correct: list[ClassifiedVoucher] = []

    for row in voucher_rows:
        voucher_no = row["voucher_no"]
        posting_date = row["posting_date"]
        accounts = voucher_account_map.get(voucher_no, [])

        if row["missing_detail_rows"] <= 0:
            already_correct.append(
                {
                    "voucher_no": voucher_no,
                    "posting_date": posting_date,
                    "reason": "",
                    "accounts": accounts,
                }
            )
            continue

        block_reason = _get_block_reason(
            posting_date=posting_date,
            latest_closed_period=latest_closed_period,
            repost_allowed=repost_allowed,
        )
        if block_reason:
            blocked.append(
                {
                    "voucher_no": voucher_no,
                    "posting_date": posting_date,
                    "reason": block_reason,
                    "accounts": accounts,
                }
            )
            continue

        eligible.append(
            {
                "voucher_no": voucher_no,
                "posting_date": posting_date,
                "reason": _("Ready to repost"),
                "accounts": accounts,
            }
        )

    return {
        "filters": filters,
        "total_submitted_vouchers": len(voucher_rows),
        "eligible": _build_bucket(eligible),
        "blocked": _build_bucket(blocked),
        "already_correct": _build_bucket(already_correct),
    }


def build_reason_summary_groups(
    items: list[ClassifiedVoucher],
    *,
    group_limit: int | None = _PREVIEW_SAMPLE_LIMIT,
    sample_limit: int | None = _GROUP_SAMPLE_LIMIT,
    include_full_lists: bool = False,
) -> list[ReasonSummaryGroup]:
    """Build grouped reason summaries with optional full account/voucher lists."""
    return _build_reason_groups(
        items,
        group_limit=group_limit,
        sample_limit=sample_limit,
        include_full_lists=include_full_lists,
    )


def validate_rebuild_ledger_mode() -> None:
    """Reject reposting that would move historical reversals into today's period."""
    if frappe.db.get_single_value(
        "Accounts Settings", "enable_immutable_ledger", cache=False
    ):
        frappe.throw(
            _(
                "Historical GL rebuild is not supported while Immutable Ledger is enabled. "
                "Rebuilding would change earlier-period balances."
            ),
            title=_("Historical GL Rebuild Blocked"),
        )


def rebuild_single_voucher(
    voucher_no: str,
    *,
    reference_detail_backfilled: bool = False,
) -> None:
    """Rebuild one historical Journal Entry through the official repost path."""
    validate_rebuild_ledger_mode()
    check_rebuild_voucher(voucher_no)
    if not reference_detail_backfilled:
        backfill_reference_detail_no(voucher_no)

    try:
        # nosemgrep: frappe-get-doc-without-check  # noqa: ERA001
        doc = cast("JournalEntry", frappe.get_doc("Journal Entry", voucher_no))
    except frappe.DoesNotExistError:
        frappe.throw(_("Journal Entry {0} no longer exists.").format(voucher_no))
        return
    if doc.docstatus != 1:
        frappe.throw(_("Journal Entry {0} is no longer submitted.").format(voucher_no))
    doc.validate_for_repost()

    flags = cast("frappe._dict[str, object]", frappe.flags)
    previous_flag = getattr(
        flags,
        "through_repost_accounting_ledger",
        None,
    )
    flags.through_repost_accounting_ledger = True

    # Bypass validate_account_party_type during repost: historical JEs may
    # have party set on non-Receivable/Payable/Equity accounts (TVA, CNSS,
    # bank loans). This was valid when originally submitted; the repost
    # should not reject it.
    #
    # Thread-safety caveat: module-level replacement is not thread-safe.
    # Acceptable here — rebuild jobs run in dedicated background workers.
    _orig_validate, _orig_gl_entry_validate, _orig_validate_balance_type = (
        erpnext_party.validate_account_party_type,
        erpnext_gl_entry.validate_account_party_type,
        erpnext_gl_entry.validate_balance_type,
    )

    def _noop_validate(self: object) -> None:  # noqa: ARG001
        return

    def _noop_validate_balance_type(
        account: object,  # noqa: ARG001
        adv_adj: bool = False,  # noqa: ARG001, V107 - ERPNext validation callback keyword contract.
    ) -> None:
        return

    erpnext_party.validate_account_party_type = _noop_validate
    erpnext_gl_entry.validate_account_party_type = _noop_validate
    erpnext_gl_entry.validate_balance_type = _noop_validate_balance_type

    try:
        doc.make_gl_entries(1)
        doc.make_gl_entries()
        sync_journal_entry_gl_letters(
            cast("_JournalEntryDoc", doc), ignore_setting=True
        )
    finally:
        erpnext_party.validate_account_party_type = _orig_validate
        erpnext_gl_entry.validate_account_party_type = _orig_gl_entry_validate
        erpnext_gl_entry.validate_balance_type = _orig_validate_balance_type
        if previous_flag is None:
            with suppress(AttributeError):
                del flags.through_repost_accounting_ledger
        else:
            flags.through_repost_accounting_ledger = previous_flag


def _get_subset_voucher_rows(filters: RebuildFilters) -> list[VoucherSnapshot]:
    """Return submitted Journal Entries in the selected company/date subset."""
    query_template = """
        SELECT
            je.name AS voucher_no,
            je.posting_date,
            SUM(
                CASE
                    WHEN IFNULL(gle.voucher_detail_no, '') = '' THEN 1
                    ELSE 0
                END
            ) AS missing_detail_rows,
            COUNT(*) AS active_gl_rows
        FROM `tabJournal Entry` je
        INNER JOIN `tabGL Entry` gle
            ON gle.voucher_type = 'Journal Entry'
            AND gle.voucher_no = je.name
            AND gle.is_cancelled = 0
        WHERE
            je.docstatus = 1
            AND je.company = %(company)s
            /*date_filter_clause*/
        GROUP BY je.name, je.posting_date
        ORDER BY je.posting_date ASC, je.name ASC
    """
    rows = _run_subset_query(filters, query_template=query_template)
    return [
        {
            "voucher_no": cast("str", row["voucher_no"]),
            "posting_date": str(row["posting_date"]),
            "missing_detail_rows": cast("int", row["missing_detail_rows"]),
            "active_gl_rows": cast("int", row["active_gl_rows"]),
        }
        for row in rows
    ]


def _get_subset_voucher_account_map(filters: RebuildFilters) -> dict[str, list[str]]:
    """Return account labels grouped by voucher for the selected subset."""
    query_template = """
        SELECT
            je.name AS voucher_no,
            jea.account
        FROM `tabJournal Entry` je
        INNER JOIN `tabJournal Entry Account` jea
            ON jea.parent = je.name
            AND jea.parenttype = 'Journal Entry'
            AND jea.docstatus < 2
        WHERE
            je.docstatus = 1
            AND je.company = %(company)s
            /*date_filter_clause*/
        ORDER BY je.posting_date ASC, je.name ASC, jea.idx ASC
    """
    rows = _run_subset_query(filters, query_template=query_template)
    account_map: dict[str, list[str]] = {}
    for row in rows:
        voucher_no = cast("str", row["voucher_no"])
        account = cast("str", row["account"])
        if not account:
            continue
        account_map.setdefault(voucher_no, []).append(account)

    return {k: _unique_ordered(v) for k, v in account_map.items()}


def _get_latest_closed_period_end(company: str) -> str | None:
    """Return the latest closed Period Closing Voucher end date for a company."""
    latest_pcv = frappe.db.get_all(
        "Period Closing Voucher",
        filters={"company": company, "docstatus": 1},
        pluck="period_end_date",
        order_by="period_end_date desc",
        limit=1,
    )
    return str(latest_pcv[0]) if latest_pcv else None


def _is_journal_entry_repost_allowed() -> bool:
    """Return whether Journal Entry is enabled in Repost Accounting Ledger."""
    return bool(
        frappe.db.exists(
            "Repost Allowed Types",
            {"document_type": "Journal Entry", "allowed": True},
        )
    )


def _get_block_reason(
    *,
    posting_date: str,
    latest_closed_period: str | None,
    repost_allowed: bool,
) -> str | None:
    """Return the reason a historical voucher cannot be rebuilt."""
    if not repost_allowed:
        return _("Journal Entry is not enabled in Repost Accounting Ledger Settings.")

    if latest_closed_period and _required_date(posting_date) <= _required_date(
        latest_closed_period
    ):
        return _("Voucher falls within a closed fiscal year.")

    return None


def _run_subset_query(
    filters: RebuildFilters,
    *,
    query_template: str,
) -> list[dict[str, object]]:
    """Run one scoped static query for whole history or date-range subsets."""
    date_filter_sql = "" if filters["whole_history"] else _DATE_RANGE_FILTER_SQL
    query = query_template.replace(_DATE_FILTER_PLACEHOLDER, date_filter_sql)
    return frappe.db.sql(query, filters, as_dict=True)


def _build_bucket(items: list[ClassifiedVoucher]) -> PreviewBucket:
    """Return a UI-friendly bucket with count, samples, and raw items."""
    reasons = {item["reason"] for item in items if item["reason"]}
    summary_reason = reasons.pop() if len(reasons) == 1 else ""
    samples = [item["voucher_no"] for item in items[:_PREVIEW_SAMPLE_LIMIT]]
    return {
        "count": len(items),
        "summary_reason": summary_reason,
        "samples": samples,
        "groups": _build_reason_groups(items),
        "items": items,
    }


def _build_reason_groups(
    items: list[ClassifiedVoucher],
    *,
    group_limit: int | None = _PREVIEW_SAMPLE_LIMIT,
    sample_limit: int | None = _GROUP_SAMPLE_LIMIT,
    include_full_lists: bool = False,
) -> list[ReasonSummaryGroup]:
    """Collapse repeated reason text into grouped voucher/account summaries."""
    grouped: dict[str, _ReasonGroupAccumulator] = {}
    for item in items:
        if item["reason"]:
            _accumulate_reason(grouped, item, sample_limit=sample_limit)
    groups = [
        _reason_group(
            bucket, sample_limit=sample_limit, include_full_lists=include_full_lists
        )
        for bucket in grouped.values()
    ]
    groups.sort(key=_reason_group_sort_key)
    return groups if group_limit is None else groups[:group_limit]


def _accumulate_reason(
    grouped: dict[str, _ReasonGroupAccumulator],
    item: ClassifiedVoucher,
    *,
    sample_limit: int | None,
) -> None:
    reason = item["reason"]
    reason_summary = _summarise_reason(reason)
    accounts = _unique_ordered(
        item.get("accounts", []) + extract_accounts_from_reason(reason)
    )
    bucket = grouped.setdefault(
        reason_summary,
        {
            "reason": reason_summary,
            "voucher_count": 0,
            "accounts": set(),
            "voucher_samples": [],
        },
    )
    bucket["voucher_count"] += 1
    voucher_samples = bucket["voucher_samples"]
    if sample_limit is None or len(voucher_samples) < sample_limit:
        voucher_samples.append(item["voucher_no"])
    bucket["accounts"].update(accounts)


def _reason_group(
    bucket: _ReasonGroupAccumulator,
    *,
    sample_limit: int | None,
    include_full_lists: bool,
) -> ReasonSummaryGroup:
    accounts = sorted(bucket["accounts"])
    voucher_samples = list(bucket["voucher_samples"])
    group: ReasonSummaryGroup = {
        "reason": bucket["reason"],
        "voucher_count": bucket["voucher_count"],
        "unique_account_count": len(accounts),
        "account_samples": accounts
        if sample_limit is None
        else accounts[:sample_limit],
        "voucher_samples": voucher_samples
        if sample_limit is None
        else voucher_samples[:sample_limit],
    }
    if include_full_lists:
        group["accounts"] = accounts
        group["vouchers"] = sorted(bucket["voucher_samples"])
    return group


def _reason_group_sort_key(group: ReasonSummaryGroup) -> tuple[int, str]:
    return -group["voucher_count"], group["reason"]


def _summarise_reason(reason: str) -> str:
    """Strip repeated account details from a rebuild reason for grouping."""
    normalised = normalise_reason_text(reason)
    if not normalised:
        return ""

    if "Party is set on a non-Receivable/Payable/Equity account" in normalised:
        return _("Party is set on a non-Receivable/Payable/Equity account")

    if (
        "Party Type and Party can only be set for Receivable / Payable account"
        in normalised
    ):
        return _(
            "Party Type and Party can only be set for Receivable / Payable account"
        )

    balance_match = _BALANCE_ACCOUNT_PATTERN.search(normalised)
    if balance_match:
        return _("Account balance must always be {0}").format(balance_match.group(2))

    lines = [line.strip() for line in normalised.splitlines() if line.strip()]
    for line in lines:
        if _ACCOUNT_LINE_PATTERN.match(line):
            continue
        return line

    return lines[0] if lines else normalised


def extract_accounts_from_reason(reason: str) -> list[str]:
    """Extract account labels embedded in rebuild validation messages."""
    normalised = _HTML_BREAK_PATTERN.sub("\n", reason or "")
    normalised = re.sub(
        r"frappe\.exceptions\.[^:]+:\s*",
        "",
        normalised,
        flags=re.IGNORECASE,
    )
    accounts: list[str] = []

    colon_match = re.search(
        r"account:\s*(.+)$",
        normalised,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if colon_match:
        accounts.append(colon_match.group(1).strip())

    balance_match = _BALANCE_ACCOUNT_PATTERN.search(normalised)
    if balance_match:
        accounts.append(balance_match.group(1).strip())

    for line in normalised.splitlines():
        candidate = line.strip()
        if _ACCOUNT_LINE_PATTERN.match(candidate):
            accounts.append(candidate)

    return _unique_ordered(accounts)


def normalise_reason_text(reason: str) -> str:
    """Normalise HTML/newline heavy validation messages for display grouping."""
    text = _HTML_BREAK_PATTERN.sub("\n", reason or "")
    validation_lines = _VALIDATION_ERROR_LINE_PATTERN.findall(text)
    if validation_lines:
        text = validation_lines[-1]
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _unique_ordered(items: Iterable[str]) -> list[str]:
    """Return items in insertion order with duplicates and blanks removed."""
    return list(dict.fromkeys(item for item in items if item))


def backfill_reference_detail_no(voucher_no: str) -> None:
    """Populate missing reference detail numbers from the child row names."""
    frappe.db.sql(
        """
        UPDATE `tabJournal Entry Account`
        SET reference_detail_no = name
        WHERE
            parent = %(voucher_no)s
            AND IFNULL(reference_detail_no, '') = ''
        """,
        {"voucher_no": voucher_no},
    )


def backfill_reference_detail_no_bulk(voucher_nos: list[str]) -> None:
    """Populate missing reference detail numbers for a voucher batch."""
    if not voucher_nos:
        return

    jea = frappe.qb.DocType("Journal Entry Account")
    (
        frappe.qb.update(jea)
        .set(jea.reference_detail_no, jea.name)
        .where(jea.parent.isin(voucher_nos))
        .where(jea.reference_detail_no.isnull() | (jea.reference_detail_no == ""))
    ).run()
