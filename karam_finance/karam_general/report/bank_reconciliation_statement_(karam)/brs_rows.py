from typing import Any


def get_balance_row(
    label: str, amount: float, account_currency: str | None
) -> dict[str, Any]:
    if amount > 0:
        return {
            "payment_entry": label,
            "debit": amount,
            "credit": 0,
            "account_currency": account_currency,
        }
    return {
        "payment_entry": label,
        "debit": 0,
        "credit": abs(amount),
        "account_currency": account_currency,
    }
