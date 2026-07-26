import pytest

from pii_guard import PIIGuard

SAMPLE_REPORT = """
Patient Name: John Alan Smith
Date of Birth: 03/14/1958
MRN: MRN-88213
Address: 42 Elm Grove, Springfield
Phone: (212) 947-3823
Email: john.smith@example.com
Policy Number: POL-2938471

Test Date: 2026-01-15

Glucose: 95 mg/dL (70-99)
Total Cholesterol: 210 mg/dL (<200)
"""

RAW_PII_SUBSTRINGS = [
    "John Alan Smith",
    "03/14/1958",
    "MRN-88213",
    "42 Elm Grove",
    "(212) 947-3823",
    "john.smith@example.com",
    "POL-2938471",
]


@pytest.fixture(scope="module")
def guard() -> PIIGuard:
    return PIIGuard()


def test_redacts_all_known_pii(guard: PIIGuard):
    result = guard.redact(SAMPLE_REPORT)
    for substring in RAW_PII_SUBSTRINGS:
        assert substring not in result.redacted_text, f"raw PII leaked: {substring!r}"


def test_had_pii_true_for_sample(guard: PIIGuard):
    result = guard.redact(SAMPLE_REPORT)
    assert result.had_pii is True
    assert sum(result.entities_found.values()) > 0


def test_preserves_clinically_relevant_test_date(guard: PIIGuard):
    result = guard.redact(SAMPLE_REPORT)
    assert "2026-01-15" in result.redacted_text


def test_preserves_test_values(guard: PIIGuard):
    result = guard.redact(SAMPLE_REPORT)
    assert "Glucose" in result.redacted_text
    assert "95 mg/dL" in result.redacted_text


def test_empty_text_returns_no_pii(guard: PIIGuard):
    result = guard.redact("")
    assert result.had_pii is False
    assert result.redacted_text == ""


def test_summary_no_pii_message():
    from pii_guard import PIIRedactionResult

    result = PIIRedactionResult(redacted_text="clean text", entities_found={})
    assert result.summary() == "No personal identifiers detected."


def test_summary_lists_entities():
    from pii_guard import PIIRedactionResult

    result = PIIRedactionResult(
        redacted_text="...", entities_found={"PERSON": 1, "EMAIL_ADDRESS": 1}
    )
    summary = result.summary()
    assert summary.startswith("Removed before processing:")
    assert "1 person" in summary
    assert "1 email address" in summary


# ---------------------------------------------------------------------------
# Regression tests for over-redaction on lab-report vocabulary and dates
# (found via a real report: test names and timestamps were being flagged
# as PERSON/PHONE_NUMBER false positives).
# ---------------------------------------------------------------------------

LAB_REPORT_SAMPLE = """
DEPARTMENT OF LABORATORY MEDICINE-BIOCHEMISTRY
Alkaline Phosphatase
Method : IFCC AMP buffer
: 107
U/L
30-115
Vitamin B12
Method : Chemiluminescence
: 284.00
pg/mL
180-914
LDL Cholesterol
Method : Enzymatic
: 110
mg/dL
Optimal: <100
Page 1 of 11
Printed By : admin
Printed On: 12-06-2026 04:59:08
Patient Name
Lab Ref No/UHID
Age / Gender
:
:
: Mrs. Jane Doe
Referred By   Dr.
: DR. HOSPITAL CASE
Registration Loc
: RHC Sassoon Rd
Release Date
:
12-06-2026 02:10 PM
Report Date
:
12-06-2026 02:02 PM
"""


def test_lab_test_names_survive_redaction(guard: PIIGuard):
    result = guard.redact(LAB_REPORT_SAMPLE)
    assert "Alkaline Phosphatase" in result.redacted_text
    assert "Vitamin B12" in result.redacted_text
    assert "LDL Cholesterol" in result.redacted_text


def test_field_labels_not_flagged_as_person(guard: PIIGuard):
    result = guard.redact(LAB_REPORT_SAMPLE)
    assert "Registration Loc" in result.redacted_text
    assert "Report Date" in result.redacted_text


def test_timestamps_not_flagged_as_phone_number(guard: PIIGuard):
    result = guard.redact(LAB_REPORT_SAMPLE)
    assert "12-06-2026 04:59:08" in result.redacted_text
    assert "12-06-2026 02:10 PM" in result.redacted_text
    assert "12-06-2026 02:02 PM" in result.redacted_text


def test_real_patient_name_still_redacted_in_dense_layout(guard: PIIGuard):
    """
    The patient's name and its "Patient Name" label sit several tokens
    apart in this layout (other field labels/colons between them) -- this
    is what broke naive context-anchoring in testing. Confirm the name is
    still redacted despite that distance (context anchoring here is
    additive-only, not a gate).
    """
    result = guard.redact(LAB_REPORT_SAMPLE)
    assert "Jane Doe" not in result.redacted_text


def test_reference_range_not_flagged_as_phone_number(guard: PIIGuard):
    result = guard.redact("HDL Cholesterol: 75 mg/dL 35.0-65.0")
    assert "35.0-65.0" in result.redacted_text
