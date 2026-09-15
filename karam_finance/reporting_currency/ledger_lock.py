"""A site-scoped database advisory lock for reporting-ledger writers.

The lock survives transaction commits and has no expiring lease. The database
releases it if the worker connection dies. Document writes retain it until their
transaction ends; background operations retain it across their commit boundaries.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import frappe
from frappe import _

if TYPE_CHECKING:
    from collections.abc import Generator

    from frappe.database.database import Database


@dataclass
class _LedgerLock:
    database: Database
    name: str
    scopes: int = 0
    released: bool = False

    def release(self) -> None:
        if self.scopes or self.released:
            return
        self.database.multisql(
            {
                "mariadb": "SELECT RELEASE_LOCK(%s)",
                "postgres": "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
            },
            (self.name,),
        )
        self.released = True


def hold_ledger_lock() -> _LedgerLock:
    """Keep a native lock until commit/rollback; fail immediately on contention."""
    database = frappe.local.db
    locks: dict[int, _LedgerLock] | None = getattr(
        frappe.local, "_reporting_ledger_locks", None
    )
    if locks is None:
        locks = {}
        frappe.local._reporting_ledger_locks = locks
    existing = locks.get(id(database))
    if existing and not existing.released:
        return existing
    name = (
        "karam_finance:reporting:"
        + hashlib.sha256(str(frappe.conf.db_name).encode()).hexdigest()[:32]
    )
    acquired = database.multisql(
        {
            "mariadb": "SELECT GET_LOCK(%s, 0)",
            "postgres": "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
        },
        (name,),
    )[0][0]
    if not acquired:
        frappe.throw(
            _(
                "A reporting ledger operation is already running. Please try again after it finishes."
            )
        )
    lock = _LedgerLock(database, name)
    locks[id(database)] = lock
    database.after_commit.add(lock.release)
    database.after_rollback.add(lock.release)
    return lock


@contextmanager
def ledger_operation() -> Generator[None]:
    """Hold the lock across an operation's commits, rolling back before failure release."""
    lock = hold_ledger_lock()
    lock.scopes += 1
    try:
        yield
    except Exception:
        lock.database.rollback()
        raise
    finally:
        lock.scopes -= 1
        lock.release()
