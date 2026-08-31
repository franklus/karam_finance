"""Read-only comparative benchmark for ERPNext and Karam General Ledger.

Run from the bench Python environment after adding the app root to ``sys.path``,
or import it from an interactive bench console. The module is a developer tool,
not an installed whitelisted method.

The function never writes documents, changes settings, migrates, or persists
benchmark data. It reports wall time, SQL call count, rows materialised by
those calls, output rows, and peak Python allocation for repeated runs.
"""

from __future__ import annotations

import copy
import cProfile
import io
import pstats
import statistics
import time
import tracemalloc
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

import frappe


def benchmark(
    filters: dict[str, Any],
    repeats: int = 5,
    warmups: int = 1,
    include_query_timings: bool = False,
    include_stage_timings: bool = False,
) -> dict[str, Any]:
    """Compare identical read-only filters against both report pipelines."""
    if repeats < 1 or warmups < 0:
        raise ValueError("repeats must be positive and warmups cannot be negative")

    import importlib

    import erpnext.accounts.report.general_ledger.general_ledger as upstream

    karam = importlib.import_module(
        "karam_finance.karam_general.report.general_ledger_(karam).general_ledger_(karam)"
    )
    reports: dict[str, Callable] = {
        "upstream_v16": upstream.execute,
        "karam": karam.execute,
    }

    results = {
        name: _benchmark_report(
            report,
            _filters_for_report(filters, name),
            repeats,
            warmups,
            include_query_timings,
            include_stage_timings,
        )
        for name, report in reports.items()
    }
    return {
        "filters": dict(filters),
        "repeats": repeats,
        "warmups": warmups,
        "reports": results,
    }


def profile(
    filters: dict[str, Any], report_name: str = "karam", limit: int = 30
) -> str:
    """Return a cProfile report for one read-only report execution."""
    import importlib

    reports = {
        "upstream_v16": importlib.import_module(
            "erpnext.accounts.report.general_ledger.general_ledger"
        ).execute,
        "karam": importlib.import_module(
            "karam_finance.karam_general.report.general_ledger_(karam).general_ledger_(karam)"
        ).execute,
    }
    try:
        report = reports[report_name]
    except KeyError as exc:
        raise ValueError(f"Unknown report: {report_name}") from exc

    profiler = cProfile.Profile()
    profiler.enable()
    try:
        report(frappe._dict(copy.deepcopy(filters)))
    finally:
        profiler.disable()

    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream).sort_stats("cumtime")
    stats.print_stats(limit)
    return stream.getvalue()


def compare_outputs(
    filters: dict[str, Any], *, joined_voucher_data: bool = False
) -> dict[str, Any]:
    """Compare logical upstream/Karam rows while ignoring approved extensions."""
    import importlib

    upstream = importlib.import_module(
        "erpnext.accounts.report.general_ledger.general_ledger"
    )
    karam = importlib.import_module(
        "karam_finance.karam_general.report.general_ledger_(karam).general_ledger_(karam)"
    )
    values = copy.deepcopy(filters)
    if joined_voucher_data:
        values["_join_karam_voucher_data"] = 1
        values["_join_karam_voucher_union"] = 1

    _upstream_columns, upstream_rows = upstream.execute(
        frappe._dict(_filters_for_report(values, "upstream_v16"))
    )
    _karam_columns, karam_rows = karam.execute(frappe._dict(values))

    upstream_rows = _logical_rows(upstream_rows)
    karam_rows = _logical_rows(karam_rows)
    mismatches = []
    for index, (upstream_row, karam_row) in enumerate(
        zip(upstream_rows, karam_rows, strict=False)
    ):
        if upstream_row != karam_row:
            mismatches.append(
                {
                    "index": index,
                    "upstream": upstream_row,
                    "karam": karam_row,
                }
            )
            if len(mismatches) >= 5:
                break

    return {
        "upstream_logical_rows": len(upstream_rows),
        "karam_logical_rows": len(karam_rows),
        "mismatch_count_lower_bound": len(mismatches),
        "first_mismatches": mismatches,
    }


def _logical_rows(rows: list[dict]) -> list[tuple[tuple[str, str], ...]]:
    """Strip Karam-only presentation markers for an output comparison."""
    ignored_fields = {
        "account_currency",
        "karam_series",
        "translation",
        "letter",
        "row_type",
        "is_separator",
        "debit_in_company_currency",
        "credit_in_company_currency",
    }
    logical_rows = []
    for row in rows:
        if row.get("is_separator") or row.get("row_type") == "separator":
            continue
        logical_rows.append(
            tuple(
                (
                    fieldname,
                    repr(
                        _normalise_against_voucher(row.get(fieldname))
                        if fieldname == "against_voucher"
                        else row.get(fieldname)
                    ),
                )
                for fieldname in sorted(row)
                if fieldname not in ignored_fields
            )
        )
    return logical_rows


def _normalise_against_voucher(value):
    """Ignore repeated tokens that Karam intentionally de-duplicates."""
    if not isinstance(value, str) or "," not in value:
        return value
    return ", ".join(dict.fromkeys(part.strip() for part in value.split(",")))


