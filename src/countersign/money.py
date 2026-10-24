"""Money as integer minor units plus an ISO currency code. No floats anywhere (PLAN 2.1).

Inside CounterSign money is {"minor": 36000, "currency": "USD"}. PayPal speaks decimal
strings: {"value": "360.00", "currency_code": "USD"}, or "currency" in Payouts. Parsing is
exact; a string that would need rounding is rejected, never rounded.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Self

# Minor-unit exponent per supported currency. Unknown currencies are rejected.
EXPONENTS: Mapping[str, int] = {"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0}

# Maker share rounding: nearest minor unit, ties away from zero (half-up on the magnitude).
# demo_week needs it: 90% of $333.33 is 29999.7 cents and the expected payout is $300.00.
SHARE_ROUNDING = "HALF_UP"
BPS_PER_WHOLE = 10_000

_CURRENCY = re.compile(r"[A-Z]{3}")


class MoneyError(ValueError):
    """Malformed amount, unknown currency or an operation money does not allow."""


class CurrencyMismatch(MoneyError):
    """Two amounts in different currencies were combined or compared."""


def _plain_int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{what} must be an int, not {type(value).__name__}.")
    return value


def _half_up_div(numerator: int, denominator: int) -> int:
    """numerator / denominator to the nearest int, ties away from zero."""
    quotient, remainder = divmod(abs(numerator), denominator)
    if 2 * remainder >= denominator:
        quotient += 1
    return -quotient if numerator < 0 else quotient


@dataclass(frozen=True, slots=True)
class Money:
    minor: int
    currency: str

    def __post_init__(self):
        _plain_int(self.minor, "minor")
        if not isinstance(self.currency, str) or not _CURRENCY.fullmatch(self.currency):
            raise MoneyError(f"Currency must be 3 uppercase letters, got {self.currency!r}.")
        if self.currency not in EXPONENTS:
            raise MoneyError(f"Unsupported currency {self.currency}.")

    @classmethod
    def zero(cls, currency: str) -> Self:
        """Start value for sum(): sum(amounts, Money.zero("USD"))."""
        return cls(0, currency)

    @property
    def exponent(self) -> int:
        return EXPONENTS[self.currency]

    def _same(self, other: object) -> "Money":
        if not isinstance(other, Money):
            raise TypeError(f"Expected Money, got {type(other).__name__}.")
        if other.currency != self.currency:
            raise CurrencyMismatch(f"Cannot mix {self.currency} and {other.currency}.")
        return other

    def __add__(self, other: "Money") -> "Money":
        return Money(self.minor + self._same(other).minor, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        return Money(self.minor - self._same(other).minor, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.minor, self.currency)

    def __lt__(self, other: "Money") -> bool:
        return self.minor < self._same(other).minor

    def __le__(self, other: "Money") -> bool:
        return self.minor <= self._same(other).minor

    def __gt__(self, other: "Money") -> bool:
        return self.minor > self._same(other).minor

    def __ge__(self, other: "Money") -> bool:
        return self.minor >= self._same(other).minor

    def __mul__(self, factor: int) -> "Money":
        return Money(self.minor * _plain_int(factor, "Multiplier"), self.currency)

    __rmul__ = __mul__

    def __truediv__(self, other: object):
        raise TypeError("Money cannot be divided; use allocate() or share_bps().")

    __floordiv__ = __truediv__

    def share_bps(self, bps: int) -> "Money":
        """bps/10000 of this amount, rounded per SHARE_ROUNDING."""
        if not 0 <= _plain_int(bps, "Basis points") <= BPS_PER_WHOLE:
            raise MoneyError(f"Basis points must be 0..{BPS_PER_WHOLE}, got {bps}.")
        return Money(_half_up_div(self.minor * bps, BPS_PER_WHOLE), self.currency)

    def share_pct(self, pct: int) -> "Money":
        """pct percent of this amount, rounded per SHARE_ROUNDING."""
        if not 0 <= _plain_int(pct, "Percent") <= 100:
            raise MoneyError(f"Percent must be 0..100, got {pct}.")
        return self.share_bps(pct * 100)

    def allocate(self, parts: int | Sequence[int]) -> list["Money"]:
        """Split into parts (a count or integer weights) that sum exactly to self.

        Each part gets the floor of its exact share; leftover units go one each to the
        largest fractional remainders, earlier parts first on ties. Negative amounts
        allocate their magnitude and negate, so allocate(-x) == [-p for p in allocate(x)].
        """
        if isinstance(parts, Sequence):
            weights = [_plain_int(w, "Weight") for w in parts]
        else:
            weights = [1] * _plain_int(parts, "Parts")
        if not weights or any(w < 0 for w in weights) or sum(weights) == 0:
            raise MoneyError("Allocate needs at least one non-negative weight with a positive sum.")
        total, magnitude = sum(weights), abs(self.minor)
        floors = [magnitude * w // total for w in weights]
        remainders = [magnitude * w % total for w in weights]
        leftover = magnitude - sum(floors)
        order = sorted(range(len(weights)), key=lambda i: (-remainders[i], i))
        for i in order[:leftover]:
            floors[i] += 1
        sign = -1 if self.minor < 0 else 1
        return [Money(sign * f, self.currency) for f in floors]

    @classmethod
    def from_paypal(cls, amount: Mapping[str, object]) -> Self:
        """Parse {"value", "currency_code"} or Payouts' {"value", "currency"} exactly."""
        if not isinstance(amount, Mapping):
            raise MoneyError("PayPal amount must be an object.")
        keys = [k for k in ("currency_code", "currency") if k in amount]
        if len(keys) != 1:
            raise MoneyError("PayPal amount needs exactly one of currency_code or currency.")
        currency, value = amount[keys[0]], amount.get("value")
        if not isinstance(currency, str) or currency not in EXPONENTS:
            raise MoneyError(f"Unsupported currency {currency!r}.")
        exponent = EXPONENTS[currency]
        pattern = r"-?[0-9]+" if exponent == 0 else rf"-?[0-9]+(?:\.[0-9]{{1,{exponent}}})?"
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise MoneyError(f"Malformed {currency} value {value!r}.")
        negative = value.startswith("-")
        whole, _, fraction = value.lstrip("-").partition(".")
        minor = int(whole) * 10**exponent + int(fraction.ljust(exponent, "0") or "0")
        return cls(-minor if negative else minor, currency)

    def to_paypal(
        self, currency_key: Literal["currency_code", "currency"] = "currency_code"
    ) -> dict[str, str]:
        """PayPal decimal string with exactly the currency's minor digits."""
        if currency_key not in ("currency_code", "currency"):
            raise MoneyError(f"Unknown currency key {currency_key!r}.")
        sign = "-" if self.minor < 0 else ""
        whole, fraction = divmod(abs(self.minor), 10**self.exponent)
        value = f"{sign}{whole}.{fraction:0{self.exponent}d}" if self.exponent else f"{sign}{whole}"
        return {"value": value, currency_key: self.currency}
