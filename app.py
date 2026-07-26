"""
app.py

Streamlit UI, session-state, wiring. No LLM/business logic lives here --
that's extraction.py / agent.py. This file's job is screens, state, and
calling into those modules.

Flow: Upload -> Process -> Summary -> Chat. One task per screen, linear,
no dashboard clutter -- audience is older, non-technical users (CLAUDE.md).
"""

from __future__ import annotations

import uuid

import streamlit as st

from agent import build_chat_agent, generate_summary, send_chat_message
from extraction import (
    FileValidationError,
    extract_structured,
    load_pdf_text,
    ocr_fallback,
    validate_upload,
)
from pii_guard import PIIGuard, PIIRedactionResult
from ui_components import (
    render_disclaimer,
    render_friendly_error,
    render_pii_summary,
    render_result_cards,
    render_scanned_document_notice,
    render_search_warning,
)

st.set_page_config(page_title="Health Report Analyser", page_icon="🩺", layout="centered")

st.markdown(
    """
<style>
html, body, [class*="css"] { font-size: 1.15rem; }
.stButton button { font-size: 1.1rem; padding: 0.6rem 1.2rem; }
.block-container { padding-top: 2.5rem; padding-bottom: 3rem; }

/* More breathing room between fields in the diet-plan form. */
.st-key-diet-plan-form [data-testid="stElementContainer"] {
    margin-bottom: 0.5rem;
}

/* Round the diet-plan form's select/multiselect/text-input boxes to match
   the rounded button below them. */
.st-key-diet-plan-form [data-testid="stSelectbox"] [role="group"],
.st-key-diet-plan-form [data-testid="stTextInputRootElement"],
.st-key-diet-plan-form [data-testid="stMultiSelect"] [data-baseweb="select"] > div {
    border-radius: 10px;
}

/* Soften the "not medical advice" info banner into a gentle notice rather
   than a bold alert box -- color/icon unchanged, just softer corners and
   border so it doesn't read as urgent. Scoped to st.info only (via the
   Info-specific alert content testid) so st.warning/st.error elsewhere
   keep their normal, more attention-grabbing style. */
.stAlert:has([data-testid="stAlertContentInfo"]) [data-testid="stAlertContainer"] {
    border-radius: 8px;
    border: 1px solid rgba(49, 112, 187, 0.25);
    box-shadow: none;
}
</style>
""",
    unsafe_allow_html=True,
)

RESTRICTION_OPTIONS = [
    "Vegetarian",
    "Vegan",
    "Diabetic-friendly",
    "Gluten-free",
    "Dairy-free",
    "Nut allergy",
    "Halal",
    "Kosher",
    "Low-sodium",
]

CUISINE_OPTIONS = [
    "Indian",
    "Mediterranean",
    "Mexican",
    "Chinese",
    "Italian",
    "American",
    "Japanese",
]


@st.cache_resource
def get_pii_guard() -> PIIGuard:
    return PIIGuard()


def _init_session_state() -> None:
    if "thread_id" not in st.session_state:
        st.session_state.thread_id = str(uuid.uuid4())
    if "stage" not in st.session_state:
        st.session_state.stage = "upload"
    if "extraction_result" not in st.session_state:
        st.session_state.extraction_result = None
    if "chat_agent" not in st.session_state:
        st.session_state.chat_agent = None
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "summary_result" not in st.session_state:
        st.session_state.summary_result = None
    if "pii_summary_text" not in st.session_state:
        st.session_state.pii_summary_text = None


