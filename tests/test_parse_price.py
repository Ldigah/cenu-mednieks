import pytest

from parse import parse_price


@pytest.mark.parametrize("raw,expected", [
    ("12,99 €", 12.99),
    ("1 299,99 €", 1299.99),
    ("1 299 €", 1299.0),
    ("1299.99", 1299.99),
    ("1,299.99", 1299.99),
    ("1.299,99", 1299.99),
    ("1.299.99", 1299.99),
    ("€ 89", 89.0),
    ("12,99\u00a0€", 12.99),          # non-breaking space
    ("89,-", 89.0),                    # trailing separator stripped
    ("N/A", None),
    ("", None),
    (None, None),
    ("12.", 12.0),
    (19.5, 19.5),
    (1299, 1299.0),
])
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected
