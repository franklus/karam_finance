"""Invalid DOE rates must fail before replacement starts, including legacy settings."""

from itertools import product
from typing import Any, override
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from karam_finance.reporting_currency import ledger_lock
from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync import doe
from karam_finance.reporting_currency.doctype.reporting_currency_settings import (
    reporting_currency_settings as settings_module,
)

INVALID_RATES = (0, None, -1, float("nan"), float("inf"), float("-inf"))


class TestDoeRateValidation(TestCase):
    @override  # noqa: V105 - unittest and Frappe test lifecycle callback.
    def setUp(self) -> None:
        # Exercise controller validation without loading site metadata.
        self.settings = object.__new__(settings_module.ReportingCurrencySettings)
        self.parameters: list[frappe._dict[str, Any]] = [
            frappe._dict(idx=1, exchange_rate=83, doe_posting_date="2026-01-01"),
            frappe._dict(idx=2, exchange_rate=0, doe_posting_date="2026-02-01"),
        ]
        self.settings.__dict__.update(
            reporting_currency="USD",
            rc_parameters=self.parameters,
        )
        self.frappe_mock = Mock()
        self.frappe_mock.get_single.return_value = self.settings
        self.frappe_mock.throw.side_effect = self.throw_validation_error
        for module in (doe, settings_module):
            self.enterContext(patch.object(module, "frappe", self.frappe_mock))
            self.enterContext(patch.object(module, "_", side_effect=str))
        self.enterContext(
            patch.object(doe, "get_reporting_company", return_value="Karam")
        )
        self.enterContext(patch.object(doe, "_publish_progress"))
        self.enterContext(patch.object(doe, "validate_offset_accounts"))
        self.enterContext(patch.object(settings_module, "validate_offset_accounts"))
        self.enterContext(patch.object(doe, "hold_ledger_lock"))
        self.enterContext(
            patch.object(
                ledger_lock,
                "hold_ledger_lock",
                side_effect=lambda: Mock(
                    database=self.frappe_mock.db,
                    scopes=0,
                ),
            )
        )

    @staticmethod
    def throw_validation_error(message: str) -> None:
        raise frappe.ValidationError(message)

    def test_settings_reject_every_invalid_rate_with_row_number(self) -> None:
        for rate in INVALID_RATES:
            with self.subTest(rate=rate):
                self.settings.rc_parameters[1].exchange_rate = rate
                with self.assertRaisesRegex(
                    frappe.ValidationError, "Row 2:.*greater than zero"
                ):
                    settings_module.ReportingCurrencySettings._validate_rc_parameters(
                        self.settings
                    )

    def test_positive_rates_and_empty_optional_parameters_are_allowed(self) -> None:
        for rate in (0.0001, 1, 89500):
            self.settings.rc_parameters[1].exchange_rate = rate
            settings_module.ReportingCurrencySettings._validate_rc_parameters(
                self.settings
            )
        self.settings.rc_parameters = []
        settings_module.ReportingCurrencySettings._validate_rc_parameters(self.settings)
        self.frappe_mock.throw.assert_not_called()

    def test_invalid_settings_are_not_enqueued(self) -> None:
        with self.assertRaisesRegex(frappe.ValidationError, "Row 2:"):
            doe.compute_doe(background=True)
        self.frappe_mock.enqueue.assert_not_called()

    def test_both_workers_validate_all_rows_before_deletion(self) -> None:
        workers = (doe._compute_doe_background, doe.compute_doe_inline)
        for worker, rate in product(workers, INVALID_RATES):
            with self.subTest(worker=worker.__name__, rate=rate):
                self.frappe_mock.reset_mock()
                self.settings.rc_parameters[1].exchange_rate = rate
                with self.assertRaisesRegex(frappe.ValidationError, "Row 2:"):
                    worker()
                self.frappe_mock.db.sql.assert_not_called()
                self.frappe_mock.db.bulk_insert.assert_not_called()
                self.frappe_mock.db.savepoint.assert_not_called()
                self.frappe_mock.db.rollback.assert_called_once()
                self.frappe_mock.msgprint.assert_not_called()
