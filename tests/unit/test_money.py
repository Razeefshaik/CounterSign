from dataclasses import FrozenInstanceError

import pytest

from countersign.money import EXPONENTS, CurrencyMismatch, Money, MoneyError

USD = "USD"


def usd(minor: int) -> Money:
    return Money(minor, USD)


# Construction


def test_money_is_frozen_hashable_and_compares_by_value():
    a = usd(40000)
    assert a == Money(40000, "USD")
    assert a != Money(40000, "EUR")
    assert len({a, usd(40000), Money(40000, "EUR")}) == 2
    with pytest.raises(FrozenInstanceError):
        a.minor = 1  # type: ignore[misc]


@pytest.mark.parametrize("minor", [1.0, 400.5, "400", None, True, False])
def test_minor_must_be_a_plain_int(minor):
    with pytest.raises(TypeError):
        Money(minor, USD)  # type: ignore[arg-type]


@pytest.mark.parametrize("currency", ["usd", "US", "USDD", "U$D", "", " USD", 840, None])
def test_currency_must_be_three_uppercase_letters(currency):
    with pytest.raises(MoneyError):
        Money(1, currency)  # type: ignore[arg-type]


def test_unknown_currency_is_rejected():
    with pytest.raises(MoneyError, match="Unsupported"):
        Money(1, "XYZ")


