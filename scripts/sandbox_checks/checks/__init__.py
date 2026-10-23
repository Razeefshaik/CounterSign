"""Lazy check registry. Each module supplies get_steps() -> list[runner.Step]."""

from importlib import import_module

CHECK_MODULES = {
    "D1": "d01_auth",
    "D2": "d02_money_in",
    "D3": "d03_payout",
    "D4": "d04_unclaimed",
    "D5": "d05_cancel",
    "D6": "d06_idempotency",
    "D7": "d07_negative",
    "D8": "d08_dispute_create",
    "D9": "d09_evidence",
    "D10": "d10_settle",
    "D11": "d11_webhooks",
    "D12": "d12_balances",
    "D13": "d13_toolkit",
}


class CheckUnavailable(RuntimeError):
    """The task card for a check has not been implemented yet."""


def get_steps(check_id: str):
    if check_id not in CHECK_MODULES:
        raise CheckUnavailable("Choose a check from D1 through D13.")
    name = f"{__name__}.{CHECK_MODULES[check_id]}"
    try:
        module = import_module(name)
    except ModuleNotFoundError as error:
        if error.name != name:
            raise
        raise CheckUnavailable(f"{check_id} is not implemented yet.") from None
    return module.get_steps()
