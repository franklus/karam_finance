"""Read-only comparative benchmark for the Bank Reconciliation Statement.

Import the module from a bench console or a project-local runner and call
``benchmark``, ``profile``, or ``benchmark_stages`` with read-only report
filters.  The module is intentionally not exposed as a ``bench execute``
method path because ``tools`` is not an application package.

The benchmark only observes report execution. It does not write documents,
change site settings, migrate, or persist benchmark data.
"""

from __future__ import annotations

import copy
import importlib
import statistics
import time
from collections.abc import Callable
from operator import itemgetter
from types import ModuleType
from typing import Any, cast

import frappe

_KARAM_REPORT = (
    "karam_finance.karam_general.report.bank_reconciliation_statement_(karam)."
    "bank_reconciliation_statement_(karam)"
)


def benchmark(  # noqa: V103 - documented interactive developer-tool entry point.
    filters: dict[str, Any], *, repeats: int = 15, warmups: int = 3
) -> dict[str, Any]:
    """Compare equivalent upstream and Karam report executions."""
    if repeats < 1 or warmups < 0:
        message = "repeats must be positive and warmups cannot be negative"
        raise ValueError(message)

    upstream = importlib.import_module(
        "erpnext.accounts.report.bank_reconciliation_statement."
        "bank_reconciliation_statement"
    )
    karam = importlib.import_module(_KARAM_REPORT)
    reports: dict[str, Callable[..., Any]] = {
        "upstream_v16": upstream.execute,
        "karam": karam.execute,
    }
    samples: dict[str, list[dict[str, Any]]] = {name: [] for name in reports}

    for report in reports.values():
        for _ in range(warmups):
            report(frappe._dict(copy.deepcopy(filters)))

    for _ in range(repeats):
        for name, report in reports.items():
            samples[name].append(_sample(report, filters))

    return {
        "filters": dict(filters),
        "repeats": repeats,
        "warmups": warmups,
        "reports": {
            name: _summarise(report_samples) for name, report_samples in samples.items()
        },
    }


def profile(  # noqa: V103 - documented interactive developer-tool entry point.
    filters: dict[str, Any], report_name: str = "karam"
) -> list[dict[str, Any]]:
    """Return one report's SQL timings without changing database state."""
    reports = {
        "upstream_v16": importlib.import_module(
            "erpnext.accounts.report.bank_reconciliation_statement."
            "bank_reconciliation_statement"
        ).execute,
        "karam": importlib.import_module(_KARAM_REPORT).execute,
    }
    report = reports[report_name]
    timings: list[dict[str, Any]] = []
    original_sql = frappe.db.sql

    def timing_sql(
        *args: Any, sql: Callable[..., Any] = original_sql, **kwargs: Any
    ) -> Any:
        started = time.perf_counter()
        result = sql(*args, **kwargs)
        timings.append(
            {
                "wall_time_ms": round((time.perf_counter() - started) * 1000, 3),
                "rows": len(result) if isinstance(result, list) else None,
                "sql": " ".join(str(args[0]).split())[:300],
            }
        )
        return result

    frappe.db.sql = cast(Any, timing_sql)
    try:
        report(frappe._dict(copy.deepcopy(filters)))
    finally:
        frappe.db.sql = original_sql
    return sorted(timings, key=itemgetter("wall_time_ms"), reverse=True)


def profile_stages(filters: dict[str, Any]) -> dict[str, Any]:
    """Profile report stages and SQL timings for one read-only execution."""
    upstream = importlib.import_module(
        "erpnext.accounts.report.bank_reconciliation_statement."
        "bank_reconciliation_statement"
    )
    karam = importlib.import_module(_KARAM_REPORT)
    reports = {
        "upstream_v16": upstream,
        "karam": karam,
    }
    result: dict[str, Any] = {}
    for name, module in reports.items():
        result[name] = _profile_report_stages(module, name, filters)
    return result


def benchmark_stages(  # noqa: V103 - documented interactive developer-tool entry point.
    filters: dict[str, Any], *, repeats: int = 10, warmups: int = 3
) -> dict[str, Any]:
    """Summarise repeated interleaved stage timings for both reports."""
    upstream = importlib.import_module(
        "erpnext.accounts.report.bank_reconciliation_statement."
        "bank_reconciliation_statement"
    ).execute
    karam = importlib.import_module(_KARAM_REPORT).execute
    reports = (upstream, karam)
    for _ in range(warmups):
        for report in reports:
            report(frappe._dict(copy.deepcopy(filters)))

    observations: dict[str, list[dict[str, Any]]] = {"upstream_v16": [], "karam": []}
    for _ in range(repeats):
        current = profile_stages(filters)
        observations["upstream_v16"].append(current["upstream_v16"])
        observations["karam"].append(current["karam"])

    summary = {}
    for name, samples in observations.items():
        summary[name] = _summarise_stages(samples)
    return {
        "filters": dict(filters),
        "repeats": repeats,
        "warmups": warmups,
        "reports": summary,
    }


def _stage_targets(module: ModuleType, report_name: str) -> list[tuple[Any, str]]:
    if report_name == "upstream_v16":
        names = (
            "get_entries",
            "get_journal_entries",
            "get_payment_entries",
            "get_purchase_invoices",
            "get_pos_entries",
            "get_balance_on",
            "get_amounts_not_reflected_in_system",
            "get_amounts_not_reflected_in_system_for_bank_reconciliation_statement",
        )
        return [(module, name) for name in names]

    targets = [(module, "get_entries"), (module, "get_balance_on")]
    targets.extend(
        (module, name)
        for name in (
            "get_amounts_not_reflected_in_system",
            "get_journal_entries",
            "get_payment_entries",
            "get_purchase_invoices",
            "get_pos_entries",
        )
    )
    for child_name, names in {
        "brs_aggregation": (
            "get_entries",
            "get_amounts_not_reflected_in_system",
        ),
        "brs_queries": (
            "get_entries_for_bank_reconciliation_statement",
            "get_amounts_not_reflected_in_system",
            "get_balance_on",
            "get_journal_entries",
            "get_payment_entries",
            "get_purchase_invoices",
            "get_pos_entries",
        ),
        "brs_enrichment": ("enrich_je_party", "populate_missing_party_names"),
    }.items():
        child = getattr(module, child_name, None)
        if child:
            targets.extend((child, name) for name in names)
    return targets


