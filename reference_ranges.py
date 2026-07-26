"""
reference_ranges.py

Two things:
1. parse_range_string() -- turns a reference range as printed on a report
   ("70-99", "<200", ">40", "70 - 99 mg/dL", en/em dash variants, etc.) into
   a (low, high) tuple of floats. Either side may be None (open-ended range).
2. compute_status() -- pure numeric comparison of a value against a parsed
   range. This is the ONLY place status is decided -- the LLM never sees
   this logic and never computes status itself.
3. FALLBACK_RANGES -- a small curated table of common adult reference ranges,
   used only when a report doesn't print its own range.

NOTE: FALLBACK_RANGES is a general adult baseline only. It is not
personalized by age or sex. This is a known, documented limitation, not a
hidden assumption -- surface it in the UI wherever range_source ==
"fallback_table" seems useful, and don't extend this table to pretend
otherwise without adding real age/sex dimensions.
"""

from __future__ import annotations

import re

from models import Status

# ---------------------------------------------------------------------------
# Range string parsing
# ---------------------------------------------------------------------------

# Normalize en-dash/em-dash/minus-sign variants to a plain hyphen before
# parsing so "70–99" and "70—99" behave the same as "70-99".
_DASH_CHARS = "‐‑‒–—−"
_DASH_RE = re.compile(f"[{_DASH_CHARS}]")

_NUMBER = r"\d+(?:\.\d+)?"

_BETWEEN_RE = re.compile(rf"^\s*({_NUMBER})\s*-\s*({_NUMBER})\s*")
_LESS_THAN_RE = re.compile(rf"^\s*<=?\s*({_NUMBER})\s*")
_GREATER_THAN_RE = re.compile(rf"^\s*>=?\s*({_NUMBER})\s*")


def parse_range_string(raw: str) -> tuple[float | None, float | None]:
    """
    Parse a reference-range string into (low, high). Either side may be
    None for an open-ended range (e.g. "<200" -> (None, 200.0)).
    Returns (None, None) for empty/unparseable input -- never raises.
    """
    if not raw or not raw.strip():
        return (None, None)

    text = _DASH_RE.sub("-", raw.strip())
    # Strip a leading unit-free label like "Normal:" if present, and any
    # trailing unit text (e.g. "70-99 mg/dL") -- we only need the numbers.
    text = text.strip()

    m = _BETWEEN_RE.match(text)
    if m:
        low, high = float(m.group(1)), float(m.group(2))
        if low > high:
            low, high = high, low
        return (low, high)

    m = _LESS_THAN_RE.match(text)
    if m:
        return (None, float(m.group(1)))

    m = _GREATER_THAN_RE.match(text)
    if m:
        return (float(m.group(1)), None)

    return (None, None)


# ---------------------------------------------------------------------------
# Status computation
# ---------------------------------------------------------------------------

# How close to a boundary (as a fraction of the range width) counts as
# "borderline" rather than a clean NORMAL/HIGH/LOW. Only applies when both
# bounds are known -- open-ended ranges just get a HIGH/LOW/NORMAL call.
_BORDERLINE_FRACTION = 0.05


def compute_status(value: float, range_low: float | None, range_high: float | None) -> Status:
    """
    Pure numeric comparison of value against a parsed range. No LLM
    involvement. Returns UNVERIFIED when neither bound is available.
    """
    if range_low is None and range_high is None:
        return "UNVERIFIED"

    if range_low is not None and value < range_low:
        return "LOW"
    if range_high is not None and value > range_high:
        return "HIGH"

    if range_low is not None and range_high is not None:
        width = range_high - range_low
        if width > 0:
            margin = width * _BORDERLINE_FRACTION
            if value - range_low <= margin or range_high - value <= margin:
                return "BORDERLINE"

    return "NORMAL"


# ---------------------------------------------------------------------------
# Fallback reference ranges (general adult baseline)
# ---------------------------------------------------------------------------

# test_name (lowercase, matched case-insensitively) -> (low, high, unit)
# Expand as needed. Units are informational only (for display / sanity
# checking); status computation compares raw numeric value against
# range_low/range_high regardless of unit.
FALLBACK_RANGES: dict[str, tuple[float | None, float | None, str]] = {
    "glucose": (70.0, 99.0, "mg/dL"),
    "glucose, fasting": (70.0, 99.0, "mg/dL"),
    "hba1c": (4.0, 5.6, "%"),
    "total cholesterol": (None, 200.0, "mg/dL"),
    "cholesterol, total": (None, 200.0, "mg/dL"),
    "ldl cholesterol": (None, 100.0, "mg/dL"),
    "ldl": (None, 100.0, "mg/dL"),
    "hdl cholesterol": (40.0, None, "mg/dL"),
    "hdl": (40.0, None, "mg/dL"),
    "triglycerides": (None, 150.0, "mg/dL"),
    "sodium": (135.0, 145.0, "mmol/L"),
    "potassium": (3.5, 5.1, "mmol/L"),
    "chloride": (98.0, 107.0, "mmol/L"),
    "calcium": (8.6, 10.3, "mg/dL"),
    "hemoglobin": (13.0, 17.0, "g/dL"),
    "hematocrit": (38.0, 50.0, "%"),
    "wbc": (4.5, 11.0, "10^3/uL"),
    "white blood cell count": (4.5, 11.0, "10^3/uL"),
    "rbc": (4.2, 5.9, "10^6/uL"),
    "platelet count": (150.0, 450.0, "10^3/uL"),
    "platelets": (150.0, 450.0, "10^3/uL"),
    "tsh": (0.4, 4.0, "mIU/L"),
    "creatinine": (0.6, 1.3, "mg/dL"),
    "bun": (7.0, 20.0, "mg/dL"),
    "blood urea nitrogen": (7.0, 20.0, "mg/dL"),
    "alt": (7.0, 56.0, "U/L"),
    "ast": (10.0, 40.0, "U/L"),
    "vitamin d": (30.0, 100.0, "ng/mL"),
    "vitamin b12": (200.0, 900.0, "pg/mL"),
}


def lookup_fallback_range(test_name: str) -> tuple[float | None, float | None] | None:
    """Case-insensitive lookup into FALLBACK_RANGES. Returns None if absent."""
    entry = FALLBACK_RANGES.get(test_name.strip().lower())
    if entry is None:
        return None
    low, high, _unit = entry
    return (low, high)
