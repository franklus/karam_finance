"""Committed database evidence for confirmed currency transitions and rollback."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any, override
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from karam_finance.reporting_currency import currency_change
from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync import (
    doe,
    orchestrator,
)
from karam_finance.tests.site_safety import require_disposable_test_site

SETTINGS = "Reporting Currency Settings"
LEDGER = "Reporting Currency GLE"


class TestFinanceReviewCurrencyChange(IntegrationTestCase):
    # Committed fixtures and a second connection are intentional in atomicity tests.
    SHOW_TRANSACTION_COMMIT_WARNINGS = False  # noqa: V107 - Frappe IntegrationTestCase configuration.

    @classmethod
    @override
    def setUpClass(_cls) -> None:
        require_disposable_test_site()
        super().setUpClass()

    @override
    def setUp(self) -> None:
        require_disposable_test_site()
        super().setUp()
        self.token = "currency_review_" + frappe.generate_hash(length=8)
        self.default_currency = self.token + "_BASE"
        self.reporting_currency = self.token + "_REPORT"
        self.new_currency = self.token + "_TARGET"
        self.company = self.token
        frappe.set_user(  # nosemgrep: frappe-setuser — native fixture requires System Manager access.
            "Administrator"
        )
        self.singles = frappe.db.sql(
            "SELECT doctype, field, value FROM `tabSingles` WHERE doctype=%s", SETTINGS
        )
        self.children = {
            str(field.options): frappe.get_all(
                field.options, filters={"parent": SETTINGS}, fields=["*"]
            )
            for field in frappe.get_meta(SETTINGS).get_table_fields()
        }
        self.addCleanup(self._restore_fixture)
        # Initialise this framework connection before a transaction is under test.
        with self.secondary_connection():
            pass
        frappe.local.db = self._primary_connection
        self._create_accounts()
        self._configure_settings()
        self._create_ledger()
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
        self.revision = str(frappe.get_single(SETTINGS).modified)
        self.original_ledger = self._read_ledger()

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def _create_accounts(self) -> None:
        for currency in (
            self.default_currency,
            self.reporting_currency,
            self.new_currency,
        ):
            self._insert(
                "Currency",
                currency,
                currency_name=currency,
                enabled=1,
                fraction_units=100,
                smallest_currency_fraction_value=0.01,
            )
        self._insert(
            "Company",
            self.company,
            company_name=self.company,
            abbr="CR" + self.token[-6:].upper(),
            default_currency=self.default_currency,
        )
        self._insert(
            "Fiscal Year",
            self.token,
            year_start_date="2096-01-01",
            year_end_date="2096-12-31",
            disabled=0,
        )
        for suffix, root in (("Cash", "Asset"), ("Sales", "Income"), ("FX", "Expense")):
            self._insert(
                "Account",
                self.token + suffix,
                company=self.company,
                account_name=suffix,
                account_currency=self.default_currency,
                root_type=root,
                report_type="Balance Sheet" if root == "Asset" else "Profit and Loss",
                account_type="Cash" if root == "Asset" else "",
                is_group=0,
                disabled=0,
            )
        for suffix, currency, rate in (
            ("old", self.reporting_currency, 2),
            ("new", self.new_currency, 3),
        ):
            self._insert(
                "Currency Exchange",
                self.token + suffix,
                from_currency=self.default_currency,
                to_currency=currency,
                date="2096-01-01",
                exchange_rate=rate,
                for_buying=1,
                for_selling=1,
            )

    def _configure_settings(self) -> None:
        settings = frappe.get_single(SETTINGS)
        settings.update(
            {
                "reporting_currency": self.reporting_currency,
                "last_sync_timestamp": None,
                "last_ce_sync_timestamp": None,
            }
        )
        settings.set("rc_parameters", [])
        settings.append(
            "rc_parameters",
            {
                "doe_posting_date": "2096-12-31",
                "exchange_rate": 0.5,
                "profit_account": self.token + "FX",
                "loss_account": self.token + "FX",
            },
        )
        settings.save()

    def _create_ledger(self) -> None:
        for suffix, debit, credit in (("Cash", 100, 0), ("Sales", 0, 100)):
            name = self.token + suffix
            self._insert(
                "GL Entry",
                name,
                company=self.company,
                account=name,
                account_currency=self.default_currency,
                debit=debit,
                credit=credit,
                debit_in_account_currency=debit,
                credit_in_account_currency=credit,
                posting_date="2096-01-15",
                fiscal_year=self.token,
                is_cancelled=0,
                docstatus=1,
                is_opening="No",
                voucher_type="Journal Entry",
                voucher_no=self.token,
            )
            values = frappe.get_doc("GL Entry", name).as_dict()
            values.pop("doctype")
            values.pop("name")
            values.update(
                gl_entry=name,
                reporting_currency=self.reporting_currency,
                reporting_doe=0,
                manual_entry=0,
                reporting_debit=debit * 2,
                reporting_credit=credit * 2,
            )
            self._insert(LEDGER, "RC-" + name, **values)

    def _restore_fixture(self) -> None:
        frappe.local.db = self._primary_connection
        frappe.db.rollback()
        frappe.db.delete(LEDGER, {"company": self.company})
        frappe.db.delete("GL Entry", {"company": self.company})
        frappe.db.delete("Account", {"company": self.company})
        frappe.db.delete("Fiscal Year", {"name": self.token})
        frappe.db.delete(
            "Currency Exchange",
            {"name": ["in", [self.token + "old", self.token + "new"]]},
        )
        frappe.db.delete(
            "Currency",
            {
                "name": [
                    "in",
                    [
                        self.default_currency,
                        self.reporting_currency,
                        self.new_currency,
                    ],
                ]
            },
        )
        frappe.db.delete("Company", self.company)
        frappe.db.delete("Singles", {"doctype": SETTINGS})
        if self.singles:
            frappe.db.bulk_insert(
                "Singles", fields=["doctype", "field", "value"], values=self.singles
            )
        for doctype, rows in self.children.items():
            frappe.db.delete(doctype, {"parent": SETTINGS})
            for row in rows:
                values = dict(row)
                name = values.pop("name")
                self._insert(doctype, name, **values)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
        frappe.clear_cache(doctype=SETTINGS)
        self._assert_settings_baseline()
        require_disposable_test_site()

    @staticmethod
    def _row_key(row: dict[str, Any]) -> str:
        return json.dumps(row, sort_keys=True, default=str)

    def _assert_settings_baseline(self) -> None:
        current_singles = frappe.db.sql(
            "SELECT doctype, field, value FROM `tabSingles` WHERE doctype=%s",
            SETTINGS,
        )
        assert current_singles == self.singles
        for doctype, rows in self.children.items():
            current = frappe.get_all(
                doctype, filters={"parent": SETTINGS}, fields=["*"]
            )
            assert sorted([self._row_key(row) for row in current]) == sorted(
                [self._row_key(row) for row in rows]
            )

    def _read_ledger(self) -> list[Any]:
        return frappe.get_all(
            LEDGER,
            filters={"name": ["like", f"%{self.token}%"]},
            fields=["*"],
            order_by="name",
        )

    def _run_change(self) -> dict[str, Any]:
        return currency_change.run_currency_change_job(
            self.new_currency,
            self.revision,
            progress_event=self.token,
            done_event=self.token + "_done",
            user="Administrator",
            doe_rates={
                row.name: 0.25
                for row in frappe.get_single(SETTINGS).get("rc_parameters")
            },
        )

    def _assert_original_committed_state(self) -> None:
        with self.secondary_connection():
            frappe.db.rollback()
            assert (
                frappe.db.get_single_value(SETTINGS, "reporting_currency", cache=False)
                == self.reporting_currency
            )
            assert self._read_ledger() == self.original_ledger
            assert (
                frappe.get_single(SETTINGS).get("rc_parameters")[0].exchange_rate == 0.5
            )

    def test_success_commits_currency_generated_gl_doe_and_cutoffs_together(
        self,
    ) -> None:
        result = self._run_change()
        with self.secondary_connection():
            frappe.db.rollback()
            settings = frappe.get_single(SETTINGS)
            rows = self._read_ledger()
            assert settings.get("reporting_currency") == self.new_currency
            assert settings.get("last_sync_timestamp") == settings.get(
                "last_ce_sync_timestamp"
            )
            assert settings.get("last_sync_timestamp")
            assert {row.reporting_currency for row in rows} == {self.new_currency}
            assert len([row for row in rows if not row.reporting_doe]) == 2
            assert any(row.reporting_doe for row in rows)
        assert result["status"] == "success"
        assert result["inserted"] == 2

    def test_currency_change_requires_replacement_doe_rates(self) -> None:
        before = frappe.get_single(SETTINGS).as_dict()
        with self.assertRaisesRegex(frappe.ValidationError, "replacement DOE rates"):
            currency_change.run_currency_change_job(
                self.new_currency,
                self.revision,
                progress_event=self.token,
                done_event=self.token + "_done",
                user="Administrator",
            )
        assert frappe.get_single(SETTINGS).as_dict() == before

    def test_currency_change_uses_explicit_new_currency_doe_rate(self) -> None:
        parameter = frappe.get_single(SETTINGS).get("rc_parameters")[0]
        before = frappe.get_single(SETTINGS).as_dict()
        with patch.object(frappe, "enqueue") as queue:
            currency_change.enqueue_currency_change(
                self.new_currency,
                self.revision,
                confirmed=True,
                doe_rates=json.dumps({parameter.name: "0.25"}),
            )
        assert frappe.get_single(SETTINGS).as_dict() == before
        payload = queue.call_args.kwargs
        result = currency_change.run_currency_change_job(
            **{
                key: payload[key]
                for key in (
                    "requested_currency",
                    "expected_modified",
                    "doe_rates",
                    "progress_event",
                    "done_event",
                    "user",
                )
            }
        )
        assert result["status"] == "success"
        settings = frappe.get_single(SETTINGS)
        assert settings.get("reporting_currency") == self.new_currency
        assert settings.get("rc_parameters")[0].exchange_rate == 0.25
        names = frappe.get_list(
            LEDGER,
            filters={
                "name": ["like", f"%{self.token}%"],
                "account": self.token + "Cash",
            },
            pluck="name",
        )
        rows = [frappe.get_doc(LEDGER, name) for name in names]
        assert sorted(
            (
                row.get("reporting_doe"),
                row.get("reporting_debit"),
                row.get("reporting_credit"),
            )
            for row in rows
        ) == [(0, 300, 0), (1, 100, 0)]

    def test_currency_change_rejects_non_numeric_doe_rates_before_queueing(
        self,
    ) -> None:
        parameter = frappe.get_single(SETTINGS).get("rc_parameters")[0]
        before = frappe.get_single(SETTINGS).as_dict()
        with patch.object(frappe, "enqueue") as queue:
            for value in (
                True,
                list[str](),
                dict[str, float](),
                "not a rate",
                0,
                -1,
                "NaN",
                "Infinity",
            ):
                with (
                    self.subTest(rate=value),
                    self.assertRaises(frappe.ValidationError),
                ):
                    currency_change.enqueue_currency_change(
                        self.new_currency,
                        self.revision,
                        confirmed=True,
                        doe_rates={parameter.name: value},
                    )
            queue.assert_not_called()
        assert frappe.get_single(SETTINGS).as_dict() == before

    def test_currency_change_rejects_incomplete_or_unknown_doe_parameters(self) -> None:
        parameter = frappe.get_single(SETTINGS).get("rc_parameters")[0]
        before = frappe.get_single(SETTINGS).as_dict()
        with patch.object(frappe, "enqueue") as queue:
            for rates in (
                dict[str, float](),
                {"unknown": 0.25},
                {parameter.name: 0.25, "extra": 0.2},
            ):
                with (
                    self.subTest(rates=rates),
                    self.assertRaisesRegex(frappe.ValidationError, "every current"),
                ):
                    currency_change.enqueue_currency_change(
                        self.new_currency,
                        self.revision,
                        confirmed=True,
                        doe_rates=rates,
                    )
            queue.assert_not_called()
        assert frappe.get_single(SETTINGS).as_dict() == before

    def test_gl_insertion_failure_preserves_previous_committed_state(self) -> None:
        self._fail_insertion(reporting_doe=0)

    def test_doe_insertion_failure_preserves_previous_committed_state(self) -> None:
        self._fail_insertion(reporting_doe=1)

    def _fail_insertion(self, *, reporting_doe: int) -> None:
        original_insert = frappe.local.db.bulk_insert

        def insert(
            doctype: str,
            fields: Sequence[str],
            values: Iterable[Sequence[Any]],
            **kwargs: Any,
        ) -> None:
            materialised = list(values)
            original_insert(doctype, fields, materialised, **kwargs)
            record = dict(zip(fields, materialised[0], strict=True))
            if doctype == LEDGER and record.get("reporting_doe", 0) == reporting_doe:
                self._assert_original_committed_state()
                message = "Injected database insertion failure"
                raise RuntimeError(message)

        with (
            patch.object(frappe.local.db, "bulk_insert", side_effect=insert),
            self.assertRaisesRegex(RuntimeError, "Injected database insertion failure"),
        ):
            self._run_change()
        self._assert_original_committed_state()

    def test_stale_revision_and_unconfirmed_requests_do_not_queue(self) -> None:
        with patch.object(frappe, "enqueue") as enqueue:
            with self.assertRaisesRegex(frappe.ValidationError, "Confirm"):
                currency_change.enqueue_currency_change(
                    self.new_currency, self.revision
                )
            with self.assertRaisesRegex(frappe.ValidationError, "Reload"):
                currency_change.enqueue_currency_change(
                    self.new_currency, "2000-01-01", confirmed=True
                )
            enqueue.assert_not_called()
        self._assert_original_committed_state()

    def test_manual_entry_with_old_currency_blocks_transition(self) -> None:
        self._insert(
            LEDGER,
            self.token + "manual",
            company=self.company,
            account=self.token + "Cash",
            manual_entry=1,
            reporting_currency=self.reporting_currency,
        )
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
        with self.assertRaisesRegex(frappe.ValidationError, "Manual reporting entries"):
            self._run_change()
        assert (
            frappe.db.get_single_value(SETTINGS, "reporting_currency", cache=False)
            == self.reporting_currency
        )
        assert frappe.db.exists(LEDGER, self.token + "manual")

    def _sync(self) -> None:
        orchestrator.run_reporting_currency_sync_job(
            self.token, self.token + "_done", "Administrator"
        )

    def test_normal_sync_worker_reads_rate_changed_after_preflight(self) -> None:
        """A queued sync must use the rate committed before worker start."""
        with patch.object(frappe, "enqueue", return_value=None) as enqueue:
            queued = orchestrator.enqueue_reporting_currency_sync()

        payload = enqueue.call_args.kwargs
        assert payload["cached_currency_coverage"]
        assert queued["progress_event"] == payload["progress_event"]

        # This commit represents a rate correction made while the job was
        # waiting.  The worker must acquire it in its own transaction rather
        # than converting with the foreground pre-flight snapshot.
        frappe.db.set_value("Currency Exchange", self.token + "old", "exchange_rate", 4)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — queued-input race evidence.

        orchestrator.run_reporting_currency_sync_job(
            progress_event=payload["progress_event"],
            done_event=payload["done_event"],
            user=payload["user"],
            cached_currency_coverage=payload["cached_currency_coverage"],
        )

        row = frappe.get_doc(LEDGER, "RC-" + self.token + "Cash")
        assert row.get("source_exchange_rate") == 4
        assert row.get("reporting_debit") == 400

    def _generated_values(self) -> list[Any]:
        return frappe.get_all(
            LEDGER,
            filters={
                "name": ["like", f"%{self.token}%"],
                "manual_entry": 0,
            },
            fields=[
                "gl_entry",
                "account",
                "posting_date",
                "reporting_currency",
                "reporting_doe",
                "reporting_debit",
                "reporting_credit",
                "debit",
                "credit",
                "party_type",
                "party",
            ],
            order_by="account, reporting_doe, gl_entry",
        )

    def test_incremental_physical_delete_and_repost_matches_full_rebuild(self) -> None:
        self._sync()
        self._insert(
            LEDGER,
            self.token + "manual",
            company=self.company,
            account=self.token + "Cash",
            manual_entry=1,
            reporting_currency=self.reporting_currency,
            posting_date="2096-01-15",
            reporting_debit=7,
        )
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
        manual = frappe.get_doc(LEDGER, self.token + "manual").as_dict()
        replacement = frappe.get_doc("GL Entry", self.token + "Cash").as_dict()
        replacement.pop("doctype")
        replacement.pop("name")
        replacement.pop("modified")
        replacement.update(debit=125, debit_in_account_currency=125)
        frappe.db.delete("GL Entry", {"name": self.token + "Cash"})
        self._insert("GL Entry", self.token + "reposted", **replacement)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
        self._sync()
        incremental = self._generated_values()
        assert not frappe.db.exists(LEDGER, {"gl_entry": self.token + "Cash"})
        assert frappe.db.exists(LEDGER, {"gl_entry": self.token + "reposted"})
        frappe.db.set_single_value(
            SETTINGS, {"last_sync_timestamp": None, "last_ce_sync_timestamp": None}
        )
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
        self._sync()
        assert self._generated_values() == incremental
        assert frappe.get_doc(LEDGER, self.token + "manual").as_dict() == manual

    def test_failed_incremental_insertion_restores_cleanup_and_replacements(
        self,
    ) -> None:
        self._sync()
        self.original_ledger = self._read_ledger()
        frappe.db.delete("GL Entry", {"name": self.token + "Cash"})
        frappe.db.set_value(
            "GL Entry",
            self.token + "Sales",
            {"credit": 150, "credit_in_account_currency": 150},
        )
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
        insert = frappe.local.db.bulk_insert

        def fail_after_insert(*args: Any, **kwargs: Any) -> None:
            insert(*args, **kwargs)
            message = "Injected sync insertion failure"
            raise RuntimeError(message)

        with (
            patch.object(frappe.local.db, "bulk_insert", side_effect=fail_after_insert),
            self.assertRaisesRegex(RuntimeError, "Injected sync insertion failure"),
        ):
            self._sync()
        self._assert_original_committed_state()

    def test_standalone_doe_keeps_both_source_cutoffs(self) -> None:
        self._sync()
        before = frappe.get_single(SETTINGS)
        cutoffs = (
            before.get("last_sync_timestamp"),
            before.get("last_ce_sync_timestamp"),
        )
        doe._compute_doe_background()
        after = frappe.get_single(SETTINGS)
        assert (
            after.get("last_sync_timestamp"),
            after.get("last_ce_sync_timestamp"),
        ) == cutoffs

    def test_incremental_cancellation_preserves_a_manual_source_link(self) -> None:
        self._sync()
        frappe.db.delete(LEDGER, {"gl_entry": self.token + "Cash"})
        self._insert(
            LEDGER,
            self.token + "manual",
            company=self.company,
            account=self.token + "Cash",
            manual_entry=1,
            reporting_currency=self.reporting_currency,
            gl_entry=self.token + "Cash",
            posting_date="2096-01-15",
            reporting_debit=7,
        )
        frappe.db.set_value("GL Entry", self.token + "Cash", "is_cancelled", 1)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
        manual = frappe.get_doc(LEDGER, self.token + "manual").as_dict()
        self._sync()
        assert frappe.get_doc(LEDGER, self.token + "manual").as_dict() == manual

    def test_cancelled_source_is_removed_by_incremental_and_full_sync(self) -> None:
        self._sync()
        cancelled = self.token + "Cash"
        assert frappe.db.exists(LEDGER, {"gl_entry": cancelled})
        frappe.db.set_value("GL Entry", cancelled, "is_cancelled", 1)
        # Keep an active update so the incremental path does not fall back to full.
        frappe.db.set_value("GL Entry", self.token + "Sales", "remarks", "Updated")
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — committed sync fixture.
        self._sync()
        assert not frappe.db.exists(LEDGER, {"gl_entry": cancelled})
        assert frappe.db.exists(LEDGER, {"gl_entry": self.token + "Sales"})
        incremental = self._generated_values()
        frappe.db.set_single_value(
            SETTINGS, {"last_sync_timestamp": None, "last_ce_sync_timestamp": None}
        )
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — committed sync fixture.
        self._sync()
        assert not frappe.db.exists(LEDGER, {"gl_entry": cancelled})
        assert self._generated_values() == incremental

    def test_changed_offset_account_fails_before_existing_doe_is_deleted(self) -> None:
        self._sync()
        existing = self._generated_values()
        frappe.db.set_value("Account", self.token + "FX", "disabled", 1)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
        with self.assertRaisesRegex(frappe.ValidationError, "DOE offset account"):
            doe.compute_doe_inline()
        assert self._generated_values() == existing

    def test_missing_manual_currency_blocks_worker(self) -> None:
        self._insert(
            LEDGER,
            self.token + "manual",
            company=self.company,
            account=self.token + "Cash",
            manual_entry=1,
            reporting_currency=None,
        )
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
        with self.assertRaisesRegex(frappe.ValidationError, "Manual reporting entries"):
            self._run_change()
        assert (
            frappe.db.get_value(LEDGER, self.token + "manual", "reporting_currency")
            is None
        )

    def test_duplicate_confirmed_requests_share_a_job_and_revalidate_revision(
        self,
    ) -> None:
        rates = {
            row.name: 0.25 for row in frappe.get_single(SETTINGS).get("rc_parameters")
        }
        with patch.object(frappe, "enqueue") as enqueue:
            first = currency_change.enqueue_currency_change(
                self.new_currency, self.revision, confirmed=True, doe_rates=rates
            )
            second = currency_change.enqueue_currency_change(
                self.new_currency, self.revision, confirmed=True, doe_rates=rates
            )
        assert first == second
        assert enqueue.call_args.kwargs["deduplicate"] is True
        assert enqueue.call_args.kwargs["enqueue_after_commit"] is True
        assert enqueue.call_args.kwargs["doe_rates"] == rates
        frappe.db.rollback()
        frappe.db.set_single_value(SETTINGS, "modified", "2099-01-01 00:00:00")
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
        with self.assertRaisesRegex(frappe.ValidationError, "Reload"):
            self._run_change()
        self._assert_original_committed_state()
