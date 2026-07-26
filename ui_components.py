"""
ui_components.py

Reusable Streamlit rendering helpers. No LLM/business logic here -- pure
presentation. Large text, high contrast, one card per test, in service of
the older/non-technical target audience (see CLAUDE.md).
"""

from __future__ import annotations

import streamlit as st

from models import Status, TestResult

DISCLAIMER_TEXT = (
    "⚠️ **This is not medical advice.** Always confirm any concerns with your doctor."
)

_STATUS_STYLE: dict[Status, dict[str, str]] = {
    "NORMAL": {"bg": "#e6f4ea", "fg": "#1e4620", "label": "Normal"},
    "BORDERLINE": {"bg": "#fff4e0", "fg": "#7a4b00", "label": "Borderline"},
    "HIGH": {"bg": "#fdeaea", "fg": "#7a1212", "label": "High"},
    "LOW": {"bg": "#fdeaea", "fg": "#7a1212", "label": "Low"},
    "UNVERIFIED": {"bg": "#eeeeee", "fg": "#444444", "label": "No range available"},
}

_STATUS_MEANING: dict[Status, str] = {
    "NORMAL": "This result is within the typical healthy range.",
    "BORDERLINE": "This result is close to the edge of the typical range — worth keeping an eye on.",
    "HIGH": "This result is above the typical range.",
    "LOW": "This result is below the typical range.",
    "UNVERIFIED": "We couldn't find a reference range to compare this to.",
}

# Result-group header tints (subtle washes, softer than the card
# backgrounds above so a whole row of expander headers doesn't compete
# visually with the cards inside them). Keyed by the st.expander `key=`
# used below, which Streamlit exposes as a stable `.st-key-{key}` CSS
# class -- that's what lets each group's header be tinted individually.
_GROUP_STYLE = {
    "rc-group-attention": "#fdf2f2",
    "rc-group-borderline": "#fdf8ee",
    "rc-group-normal": "#f0f8f2",
    "rc-group-unverified": "#f3f3f3",
}

_RESULT_GROUPS_CSS = """
<style>
/* Card-like container wrapping the whole four-group results block. */
.st-key-rc-results-card {
    border: 1px solid #e4e4e4;
    border-radius: 14px;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.05);
    padding: 0.75rem 0.9rem 0.25rem;
    margin-bottom: 1.5rem;
}
</style>
""" + "\n".join(
    f"""<style>
.st-key-{key} [data-testid="stExpander"] summary {{
    background-color: {bg} !important;
    border-radius: 10px;
}}
</style>"""
    for key, bg in _GROUP_STYLE.items()
)


def render_disclaimer() -> None:
    st.info(DISCLAIMER_TEXT)


def render_result_card(test: TestResult) -> None:
    style = _STATUS_STYLE[test.status]
    meaning = _STATUS_MEANING[test.status]
    range_text = f"Typical range: {test.range_raw}" if test.range_raw else "No range printed on report"
    st.markdown(
        f"""
<div style="background-color:{style['bg']};color:{style['fg']};
            border-radius:12px;padding:1.25rem 1.5rem;margin-bottom:1rem;">
  <div style="font-size:1.3rem;font-weight:700;">{test.test_name}</div>
  <div style="font-size:1.6rem;font-weight:800;margin:0.25rem 0;">
    {test.value} {test.unit}
    <span style="font-size:1rem;font-weight:600;margin-left:0.75rem;">{style['label']}</span>
  </div>
  <div style="font-size:1.05rem;">{meaning}</div>
  <div style="font-size:0.95rem;opacity:0.8;margin-top:0.25rem;">{range_text}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def render_result_cards(tests: list[TestResult]) -> None:
    if not tests:
        st.warning("We couldn't find any test results in this report.")
        return

    attention = [t for t in tests if t.status in ("HIGH", "LOW")]
    borderline = [t for t in tests if t.status == "BORDERLINE"]
    normal = [t for t in tests if t.status == "NORMAL"]
    unverified = [t for t in tests if t.status == "UNVERIFIED"]

    st.markdown(_RESULT_GROUPS_CSS, unsafe_allow_html=True)

    with st.container(key="rc-results-card"):
        if attention:
            with st.expander(f"🔴 Needs attention **({len(attention)})**", expanded=True, key="rc-group-attention"):
                for test in attention:
                    render_result_card(test)

        if borderline:
            with st.expander(f"🟡 Borderline **({len(borderline)})**", expanded=True, key="rc-group-borderline"):
                for test in borderline:
                    render_result_card(test)

        if normal:
            with st.expander(f"✅ Normal **({len(normal)})**", expanded=False, key="rc-group-normal"):
                for test in normal:
                    render_result_card(test)

        if unverified:
            with st.expander(f"⚪ Couldn't verify **({len(unverified)})**", expanded=False, key="rc-group-unverified"):
                for test in unverified:
                    render_result_card(test)


def render_pii_summary(summary_line: str) -> None:
    st.caption(f"🔒 {summary_line}")


def render_scanned_document_notice() -> None:
    st.warning(
        "📄 This looks like a scanned document. To read it, the image will be "
        "sent to Google's AI for processing, **including any personal details "
        "visible on the page** (this is different from how we handle typed/"
        "digital reports, which have personal details removed first)."
    )


def render_search_warning(message: str) -> None:
    st.warning(f"🔍 {message}")


def render_friendly_error(message: str) -> None:
    st.error(message)


def render_processing_status_step(container, message: str) -> None:
    container.write(message)
