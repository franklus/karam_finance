"""Frappe hooks for Karam Finance."""

from karam_finance.karam_series.constants.constants import (
    KARAM_DOCTYPES as _KARAM_DOCTYPES,
)

type DocEventValue = str | list[str]
type DocEvent = dict[str, DocEventValue]

app_name = "karam_finance"  # noqa: V107 - Frappe app metadata loader.
app_title = "Karam Finance"  # noqa: V107 - Frappe app metadata loader.
app_publisher = "Noospheric"  # noqa: V107 - Frappe app metadata loader.
app_description = "Karam-specific finance, reconciliation, reporting, numbering, and operational controls for ERPNext."  # noqa: V107 - Frappe app metadata loader.
app_email = "repast_pesos42@icloud.com"  # noqa: V107 - Frappe app metadata loader.
app_license = "mit"  # noqa: V107 - Frappe app metadata loader.
app_include_js = [
    "currency_formatter.bundle.js",
    "report_table_ux.bundle.js",
    "/assets/karam_finance/js/karam_general/item_price_on_rate_mismatch_state.js",
    "/assets/karam_finance/js/karam_general/item_price_on_rate_mismatch_prompt.js",
    "/assets/karam_finance/js/karam_general/item_price_on_rate_mismatch.js",
]

# DocType class overrides
# Preserve the installed Bank Clearance subclass contract; it delegates to super().
# nosemgrep: override-doctype-class
override_doctype_class = {  # noqa: V107 - Frappe controller override loader.
    "Bank Clearance": "karam_finance.overrides.bank_clearance.KaramBankClearance",
}

# AccountsController descendants: guard manual links before their on_trash SQL.
extend_doctype_class = {  # noqa: V107 - Frappe controller extension loader.
    doctype: [
        "karam_finance.reporting_currency.source_deletion.ReportingSourceDeletionMixin"
    ]
    for doctype in (
        "Asset",
        "Asset Capitalization",
        "Asset Repair",
        "Delivery Note",
        "Dunning",
        "Invoice Discounting",
        "Journal Entry",
        "Material Request",
        "Payment Entry",
        "Period Closing Voucher",
        "POS Invoice",
        "Purchase Invoice",
        "Purchase Order",
        "Purchase Receipt",
        "Quotation",
        "Request for Quotation",
        "Sales Invoice",
        "Sales Order",
        "Serial No",
        "Stock Entry",
        "Stock Reconciliation",
        "Subcontracting Inward Order",
        "Subcontracting Order",
        "Subcontracting Receipt",
        "Supplier Quotation",
    )
}

# DocType JS includes (generated from curated list)
doctype_js = dict.fromkeys(
    _KARAM_DOCTYPES, "public/js/karam_series/karam_series_filter.js"
)
doctype_js["Bank Clearance"] = "public/js/overrides/bank_clearance.js"
doctype_js["Stock Settings"] = (
    "public/js/karam_general/stock_settings_item_price_mismatch.js"
)
doctype_js["Letter Reconciliation"] = "public/js/letter_reconciliation/selection.js"
doctype_js["Letter Reconciliation Settings"] = (
    "public/js/letter_reconciliation/rebuild_messages.js"
)
doctype_list_js = {  # noqa: V107 - Frappe list-view asset loader.
    "Reporting Currency GLE": (
        "public/js/reporting_currency/reporting_currency_gle_list.js"
    ),
    "Repost Item Valuation": "public/js/overrides/repost_item_valuation_list.js",
}

# Installation
after_install = "karam_finance.migrate.after_install"

# Migrate hooks
# Widen columns BEFORE schema sync to prevent truncation errors on existing data
before_migrate = "karam_finance.common.db_schema.ensure_currency_columns_capacity"  # noqa: V107 - Frappe migration hook loader.
after_migrate = "karam_finance.migrate.after_migrate"

# Ensure currency columns remain widened whenever DocTypes are updated
after_doctype_update = [  # noqa: V107 - Frappe schema hook loader.
    "karam_finance.common.db_schema.ensure_currency_columns_capacity"
]

# Document Events
doc_events: dict[str, DocEvent] = {}

doc_events["*"] = {
    "on_trash": "karam_finance.reporting_currency.source_deletion.remove_generated_voucher_links",
    "on_cancel": "karam_finance.reporting_currency.source_deletion.allow_generated_voucher_cancellation",
}

doc_events.setdefault("Stock Settings", {}).update(
    {
        "validate": (
            "karam_finance.karam_general.utils.item_price_on_rate_mismatch.sync_stock_settings_rate_mismatch_rows"
        )
    }
)

_karam_series_doctypes = list(_KARAM_DOCTYPES)

for _dt in _karam_series_doctypes:
    doc_events[_dt] = {
        "before_save": [
            "karam_finance.karam_general.utils.date_fields.populate_karam_date_fields",
            "karam_finance.karam_series.utils.hooks.populate_karam_series_fields",
        ],
        "before_insert": [
            "karam_finance.karam_general.utils.date_fields.populate_karam_date_fields",
            "karam_finance.karam_series.utils.hooks.populate_karam_series_fields",
        ],
        "validate": [
            "karam_finance.karam_series.utils.hooks.validate_karam_series_applicability",
        ],
    }

# Letter Reconciliation doc events
doc_events.setdefault("GL Entry", {}).update(
    {
        "on_trash": (
            "karam_finance.reporting_currency.source_deletion.remove_generated_gl_snapshot"
        ),
        "before_insert": (
            "karam_finance.letter_reconciliation.utils.doc_events.gl_entry_before_insert"
        ),
        # Acquire the reporting-ledger lock before Frappe changes the source
        # name. The after_rename hook retains the guard for direct callers.
        "before_rename": (
            "karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.orchestrator.on_gl_entry_before_rename"
        ),
        # Update RC GLE records when GL Entries are renamed by ERPNext's scheduled job
        "after_rename": (
            "karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.on_gl_entry_rename"
        ),
    }
)
doc_events.setdefault("Journal Entry", {}).update(
    {
        # Journal Entry names are generated after before_insert, so keep the
        # derived date fields and inherited Series in place before naming.
        "before_insert": [
            "karam_finance.karam_general.utils.date_fields.populate_karam_date_fields",
            "karam_finance.karam_series.utils.hooks.populate_karam_series_from_source_document",
        ],
        "before_submit": (
            "karam_finance.letter_reconciliation.utils.doc_events.je_before_submit"
        ),
        "on_update_after_submit": (
            "karam_finance.letter_reconciliation.utils.doc_events.journal_entry_on_update_after_submit"
        ),
    }
)

# Top-level hook for ERPNext's rename_temporarily_named_docs() scheduled job.
# This fires on_gle_rename (not the doc_event after_rename) so both paths are covered.
on_gle_rename = [  # noqa: V107 - ERPNext temporary GL-name scheduler hook.
    "karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.orchestrator.on_gle_rename_hook"
]


# Reporting snapshots inherit source GL visibility, including custom dimensions.
permission_query_conditions = {  # noqa: V107 - Frappe permission hook loader.
    "Reporting Currency GLE": "karam_finance.reporting_currency.permissions.permission_query_conditions",
}
has_permission = {  # noqa: V107 - Frappe permission hook loader.
    "Reporting Currency GLE": "karam_finance.reporting_currency.permissions.has_permission",
}
