"""Contract tests for reporting-currency sync phases and repair helpers.

These tests deliberately use Frappe doubles: they exercise the decision and
write boundaries without loading legacy fixtures or changing an active site.
"""

from __future__ import annotations

import importlib
from datetime import date
from operator import itemgetter
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, call, patch

import frappe

from . import exchange_rates, phases, validation
from .context import InsertionContext

# The repair module creates its site logger at import time.  Keep this unit
# suite independent of an active Bench site while retaining its real functions.
with patch.object(frappe, "logger", return_value=Mock()):
    reconcile = importlib.import_module(
        "karam_finance.reporting_currency.doctype.reporting_currency_gle.sync."
        "reconcile_gl_entry_links"
    )


def _raise_validation(message: str, **_kwargs: object) -> None:
    raise frappe.ValidationError(message)


class TestSyncPhases(TestCase):
    def test_validation_falls_back_to_full_sync_when_incremental_is_empty(self) -> None:
        database = Mock()
        database.count.return_value = 3
        database.exists.side_effect = [True, False]
        with (
            patch.object(phases, "frappe") as frappe_mock,
            patch.object(
                phases, "fetch_gl_entries", side_effect=[[], [{"company": "Karam"}]]
            ),
            patch.object(phases, "get_company_default_currency", return_value="USD"),
            patch.object(
                phases,
                "validate_currency_exchange_coverage",
                return_value={"direct": True},
            ),
            patch.object(phases, "publish_sync_progress"),
        ):
            frappe_mock.db = database
            result = phases.run_validation_phase("event", "user", "EUR", "cutoff")

        assert result == (
            [{"company": "Karam"}],
            "USD",
            False,
            "Full Sync (rebuild)",
            {"direct": True},
            None,
        )
        assert database.count.call_args == call("GL Entry", {"docstatus": 1})

    def test_validation_returns_early_without_source_rows(self) -> None:
        with (
            patch.object(phases, "fetch_gl_entries", return_value=[]),
            patch.object(phases, "publish_sync_progress") as progress,
        ):
            result = phases.run_validation_phase("event", None, "USD", None)
        assert result == ([], "", False, "Full Sync", {}, None)
        assert progress.call_args.args[1] == phases.PROGRESS_COMPLETE

    def test_validation_uses_foreground_snapshot_without_rechecking_rates(self) -> None:
        entries = [{"company": "Karam", "account_currency": "LBP"}]
        coverage = {"direct": True, "inverse": False}
        with (
            patch.object(phases, "fetch_gl_entries", return_value=entries),
            patch.object(
                phases, "validate_currency_exchange_coverage"
            ) as validate_rates,
            patch.object(phases, "get_company_default_currency") as currency,
            patch.object(phases, "publish_sync_progress"),
        ):
            result = phases.run_validation_phase(
                "event", None, "USD", None, coverage, "LBP"
            )
        assert result == (entries, "LBP", False, "Full Sync", coverage, None)
        validate_rates.assert_not_called()
        currency.assert_not_called()

    def test_deletion_keeps_orphan_cleanup_out_of_incremental_runs(self) -> None:
        with (
            patch.object(
                phases, "fetch_cancelled_gl_entries", return_value=[{"name": "GLE-1"}]
            ),
            patch.object(
                phases, "delete_rc_gle_for_cancelled_gl_entries", return_value=2
            ),
            patch.object(phases, "cleanup_orphaned_rc_gle_records") as cleanup,
            patch.object(phases, "publish_sync_progress"),
        ):
            result = phases.run_deletion_phase("event", None, "cutoff", True)
        assert result == {"deleted_cancelled": 2, "deleted_orphaned": 0}
        cleanup.assert_not_called()

    def test_deletion_full_sync_repairs_orphans_even_without_cancellations(
        self,
    ) -> None:
        with (
            patch.object(phases, "fetch_cancelled_gl_entries", return_value=[]),
            patch.object(phases, "cleanup_orphaned_rc_gle_records", return_value=4),
            patch.object(phases, "publish_sync_progress"),
        ):
            assert phases.run_deletion_phase("event", None, None, False) == {
                "deleted_cancelled": 0,
                "deleted_orphaned": 4,
            }

    def test_temporal_phase_validates_new_timeline_but_trusts_validated_snapshot(
        self,
    ) -> None:
        timeline = [{"date": date(2026, 1, 1), "rate": 1}]
        with (
            patch.object(
                phases, "build_exchange_rate_timeline", return_value=timeline
            ) as build,
            patch.object(phases, "validate_temporal_coverage") as validate_dates,
            patch.object(phases, "publish_sync_progress"),
        ):
            assert phases.run_temporal_reconciliation_phase(
                "event", None, [{"name": "GLE"}], "LBP", "USD", {"direct": True}
            ) == (timeline, [date(2026, 1, 1)])
            assert phases.run_temporal_reconciliation_phase(
                "event", None, [], "LBP", "USD", {}, timeline
            ) == (timeline, [date(2026, 1, 1)])
        build.assert_called_once()
        validate_dates.assert_called_once()

    def test_conversion_propagates_record_failure_before_any_insert(self) -> None:
        failure = RuntimeError("bad rate")
        with (
            patch.object(phases, "process_gl_entry", side_effect=failure),
            patch.object(phases, "publish_sync_progress"),
            self.assertRaisesRegex(RuntimeError, "bad rate"),
        ):
            phases.run_conversion_phase(
                "event", None, [{"name": "GLE-1"}], [], [], "LBP", "USD"
            )

    def test_incremental_insert_deletes_source_links_and_records_watermark(
        self,
    ) -> None:
        database = Mock()
        context = InsertionContext(is_incremental=True, cutoff="2026-09-09 10:00:00")
        with (
            patch.object(phases, "frappe") as frappe_mock,
            patch.object(phases, "_delete_incremental_records") as delete,
            patch.object(phases, "_insert_rc_gle_records", return_value=1),
            patch.object(phases, "publish_sync_progress"),
        ):
            frappe_mock.db = database
            result = phases.run_insertion_phase(
                "event", None, [{"gl_entry": "GLE-1"}], [{"name": "GLE-1"}], context
            )
        assert result == {"inserted": 1}
        delete.assert_called_once_with(["GLE-1"], "event", None)
        database.set_single_value.assert_called_once_with(
            "Reporting Currency Settings",
            {
                "last_sync_timestamp": context.cutoff,
                "last_ce_sync_timestamp": context.cutoff,
            },
            update_modified=False,
        )

    def test_full_insert_preserves_manual_rows_and_names_hash_links(self) -> None:
        database = Mock()
        record = {"gl_entry": "a1b2"}
        with (
            patch.object(phases, "frappe") as frappe_mock,
            patch.object(phases, "publish_sync_progress"),
        ):
            frappe_mock.db = database
            result = phases.run_insertion_phase(
                "event",
                None,
                [],
                [],
                InsertionContext(is_incremental=False, cutoff="t"),
            )
            phases._assign_rc_gle_name(record)
        assert result == {"inserted": 0}
        assert (
            "manual_entry = 0 OR manual_entry IS NULL" in database.sql.call_args.args[0]
        )
        assert record["name"] == "RC-a1b2"

    def test_default_currency_rejects_mixed_company_source(self) -> None:
        with (
            patch.object(phases, "frappe") as frappe_mock,
            self.assertRaises(frappe.ValidationError),
        ):
            frappe_mock.throw.side_effect = _raise_validation
            phases._default_currency_for_entries([{"company": "A"}, {"company": "B"}])

    def test_prepare_chunk_uses_first_record_schema_and_assigns_series_names(
        self,
    ) -> None:
        records = [
            {
                "doctype": "Reporting Currency GLE",
                "gl_entry": "ACC-GLE-2026-00007",
                "debit": 5,
            },
            {"doctype": "Reporting Currency GLE", "gl_entry": "hash", "debit": 6},
        ]
        fields = [key for key in records[0] if key != "doctype"]
        assert phases._prepare_insert_chunk(records, fields) == [
            ["ACC-GLE-2026-00007", 5],
            ["hash", 6],
        ]
        assert [row["name"] for row in records] == ["KE-RCGLE-2026-00007", "RC-hash"]

    def test_incremental_delete_uses_parameterised_source_names(self) -> None:
        database = Mock()
        with (
            patch.object(phases, "frappe") as frappe_mock,
            patch.object(phases, "publish_sync_progress") as progress,
        ):
            frappe_mock.db = database
            phases._delete_incremental_records(["GLE-1", "GLE-2"], "event", "user")
        sql, values = database.sql.call_args.args
        assert "gl_entry IN (%s, %s)" in sql
        assert values == ("GLE-1", "GLE-2")
        assert progress.call_args.args[1] == phases.PROGRESS_PHASE4_DELETE