def _filters_for_report(filters: dict[str, Any], name: str) -> dict[str, Any]:
    """Translate the British UI aliases for the upstream report only."""
    values = copy.deepcopy(filters)
    if name == "upstream_v16" and values.get("categorize_by"):
        values["categorize_by"] = str(values["categorize_by"]).replace(
            "Categorise", "Categorize"
        )
    return values


def _benchmark_report(
    report: Callable,
    filters: dict[str, Any],
    repeats: int,
    warmups: int,
    include_query_timings: bool,
    include_stage_timings: bool,
) -> dict[str, Any]:
    import importlib

    module = importlib.import_module(report.__module__)
    stage_names = (
        "get_gl_entries",
        "get_data_with_opening_closing",
        "get_result_as_list",
        "_set_bill_no",
        "_init_gle_map",
        "_get_account_wise_gle",
        "set_bill_no",
        "initialize_gle_map",
        "get_accountwise_gle",
        "get_party_name_map",
        "_attach_series_translation",
        "_fetch_voucher_data",
        "_apply_voucher_data_to_entries",
        "_collect_voucher_targets",
        "_build_qb_conditions",
        "_select_fields",
        "_apply_union_voucher_data",
        "_get_union_voucher_source_query",
        "_apply_bill_no_join",
        "_build_voucher_query",
        "convert_to_presentation_currency",
        "get_currency",
    )

    for _ in range(warmups):
        report(frappe._dict(copy.deepcopy(filters)))

    samples: list[dict[str, Any]] = []
    for _ in range(repeats):
        sql_calls = 0
        materialised_rows = 0
        query_timings = []
        original_sql = frappe.db.sql

        def counting_sql(*args, sql=original_sql, timings=query_timings, **kwargs):
            nonlocal sql_calls, materialised_rows
            started = time.perf_counter()
            result = sql(*args, **kwargs)
            sql_calls += 1
            if isinstance(result, list):
                materialised_rows += len(result)
            if include_query_timings:
                timings.append(
                    {
                        "wall_time_ms": round(
                            (time.perf_counter() - started) * 1000, 3
                        ),
                        "rows": len(result) if isinstance(result, list) else None,
                        "sql": " ".join(str(args[0]).split())[:300],
                    }
                )
            return result

        frappe.db.sql = counting_sql
        tracemalloc.start()
        started = time.perf_counter()
        with _stage_timer(module, stage_names, include_stage_timings) as stage_timings:
            try:
                _columns, rows = report(frappe._dict(copy.deepcopy(filters)))
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000
                _current, peak_bytes = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                frappe.db.sql = original_sql

        samples.append(
            {
                "wall_time_ms": round(elapsed_ms, 3),
                "query_count": sql_calls,
                "materialised_db_rows": materialised_rows,
                "output_rows": len(rows),
                "peak_memory_bytes": peak_bytes,
                **(
                    {
                        "slowest_queries": sorted(
                            query_timings,
                            key=lambda query: query["wall_time_ms"],
                            reverse=True,
                        )[:10]
                    }
                    if include_query_timings
                    else {}
                ),
                **({"stage_timings": stage_timings} if include_stage_timings else {}),
            }
        )

    return {
        "samples": samples,
        "median_wall_time_ms": round(
            statistics.median(sample["wall_time_ms"] for sample in samples), 3
        ),
        "p95_wall_time_ms": round(_percentile(samples, "wall_time_ms", 0.95), 3),
        "median_query_count": statistics.median(
            sample["query_count"] for sample in samples
        ),
        "median_materialised_db_rows": statistics.median(
            sample["materialised_db_rows"] for sample in samples
        ),
        "median_output_rows": statistics.median(
            sample["output_rows"] for sample in samples
        ),
        "median_peak_memory_bytes": statistics.median(
            sample["peak_memory_bytes"] for sample in samples
        ),
    }


@contextmanager
def _stage_timer(module, stage_names, enabled):
    """Temporarily measure report stages without changing the report module."""
    timings = []
    originals = {}
    if enabled:
        for stage_name in stage_names:
            stage = getattr(module, stage_name, None)
            if not stage:
                continue
            targets = [module]
            owner = __import__(stage.__module__, fromlist=[stage_name])
            if owner is not module:
                targets.append(owner)
            for target in targets:
                if not hasattr(target, stage_name):
                    continue
                key = (target, stage_name)
                if key in originals:
                    continue
                original = getattr(target, stage_name)
                originals[key] = original

                def timed_stage(
                    *args,
                    _stage=original,
                    _stage_name=stage_name,
                    _timings=timings,
                    **kwargs,
                ):
                    started = time.perf_counter()
                    result = _stage(*args, **kwargs)
                    _timings.append(
                        {
                            "stage": _stage_name,
                            "wall_time_ms": round(
                                (time.perf_counter() - started) * 1000, 3
                            ),
                        }
                    )
                    return result

                setattr(target, stage_name, timed_stage)

    try:
        yield timings
    finally:
        for (owner, stage_name), stage in originals.items():
            setattr(owner, stage_name, stage)


def _percentile(samples: list[dict[str, Any]], key: str, percentile: float) -> float:
    values = sorted(float(sample[key]) for sample in samples)
    index = min(len(values) - 1, max(0, int((len(values) - 1) * percentile)))
    return values[index]
