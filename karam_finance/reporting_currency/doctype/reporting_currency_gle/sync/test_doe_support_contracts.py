"""Pure contract tests for sync support functions and DOE boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, override
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from karam_finance.reporting_currency import ledger_lock

from . import doe, utils


def _raise_validation(message: str, *_args: object, **_kwargs: object) -> None:
    raise frappe.ValidationError(message)


def _quote_for_test(value: str) -> str:
    return f"'{value}'"


def _identity_text(message: str) -> str:
    return message


def _translated_text(message: str) -> str:
    return f"translated:{message}"


class TestCsvAndDoeContracts(TestCase):
    @override
    def setUp(self) -> None:
        # Native lock behaviour is exercised by separate-connection integration tests.
        for target in (ledger_lock, doe):
            lock_patch = patch.object(
                target,
                "hold_ledger_lock",
                side_effect=lambda: Mock(
                    database=doe.frappe.db,
                    scopes=0,
                ),
            )
            lock_patch.start()
            self.addCleanup(lock_patch.stop)

    def test_progress_publication_preserves_event_payload_and_target_user(self) -> None:
        with (
            patch.object(utils, "frappe") as frappe_mock,
            patch.object(utils, "_", side_effect=_translated_text),
        ):
            utils.publish_sync_progress("sync-event", 42, "Working", "test@example.com")
        frappe_mock.publish_realtime.assert_called_once_with(
            "sync-event",
            message={
                "current": 42,
                "total": utils.PROGRESS_COMPLETE,
                "message": "translated:Working",
            },
            user="test@example.com",
        )

    def test_csv_exports_enforce_role_and_preserve_rows(self) -> None:
        response = SimpleNamespace(filename=None, filecontent=None, type=None)
        database = Mock()
        database.get_all.return_value = [
            {
                "name": "GLE-1",
                "posting_date": "2026-01-01",
                "account": "Bank",
                "voucher_no": "JV-1",
                "voucher_type": "Journal Entry",
                "debit_in_account_currency": 2,
                "credit_in_account_currency": 0,
                "remarks": "ok",
            }
        ]
        with patch.object(utils, "frappe") as frappe_mock:
            frappe_mock.db = database
            frappe_mock.local.response = response
            frappe_mock.utils.now_datetime.return_value.strftime.return_value = (
                "20260101_000000"
            )
            utils.export_missing_currency_gl_entries_csv("EUR", "USD")
        frappe_mock.only_for.assert_called_once_with("System Manager")
        assert response.type == "download"
        assert "GLE-1" in response.filecontent
        assert database.get_all.call_args.kwargs["filters"] == {
            "docstatus": 1,
            "account_currency": "EUR",
        }

    def test_missing_currency_export_stops_after_denied_role(self) -> None:
        database = Mock()
        with (
            patch.object(utils, "frappe") as frappe_mock,
            self.assertRaises(PermissionError),
        ):
            frappe_mock.db = database
            frappe_mock.only_for.side_effect = PermissionError("denied")
            utils.export_missing_currency_gl_entries_csv("EUR", "USD")
        database.get_all.assert_not_called()

    def test_missing_currency_export_empty_result_throws_without_download(self) -> None:
        database = Mock()
        empty_currency_entries: list[dict[str, Any]] = []
        database.get_all.return_value = empty_currency_entries
        with (
            patch.object(utils, "frappe") as frappe_mock,
            patch.object(utils, "_", side_effect=_identity_text),
            patch.object(utils, "_generate_csv_download") as download,
            self.assertRaises(frappe.ValidationError),
        ):
            frappe_mock.db = database
            frappe_mock.throw.side_effect = _raise_validation
            utils.export_missing_currency_gl_entries_csv("EUR", "USD")
        download.assert_not_called()

    def test_doe_maps_accounts_and_refuses_invalid_date_before_naming(self) -> None:
        with patch.object(
            doe.frappe,
            "get_all",
            return_value=[{"name": "Profit", "account_currency": None}],
        ):
            assert doe._get_profit_loss_currency_map("Profit", "", "USD") == {
                "Profit": "USD"
            }
        with (
            patch.object(doe, "getdate", return_value=None),
            self.assertRaises(frappe.ValidationError),
        ):
            doe._required_doe_date("bad-date")

    def test_doe_entrypoint_rejects_missing_currency_and_queues_valid_work(
        self,
    ) -> None:
        settings = SimpleNamespace(reporting_currency="USD", rc_parameters=["rate"])
        with patch.object(doe, "frappe") as frappe_mock:
            frappe_mock.get_single.return_value = SimpleNamespace(
                reporting_currency=None, rc_parameters=[]
            )
            frappe_mock.throw.side_effect = _raise_validation
            with self.assertRaises(frappe.ValidationError):
                doe.compute_doe()

            frappe_mock.get_single.return_value = settings
            frappe_mock.enqueue.return_value = Mock(id="job-1")
            with (
                patch.object(doe, "validate_doe_exchange_rates") as validate_rates,
                patch.object(doe, "now", return_value="now"),
            ):
                result = doe.compute_doe(background=True)
        assert result["job_id"]
        validate_rates.assert_called_once_with(["rate"])
        assert frappe_mock.enqueue.call_args.kwargs["queue"] == "long"

    def test_doe_exclusion_sql_and_result_are_deterministic(self) -> None:
        database = Mock()
        database.sql_list.return_value = ["Bank", "Cash"]
        database.escape.side_effect = _quote_for_test
        with patch.object(doe, "frappe") as frappe_mock:
            frappe_mock.db = database
            assert (
                doe._get_excluded_accounts_condition()
                == "AND rc.account NOT IN ('Bank', 'Cash')"
            )
        assert doe._doe_result([{}, {}], 3) == {
            "success": True,
            "message": "DOE computation completed. Processed 3 accounts, created 2 records.",
            "accounts_processed": 3,
            "records_created": 2,
        }

    def test_doe_parameter_without_accounts_is_skipped_without_creating_records(
        self,
    ) -> None:
        row = SimpleNamespace(
            exchange_rate=1,
            doe_posting_date="2026-01-01",
            profit_account="",
            loss_account="Loss",
            idx=4,
        )
        context = doe._DOEComputation(excluded_accounts_condition="")
        with patch.object(doe, "frappe") as frappe_mock:
            assert doe._process_doe_parameter(row, "Karam", "USD", context=context) == (
                [],
                0,
                None,
            )
        frappe_mock.log_error.assert_called_once()

    def test_doe_total_calculation_uses_supplied_snapshot_without_querying(
        self,
    ) -> None:
        totals = {
            "total_debit": 100,
            "total_credit": 20,
            "total_reporting_debit": 5,
            "total_reporting_credit": 1,
        }
        database = Mock()
        with (
            patch.object(
                doe.frappe,
                "get_system_settings",
                return_value="Banker's Rounding (legacy)",
            ),
            patch.object(doe.frappe, "db", database),
        ):
            calculated = doe._compute_doe_for_account(
                "Karam",
                "Bank",
                _account_currency="LBP",
                _reporting_currency="USD",
                exchange_rate=4,
                account_totals=totals,
            )
        assert calculated == {
            "total_debit_default_currency": 100.0,
            "total_credit_default_currency": 20.0,
            "difference_default_currency": 80.0,
            "reporting_debit_total": 5.0,
            "reporting_credit_total": 1.0,
            "difference_reporting_currency": 4.0,
            "reporting_doe_difference": 20.0,
            "final_amount": 16.0,
        }
        database.sql.assert_not_called()

    def test_doe_name_counter_uses_trailing_serial_and_recovers_from_malformed_name(
        self,
    ) -> None:
        database = Mock()
        database.sql.side_effect = [[("KE-RCDOE-GLE-2026-00009",)], [("bad",)]]
        with patch.object(doe, "frappe") as frappe_mock:
            frappe_mock.db = database
            assert doe._get_starting_doe_number("2026-01-01") == {
                "year": 2026,
                "counter": 10,
            }
            assert doe._get_starting_doe_number("2026-01-01") == {
                "year": 2026,
                "counter": 1,
            }

    def test_doe_helpers_cover_empty_maps_inline_dispatch_and_progress_payload(
        self,
    ) -> None:
        with patch.object(doe, "frappe") as frappe_mock:
            assert doe._get_profit_loss_currency_map("", "", "USD") == {}
        frappe_mock.get_all.assert_not_called()

        with (
            patch.object(doe, "frappe") as frappe_mock,
            patch.object(
                doe, "_compute_doe_background", return_value={"success": True}
            ) as compute,
            patch.object(doe, "validate_doe_exchange_rates") as validate_rates,
        ):
            settings = SimpleNamespace(reporting_currency="USD", rc_parameters=["row"])
            frappe_mock.get_single.return_value = settings
            assert doe.compute_doe(background=False) == {"success": True}
        compute.assert_called_once_with()
        frappe_mock.enqueue.assert_not_called()
        frappe_mock.only_for.assert_called_once_with("System Manager")
        validate_rates.assert_called_once_with(settings.rc_parameters)

        with (
            patch.object(doe, "frappe") as frappe_mock,
            patch.object(doe, "now", return_value="2026-01-01 01:02:03"),
        ):
            frappe_mock.session.user = "tester@example.com"
            doe._publish_progress(42, "Working")
        frappe_mock.publish_realtime.assert_called_once_with(
            "doe_progress",
            {"progress": 42, "message": "Working", "timestamp": "2026-01-01 01:02:03"},
            user="tester@example.com",
        )

    def test_doe_background_validation_failures_rollback_without_insert(self) -> None:
        no_parameters: list[Any] = []
        cases = [
            (None, "USD", ["row"], 1, "No RC GLE records found"),
            ("Karam", None, ["row"], 1, "Reporting Currency is not configured"),
            ("Karam", "USD", no_parameters, 1, "No RC Parameters defined"),
            ("Karam", "USD", ["row"], 0, "No RC GLE records found"),
        ]
        for company, currency, parameters, count, message in cases:
            with self.subTest(
                company=company, currency=currency, parameters=parameters, count=count
            ):
                database = Mock()
                settings = SimpleNamespace(
                    reporting_currency=currency, rc_parameters=parameters
                )
                with (
                    patch.object(doe, "frappe") as frappe_mock,
                    patch.object(doe, "get_reporting_company", return_value=company),
                    patch.object(doe, "_publish_progress") as progress,
                    patch.object(doe, "_bulk_insert_doe_records") as insert,
                    patch.object(doe, "validate_doe_exchange_rates"),
                    patch.object(doe, "validate_offset_accounts"),
                    self.assertRaisesRegex(frappe.ValidationError, message),
                ):
                    frappe_mock.db = database
                    frappe_mock.get_single.return_value = settings
                    frappe_mock.throw.side_effect = _raise_validation
                    database.count.return_value = count
                    doe._compute_doe_background()
                database.rollback.assert_called_once()
                database.sql.assert_not_called()
                insert.assert_not_called()
                assert progress.call_args.args[0] == -1

    def test_doe_inline_returns_validation_results_and_skips_empty_insert(self) -> None:
        no_parameters: list[Any] = []
        for company, currency, parameters, expected in (
            (None, "USD", ["row"], "No RC GLE records found"),
            ("Karam", None, ["row"], "Reporting Currency not configured"),
            ("Karam", "USD", no_parameters, "No RC Parameters defined"),
        ):
            with self.subTest(
                company=company, currency=currency, parameters=parameters
            ):
                database = Mock()
                with (
                    patch.object(doe, "frappe") as frappe_mock,
                    patch.object(doe, "get_reporting_company", return_value=company),
                ):
                    frappe_mock.db = database
                    frappe_mock.get_single.return_value = SimpleNamespace(
                        reporting_currency=currency, rc_parameters=parameters
                    )
                    assert doe.compute_doe_inline()["message"] == expected
                database.rollback.assert_not_called()
                database.savepoint.assert_not_called()
                database.sql.assert_not_called()

        database = Mock()
        settings = SimpleNamespace(reporting_currency="USD", rc_parameters=["row"])
        with (
            patch.object(doe, "frappe") as frappe_mock,
            patch.object(doe, "get_reporting_company", return_value="Karam"),
            patch.object(doe, "validate_doe_exchange_rates"),
            patch.object(doe, "validate_offset_accounts"),
            patch.object(doe, "_get_excluded_accounts_condition", return_value=""),
            patch.object(
                doe, "_get_rc_parameters_sorted_by_date", return_value=["row"]
            ),
            patch.object(doe, "_collect_doe_records", return_value=([], 0)),
            patch.object(doe, "_bulk_insert_doe_records") as insert,
        ):
            frappe_mock.db = database
            frappe_mock.get_single.return_value = settings
            assert doe.compute_doe_inline() == {
                "success": True,
                "message": "DOE computation completed. Processed 0 accounts, created 0 records.",
                "accounts_processed": 0,
                "records_created": 0,
            }
        insert.assert_not_called()

    def test_empty_background_finish_preserves_source_watermarks(
        self,
    ) -> None:
        database = Mock()
        settings = Mock()
        with (
            patch.object(doe, "frappe") as frappe_mock,
            patch.object(doe, "_publish_progress"),
            patch.object(doe, "_bulk_insert_doe_records") as insert,
            patch.object(doe, "now", return_value="watermark"),
        ):
            frappe_mock.db = database
            assert doe._finish_background_doe(settings, [], 0) == {
                "success": True,
                "message": "DOE computation completed. Processed 0 accounts, created 0 records.",
                "accounts_processed": 0,
                "records_created": 0,
            }
        insert.assert_not_called()
        settings.db_set.assert_not_called()
        database.commit.assert_called_once()

    def test_doe_parameter_with_no_account_groups_returns_row_status(self) -> None:
        row = SimpleNamespace(
            exchange_rate=4,
            doe_posting_date="2026-01-01",
            profit_account="Profit",
            loss_account="Loss",
            idx=3,
        )
        context = doe._DOEComputation(excluded_accounts_condition="")
        with (
            patch("erpnext.accounts.utils.get_fiscal_year", return_value=("2026",)),
            patch.object(doe, "_get_profit_loss_currency_map", return_value={}),
            patch.object(
                doe,
                "_get_starting_doe_number",
                return_value={"year": 2026, "counter": 1},
            ),
            patch.object(doe, "_get_accounts_with_totals", return_value=[]),
            patch.object(
                doe.frappe,
                "get_system_settings",
                return_value="Banker's Rounding",
            ),
        ):
            assert doe._process_doe_parameter(row, "Karam", "USD", context=context) == (
                [],
                0,
                "Row 3 (until 2026-01-01): No accounts",
            )

    def test_doe_account_helpers_use_zero_aggregate_and_cache_profit_loss_currency(
        self,
    ) -> None:
        database = Mock()
        zero = {
            "total_debit": 0,
            "total_credit": 0,
            "total_reporting_debit": 0,
            "total_reporting_credit": 0,
        }
        nonzero = {
            "total_debit": 100,
            "total_credit": 20,
            "total_reporting_debit": 5,
            "total_reporting_credit": 1,
        }
        database.sql.side_effect = [[zero], [nonzero]]
        with (
            patch.object(doe.frappe, "db", database),
            patch.object(
                doe.frappe,
                "get_system_settings",
                return_value="Banker's Rounding",
            ),
        ):
            zero_result = doe._compute_doe_for_account(
                "Karam",
                "Bank",
                _account_currency="LBP",
                _reporting_currency="USD",
                exchange_rate=4,
            )
            nonzero_result = doe._compute_doe_for_account(
                "Karam",
                "Bank",
                _account_currency="LBP",
                _reporting_currency="USD",
                exchange_rate=4,
            )
        assert zero_result is not None
        assert nonzero_result is not None
        assert zero_result["final_amount"] == 0
        assert nonzero_result["final_amount"] == 16
        for sql_call in database.sql.call_args_list:
            assert "WHERE company = %s" in sql_call.args[0]
            assert "AND account = %s" in sql_call.args[0]
            assert sql_call.args[1] == ("Karam", "Bank")

        cache: dict[str, str | None] = {}
        data = {
            "total_debit_default_currency": 1,
            "total_credit_default_currency": 0,
            "difference_default_currency": 1,
            "reporting_debit_total": 0,
            "reporting_credit_total": 0,
            "difference_reporting_currency": 0,
            "reporting_doe_difference": 1,
            "final_amount": 1,
        }
        database = Mock()
        database.get_value.return_value = None
        with patch.object(doe, "frappe") as frappe_mock:
            frappe_mock.db = database
            records = doe._create_doe_records(
                "Bank",
                "LBP",
                None,
                None,
                data,
                "2026-01-01",
                "Profit",
                "Loss",
                "USD",
                "2026",
                "Karam",
                {"year": 2026, "counter": 1},
                cache,
            )
        assert records[1]["account_currency"] == "USD"
        assert cache == {"Profit": "USD"}
        database.get_value.assert_called_once_with(
            "Account", "Profit", "account_currency"
        )

    def test_doe_starting_name_rejects_sentinel_and_recovers_numeric_suffixes(
        self,
    ) -> None:
        with self.assertRaises(frappe.ValidationError):
            doe._get_starting_doe_number("0000-00-00")
        database = Mock()
        no_existing_names: list[tuple[str]] = []
        database.sql.side_effect = [
            [("KE-RCDOE-GLE-2026-not-a-number",)],
            no_existing_names,
        ]
        with patch.object(doe, "frappe") as frappe_mock:
            frappe_mock.db = database
            assert doe._get_starting_doe_number("2026-01-01") == {
                "year": 2026,
                "counter": 1,
            }
            assert doe._get_starting_doe_number("2026-01-01") == {
                "year": 2026,
                "counter": 1,
            }
