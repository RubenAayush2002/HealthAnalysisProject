import pytest

from reference_ranges import compute_status, parse_range_string


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("70-99", (70.0, 99.0)),
        ("70 - 99", (70.0, 99.0)),
        ("<200", (None, 200.0)),
        ("< 200", (None, 200.0)),
        (">40", (40.0, None)),
        ("> 40", (40.0, None)),
        ("70–99", (70.0, 99.0)),  # en dash
        ("70—99", (70.0, 99.0)),  # em dash
        ("99-70", (70.0, 99.0)),  # reversed order still normalized
        ("", (None, None)),
        ("   ", (None, None)),
        ("not a range", (None, None)),
        ("negative", (None, None)),
    ],
)
def test_parse_range_string(raw, expected):
    assert parse_range_string(raw) == expected


@pytest.mark.parametrize(
    "value,low,high,expected",
    [
        (85, 70, 99, "NORMAL"),
        (50, 70, 99, "LOW"),
        (150, 70, 99, "HIGH"),
        (70.5, 70, 99, "BORDERLINE"),  # near low boundary
        (98.9, 70, 99, "BORDERLINE"),  # near high boundary
        (250, None, 200, "HIGH"),
        (150, None, 200, "NORMAL"),
        (50, 40, None, "NORMAL"),
        (30, 40, None, "LOW"),
        (100, None, None, "UNVERIFIED"),
    ],
)
def test_compute_status(value, low, high, expected):
    assert compute_status(value, low, high) == expected
