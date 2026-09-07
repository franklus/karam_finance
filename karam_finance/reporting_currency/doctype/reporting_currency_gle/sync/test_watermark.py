"""Regression coverage for source-acquisition sync watermarks."""

from __future__ import annotations

from operator import eq, gt
from typing import Any, ClassVar, cast
from unittest.mock import Mock, patch

from frappe.tests.utils import FrappeTestCase

from . import context, data_fetch, orchestrator, phases


class _FakeColumn:
    __hash__: ClassVar[None] = None

    def __init__(self, name: str, conditions: list[tuple[str, str, object]]) -> None:
        self.name = name
        self.conditions = conditions

    def __eq__(self, value: object) -> bool:
        self.conditions.append(("eq", self.name, value))
        return True

    def __gt__(self, value: object) -> bool:
        self.conditions.append(("gt", self.name, value))
        return True


class _FakeTable:
    def __init__(self, conditions: list[tuple[str, str, object]]) -> None:
        self.conditions = conditions

    def __getitem__(self, name: str) -> _FakeColumn:
        return _FakeColumn(name, self.conditions)

    def __getattr__(self, name: str) -> _FakeColumn:
        return _FakeColumn(name, self.conditions)


class _FakeQuery:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.conditions: list[tuple[str, str, object]] = []

    def select(self, *_fields: object) -> _FakeQuery:
        return self

    def where(self, _condition: bool) -> _FakeQuery:
        return self

    def orderby(self, *_fields: object) -> _FakeQuery:
        return self

    def run(self, **_kwargs: object) -> list[dict[str, Any]]:
        rows = self.rows
        for operator, field, value in self.conditions:
            comparator = {"eq": eq, "gt": gt}.get(operator)
            if comparator:
                rows = [row for row in rows if comparator(row[field], cast(Any, value))]
        return rows


class _FakeQueryBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.query = _FakeQuery(rows)
        self.conditions = self.query.conditions

    def DocType(self, _doctype: str) -> _FakeTable:
        return _FakeTable(self.conditions)

    def from_(self, _table: _FakeTable) -> _FakeQuery:
        return self.query