def _process_upload(file_bytes: bytes) -> None:
    status = st.status("Reading your report...", expanded=True)
    try:
        validate_upload(file_bytes)
    except FileValidationError as exc:
        status.update(label="We hit a problem", state="error")
        render_friendly_error(str(exc))
        return

    try:
        document = load_pdf_text(file_bytes)

        if document.scanned_page_numbers:
            render_scanned_document_notice()
            status.write("Reading the scanned pages with AI vision...")
            document = ocr_fallback(file_bytes, document)

        status.write("Removing personal details...")
        guard = get_pii_guard()
        redacted_pages = []
        aggregated_entities: dict[str, int] = {}
        for page in document.pages:
            if page.path == "digital_text":
                result = guard.redact(page.text)
                redacted_pages.append((page.page_number, result.redacted_text, page.path))
                for entity_type, count in result.entities_found.items():
                    aggregated_entities[entity_type] = aggregated_entities.get(entity_type, 0) + count
            else:
                # Path B: no redaction performed, by design (see extraction.py).
                redacted_pages.append((page.page_number, page.text, page.path))

        # One aggregated total across all digital-text pages, not one line
        # per page -- PIIRedactionResult.summary() collapses cleanly to a
        # single sentence when given the merged entity counts.
        pii_summary_text = PIIRedactionResult(
            redacted_text="", entities_found=aggregated_entities
        ).summary()
        render_pii_summary(pii_summary_text)
        st.session_state.pii_summary_text = pii_summary_text

        status.write("Checking your results...")
        from extraction import LoadedDocument, PageExtraction

        redacted_document = LoadedDocument(
            pages=[
                PageExtraction(page_number=num, text=text, path=path)
                for num, text, path in redacted_pages
            ]
        )
        extraction_result = extract_structured(redacted_document)
    except Exception:
        status.update(label="We hit a problem", state="error")
        render_friendly_error(
            "We couldn't finish reading this report. This is usually temporary "
            "— please try again in a moment. If it keeps happening, try a "
            "clearer scan or a different file."
        )
        return

    status.update(label="Almost done...", state="running")
    st.session_state.extraction_result = extraction_result
    st.session_state.stage = "summary"
    status.update(label="Done!", state="complete")
    st.rerun()


def _render_upload_screen() -> None:
    st.title("🩺 Health Report Analyser")
    render_disclaimer()
    st.write(
        "Upload your blood test or health report as a PDF. "
        "We'll remove your personal details, explain your results in plain "
        "English, and help you build a diet plan."
    )
    uploaded = st.file_uploader("Choose a PDF file", type=["pdf"])
    if uploaded is not None:
        file_bytes = uploaded.read()
        _process_upload(file_bytes)


def _render_summary_screen() -> None:
    st.title("Your results")
    render_disclaimer()

    extraction_result = st.session_state.extraction_result
    if extraction_result.extraction_warnings:
        with st.expander("Some notes about this report"):
            for warning in extraction_result.extraction_warnings:
                st.write(f"- {warning}")

    render_result_cards(extraction_result.tests)

    st.subheader("Get a summary and diet plan")
    with st.container(key="diet-plan-form"):
        cuisine = st.selectbox("Choose a cuisine", CUISINE_OPTIONS)
        restrictions = st.multiselect("Dietary restrictions or allergies", RESTRICTION_OPTIONS)
        other = st.text_input("Other restrictions (optional)")
        all_restrictions = restrictions + ([other] if other.strip() else [])

        if st.button("Generate my summary and diet plan", type="primary"):
            with st.spinner("Putting together your summary and diet plan..."):
                try:
                    result = generate_summary(
                        tests=extraction_result.tests,
                        cuisine=cuisine,
                        restrictions=all_restrictions,
                        thread_id=st.session_state.thread_id,
                    )
                except Exception:
                    result = None
                    render_friendly_error(
                        "We couldn't put together your summary right now. "
                        "Please try again in a moment."
                    )
            if result is not None:
                st.session_state.summary_result = result

    if st.session_state.summary_result is not None:
        result = st.session_state.summary_result
        if not result.search_grounded and result.search_warning:
            render_search_warning(result.search_warning)
        st.markdown(result.text)

    st.divider()
    if st.button("Chat about my results →"):
        st.session_state.stage = "chat"
        st.rerun()


def _render_chat_screen() -> None:
    st.title("Chat about your results")
    render_disclaimer()

    if st.session_state.chat_agent is None:
        st.session_state.chat_agent = build_chat_agent(
            tests=st.session_state.extraction_result.tests,
            thread_id=st.session_state.thread_id,
        )

    for role, content in st.session_state.chat_history:
        with st.chat_message(role):
            st.write(content)

    user_message = st.chat_input("Ask a question about your results, diet, or fitness...")
    if user_message:
        st.session_state.chat_history.append(("user", user_message))
        with st.chat_message("user"):
            st.write(user_message)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    response = send_chat_message(
                        st.session_state.chat_agent, user_message, st.session_state.thread_id
                    )
                except Exception:
                    response = (
                        "Sorry, something went wrong answering that. "
                        "Please try asking again."
                    )
            st.write(response)
        st.session_state.chat_history.append(("assistant", response))

    st.divider()
    if st.button("← Back to summary"):
        st.session_state.stage = "summary"
        st.rerun()


def main() -> None:
    _init_session_state()
    if st.session_state.stage == "upload":
        _render_upload_screen()
    elif st.session_state.stage == "summary":
        _render_summary_screen()
    elif st.session_state.stage == "chat":
        _render_chat_screen()


if __name__ == "__main__":
    main()