class TestExchangeRatesAndValidation(TestCase):
    def test_empty_rate_coverage_avoids_currency_exchange_queries(self) -> None:
        database = Mock()
        with patch.object(exchange_rates, "frappe") as frappe_mock:
            frappe_mock.db = database
            assert (
                exchange_rates.build_exchange_rate_timeline(
                    "LBP", "USD", {"direct": False, "inverse": False}
                )
                == []
            )
        database.get_all.assert_not_called()

    def test_timeline_prefers_direct_rate_on_same_day_and_retains_inverse_history(
        self,
    ) -> None:
        database = Mock()
        database.get_all.side_effect = [
            [{"name": "direct", "date": "2026-02-01", "exchange_rate": 2}],
            [
                {"name": "inverse-old", "date": "2026-01-01", "exchange_rate": 4},
                {"name": "inverse-same", "date": "2026-02-01", "exchange_rate": 0.5},
            ],
        ]
        with patch.object(exchange_rates, "frappe") as frappe_mock:
            frappe_mock.db = database
            timeline = exchange_rates.build_exchange_rate_timeline(
                "LBP", "USD", {"direct": True, "inverse": True}
            )
        assert [(row["currency_exchange"], row["direction"]) for row in timeline] == [
            ("inverse-old", "inverse"),
            ("direct", "direct"),
        ]

    def test_temporal_coverage_skips_reporting_currency_rows_and_reports_prior_rows(
        self,
    ) -> None:
        earliest = date(2026, 1, 1)
        with patch.object(exchange_rates, "_throw_temporal_coverage_error") as throw:
            exchange_rates.validate_temporal_coverage(
                [
                    {
                        "name": "already-usd",
                        "account_currency": "USD",
                        "posting_date": "2025-01-01",
                    },
                    {
                        "name": "old",
                        "account_currency": "LBP",
                        "posting_date": "2025-12-31",
                        "account": "Bank",
                        "voucher_no": "JV-1",
                    },
                ],
                [{"date": earliest}],
                "LBP",
                "USD",
            )
        assert throw.call_args.args[0] == [
            {
                "gle": "old",
                "date": date(2025, 12, 31),
                "account": "Bank",
                "voucher": "JV-1",
            }
        ]

    def test_temporal_error_caches_large_sorted_failure_list_before_reporting(
        self,
    ) -> None:
        entries = [
            {
                "gle": f"GLE-{index}",
                "date": date(2025, 1, 22 - index),
                "voucher": "JV",
            }
            for index in range(21)
        ]
        cache = Mock()
        with patch.object(exchange_rates, "frappe") as frappe_mock:
            frappe_mock.cache = cache
            frappe_mock.generate_hash.return_value = "hash"
            exchange_rates._throw_temporal_coverage_error(
                entries, date(2026, 1, 1), "LBP", reporting_currency="USD"
            )
        cache.set_value.assert_called_once_with(
            "temporal_validation_entries_hash",
            sorted(entries, key=itemgetter("date")),
            expires_in_sec=300,
        )
        (message,) = frappe_mock.throw.call_args.args
        assert "Download Full List (CSV) - 21 entries" in message
        assert (
            "GL Entries Precede Exchange Rate Data"
            in frappe_mock.throw.call_args.kwargs["title"]
        )

        cache.reset_mock()
        with patch.object(exchange_rates, "frappe") as below_threshold:
            exchange_rates._throw_temporal_coverage_error(
                sorted(entries, key=itemgetter("date"))[:20],
                date(2026, 1, 1),
                "LBP",
                reporting_currency="USD",
            )
        below_threshold.cache.set_value.assert_not_called()

    def test_pre_timeline_lookup_fails_closed(self) -> None:
        with (
            patch.object(exchange_rates, "frappe") as frappe_mock,
            self.assertRaises(frappe.ValidationError),
        ):
            frappe_mock.throw.side_effect = _raise_validation
            exchange_rates.get_applicable_rate(
                "2025-01-01",
                "LBP",
                "USD",
                [{"date": date(2026, 1, 1)}],
                [date(2026, 1, 1)],
            )

    def test_rate_lookup_caches_a_parsed_string_date_and_uses_latest_rate(self) -> None:
        timeline = [
            {"date": date(2026, 1, 1), "rate": 2, "currency_exchange": "CE-1"},
            {"date": date(2026, 2, 1), "rate": 3, "currency_exchange": "CE-2"},
        ]
        cache: dict[str, date] = {}
        result = exchange_rates.get_applicable_rate(
            "2026-02-15",
            "LBP",
            "USD",
            timeline,
            [date(2026, 1, 1), date(2026, 2, 1)],
            cache,
        )
        assert cache == {"2026-02-15": date(2026, 2, 15)}
        assert result is timeline[1]

    def test_rate_lookup_reuses_a_supplied_cached_date(self) -> None:
        timeline = [
            {"date": date(2026, 1, 1), "rate": 2, "currency_exchange": "CE-1"},
            {"date": date(2026, 2, 1), "rate": 3, "currency_exchange": "CE-2"},
        ]
        with patch.object(exchange_rates, "_required_rate_date") as parse:
            result = exchange_rates.get_applicable_rate(
                "2026-02-15",
                "LBP",
                "USD",
                timeline,
                [date(2026, 1, 1), date(2026, 2, 1)],
                {"2026-02-15": date(2026, 2, 15)},
            )
        assert result is timeline[1]
        parse.assert_not_called()

    def test_rate_lookup_accepts_a_date_object_without_parsing(self) -> None:
        timeline = [{"date": date(2026, 1, 1), "rate": 2}]
        with patch.object(exchange_rates, "_required_rate_date") as parse:
            result = exchange_rates.get_applicable_rate(
                date(2026, 1, 2),
                "LBP",
                "USD",
                timeline,
                [date(2026, 1, 1)],
            )
        assert result is timeline[0]
        parse.assert_not_called()

    def test_temporal_coverage_returns_for_empty_timeline_or_entries_and_skips_reporting_currency(
        self,
    ) -> None:
        with patch.object(exchange_rates, "_throw_temporal_coverage_error") as throw:
            exchange_rates.validate_temporal_coverage(
                [{"posting_date": None}], [], "LBP", "USD"
            )
            exchange_rates.validate_temporal_coverage(
                [], [{"date": date(2026, 1, 1)}], "LBP", "USD"
            )
            exchange_rates.validate_temporal_coverage(
                [{"account_currency": "USD", "posting_date": None}],
                [{"date": date(2026, 1, 1)}],
                "LBP",
                "USD",
            )
            exchange_rates.validate_temporal_coverage(
                [{"account_currency": "LBP", "posting_date": "2026-01-02"}],
                [{"date": date(2026, 1, 1)}],
                "LBP",
                "USD",
            )
        throw.assert_not_called()

    def test_coverage_avoids_database_when_every_row_is_reporting_currency(
        self,
    ) -> None:
        with patch.object(validation, "frappe") as frappe_mock:
            assert validation.validate_currency_exchange_coverage(
                [{"account_currency": "USD"}, {"account_currency": None}], "USD", "LBP"
            ) == {"direct": False, "inverse": False}
        frappe_mock.db.exists.assert_not_called()

    def test_coverage_accepts_inverse_only_and_rejects_missing_pair(self) -> None:
        database = Mock()
        database.exists.side_effect = [False, "CE-inverse"]
        with patch.object(validation, "frappe") as frappe_mock:
            frappe_mock.db = database
            assert validation.validate_currency_exchange_coverage(
                [{"account_currency": "EUR"}], "USD", "LBP"
            ) == {"direct": False, "inverse": True}

        database.exists.side_effect = [False, False]
        with (
            patch.object(validation, "_throw_missing_exchange_error") as missing,
            patch.object(validation, "frappe") as frappe_mock,
        ):
            frappe_mock.db = database
            validation.validate_currency_exchange_coverage(
                [{"name": "GLE", "account_currency": "EUR"}], "USD", "LBP"
            )
        assert missing.call_args.args[1] == {"EUR"}

    def test_settings_rejects_missing_currency_or_parameters_and_returns_snapshot(
        self,
    ) -> None:
        query = Mock()
        query.select.return_value = query
        query.where.return_value = query
        query.distinct.return_value = query
        query.limit.return_value = query
        query.run.return_value = ["Karam"]
        frappe_mock = Mock()
        frappe_mock.qb.DocType.return_value = Mock()
        frappe_mock.qb.from_.return_value = query
        frappe_mock.throw.side_effect = _raise_validation
        with patch.object(validation, "frappe", frappe_mock):
            frappe_mock.get_single.return_value = SimpleNamespace(
                reporting_currency=None, rc_parameters=["row"], last_sync_timestamp="t"
            )
            with self.assertRaises(frappe.ValidationError):
                validation.validate_settings()
            frappe_mock.get_single.return_value = SimpleNamespace(
                reporting_currency="USD", rc_parameters=[], last_sync_timestamp="t"
            )
            with self.assertRaises(frappe.ValidationError):
                validation.validate_settings()
            parameters = [SimpleNamespace(name="rate")]
            frappe_mock.get_single.return_value = SimpleNamespace(
                reporting_currency="USD",
                rc_parameters=parameters,
                last_sync_timestamp="t",
            )
            assert validation.validate_settings() == {
                "reporting_currency": "USD",
                "last_sync_timestamp": "t",
                "rc_parameters": parameters,
            }

    def test_company_currency_requires_configuration(self) -> None:
        database = Mock()
        database.get_value.return_value = None
        with (
            patch.object(validation, "frappe") as frappe_mock,
            self.assertRaises(frappe.ValidationError),
        ):
            frappe_mock.db = database
            frappe_mock.throw.side_effect = _raise_validation
            validation.get_company_default_currency("Karam")

    def test_site_wide_settings_and_doe_company_reject_ambiguous_companies(
        self,
    ) -> None:
        query = Mock()
        query.select.return_value = query
        query.where.return_value = query
        query.distinct.return_value = query
        query.limit.return_value = query
        query.run.return_value = ["A", "B"]
        frappe_mock = Mock()
        frappe_mock.qb.DocType.return_value = Mock()
        frappe_mock.qb.from_.return_value = query
        frappe_mock.throw.side_effect = _raise_validation
        with (
            patch.object(validation, "frappe", frappe_mock),
            self.assertRaises(frappe.ValidationError),
        ):
            validation.get_reporting_company()