class TestReportingCurrencySyncWatermark(FrappeTestCase):
    """A run must watermark its acquisition boundary, never completion."""

    def test_insertion_persists_acquisition_cutoff(self) -> None:
        frappe_mock = Mock()
        with (
            patch.object(phases, "frappe", frappe_mock),
            patch.object(phases, "publish_sync_progress"),
        ):
            stats = phases.run_insertion_phase(
                "event",
                "Administrator",
                [],
                [],
                context.InsertionContext(
                    is_incremental=False,
                    cutoff="2026-09-07 12:00:00.123456",
                ),
            )

        assert stats == {"inserted": 0}
        frappe_mock.db.set_single_value.assert_called_once_with(
            phases.DOCTYPE_RC_SETTINGS,
            {
                "last_sync_timestamp": "2026-09-07 12:00:00.123456",
                "last_ce_sync_timestamp": "2026-09-07 12:00:00.123456",
            },
            update_modified=False,
        )

    def test_uncut_cached_snapshot_is_discarded_at_worker_boundary(self) -> None:
        gl_entries = [{"name": "GLE-1"}]
        with (
            patch.object(
                orchestrator, "now", return_value="2026-09-07 12:00:00.123456"
            ),
            patch.object(
                orchestrator,
                "validate_settings",
                return_value={
                    "reporting_currency": "USD",
                    "last_sync_timestamp": "2026-09-07 11:00:00.000000",
                },
            ),
            patch.object(
                orchestrator,
                "run_validation_phase",
                return_value=(
                    gl_entries,
                    "USD",
                    True,
                    "Incremental Sync",
                    {},
                    "2026-09-07 11:00:00.000000",
                ),
            ) as validation,
            patch.object(orchestrator, "run_deletion_phase", return_value={}),
            patch.object(
                orchestrator,
                "run_temporal_reconciliation_phase",
                return_value=([], []),
            ),
            patch.object(orchestrator, "run_conversion_phase", return_value=[]),
            patch.object(
                orchestrator,
                "run_insertion_phase",
                return_value={"inserted": 0},
            ) as insertion,
            patch.object(orchestrator, "publish_sync_progress"),
        ):
            orchestrator.sync_reporting_currency_entries(
                "event",
                cached_currency_coverage={"direct": True},
                cached_rate_timeline=[{"date": "2026-01-01"}],
                cached_default_currency="USD",
            )

        assert validation.call_args.args[4] is None
        assert validation.call_args.args[5] is None
        assert insertion.call_args.args[4].cutoff == ("2026-09-07 12:00:00.123456")

    def test_enqueue_carries_cutoff_taken_before_cached_rate_snapshot(self) -> None:
        events: list[str] = []
        frappe_mock = Mock()
        frappe_mock.has_permission.return_value = True
        frappe_mock.session.user = "Administrator"
        frappe_mock.generate_hash.return_value = "event-hash"
        frappe_mock.enqueue.return_value = Mock(id="job-1")

        def sql(query: str, *args: object, **kwargs: object) -> list[object]:
            del args, kwargs
            events.append("gl_read")
            if "COUNT" in query:
                return [(1,)]
            return [
                {
                    "name": "GLE-1",
                    "posting_date": "2026-01-01",
                    "account": "Receivable",
                    "account_currency": "USD",
                    "voucher_no": "SI-1",
                    "company": "Karam",
                }
            ]

        frappe_mock.db.sql.side_effect = sql

        with (
            patch.object(orchestrator, "frappe", frappe_mock),
            patch.object(
                orchestrator,
                "now",
                side_effect=lambda: (
                    events.append("cutoff") or "2026-09-07 12:00:00.123456"
                ),
            ),
            patch.object(
                orchestrator,
                "validate_settings",
                side_effect=lambda: (
                    events.append("settings")
                    or {
                        "reporting_currency": "EUR",
                        "last_sync_timestamp": None,
                    }
                ),
            ),
            patch.object(
                orchestrator,
                "get_company_default_currency",
                return_value="USD",
            ),
            patch.object(
                orchestrator,
                "validate_currency_exchange_coverage",
                return_value={"direct": True},
            ),
            patch.object(
                orchestrator,
                "build_exchange_rate_timeline",
                return_value=[{"date": "2026-01-01", "rate": 1}],
            ),
            patch.object(orchestrator, "validate_temporal_coverage"),
        ):
            result = orchestrator.enqueue_reporting_currency_sync()

        assert result["job_id"] == "job-1"
        assert events[:2] == ["cutoff", "settings"]
        snapshot = frappe_mock.enqueue.call_args.kwargs["cached_currency_coverage"]
        assert isinstance(snapshot, orchestrator.SyncSnapshot)
        assert snapshot.cutoff == "2026-09-07 12:00:00.123456"
        assert snapshot.reporting_currency == "EUR"
        assert snapshot.rate_timeline == [{"date": "2026-01-01", "rate": 1}]

    def test_next_incremental_includes_row_modified_after_cutoff(self) -> None:
        """A row changed during a sync remains eligible for its next run."""
        cutoff = "2026-09-07 12:00:00.100000"
        rows: list[dict[str, Any]] = [
            {"name": "GLE-BEFORE", "docstatus": 1, "modified": cutoff},
            {
                "name": "GLE-DURING",
                "docstatus": 1,
                "modified": "2026-09-07 12:00:00.200000",
            },
        ]
        query_builder = _FakeQueryBuilder(rows)

        with patch.object(data_fetch.frappe, "qb", query_builder):
            result = data_fetch.fetch_gl_entries(last_sync_timestamp=cutoff)

        assert [row["name"] for row in result] == ["GLE-DURING"]
        assert ("gt", "modified", cutoff) in query_builder.query.conditions
