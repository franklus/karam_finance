def get_balance_row(label, amount, account_currency):
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