class TestLinkReconciliation(TestCase):
    def test_reconciliation_returns_immediately_without_orphans_or_writes(self) -> None:
        database = Mock()
        with (
            patch.object(reconcile, "_get_orphaned_entries", return_value=[]),
            patch.object(reconcile, "_get_gl_hash_index") as hash_index,
            patch.object(reconcile, "frappe") as frappe_mock,
        ):
            frappe_mock.db = database
            assert reconcile.reconcile_gl_entry_links() == {
                "status": "success",
                "fixed": 0,
                "errors": 0,
            }
        hash_index.assert_not_called()
        database.sql.assert_not_called()
        database.commit.assert_not_called()

    def test_orphan_hash_forwards_exact_gl_identity_fields(self) -> None:
        orphan = SimpleNamespace(
            voucher_type="Journal Entry",
            voucher_no="JV-001",
            account="Bank - K",
            posting_date="2026-02-01",
            debit=12.34,
            credit=5.67,
        )
        with patch.object(
            reconcile, "get_gl_entry_stable_hash", return_value="hash"
        ) as hash_entry:
            assert reconcile._orphan_hash(orphan) == "hash"
        hash_entry.assert_called_once_with(
            {
                "voucher_type": "Journal Entry",
                "voucher_no": "JV-001",
                "account": "Bank - K",
                "posting_date": "2026-02-01",
                "debit": 12.34,
                "credit": 5.67,
            }
        )

    def test_repair_reads_orphans_hashes_every_source_row_and_commits_requested_result(
        self,
    ) -> None:
        orphan = SimpleNamespace(
            rc_name="RC-1",
            gl_entry_hash="h",
            voucher_type="JV",
            voucher_no="1",
            account="Bank",
            posting_date="2026-01-01",
            debit=1,
            credit=0,
        )
        database = Mock()
        database.sql.side_effect = [
            [orphan],
            [
                SimpleNamespace(
                    name="GLE-1",
                    voucher_type="JV",
                    voucher_no="1",
                    account="Bank",
                    posting_date="2026-01-01",
                    debit=1,
                    credit=0,
                )
            ],
        ]
        with (
            patch.object(reconcile, "frappe") as frappe_mock,
            patch.object(reconcile, "get_gl_entry_stable_hash", return_value="h"),
            patch.object(reconcile, "_reconcile_orphan", return_value=True),
        ):
            frappe_mock.db = database
            assert reconcile.reconcile_gl_entry_links() == {
                "status": "success",
                "fixed": 1,
                "errors": 0,
                "total": 1,
            }
        assert "LEFT JOIN `tabGL Entry`" in database.sql.call_args_list[0].args[0]
        database.commit.assert_called_once()

    def test_hash_index_groups_legitimate_duplicates(self) -> None:
        database = Mock()
        database.sql.return_value = [
            SimpleNamespace(name="GLE-A"),
            SimpleNamespace(name="GLE-B"),
        ]
        with (
            patch.object(reconcile, "frappe") as frappe_mock,
            patch.object(
                reconcile, "get_gl_entry_stable_hash", side_effect=["same", "same"]
            ),
            patch.object(reconcile, "_log_hash_collisions") as log,
        ):
            frappe_mock.db = database
            assert reconcile._get_gl_hash_index() == {"same": ["GLE-A", "GLE-B"]}
        log.assert_called_once_with({"same": ["GLE-A", "GLE-B"]})

    def test_dry_run_reconciles_unique_match_without_write_or_commit(self) -> None:
        orphan = SimpleNamespace(
            rc_name="RC-1",
            gl_entry_hash="h",
            voucher_type="JV",
            voucher_no="1",
            account="Bank",
            posting_date="2026-01-01",
            debit=1,
            credit=0,
        )
        database = Mock()
        with (
            patch.object(reconcile, "_get_orphaned_entries", return_value=[orphan]),
            patch.object(
                reconcile, "_get_gl_hash_index", return_value={"h": ["GLE-1"]}
            ),
            patch.object(reconcile, "frappe") as frappe_mock,
        ):
            frappe_mock.db = database
            assert reconcile.reconcile_gl_entry_links(dry_run=True) == {
                "status": "success",
                "fixed": 1,
                "errors": 0,
                "total": 1,
            }
        database.sql.assert_not_called()
        database.commit.assert_not_called()

    def test_reconciliation_reports_ambiguous_and_exceptional_orphans(self) -> None:
        ambiguous = SimpleNamespace(
            rc_name="RC-A",
            gl_entry_hash="h",
            voucher_type="JV",
            voucher_no="1",
            account="Bank",
            posting_date="2026-01-01",
            debit=1,
            credit=0,
        )
        broken = SimpleNamespace(
            rc_name="RC-B",
            gl_entry_hash="boom",
            voucher_type="JV",
            voucher_no="2",
            account="Bank",
            posting_date="2026-01-01",
            debit=1,
            credit=0,
        )
        with (
            patch.object(
                reconcile, "_get_orphaned_entries", return_value=[ambiguous, broken]
            ),
            patch.object(
                reconcile, "_get_gl_hash_index", return_value={"h": ["GLE-1", "GLE-2"]}
            ),
            patch.object(
                reconcile, "_reconcile_orphan", side_effect=[False, RuntimeError("db")]
            ),
            patch.object(reconcile, "frappe") as frappe_mock,
        ):
            frappe_mock.db = Mock()
            assert reconcile.reconcile_gl_entry_links(commit=False) == {
                "status": "partial",
                "fixed": 0,
                "errors": 2,
                "total": 2,
            }
            frappe_mock.db.commit.assert_not_called()

    def test_repair_helper_writes_only_a_unique_match(self) -> None:
        orphan = SimpleNamespace(
            rc_name="RC-1",
            gl_entry_hash=None,
            voucher_type="JV",
            voucher_no="1",
            account="Bank",
            posting_date="2026-01-01",
            debit=1,
            credit=0,
        )
        database = Mock()
        with (
            patch.object(reconcile, "_orphan_hash", return_value="stable"),
            patch.object(reconcile, "frappe") as frappe_mock,
            patch.object(reconcile, "now", return_value="now"),
        ):
            frappe_mock.db = database
            assert reconcile._reconcile_orphan(orphan, {"stable": ["GLE-9"]}, False)
        assert database.sql.call_args.args[1] == ("GLE-9", "stable", "now", "RC-1")

    def test_repair_helper_refuses_missing_and_ambiguous_matches_without_writing(
        self,
    ) -> None:
        orphan = SimpleNamespace(
            rc_name="RC-1",
            gl_entry_hash="stable",
            voucher_type="JV",
            voucher_no="1",
            account="Bank",
            posting_date="2026-01-01",
            debit=1,
            credit=0,
        )
        database = Mock()
        with patch.object(reconcile, "frappe") as frappe_mock:
            frappe_mock.db = database
            assert not reconcile._reconcile_orphan(orphan, {}, False)
            assert not reconcile._reconcile_orphan(
                orphan, {"stable": ["GLE-1", "GLE-2"]}, False
            )
        database.sql.assert_not_called()

    def test_collision_logger_reports_only_colliding_hashes_and_first_five_names(
        self,
    ) -> None:
        logger = Mock()
        collisions = {
            f"h-{index}": [f"GLE-{index}-a", f"GLE-{index}-b"] for index in range(6)
        }
        with patch.object(reconcile, "_logger", logger):
            reconcile._log_hash_collisions(collisions | {"unique": ["GLE-unique"]})
        assert logger.warning.call_count == 6
        assert logger.warning.call_args_list[0].args[1:3] == (6, 12)
        assert logger.warning.call_args_list[-1].args[1] == "h-4"
