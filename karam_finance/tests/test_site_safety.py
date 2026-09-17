"""Site-free regression tests for native finance test isolation."""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import call, patch

from karam_finance.tests import site_safety


class TestSiteSafety(TestCase):
    def test_missing_allow_tests_fails_before_database_reads(self) -> None:
        with patch.object(site_safety, "frappe") as api:
            api.conf = {site_safety.DISPOSABLE_SITE_MARKER: True}
            with self.assertRaisesRegex(RuntimeError, "allow_tests"):
                site_safety.require_disposable_test_site()
            api.db.count.assert_not_called()

    def test_missing_disposable_marker_fails_before_database_reads(self) -> None:
        with patch.object(site_safety, "frappe") as api:
            api.conf = {"allow_tests": True}
            with self.assertRaisesRegex(RuntimeError, "disposable_test_site"):
                site_safety.require_disposable_test_site()
            api.db.count.assert_not_called()

    def test_nonempty_business_table_fails_closed(self) -> None:
        with patch.object(site_safety, "frappe") as api:
            api.conf = {
                "allow_tests": True,
                site_safety.DISPOSABLE_SITE_MARKER: True,
            }
            api.local.site = "karam-finance-test.local"

            def count_rows(doctype: str) -> int:
                return 4 if doctype == "Account" else 0

            api.db.count.side_effect = count_rows
            with self.assertRaisesRegex(RuntimeError, "Account=4"):
                site_safety.require_disposable_test_site()
            assert api.db.count.call_args_list == [
                call("Company"),
                call("Account"),
                call("Fiscal Year"),
                call("GL Entry"),
                call("Reporting Currency GLE"),
                call("Currency Exchange"),
            ]

    def test_empty_marked_site_passes_all_business_table_checks(self) -> None:
        with patch.object(site_safety, "frappe") as api:
            api.conf = {
                "allow_tests": "true",
                site_safety.DISPOSABLE_SITE_MARKER: "1",
            }
            api.local.site = "karam-finance-test.local"
            api.db.count.return_value = 0
            site_safety.require_disposable_test_site()
            assert [call.args[0] for call in api.db.count.call_args_list] == list(
                site_safety.CRITICAL_BUSINESS_DOCTYPES
            )

    def test_database_verification_failure_fails_closed(self) -> None:
        with patch.object(site_safety, "frappe") as api:
            api.conf = {
                "allow_tests": True,
                site_safety.DISPOSABLE_SITE_MARKER: True,
            }
            api.local.site = "karam-finance-test.local"
            api.db.count.side_effect = RuntimeError("database unavailable")
            with self.assertRaisesRegex(RuntimeError, "Could not verify"):
                site_safety.require_disposable_test_site()