def test_exponent_table():
    assert EXPONENTS == {"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0}
    assert usd(1).exponent == 2
    assert Money(1, "JPY").exponent == 0


# Arithmetic and comparison


def test_add_subtract_negate_same_currency():
    assert usd(40000) + usd(54000) == usd(94000)
    assert usd(40000) - usd(54000) == usd(-14000)
    assert -usd(36000) == usd(-36000)


def test_comparisons_same_currency():
    assert usd(1) < usd(2) <= usd(2) < usd(3)
    assert usd(3) > usd(2) >= usd(2)
    assert not usd(2) < usd(2)
    assert max(usd(5), usd(9), usd(1)) == usd(9)


def test_sum_with_zero_helper():
    assert Money.zero(USD) == usd(0)
    assert sum([usd(48600), usd(30000), usd(30000)], Money.zero(USD)) == usd(108600)
    assert sum([], Money.zero("EUR")) == Money(0, "EUR")


def test_plain_sum_without_zero_start_is_a_type_error():
    with pytest.raises(TypeError):
        sum([usd(1), usd(2)])


@pytest.mark.parametrize(
    "operation",
    [
        lambda a, b: a + b,
        lambda a, b: a - b,
        lambda a, b: a < b,
        lambda a, b: a <= b,
        lambda a, b: a > b,
        lambda a, b: a >= b,
    ],
    ids=["add", "sub", "lt", "le", "gt", "ge"],
)
def test_mixing_currencies_raises_currency_mismatch(operation):
    with pytest.raises(CurrencyMismatch, match="Cannot mix USD and EUR"):
        operation(usd(100), Money(100, "EUR"))


def test_sum_across_currencies_raises():
    with pytest.raises(CurrencyMismatch):
        sum([usd(1), Money(1, "GBP")], Money.zero(USD))


@pytest.mark.parametrize("other", [1, 1.0, "1.00", None])
def test_arithmetic_with_non_money_is_a_type_error(other):
    with pytest.raises(TypeError):
        usd(100) + other
    with pytest.raises(TypeError):
        usd(100) - other
    with pytest.raises(TypeError):
        _ = usd(100) < other


def test_multiply_by_int_both_sides():
    assert usd(30000) * 2 == usd(60000)
    assert 3 * usd(100) == usd(300)
    assert usd(100) * -1 == usd(-100)


@pytest.mark.parametrize("factor", [2.0, 0.9, True])
def test_multiply_by_float_or_bool_is_a_type_error(factor):
    with pytest.raises(TypeError):
        usd(100) * factor
    with pytest.raises(TypeError):
        factor * usd(100)


@pytest.mark.parametrize("divisor", [2, 2.0, 0.5])
def test_division_is_always_a_type_error(divisor):
    with pytest.raises(TypeError, match="allocate"):
        usd(100) / divisor
    with pytest.raises(TypeError, match="allocate"):
        usd(100) // divisor


# Maker share


def test_demo_week_shares():
    assert usd(40000).share_pct(90) == usd(36000)
    assert usd(54000).share_pct(90) == usd(48600)
    assert usd(6000).share_pct(90) == usd(5400)
    # 90% of $333.33 is 29999.7 cents; demo_week expects a $300.00 payout to C.
    assert usd(33333).share_pct(90) == usd(30000)


@pytest.mark.parametrize(
    ("minor", "bps", "expected"),
    [
        (1, 5000, 1),  # 0.5 ties away from zero
        (3, 5000, 2),  # 1.5 -> 2
        (5, 5000, 3),  # 2.5 -> 3 (not banker's rounding)
        (14, 1000, 1),  # 1.4 -> 1
        (16, 1000, 2),  # 1.6 -> 2
        (-1, 5000, -1),  # symmetric for negatives
        (-14, 1000, -1),
        (-16, 1000, -2),
        (12345, 0, 0),
        (12345, 10000, 12345),
        (10001, 1, 1),  # 1.0001
    ],
)
def test_share_bps_rounds_half_up_on_magnitude(minor, bps, expected):
    assert usd(minor).share_bps(bps) == usd(expected)


def test_share_pct_equals_share_bps_times_100():
    for minor in range(0, 1000, 7):
        assert usd(minor).share_pct(37) == usd(minor).share_bps(3700)


def test_share_is_symmetric_under_negation():
    for minor in range(0, 500, 3):
        assert usd(-minor).share_bps(3333) == -usd(minor).share_bps(3333)


@pytest.mark.parametrize("bad", [90.0, 0.9, True, "90"])
def test_share_rejects_non_int_inputs(bad):
    with pytest.raises(TypeError):
        usd(100).share_pct(bad)
    with pytest.raises(TypeError):
        usd(100).share_bps(bad)


@pytest.mark.parametrize("pct", [-1, 101])
def test_share_pct_range(pct):
    with pytest.raises(MoneyError):
        usd(100).share_pct(pct)


@pytest.mark.parametrize("bps", [-1, 10001])
def test_share_bps_range(bps):
    with pytest.raises(MoneyError):
        usd(100).share_bps(bps)


# Allocate


def test_allocate_count_spreads_remainder_to_earliest_parts():
    assert usd(100).allocate(3) == [usd(34), usd(33), usd(33)]
    assert usd(101).allocate(3) == [usd(34), usd(34), usd(33)]
    assert usd(2).allocate(4) == [usd(1), usd(1), usd(0), usd(0)]


def test_allocate_weights_uses_largest_remainder():
    # 90:10 of $333.33 -> exact 29999.7 and 3333.3; the 0.7 remainder gets the cent.
    assert usd(33333).allocate([90, 10]) == [usd(30000), usd(3333)]
    # Exact 3.5, 3.5, 3.0: tie goes to the earlier part.
    assert usd(10).allocate([35, 35, 30]) == [usd(4), usd(3), usd(3)]
    assert usd(100).allocate([1, 0, 1]) == [usd(50), usd(0), usd(50)]


@pytest.mark.parametrize("minor", [0, 1, 7, 99, 100, 33333, 108600, 999_999_999_999])
@pytest.mark.parametrize("parts", [1, 2, 3, 7, [90, 10], [1, 2, 3], [5, 0, 5], [1] * 13, [3, 3, 1]])
def test_allocate_always_sums_exactly(minor, parts):
    for amount in (usd(minor), usd(-minor)):
        split = amount.allocate(parts)
        assert sum(split, Money.zero(USD)) == amount
        assert len(split) == (parts if isinstance(parts, int) else len(parts))
        assert all(p.currency == USD for p in split)


def test_allocate_parts_stay_within_one_unit_of_exact_share():
    amount, weights = usd(33333), [3, 3, 1]
    exact = [amount.minor * w / sum(weights) for w in weights]  # test-only check
    for part, ideal in zip(amount.allocate(weights), exact):
        assert abs(part.minor - ideal) < 1


def test_allocate_is_deterministic_and_symmetric():
    assert usd(1000).allocate([1, 1, 1]) == usd(1000).allocate([1, 1, 1])
    assert usd(-100).allocate(3) == [usd(-34), usd(-33), usd(-33)]


def test_allocate_keeps_currency():
    assert Money(10, "JPY").allocate(3) == [Money(4, "JPY"), Money(3, "JPY"), Money(3, "JPY")]


@pytest.mark.parametrize("parts", [0, -1, [], [0, 0], [1, -1], [-2, 3]])
def test_allocate_rejects_empty_or_negative_parts(parts):
    with pytest.raises(MoneyError):
        usd(100).allocate(parts)


@pytest.mark.parametrize("parts", [2.0, True, [1.0, 1], [True, 1], ["1", "1"]])
def test_allocate_rejects_non_int_parts(parts):
    with pytest.raises(TypeError):
        usd(100).allocate(parts)


# PayPal decimal strings


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ({"value": "400.00", "currency_code": "USD"}, usd(40000)),
        ({"value": "486.00", "currency": "USD"}, usd(48600)),
        ({"value": "333.33", "currency_code": "USD"}, usd(33333)),
        ({"value": "0.00", "currency_code": "USD"}, usd(0)),
        ({"value": "0.01", "currency_code": "USD"}, usd(1)),
        ({"value": "-300.00", "currency_code": "USD"}, usd(-30000)),
        ({"value": "400", "currency_code": "USD"}, usd(40000)),
        ({"value": "400.5", "currency_code": "USD"}, usd(40050)),
        ({"value": "-0.00", "currency_code": "USD"}, usd(0)),
        ({"value": "12.34", "currency_code": "EUR"}, Money(1234, "EUR")),
        ({"value": "12.34", "currency_code": "GBP"}, Money(1234, "GBP")),
        ({"value": "1500", "currency_code": "JPY"}, Money(1500, "JPY")),
        ({"value": "1500.00", "currency_code": "USD", "extra": 1}, usd(150000)),
    ],
)
def test_from_paypal_parses_exactly(amount, expected):
    assert Money.from_paypal(amount) == expected


