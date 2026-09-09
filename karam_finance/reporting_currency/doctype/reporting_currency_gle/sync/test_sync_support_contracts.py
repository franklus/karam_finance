"""Pure contract tests for sync support functions and DOE boundaries."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe
from frappe.query_builder.builder import MariaDB, Table
from pypika.queries import QueryBuilder

from . import conversion, data_fetch, doe, orchestrator, phases, utils, validation


def _raise_validation(message: str, *_args: object, **_kwargs: object) -> None:
    raise frappe.ValidationError(message)


def _quote_for_test(value: str) -> str:
    return f"'{value}'"


def _identity_text(message: str) -> str:
    return message


def _translated_text(message: str) -> str:
    return f"translated:{message}"


class TestConversionContracts(TestCase):
    def test_reporting_currency_record_directly_copies_amounts_and_metadata(
        self,
    ) -> None:
        gle = {
            "name": "GLE-USD-1",
            "modified": "2026-02-03 04:05:06",
            "posting_date": "2026-02-01",
            "transaction_date": "2026-01-31",
            "fiscal_year": "2026",
            "due_date": "2026-02-28",
            "account": "Bank - K",
            "account_currency": "USD",
            "against": "Debtors - K",
            "party_type": "Customer",
            "party": "Customer-1",
            "voucher_type": "Sales Invoice",
            "voucher_no": "SINV-1",
            "voucher_subtype": "Return",
            "transaction_currency": "EUR",
            "against_voucher_type": "Payment Entry",
            "against_voucher": "PE-1",
            "voucher_detail_no": "SINV-ITEM-1",
            "transaction_exchange_rate": 1.2,
            "debit_in_account_currency": 12.345,
            "debit": 13.0,
            "debit_in_transaction_currency": 10.0,
            "credit_in_account_currency": 6.789,
            "credit": 7.0,
            "credit_in_transaction_currency": 5.0,
            "cost_center": "Main - K",
            "project": "Project-1",
            "finance_book": "Standard",
            "company": "Karam",
            "is_opening": "No",
            "is_advance": "Yes",
            "to_rename": 0,
            "is_cancelled": 0,
            "remarks": "already USD",
            "docstatus": 1,
        }
        with (
            patch.object(conversion, "get_applicable_rate") as lookup,
            patch.object(conversion, "get_gl_entry_stable_hash", return_value="stable"),
            patch.object(
                conversion.frappe,
                "get_system_settings",
                return_value="Banker's Rounding",
            ),
            patch.object(conversion.frappe, "session", SimpleNamespace(user="tester")),
            patch.object(conversion.frappe.utils, "now", return_value="now"),
        ):
            record = conversion.process_gl_entry(gle, [], [], "LBP", "USD")

        lookup.assert_not_called()
        assert record == {
            "doctype": conversion.DOCTYPE_RC_GLE,
            "name": None,
            "gl_entry": "GLE-USD-1",
            "gl_entry_hash": "stable",
            "gl_entry_modified": "2026-02-03 04:05:06",
            "posting_date": "2026-02-01",
            "transaction_date": "2026-01-31",
            "fiscal_year": "2026",
            "due_date": "2026-02-28",
            "account": "Bank - K",
            "account_currency": "USD",
            "against": "Debtors - K",
            "party_type": "Customer",
            "party": "Customer-1",
            "voucher_type": "Sales Invoice",
            "voucher_no": "SINV-1",
            "voucher_subtype": "Return",
            "transaction_currency": "EUR",
            "against_voucher_type": "Payment Entry",
            "against_voucher": "PE-1",
            "voucher_detail_no": "SINV-ITEM-1",
            "transaction_exchange_rate": 1.2,
            "debit_amount_in_account_currency": 12.345,
            "debit": 13.0,
            "debit_amount_in_transaction_currency": 10.0,
            "credit_amount_in_account_currency": 6.789,
            "credit": 7.0,
            "credit_in_transaction_currency": 5.0,
            "reporting_debit": 12.345,
            "reporting_credit": 6.789,
            "reporting_currency": "USD",
            "currency_exchange": None,
            "date": None,
            "exchange_rate": 1,
            "cost_center": "Main - K",
            "project": "Project-1",
            "finance_book": "Standard",
            "company": "Karam",
            "is_opening": "No",
            "is_advance": "Yes",
            "to_rename": 0,
            "is_cancelled": 0,
            "remarks": "already USD",
            "account_details": 0,
            "docstatus": 1,
            "manual_entry": 0,
            "creation": "now",
            "modified": "now",
            "owner": "tester",
            "modified_by": "tester",
        }

    def test_converted_record_retains_rate_identity_effective_rate_and_iso_date(
        self,
    ) -> None:
        gle = {
            "name": "GLE-1",
            "posting_date": "2026-02-01",
            "account_currency": "LBP",
            "debit": 100,
            "credit": 40,
            "company": "Karam",
            "docstatus": 1,
        }
        with (
            patch.object(
                conversion,
                "get_applicable_rate",
                return_value={
                    "rate": 4,
                    "direction": "inverse",
                    "currency_exchange": "CE-1",
                    "date": date(2026, 1, 1),
                },
            ),
            patch.object(conversion, "convert_amounts", return_value=(25.0, 10.0)),
            patch.object(conversion, "get_gl_entry_stable_hash", return_value="stable"),
            patch.object(conversion.frappe, "session", SimpleNamespace(user="tester")),
            patch.object(conversion.frappe.utils, "now", return_value="now"),
        ):
            record = conversion.process_gl_entry(gle, [], [], "LBP", "USD", {})
        assert {
            key: record[key]
            for key in (
                "currency_exchange",
                "date",
                "exchange_rate",
                "reporting_debit",
                "reporting_credit",
                "gl_entry_hash",
            )
        } == {
            "currency_exchange": "CE-1",
            "date": "2026-01-01",
            "exchange_rate": 0.25,
            "reporting_debit": 25.0,
            "reporting_credit": 10.0,
            "gl_entry_hash": "stable",
        }

    def test_zero_inverse_rate_is_safe(self) -> None:
        assert conversion.convert_amounts(
            {"debit": 4, "credit": 3}, 0, "inverse", "USD"
        ) == (0.0, 0.0)
        assert conversion._effective_exchange_rate(0, "inverse") == 0

    def test_converted_record_preserves_an_iso_string_exchange_date(self) -> None:
        with (
            patch.object(
                conversion,
                "get_applicable_rate",
                return_value={
                    "rate": 2,
                    "direction": "direct",
                    "currency_exchange": "CE-STRING-DATE",
                    "date": "2026-01-01",
                },
            ),
            patch.object(conversion, "convert_amounts", return_value=(2.0, 1.0)),
            patch.object(conversion, "get_gl_entry_stable_hash", return_value="stable"),
            patch.object(conversion.frappe, "session", SimpleNamespace(user="tester")),
            patch.object(conversion.frappe.utils, "now", return_value="now"),
        ):
            record = conversion.process_gl_entry(
                {"name": "GLE-2", "posting_date": "2026-02-01"},
                [],
                [],
                "LBP",
                "USD",
            )
        assert record["date"] == "2026-01-01"
        assert record["currency_exchange"] == "CE-STRING-DATE"


class TestDataFetchContracts(TestCase):
    def test_full_gl_fetch_uses_submitted_ordered_query_without_timestamp_filter(
        self,
    ) -> None:
        frappe_query_context = SimpleNamespace(
            qb=SimpleNamespace(DocType=Table, from_=MariaDB.from_)
        )

        with (
            patch.object(data_fetch, "frappe", frappe_query_context),
            patch.object(
                QueryBuilder,
                "run",
                autospec=True,
                return_value=[{"name": "GLE-1"}],
            ) as run,
        ):
            assert data_fetch.fetch_gl_entries() == [{"name": "GLE-1"}]

        query = run.call_args.args[0]
        assert run.call_args.kwargs == {"as_dict": True}
        assert query.get_sql() == (
            "SELECT `name`,`posting_date`,`transaction_date`,`fiscal_year`,"
            "`due_date`,`account`,`account_currency`,`against`,`party_type`,"
            "`party`,`voucher_type`,`voucher_no`,`voucher_subtype`,"
            "`transaction_currency`,`against_voucher_type`,`against_voucher`,"
            "`voucher_detail_no`,`transaction_exchange_rate`,"
            "`debit_in_account_currency`,`debit`,"
            "`debit_in_transaction_currency`,`credit_in_account_currency`,"
            "`credit`,`credit_in_transaction_currency`,`cost_center`,`project`,"
            "`finance_book`,`company`,`is_opening`,`is_advance`,`to_rename`,"
            "`is_cancelled`,`remarks`,`modified`,`docstatus` FROM `GL Entry` "
            "WHERE `docstatus`=1 ORDER BY `posting_date`,`creation`"
        )

    def test_cancelled_fetch_and_delete_bind_names_and_report_actual_count(
        self,
    ) -> None:
        database = Mock()
        database.get_all.return_value = [{"name": "GLE-1"}]
        database.sql.side_effect = [None, [(3,)]]
        with patch.object(data_fetch, "frappe") as frappe_mock:
            frappe_mock.db = database
            assert data_fetch.fetch_cancelled_gl_entries("cutoff") == [
                {"name": "GLE-1"}
            ]
            assert (
                data_fetch.delete_rc_gle_for_cancelled_gl_entries(
                    [{"name": "GLE-1"}], True
                )
                == 3
            )
        assert database.get_all.call_args.kwargs["filters"] == {
            "is_cancelled": 1,
            "modified": [">", "cutoff"],
        }
        assert database.sql.call_args_list[0].args[1] == ("GLE-1",)

    def test_empty_cancelled_input_never_executes_delete_and_orphan_cleanup_keeps_manual_rows(
        self,
    ) -> None:
        database = Mock()
        with patch.object(data_fetch, "frappe") as frappe_mock:
            frappe_mock.db = database
            assert data_fetch.delete_rc_gle_for_cancelled_gl_entries([], False) == 0
            database.sql.assert_not_called()
            database.sql.side_effect = [None, [(2,)]]
            assert data_fetch.cleanup_orphaned_rc_gle_records() == 2
        assert (
            "rc.manual_entry = 0 OR rc.manual_entry IS NULL"
            in database.sql.call_args_list[-2].args[0]
        )

    def test_cancelled_fetch_without_timestamp_has_exact_base_filter_and_fields(
        self,
    ) -> None:
        database = Mock()
        cancelled_entries: list[dict[str, Any]] = []
        database.get_all.return_value = cancelled_entries
        with patch.object(data_fetch, "frappe") as frappe_mock:
            frappe_mock.db = database
            assert data_fetch.fetch_cancelled_gl_entries() == cancelled_entries

        database.get_all.assert_called_once_with(
            data_fetch.DOCTYPE_GL_ENTRY,
            filters={"is_cancelled": 1},
            fields=[
                "name",
                "voucher_type",
                "voucher_no",
                "account",
                "posting_date",
                "debit",
                "credit",
            ],
        )

    def test_zero_orphan_cleanup_count_does_not_log(self) -> None:
        database = Mock()
        database.sql.side_effect = [None, [(0,)]]
        logger = Mock()
        with patch.object(data_fetch, "frappe") as frappe_mock:
            frappe_mock.db = database
            frappe_mock.logger.return_value = logger
            assert data_fetch.cleanup_orphaned_rc_gle_records() == 0
        frappe_mock.logger.assert_not_called()


class TestOrchestratorContracts(TestCase):
    def test_delete_all_preserves_manual_entries_resets_both_watermarks_and_commits(
        self,
    ) -> None:
        database = Mock()
        with patch.object(orchestrator, "frappe") as frappe_mock:
            frappe_mock.db = database
            orchestrator.delete_all_entries()
        frappe_mock.only_for.assert_called_once_with("System Manager")
        assert (
            "manual_entry = 0 OR manual_entry IS NULL" in database.sql.call_args.args[0]
        )
        database.set_single_value.assert_called_once_with(
            "Reporting Currency Settings",
            {"last_sync_timestamp": None, "last_ce_sync_timestamp": None},
            update_modified=False,
        )
        database.commit.assert_called_once()

    def test_gl_rename_is_a_noop_without_link_and_updates_deterministic_target(
        self,
    ) -> None:
        database = Mock()
        database.get_value.side_effect = [None, "RC-old"]
        with (
            patch.object(orchestrator, "frappe") as frappe_mock,
            patch.object(orchestrator, "now", return_value="now"),
        ):
            frappe_mock.db = database
            orchestrator._update_rc_gle_for_renamed_gl_entry(
                "old", "ACC-GLE-2026-00007"
            )
            orchestrator._update_rc_gle_for_renamed_gl_entry(
                "old", "ACC-GLE-2026-00007"
            )
        assert database.sql.call_args.args[1] == (
            "KE-RCGLE-2026-00007",
            "ACC-GLE-2026-00007",
            "now",
            "RC-old",
        )

    def test_gl_rename_uses_hash_fallback_for_linked_record(self) -> None:
        database = Mock()
        database.get_value.return_value = "RC-old"
        with (
            patch.object(orchestrator, "frappe") as frappe_mock,
            patch.object(orchestrator, "now", return_value="now"),
        ):
            frappe_mock.db = database
            orchestrator._update_rc_gle_for_renamed_gl_entry("old", "newhash")
        assert database.sql.call_args.args[1] == (
            "RC-newhash",
            "newhash",
            "now",
            "RC-old",
        )

    def test_gl_rename_hooks_forward_old_and_new_names_in_shared_order(self) -> None:
        with patch.object(
            orchestrator, "_update_rc_gle_for_renamed_gl_entry"
        ) as update:
            orchestrator.on_gl_entry_rename(
                object(), "after_rename", "old-doc", "new-doc"
            )
            orchestrator.on_gle_rename_hook(newname="new-hook", oldname="old-hook")
        assert update.call_args_list[0].args == ("old-doc", "new-doc")
        assert update.call_args_list[1].args == ("old-hook", "new-hook")

    def test_sync_returns_deletion_stats_when_validation_has_no_source_rows(
        self,
    ) -> None:
        with (
            patch.object(
                orchestrator,
                "validate_settings",
                return_value={"reporting_currency": "USD", "last_sync_timestamp": None},
            ),
            patch.object(
                orchestrator,
                "run_validation_phase",
                return_value=([], "", False, "Full Sync", {}, None),
            ),
            patch.object(
                orchestrator,
                "run_deletion_phase",
                return_value={"deleted_cancelled": 2, "deleted_orphaned": 3},
            ),
            patch.object(orchestrator, "now", return_value="2026-01-01 00:00:00"),
            patch.object(orchestrator, "publish_sync_progress"),
        ):
            assert orchestrator.sync_reporting_currency_entries("event") == {
                "inserted": 0,
                "skipped": 0,
                "errors": 0,
                "deleted": 5,
            }


class TestUncoveredSyncContracts(TestCase):
    def test_validation_uses_cached_default_currency_without_company_lookup(
        self,
    ) -> None:
        gl_entries = [{"name": "GLE-1", "company": "Karam"}]
        cached_coverage = {"USD": True}
        with (
            patch.object(
                phases,
                "_initial_sync_mode",
                return_value=(True, "Incremental Sync", "watermark"),
            ),
            patch.object(phases, "fetch_gl_entries", return_value=gl_entries) as fetch,
            patch.object(phases, "_default_currency_for_entries") as company_currency,
            patch.object(phases, "validate_currency_exchange_coverage") as validate,
            patch.object(phases, "publish_sync_progress"),
        ):
            result = phases.run_validation_phase(
                "event",
                "user",
                "USD",
                "last-sync",
                cached_coverage,
                "LBP",
            )
        assert result == (
            gl_entries,
            "LBP",
            True,
            "Incremental Sync",
            cached_coverage,
            "watermark",
        )
        fetch.assert_called_once_with(last_sync_timestamp="watermark")
        company_currency.assert_not_called()
        validate.assert_not_called()

    def test_validation_exits_when_incremental_fetch_and_source_table_are_empty(
        self,
    ) -> None:
        database = Mock()
        database.count.return_value = 0
        with (
            patch.object(
                phases,
                "_initial_sync_mode",
                return_value=(True, "Incremental Sync", "watermark"),
            ),
            patch.object(phases, "fetch_gl_entries", return_value=[]) as fetch,
            patch.object(phases, "_default_currency_for_entries") as company_currency,
            patch.object(phases, "validate_currency_exchange_coverage") as validate,
            patch.object(phases, "frappe") as frappe_mock,
            patch.object(phases, "publish_sync_progress") as progress,
        ):
            frappe_mock.db = database
            assert phases.run_validation_phase("event", "user", "USD", "last-sync") == (
                [],
                "",
                True,
                "Incremental Sync",
                {},
                "watermark",
            )
        fetch.assert_called_once_with(last_sync_timestamp="watermark")
        database.count.assert_called_once_with(
            phases.DOCTYPE_GL_ENTRY, {"docstatus": 1}
        )
        company_currency.assert_not_called()
        validate.assert_not_called()
        assert progress.call_args_list[-1].args == (
            "event",
            phases.PROGRESS_COMPLETE,
            "No GL Entries to process.",
            "user",
        )

    def test_conversion_defers_progress_until_the_final_of_two_records(self) -> None:
        gl_entries = [{"name": "GLE-1"}, {"name": "GLE-2"}]
        converted = [{"gl_entry": "GLE-1"}, {"gl_entry": "GLE-2"}]
        rate_timeline: list[dict[str, Any]] = [{"rate": "rate"}]
        rate_dates = ["date"]
        with (
            patch.object(phases, "process_gl_entry", side_effect=converted) as process,
            patch.object(phases, "publish_sync_progress") as progress,
        ):
            assert (
                phases.run_conversion_phase(
                    "event", "user", gl_entries, rate_timeline, rate_dates, "LBP", "USD"
                )
                == converted
            )
        assert process.call_count == 2
        assert process.call_args_list[0].args[:5] == (
            gl_entries[0],
            rate_timeline,
            rate_dates,
            "LBP",
            "USD",
        )
        assert process.call_args_list[0].args[5] is process.call_args_list[1].args[5]
        assert progress.call_count == 2
        assert progress.call_args_list[-1].args == (
            "event",
            phases.PROGRESS_PHASE3_END,
            "Processing GL Entry 2 of 2...",
            "user",
        )

    def test_incremental_insertion_with_no_entries_skips_delete_and_sets_watermarks(
        self,
    ) -> None:
        database = Mock()
        with (
            patch.object(phases, "frappe") as frappe_mock,
            patch.object(phases, "_delete_incremental_records") as delete,
            patch.object(phases, "publish_sync_progress"),
        ):
            frappe_mock.db = database
            assert phases.run_insertion_phase(
                "event",
                "user",
                [],
                [],
                phases.InsertionContext(is_incremental=True, cutoff="watermark"),
            ) == {"inserted": 0}
        delete.assert_not_called()
        database.sql.assert_not_called()
        database.bulk_insert.assert_not_called()
        database.set_single_value.assert_called_once_with(
            phases.DOCTYPE_RC_SETTINGS,
            {"last_sync_timestamp": "watermark", "last_ce_sync_timestamp": "watermark"},
            update_modified=False,
        )

    def test_initial_sync_rebuilds_empty_target_and_when_exchange_rates_changed(
        self,
    ) -> None:
        database = Mock()
        database.exists.side_effect = [False]
        with patch.object(phases, "frappe") as frappe_mock:
            frappe_mock.db = database
            frappe_mock.logger.return_value = Mock()
            assert phases._initial_sync_mode("cutoff", "event", None) == (
                False,
                "Full Sync (rebuild)",
                None,
            )
        database.exists.side_effect = [True, True]
        with (
            patch.object(phases, "frappe") as frappe_mock,
            patch.object(phases, "publish_sync_progress") as progress,
        ):
            frappe_mock.db = database
            frappe_mock.logger.return_value = Mock()
            assert phases._initial_sync_mode("cutoff", "event", "user") == (
                False,
                "Full Sync (CE updated)",
                None,
            )
        progress.assert_called_once()

    def test_insert_and_delete_chunk_boundaries_preserve_order_and_report_progress(
        self,
    ) -> None:
        database = Mock()
        with (
            patch.object(phases, "frappe") as frappe_mock,
            patch.object(phases, "publish_sync_progress") as progress,
        ):
            frappe_mock.db = database
            ten_thousand = [
                {
                    "doctype": "Reporting Currency GLE",
                    "name": None,
                    "gl_entry": f"ACC-GLE-2026-{i}",
                }
                for i in range(10000)
            ]
            assert phases._insert_rc_gle_records(ten_thousand, "event", "user") == 10000
            phases._delete_incremental_records(list(range(10000)), "event", "user")
            assert database.bulk_insert.call_count == 1
            assert database.bulk_insert.call_args.kwargs["values"][0] == [
                "KE-RCGLE-2026-0",
                "ACC-GLE-2026-0",
            ]
            assert len(database.sql.call_args.args[1]) == 10000
            progress.assert_called_once_with(
                "event",
                phases.PROGRESS_PHASE4_DELETE,
                "Deleted 10000 existing RC GLE records for update...",
                "user",
            )

            database.reset_mock()
            progress.reset_mock()
            records = [
                {
                    "doctype": "Reporting Currency GLE",
                    "name": None,
                    "gl_entry": f"ACC-GLE-2026-{i}",
                }
                for i in range(10001)
            ]
            assert phases._insert_rc_gle_records(records, "event", "user") == 10001
            phases._delete_incremental_records(list(range(10001)), "event", "user")
        assert database.bulk_insert.call_count == 2
        assert [
            len(call.kwargs["values"]) for call in database.bulk_insert.call_args_list
        ] == [10000, 1]
        assert [len(call.args[1]) for call in database.sql.call_args_list] == [10000, 1]
        assert database.sql.call_args_list[1].args[1] == (10000,)
        inserted = [
            row
            for call in database.bulk_insert.call_args_list
            for row in call.kwargs["values"]
        ]
        assert inserted[0] == ["KE-RCGLE-2026-0", "ACC-GLE-2026-0"]
        assert inserted[-1] == ["KE-RCGLE-2026-10000", "ACC-GLE-2026-10000"]
        assert progress.call_args_list[-1].args == (
            "event",
            phases.PROGRESS_PHASE4_DELETE,
            "Deleted 10001 existing RC GLE records for update...",
            "user",
        )

    def test_assign_name_rejects_missing_gl_entry(self) -> None:
        with (
            patch.object(phases, "frappe") as frappe_mock,
            self.assertRaises(frappe.ValidationError),
        ):
            frappe_mock.throw.side_effect = _raise_validation
            phases._assign_rc_gle_name({})

    def test_settings_and_reporting_company_cover_single_empty_and_multi_company(
        self,
    ) -> None:
        query = Mock()
        query.select.return_value = query
        query.where.return_value = query
        query.distinct.return_value = query
        query.limit.return_value = query
        frappe_mock = Mock()
        frappe_mock.qb.DocType.return_value = Mock()
        frappe_mock.qb.from_.return_value = query
        frappe_mock.throw.side_effect = _raise_validation
        with patch.object(validation, "frappe", frappe_mock):
            query.run.return_value = ["Karam", "Other"]
            with self.assertRaises(frappe.ValidationError):
                validation.validate_settings()
            no_companies: list[str] = []
            query.run.return_value = no_companies
            assert validation.get_reporting_company() is None
            query.run.return_value = ["Karam"]
            assert validation.get_reporting_company() == "Karam"

    def test_missing_exchange_error_renders_sample_table(self) -> None:
        with patch.object(validation, "frappe") as frappe_mock:
            validation._throw_missing_exchange_error(
                [
                    {
                        "name": "GLE-reporting-currency",
                        "posting_date": "2026-01-01",
                        "account": "Reporting",
                        "voucher_no": "JV-0",
                        "account_currency": "USD",
                    },
                    {
                        "name": "GLE-1",
                        "posting_date": "2026-01-01",
                        "account": "Bank",
                        "voucher_no": "JV-1",
                        "account_currency": "EUR",
                    },
                ],
                {"EUR"},
                "LBP",
                reporting_currency="USD",
            )
        assert "GLE-1" in frappe_mock.throw.call_args.args[0]
        assert (
            "Missing Currency Exchange: LBP-USD" in frappe_mock.throw.call_args.args[0]
        )
        assert "GLE-reporting-currency" not in frappe_mock.throw.call_args.args[0]

    def test_company_currency_returns_configuration_and_rejects_missing_value(
        self,
    ) -> None:
        database = Mock()
        database.get_value.side_effect = ["LBP", None]
        with (
            patch.object(validation, "frappe") as frappe_mock,
            patch.object(validation, "_", side_effect=_identity_text),
        ):
            frappe_mock.db = database
            assert validation.get_company_default_currency("Karam") == "LBP"
            frappe_mock.throw.side_effect = _raise_validation
            with self.assertRaises(frappe.ValidationError):
                validation.get_company_default_currency("Other")
        assert database.get_value.call_args_list[0].args == (
            validation.DOCTYPE_COMPANY,
            "Karam",
            "default_currency",
        )
        assert database.get_value.call_args_list[1].args == (
            validation.DOCTYPE_COMPANY,
            "Other",
            "default_currency",
        )

    def test_precision_and_temporal_csv_cover_fallback_and_cache_paths(self) -> None:
        with patch.object(utils, "frappe") as frappe_mock:
            frappe_mock.db.get_value.return_value = None
            frappe_mock.get_precision.return_value = 3
            assert utils.get_currency_precision("USD") == 3
        with patch.object(utils, "frappe") as frappe_mock:
            empty_temporal_entries: list[dict[str, Any]] = []
            frappe_mock.cache.get_value.return_value = empty_temporal_entries
            frappe_mock.throw.side_effect = _raise_validation
            with self.assertRaises(frappe.ValidationError):
                utils.export_temporal_validation_entries_csv("key", "LBP", "USD")
        with (
            patch.object(utils, "frappe") as frappe_mock,
            patch.object(utils, "_generate_csv_download") as download,
        ):
            frappe_mock.cache.get_value.return_value = [
                {
                    "gle": "GLE-1",
                    "date": "2026-01-01",
                    "account": "Bank",
                    "voucher": "JV-1",
                }
            ]
            utils.export_temporal_validation_entries_csv("key", "LBP", "USD")
        assert download.call_args.args[1][0][0] == "GLE-1"

    def test_enqueue_rejects_permission_and_handles_no_data_before_queueing(
        self,
    ) -> None:
        with (
            patch.object(orchestrator, "frappe") as frappe_mock,
            self.assertRaises(frappe.ValidationError),
        ):
            frappe_mock.has_permission.return_value = False
            frappe_mock.throw.side_effect = _raise_validation
            orchestrator.enqueue_reporting_currency_sync()
        frappe_mock.enqueue.assert_not_called()
        frappe_mock.db.sql.assert_not_called()

        database = Mock()
        no_pending_entries: list[tuple[int]] = []
        database.sql.side_effect = [[(1,)], no_pending_entries]
        with (
            patch.object(orchestrator, "frappe") as frappe_mock,
            patch.object(
                orchestrator,
                "validate_settings",
                return_value={"reporting_currency": "USD"},
            ),
            patch.object(orchestrator, "now", return_value="cutoff"),
            patch.object(orchestrator, "get_company_default_currency") as currency,
        ):
            frappe_mock.has_permission.return_value = True
            frappe_mock.db = database
            assert orchestrator.enqueue_reporting_currency_sync() == {
                "status": "no_data"
            }
        frappe_mock.enqueue.assert_not_called()
        currency.assert_not_called()
        with (
            patch.object(orchestrator, "frappe") as frappe_mock,
            patch.object(
                orchestrator,
                "validate_settings",
                return_value={"reporting_currency": "USD"},
            ),
            patch.object(orchestrator, "now", return_value="cutoff"),
        ):
            frappe_mock.has_permission.return_value = True
            frappe_mock.db.sql.return_value = [(0,)]
            assert orchestrator.enqueue_reporting_currency_sync() == {
                "status": "no_data"
            }
        frappe_mock.enqueue.assert_not_called()

    def test_background_job_commits_success_and_rolls_back_failure(self) -> None:
        database = Mock()
        with (
            patch.object(orchestrator, "frappe") as frappe_mock,
            patch.object(orchestrator, "_", side_effect=_identity_text),
            patch.object(orchestrator.time, "monotonic", side_effect=[10, 10.25]),
            patch.object(orchestrator, "ensure_currency_columns_capacity"),
            patch.object(
                orchestrator,
                "sync_reporting_currency_entries",
                return_value={"inserted": 2},
            ),
            patch.object(
                doe,
                "compute_doe_inline",
                return_value={
                    "success": True,
                    "accounts_processed": 3,
                    "records_created": 4,
                },
            ),
        ):
            frappe_mock.db = database
            orchestrator.run_reporting_currency_sync_job("event", "done")
        database.commit.assert_called_once()
        database.rollback.assert_not_called()
        assert frappe_mock.publish_realtime.call_args.kwargs["message"] == {
            "status": "success",
            "inserted": 2,
            "skipped": 0,
            "errors": 0,
            "doe_accounts_processed": 3,
            "doe_records_created": 4,
            "duration_seconds": 0.25,
        }

        database = Mock()
        with (
            patch.object(orchestrator, "frappe") as frappe_mock,
            patch.object(orchestrator, "_", side_effect=_identity_text),
            patch.object(orchestrator, "ensure_currency_columns_capacity"),
            patch.object(
                orchestrator,
                "sync_reporting_currency_entries",
                return_value={"inserted": 2},
            ),
            patch.object(doe, "compute_doe_inline", return_value={"success": False}),
        ):
            frappe_mock.db = database
            orchestrator.run_reporting_currency_sync_job("event", "done")
        database.commit.assert_called_once()
        database.rollback.assert_not_called()
        assert (
            frappe_mock.publish_realtime.call_args.kwargs["message"]["status"]
            == "success"
        )
        assert (
            "doe_accounts_processed"
            not in frappe_mock.publish_realtime.call_args.kwargs["message"]
        )

        database = Mock()
        with (
            patch.object(orchestrator, "frappe") as frappe_mock,
            patch.object(orchestrator, "_", side_effect=_identity_text),
            patch.object(orchestrator, "ensure_currency_columns_capacity"),
            patch.object(
                orchestrator,
                "sync_reporting_currency_entries",
                return_value={"inserted": 2},
            ),
            patch.object(
                doe, "compute_doe_inline", side_effect=RuntimeError("DOE failed")
            ),
        ):
            frappe_mock.db = database
            orchestrator.run_reporting_currency_sync_job("event", "done")
        database.commit.assert_called_once()
        database.rollback.assert_not_called()
        assert frappe_mock.publish_realtime.call_args.kwargs["message"]["status"] == (
            "partial_success"
        )

        database = Mock()
        with (
            patch.object(orchestrator, "frappe") as frappe_mock,
            patch.object(orchestrator, "_", side_effect=_identity_text),
            patch.object(orchestrator, "ensure_currency_columns_capacity"),
            patch.object(
                orchestrator,
                "sync_reporting_currency_entries",
                side_effect=RuntimeError("sync failure"),
            ),
            patch.object(doe, "compute_doe_inline") as compute_doe,
            self.assertRaises(RuntimeError),
        ):
            frappe_mock.db = database
            orchestrator.run_reporting_currency_sync_job("event", "done", "user")
        database.rollback.assert_called_once()
        database.commit.assert_not_called()
        compute_doe.assert_not_called()
        assert frappe_mock.publish_realtime.call_args.args[0] == "done"
        assert frappe_mock.publish_realtime.call_args.kwargs["message"] == {
            "status": "error",
            "title": "Sync Failed",
            "message": "sync failure",
        }
