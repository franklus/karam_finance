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
import importlib
import io
import pstats
import statistics
import time
import tracemalloc
from collections.abc import Callable, Generator
from contextlib import contextmanager
from operator import itemgetter
from types import ModuleType
from typing import Any, cast

import frappe


# Retain the documented interactive benchmark calling convention.
def benchmark(  # noqa: V103, PLR0917
    filters: dict[str, Any],
    repeats: int = 5,
    warmups: int = 1,
    include_query_timings: bool = False,
    include_stage_timings: bool = False,
) -> dict[str, Any]:
    """Compare identical read-only filters against both report pipelines."""
    if repeats < 1 or warmups < 0:
        message = "repeats must be positive and warmups cannot be negative"
        raise ValueError(message)

    # ERPNext report imports require an initialised Frappe site.
    import erpnext.accounts.report.general_ledger.general_ledger as upstream  # noqa: PLC0415

    karam = importlib.import_module(
        "karam_finance.karam_general.report.general_ledger_(karam).general_ledger_(karam)"
    )
    reports: dict[str, Callable[..., Any]] = {
        "upstream_v16": upstream.execute,
        "karam": karam.execute,
    }

    results = {
        name: _benchmark_report(
            report,
            _filters_for_report(filters, name),
            repeats,
            warmups=warmups,
            timing_options=(include_query_timings, include_stage_timings),
        )
        for name, report in reports.items()
    }
    return {
        "filters": dict(filters),
        "repeats": repeats,
        "warmups": warmups,
        "reports": results,
    }


def profile(  # noqa: V103 - documented interactive developer-tool entry point.
    filters: dict[str, Any], report_name: str = "karam", limit: int = 30
) -> str:
    """Return a cProfile report for one read-only report execution."""
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
        message = f"Unknown report: {report_name}"
        raise ValueError(message) from exc

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


def compare_outputs(  # noqa: V103 - documented interactive developer-tool entry point.
    filters: dict[str, Any], *, joined_voucher_data: bool = False
) -> dict[str, Any]:
    """Compare logical upstream/Karam rows while ignoring approved extensions."""
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


def _logical_rows(rows: list[dict[str, Any]]) -> list[tuple[tuple[str, str], ...]]:
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


def _normalise_against_voucher(value: Any) -> Any:
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
    report: Callable[..., Any],
    filters: dict[str, Any],
    repeats: int,
    *,
    warmups: int,
    timing_options: tuple[bool, bool],
) -> dict[str, Any]:
    include_query_timings, include_stage_timings = timing_options
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

    samples = [
        _sample_report(
            report,
            filters,
            (module, stage_names),
            include_query_timings=include_query_timings,
            include_stage_timings=include_stage_timings,
        )
        for _ in range(repeats)
    ]

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
def _stage_timer(
    module: ModuleType, stage_names: tuple[str, ...], enabled: bool
) -> Generator[list[dict[str, Any]]]:
    """Temporarily measure report stages without changing the report module."""
    timings: list[dict[str, Any]] = []
    originals: dict[tuple[ModuleType, str], Callable[..., Any]] = {}
    if enabled:
        _install_stage_timers(module, stage_names, timings=timings, originals=originals)

    try:
        yield timings
    finally:
        for (owner, stage_name), stage in originals.items():
            setattr(owner, stage_name, stage)


def _percentile(samples: list[dict[str, Any]], key: str, percentile: float) -> float:
    values = sorted(float(sample[key]) for sample in samples)
    index = min(len(values) - 1, max(0, int((len(values) - 1) * percentile)))
    return values[index]


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
        result = _stage(*args, **kwargs)
        _timings.append(
            {
                "stage": _stage_name,
                "wall_time_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        return result

    return timed_stage


def _sample_report(
    report: Callable[..., Any],
    filters: dict[str, Any],
    timing_context: tuple[ModuleType, tuple[str, ...]],
    *,
    include_query_timings: bool,
    include_stage_timings: bool,
) -> dict[str, Any]:
    module, stage_names = timing_context
    sql_calls = 0
    materialised_rows = 0
    query_timings: list[dict[str, Any]] = []
    original_sql = frappe.db.sql

    def counting_sql(
        *args: Any,
        sql: Callable[..., Any] = original_sql,
        timings: list[dict[str, Any]] = query_timings,
        **kwargs: Any,
    ) -> Any:
        nonlocal sql_calls, materialised_rows
        started = time.perf_counter()
        result = sql(*args, **kwargs)
        sql_calls += 1
        if isinstance(result, list):
            materialised_rows += len(result)
        if include_query_timings:
            timings.append(
                {
                    "wall_time_ms": round((time.perf_counter() - started) * 1000, 3),
                    "rows": len(result) if isinstance(result, list) else None,
                    "sql": " ".join(str(args[0]).split())[:300],
                }
            )
        return result

    frappe.db.sql = cast(Any, counting_sql)
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

    return {
        "wall_time_ms": round(elapsed_ms, 3),
        "query_count": sql_calls,
        "materialised_db_rows": materialised_rows,
        "output_rows": len(rows),
        "peak_memory_bytes": peak_bytes,
        **(
            {
                "slowest_queries": sorted(
                    query_timings,
                    key=itemgetter("wall_time_ms"),
                    reverse=True,
                )[:10]
            }
            if include_query_timings
            else {}
        ),
        **({"stage_timings": stage_timings} if include_stage_timings else {}),
    }


def _install_stage_timers(
    module: ModuleType,
    stage_names: tuple[str, ...],
    *,
    timings: list[dict[str, Any]],
    originals: dict[tuple[ModuleType, str], Callable[..., Any]],
) -> None:
    for stage_name in stage_names:
        targets = _timer_targets(module, stage_name)
        for target in targets:
            key = (target, stage_name)
            if not hasattr(target, stage_name) or key in originals:
                continue
            original = getattr(target, stage_name)
            originals[key] = original

            timed_stage = _timed_stage(original, stage_name, timings)

            setattr(target, stage_name, timed_stage)


def _timer_targets(module: ModuleType, stage_name: str) -> list[ModuleType]:
    stage = getattr(module, stage_name, None)
    if not stage:
        return []
    targets = [module]
    owner = __import__(stage.__module__, fromlist=[stage_name])
    if owner is not module:
        targets.append(owner)
    return targets