@pytest.mark.parametrize(
    "value",
    [
        "1.005",  # over-precise: never rounded
        "1.001",
        "1e3",
        "1E3",
        " 1.00",
        "1.00 ",
        "1.00\n",
        "",
        ".50",
        "1.",
        "+1.00",
        "--1.00",
        "1,000.00",
        "1_000.00",
        "NaN",
        "Infinity",
        "１.００",  # full-width digits
        "0x10",
        "$1.00",
        400,
        400.0,
        None,
    ],
)
def test_from_paypal_rejects_malformed_or_over_precise_usd(value):
    with pytest.raises(MoneyError, match="Malformed USD"):
        Money.from_paypal({"value": value, "currency_code": "USD"})


@pytest.mark.parametrize("value", ["1500.0", "1500.00", "1.5"])
def test_from_paypal_rejects_decimals_for_jpy(value):
    with pytest.raises(MoneyError, match="Malformed JPY"):
        Money.from_paypal({"value": value, "currency_code": "JPY"})


@pytest.mark.parametrize(
    "amount",
    [
        {"value": "1.00"},
        {"value": "1.00", "currency_code": "USD", "currency": "USD"},
        {"value": "1.00", "currency_code": "XYZ"},
        {"value": "1.00", "currency_code": "usd"},
        {"value": "1.00", "currency": None},
        {"currency_code": "USD"},
        "400.00",
        None,
    ],
)
def test_from_paypal_rejects_bad_shapes_and_currencies(amount):
    with pytest.raises(MoneyError):
        Money.from_paypal(amount)


@pytest.mark.parametrize(
    ("money", "value"),
    [
        (usd(40000), "400.00"),
        (usd(48600), "486.00"),
        (usd(1), "0.01"),
        (usd(10), "0.10"),
        (usd(0), "0.00"),
        (usd(-30000), "-300.00"),
        (usd(-5), "-0.05"),
        (usd(123456789012), "1234567890.12"),
        (Money(1234, "EUR"), "12.34"),
        (Money(1500, "JPY"), "1500"),
        (Money(-7, "JPY"), "-7"),
    ],
)
def test_to_paypal_formats_exact_minor_digits(money, value):
    assert money.to_paypal() == {"value": value, "currency_code": money.currency}
    assert money.to_paypal("currency") == {"value": value, "currency": money.currency}


def test_to_paypal_rejects_unknown_currency_key():
    with pytest.raises(MoneyError):
        usd(1).to_paypal("code")  # type: ignore[arg-type]


@pytest.mark.parametrize("minor", [0, 1, 9, 10, 99, 100, 33333, -1, -99, -30000, 10**15])
@pytest.mark.parametrize("currency", sorted(EXPONENTS))
@pytest.mark.parametrize("key", ["currency_code", "currency"])
def test_paypal_round_trip(minor, currency, key):
    money = Money(minor, currency)
    paypal = money.to_paypal(key)
    assert Money.from_paypal(paypal) == money
    assert Money.from_paypal(paypal).to_paypal(key) == paypal
