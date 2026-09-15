"""Owned Redis lease for a site's chained historical rebuild."""

import pickle
from collections.abc import Generator
from contextlib import contextmanager
from typing import cast

import frappe
from frappe.utils.redis_wrapper import DEFAULT_PICKLE_PROTOCOL, RedisWrapper
from redis.lock import Lock

RUN_KEY = "historical_gl_rebuild_running"
# Each worker has a four-hour hard timeout; leave a margin before lease expiry.
LEASE_SECONDS = 4 * 60 * 60 + 300


def acquire_rebuild_guard(run_id: str) -> bool:
    """Claim the site's guard atomically; Redis failures must fail closed."""
    cache = cast(RedisWrapper, frappe.cache)
    return bool(
        cache.set(
            cache.make_key(RUN_KEY),
            pickle.dumps(run_id, protocol=DEFAULT_PICKLE_PROTOCOL),
            nx=True,
            ex=LEASE_SECONDS,
        )
    )


def renew_rebuild_guard(run_id: str) -> bool:
    """Extend only this run's lease, using Redis rather than the request-local cache."""
    cache = cast(RedisWrapper, frappe.cache)
    return bool(
        cache.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('expire', KEYS[1], ARGV[2]) end return 0",
            1,
            cache.make_key(RUN_KEY),
            pickle.dumps(run_id, protocol=DEFAULT_PICKLE_PROTOCOL),
            LEASE_SECONDS,
        )
    )


def release_rebuild_guard(run_id: str) -> None:
    """Atomically remove this run's guard without deleting a successor's lease."""
    cache = cast(RedisWrapper, frappe.cache)
    cache.eval(
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) end return 0",
        1,
        cache.make_key(RUN_KEY),
        pickle.dumps(run_id, protocol=DEFAULT_PICKLE_PROTOCOL),
    )
    frappe.local.cache.pop(cache.make_key(RUN_KEY), None)


@contextmanager
def rebuild_execution() -> Generator[bool]:
    """Serialise worker delivery, including two deliveries of the same run token."""
    cache = cast(RedisWrapper, frappe.cache)
    lock = Lock(
        cache,
        cache.make_key("historical_gl_rebuild_execution").decode(),
        timeout=LEASE_SECONDS,
        blocking=False,
    )
    acquired = lock.acquire()
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()