def _sample(report: Callable[..., Any], filters: dict[str, Any]) -> dict[str, Any]:
    query_count = 0
    materialised_rows = 0
    original_sql = frappe.db.sql

    def counting_sql(
        *args: Any, sql: Callable[..., Any] = original_sql, **kwargs: Any
    ) -> Any:
        nonlocal query_count, materialised_rows
        result = sql(*args, **kwargs)
        query_count += 1
        if isinstance(result, list):
            materialised_rows += len(result)
        return result

    frappe.db.sql = cast(Any, counting_sql)
    started = time.perf_counter()
    try:
        _columns, rows = report(frappe._dict(copy.deepcopy(filters)))
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        frappe.db.sql = original_sql

    source_rows = sum(1 for row in rows if row.get("payment_document"))
    return {
        "wall_time_ms": round(elapsed_ms, 3),
        "query_count": query_count,
        "materialised_rows": materialised_rows,
        "source_rows": source_rows,
        "output_rows": len(rows),
    }


def _summarise(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "samples": samples,
        "median_wall_time_ms": round(
            statistics.median(sample["wall_time_ms"] for sample in samples), 3
        ),
        "median_query_count": statistics.median(
            sample["query_count"] for sample in samples
        ),
        "median_materialised_rows": statistics.median(
            sample["materialised_rows"] for sample in samples
        ),
        "median_source_rows": statistics.median(
            sample["source_rows"] for sample in samples
        ),
    }


def _timed_stage(
    original: Callable[..., Any], stage_name: str, timings: list[dict[str, Any]]
) -> Callable[..., Any]:
    def timed_stage(
        *args: Any,
        _stage: Callable[..., Any] = original,
        _stage_name=stage_name,
        _timings=timings,
        **kwargs: Any,
    ) -> Any:
        started = time.perf_counter()
        value = _stage(*args, **kwargs)
        _timings.append(
            {
                "stage": _stage_name,
                "wall_time_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        return value

    return timed_stage


def _profile_report_stages(
    module: ModuleType, name: str, filters: dict[str, Any]
) -> dict[str, Any]:
    report = module.execute
    stage_timings: list[dict[str, Any]] = []
    originals: list[tuple[Any, str, Any]] = []
    stage_targets = _stage_targets(module, name)
    for target, stage_name in stage_targets:
        original = getattr(target, stage_name, None)
        if original is None:
            continue

        timed_stage = _timed_stage(original, stage_name, stage_timings)

        setattr(target, stage_name, timed_stage)
        originals.append((target, stage_name, original))

    query_timings: list[dict[str, Any]] = []
    original_sql = frappe.db.sql

    def timing_sql(
        *args: Any,
        sql: Callable[..., Any] = original_sql,
        _query_timings: list[dict[str, Any]] = query_timings,
        **kwargs: Any,
    ) -> Any:
        started = time.perf_counter()
        rows = sql(*args, **kwargs)
        _query_timings.append(
            {
                "wall_time_ms": round((time.perf_counter() - started) * 1000, 3),
                "rows": len(rows) if isinstance(rows, list) else None,
                "sql": " ".join(str(args[0]).split())[:260],
            }
        )
        return rows

    frappe.db.sql = cast(Any, timing_sql)
    started = time.perf_counter()
    try:
        _columns, rows = report(frappe._dict(copy.deepcopy(filters)))
    finally:
        total_ms = (time.perf_counter() - started) * 1000
        frappe.db.sql = original_sql
        for target, stage_name, original in reversed(originals):
            setattr(target, stage_name, original)
    return {
        "total_wall_time_ms": round(total_ms, 3),
        "source_rows": sum(1 for row in rows if row.get("payment_document")),
        "output_rows": len(rows),
        "stages": stage_timings,
        "queries": sorted(
            query_timings,
            key=itemgetter("wall_time_ms"),
            reverse=True,
        ),
    }


def _summarise_stages(samples: list[dict[str, Any]]) -> dict[str, Any]:
    stage_values: dict[str, list[float]] = {}
    query_values: dict[str, list[float]] = {}
    for sample in samples:
        for stage in sample["stages"]:
            stage_values.setdefault(stage["stage"], []).append(stage["wall_time_ms"])
        for query in sample["queries"]:
            query_values.setdefault(query["sql"], []).append(query["wall_time_ms"])
    return {
        "median_total_ms": round(
            statistics.median(sample["total_wall_time_ms"] for sample in samples),
            3,
        ),
        "median_source_rows": statistics.median(
            sample["source_rows"] for sample in samples
        ),
        "median_output_rows": statistics.median(
            sample["output_rows"] for sample in samples
        ),
        "stage_medians_ms": {
            stage: round(statistics.median(values), 3)
            for stage, values in stage_values.items()
        },
        "slowest_query_medians_ms": [
            {
                "median_ms": round(statistics.median(values), 3),
                "sql": sql,
            }
            for sql, values in sorted(
                query_values.items(),
                key=_query_median,
                reverse=True,
            )[:10]
        ],
    }


def _query_median(item: tuple[str, list[float]]) -> float:
    return statistics.median(item[1])
