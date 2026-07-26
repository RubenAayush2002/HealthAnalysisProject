from extraction import PageExtraction, build_extraction_input


def test_build_extraction_input_labels_and_orders_pages():
    pages = [
        PageExtraction(page_number=0, text="Glucose: 95 mg/dL", path="digital_text"),
        PageExtraction(page_number=1, text="Cholesterol: 180 mg/dL", path="digital_text"),
        PageExtraction(page_number=2, text="Sodium: 140 mmol/L", path="digital_text"),
    ]

    combined = build_extraction_input(pages)

    # Each page's marker and text appear, in the order the pages were given.
    assert combined.index("=== Page 0 ===") < combined.index("=== Page 1 ===") < combined.index("=== Page 2 ===")
    assert "Glucose: 95 mg/dL" in combined
    assert "Cholesterol: 180 mg/dL" in combined
    assert "Sodium: 140 mmol/L" in combined
    assert combined.index("Glucose: 95 mg/dL") < combined.index("Cholesterol: 180 mg/dL")
    assert combined.index("Cholesterol: 180 mg/dL") < combined.index("Sodium: 140 mmol/L")


def test_build_extraction_input_uses_page_number_not_list_position():
    # Page markers should reflect the actual page_number field, even if the
    # list isn't zero-indexed sequential (e.g. only a subset of pages passed).
    pages = [
        PageExtraction(page_number=0, text="First", path="digital_text"),
        PageExtraction(page_number=3, text="Fourth", path="digital_text"),
    ]

    combined = build_extraction_input(pages)

    assert "=== Page 0 ===" in combined
    assert "=== Page 3 ===" in combined
    assert "=== Page 1 ===" not in combined


def test_build_extraction_input_empty_list():
    assert build_extraction_input([]) == ""


def test_build_extraction_input_single_page():
    pages = [PageExtraction(page_number=0, text="Only page", path="digital_text")]
    combined = build_extraction_input(pages)
    assert combined == "=== Page 0 ===\nOnly page"
