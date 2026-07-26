# Health Report Analyser — V2 Build Spec

This document is a build specification for an AI coding agent (Claude Code) to
implement the full V2 codebase. It captures the architecture, module
responsibilities, and design decisions agreed on during the V2 planning phase.
Where a decision was deliberately made a certain way for a reason (safety,
cost, UX), that reasoning is included so the implementation doesn't drift from
intent.

**Audience for the product itself:** older, non-technical users. Every UX
decision should be read through that lens — minimal steps, large clear text,
explicit status messages, no jargon, no dead ends.

**Not medical advice.** The app must say so clearly and repeatedly (upload
screen, summary screen, chat screen).

---

## 1. What this app does

A user uploads a health/blood test report PDF. The app:

1. Extracts the report's text (with OCR fallback for scanned/photographed PDFs).
2. Strips personally identifiable information (name, DOB, address, phone,
   email, MRN, insurance ID) from the text **before** any of it is sent to a
   third-party LLM or search API.
3. Extracts every test result into structured data (test name, value, unit,
   reference range, status) — status is computed in Python, not decided by
   the LLM.
4. Shows the user a plain-English summary: what's normal, what needs
   attention, and why — using color-coded cards, not a raw table.
5. Generates a diet plan for a cuisine of the user's choice, respecting any
   dietary restrictions/allergies they select, grounded in live web search.
6. Lets the user ask follow-up questions in a chat interface, which can
   answer directly from their own report's structured data or pull in
   general web search for open-ended health/diet/fitness questions.

---

## 2. Architecture overview

Two-tier separation, same principle as V1: UI code and logic code are
different files, and logic code has zero Streamlit dependency so it can be
tested headlessly.

```
app.py              Streamlit UI, session-state, wiring. No LLM/business logic.
agent.py            Orchestration: builds the extraction, summary, and chat agents.
pii_guard.py         PII detection/redaction (already implemented — see below).
extraction.py        PDF text/OCR extraction + structured data extraction & validation.
models.py            Pydantic schemas for structured test data.
model_config.py       Per-task LLM configuration (model, temperature, retry).
reference_ranges.py  Fallback reference range table + range-string parser.
tracing.py            LangSmith tracing setup (mostly config, minimal code).
prompts.py            All system prompts, centralized (not inline in agent.py).
ui_components.py       Reusable Streamlit rendering helpers (result cards, status banners).
tests/                pytest suite.
.env.example
pyproject.toml / requirements.txt
```

Data flow, end to end:

```
PDF upload
   │
   ▼
extraction.load_pdf_text()  ──(low text yield?)──► extraction.ocr_fallback()
   │
   ▼
pii_guard.PIIGuard().redact()      # ALWAYS happens before any external call
   │
   ▼
extraction.extract_structured()    # LLM extracts fields, Python computes status
   │  (returns list[TestResult], Pydantic-validated)
   │
   ├──► ui_components: render color-coded result cards
   │
   ▼
agent.generate_summary()           # ReAct agent: ,Gemini + Tavily, cuisine + restrictions aware
   │
   ▼
st.session_state.report_data       # structured data lives here for the session
   │
   ▼
agent.build_chat_agent()           # structured data injected into system prompt directly;
                                    # Tavily tool available for open-ended questions
```

**No vector DB / Chroma by default.** A lab report is ~20-50 structured
values — small enough to put directly in the chat agent's system prompt every
turn. This removes an entire dependency (embeddings, Chroma) for the common
case and makes "what was my cholesterol" answerable without any tool call.
Keep RAG as an *optional, off-by-default* code path only for reports with long
free-text doctor's notes that don't fit in context — do not build this in the
first pass; leave a clear extension point (`extraction.py` should keep raw
free-text notes in a separate field so this is easy to bolt on later).

---

## 3. Module specs

### `pii_guard.py` — already implemented, drop in as-is

Provided as a finished module (attached separately / already in repo).
Redacts PERSON, EMAIL_ADDRESS, PHONE_NUMBER, LOCATION (incl. street
addresses), US_SSN, CREDIT_CARD, MEDICAL_RECORD_NUMBER, INSURANCE_ID, and DOB
(only when near a "DOB"/"Date of Birth" label — other dates, like test dates,
are intentionally left alone since they're clinically meaningful).

**Scope: Path A (digital PDFs) only.** This module operates on extracted
text, so it applies wherever `extraction.py` has produced text — the normal
digital-PDF path. It does not apply to Path B (scanned images sent directly
to the vision LLM) — see §3 `extraction.py` for why that path is handled
differently.

Integration point: called once, immediately after text is extracted from the
PDF (`extraction.load_pdf_text()` / OCR fallback), before the text is passed
to *any* LLM call or embedding step. Instantiate once via `@st.cache_resource`
in `app.py` — loading the NER model has real cost, don't reload per session.

`PIIRedactionResult.summary()` should be surfaced to the user in the
processing status block (e.g. "Removed before processing: 1 patient name, 1
DOB, 1 email") — this is a concrete, honest transparency feature, not just
internal logging.

### `models.py` — structured data schema

```python
class TestResult(BaseModel):
    test_name: str
    value: float
    unit: str
    range_raw: str                 # exactly as printed on the report
    range_low: float | None
    range_high: float | None
    range_source: Literal["report", "fallback_table", "unavailable"]
    status: Literal["NORMAL", "HIGH", "LOW", "BORDERLINE", "UNVERIFIED"]

class ExtractionResult(BaseModel):
    tests: list[TestResult]
    free_text_notes: str | None    # doctor's notes etc, kept separate — extension
                                    # point for optional future RAG, not used by default
    extraction_warnings: list[str] # e.g. "page 3 had low text confidence"
```

### `extraction.py` — PDF/OCR extraction + structured extraction + validation

**Text extraction — two paths, with different PII handling by design:**

- **Path A (normal digital PDF, has a text layer):** `PyPDFLoader`-based text
  extraction, per page. This text goes through `pii_guard.py` redaction
  (§3, below) before it is ever sent to an LLM. Full PII protection.

- **Path B (scanned/photographed PDF, no usable text layer):** if a page's
  extracted text falls below a minimum character threshold
  (`MIN_CHARS_PER_PAGE`, configurable), treat it as image-based and send that
  page's rendered image **directly** to a vision-capable Gemini call for
  extraction — no image-level PII redaction is performed on this path.
  This is a deliberate, accepted tradeoff, not an oversight: reliably
  detecting and blacking out PII regions on a scanned image (via local OCR +
  bounding boxes) was evaluated and found too unreliable in practice — on
  sparse, non-sentence label/value layouts, generic name/location detectors
  produced false positives on lab test names themselves (e.g. blacking out
  "Glucose"), which is worse than the privacy risk it was meant to solve.
  Given that, Path B intentionally sends the raw image — including any
  visible name/DOB/address — to the cloud vision LLM.

  **This must be disclosed to the user at the point it happens** — see §4,
  scanned-document disclosure notice. Do not present Path B processing with
  the same status messaging as Path A; the privacy guarantee is genuinely
  different and the UI must say so.

**Structured extraction:**
- Use `with_structured_output(ExtractionResult)` (or tool-forced JSON) so the
  LLM returns the schema directly — no prose re-parsing.
- The LLM's job is extraction only (pull out test name/value/unit/range as
  printed). **It must not decide status.**
- After extraction, a pure-Python function parses `range_raw` into
  `range_low`/`range_high` (handles `"70-99"`, `"<200"`, `">40"`, en/em dashes,
  etc.) and computes `status` by numeric comparison. If a range is missing
  from the report, look it up in `reference_ranges.py`'s fallback table and
  set `range_source="fallback_table"`. If neither the report nor the fallback
  table has a range, set `status="UNVERIFIED"` and `range_source="unavailable"`
  — never guess.
- For long/multi-page reports: chunk by page, extract per chunk, merge
  results, and de-duplicate by `test_name` (keep the last occurrence, log a
  warning if values differ between duplicates — could indicate a merge
  issue). Add merge warnings to `extraction_warnings`.

### `reference_ranges.py`

A small curated dict of standard adult reference ranges for common tests
(glucose, total/LDL/HDL cholesterol, sodium, potassium, hemoglobin, TSH,
creatinine, etc. — expand as needed), used only as a fallback. Include a
`parse_range_string(raw: str) -> tuple[float | None, float | None]` utility
used by `extraction.py`.

Flag clearly in code comments: this table is a general adult baseline, not
personalized by age/sex — a known limitation, not a hidden assumption.

### `model_config.py` — per-task LLM configuration

```python
TASK_CONFIG = {
    "extraction": {"model": "gemini-2.5-flash", "temperature": 0},
    "summary_diet": {"model": "gemini-2.5-flash", "temperature": 0.5},
    "chat": {"model": "gemini-2.5-flash", "temperature": 0.3},
}

def get_llm_for_task(task: str): ...
```

Wrap all three in `@st.cache_resource`-compatible factory functions (cache
per task, same pattern as V1's `get_llm()`). Extraction is deliberately
temperature 0 — precision matters and it's paired with code-side validation.

### `prompts.py` — centralized system prompts

All system prompts (extraction, summary/diet, chat) live here as constants
or small template functions, not inline strings scattered across
`agent.py`. Makes them reviewable and testable in one place.

The chat system prompt must:
- Include the user's full structured `TestResult` list directly (formatted
  compactly, e.g. a small markdown table) so number-based questions never
  need a tool call.
- Explicitly refuse to give specific medication dosage recommendations —
  this must also be enforced in code (see below), not prompt-only.
- Stay scoped to health/diet/fitness topics; politely decline off-topic
  questions.

The summary/diet prompt must:
- Take the selected cuisine **and** selected dietary restrictions/allergies
  as explicit structured inputs (not just prose), and instruct the agent to
  incorporate both into web search queries themselves (e.g. "vegetarian
  diabetic-friendly Indian diet," not just "Indian diet") — restrictions
  must shape the search, not just get mentioned in the final text.

### Code-level safety guardrail (not prompt-only)

Add a simple code-side check on chat responses (regex/keyword check for
dosage-pattern language — e.g. "mg", "take X twice daily" combined with drug
name patterns) that intercepts and replaces the response with a standard
redirect-to-a-doctor message if triggered. This is a blunt backstop, not a
replacement for the prompt-level instruction — document it as such.

### `agent.py` — orchestration

- `generate_summary(extraction_result, cuisine, restrictions)`: ReAct agent,
  Gemini (`summary_diet` task config) + Tavily search tool. Produces health
  snapshot, normal/attention breakdown, cuisine+restriction-aware diet plan,
  next steps.
- `build_chat_agent(extraction_result, thread_id)`: LangGraph ReAct agent,
  Gemini (`chat` task config), Tavily search tool only (no retriever tool by
  default — see §2). Structured data is injected via the system prompt, not
  a tool. `thread_id` remains required (per-session UUID, no hardcoded
  default — this was a real cross-user bug in V1, don't regress it).

Both should go through the retry wrapper (below) and be traced (below).

### Retry logic

Use `tenacity`. Apply `@retry` with exponential backoff + jitter
(`wait_exponential_jitter`) to every external call: Gemini calls and Tavily
calls. Retry only on transient error types (timeouts, rate limits, 5xx) —
explicitly do not retry on validation errors or malformed-request errors,
since those will fail identically every time.

When Tavily search ultimately fails after retries, `generate_summary` must
still return a diet plan (ungrounded), but flag it — surface a visible
warning in the UI: "Live search unavailable — this diet plan is based on
general knowledge, not verified current sources." Never silently degrade
without telling the user.

### `tracing.py`

Mostly environment configuration:

```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=<from .env>
LANGCHAIN_PROJECT=health-report-analyser
```

Tag each trace with the session's `thread_id` (LangSmith run metadata) so a
specific user's session can be isolated when debugging. Since PII redaction
happens before any LLM call, traces should not contain raw PII either —
verify this holds (i.e. don't log the pre-redaction raw text anywhere,
including in trace metadata).

### File validation

Validate uploads beyond Streamlit's `type=["pdf"]` default: enforce a max
file size (e.g. 15MB) and a max page count (e.g. 30 pages), with a friendly
error message on rejection, not a crash.

---

## 4. UI/UX spec (`app.py`, `ui_components.py`)

Audience is older, non-technical users — every choice below is in service of
that.

- **One task per screen**, linear flow: Upload → Process → Summary → Chat.
  No dashboard-style clutter.
- **Explicit step-by-step status during processing** (`st.status`), not a
  bare spinner: "Reading your report... Removing personal details...
  Checking your results... Almost done." Include the PII redaction summary
  line here.
- **Scanned-document disclosure (Path B only):** if a page is routed through
  the image-based OCR fallback (§3), the status block must show a distinct
  message before that page is sent, e.g. *"This looks like a scanned
  document. To read it, the image will be sent to Google's AI for
  processing, including any personal details visible on the page."* This is
  a genuine difference in privacy handling vs. normal digital PDFs (Path A,
  which is fully text-redacted first) and must not be hidden behind the same
  generic status message.
- **Color-coded result cards** (green = normal, amber = borderline, red =
  high/low, gray = unverified/no range available) instead of a plain table.
  Large text, one card per test, test name + value + unit + plain-English
  one-line meaning.
- **Cuisine selection**: keep existing dropdown, but add a multi-select for
  dietary restrictions/allergies (vegetarian, vegan, diabetic-friendly,
  gluten-free, dairy-free, nut allergy, halal, kosher, low-sodium) plus one
  free-text "other" field.
- **Friendly, non-technical error messages** everywhere — never show a raw
  exception or status code to the user. "We couldn't read this file, please
  try a clearer scan" + retry button, not a traceback.
- **Disclaimer** ("Not medical advice — always confirm with your doctor")
  visible on upload, summary, and chat screens, not just once.
- Custom theme via `.streamlit/config.toml` + light custom CSS: larger base
  font size, high contrast, generous spacing between elements. No
  icon-only buttons — always pair icons with text labels.

---

## 5. Testing (`tests/`)

Minimum coverage for this pass:
- `reference_ranges.parse_range_string()` — table-driven tests covering
  `"70-99"`, `"<200"`, `">40"`, en-dash/em-dash variants, malformed input.
- Status computation logic (NORMAL/HIGH/LOW/BORDERLINE/UNVERIFIED) given
  value + parsed range.
- `pii_guard.PIIGuard.redact()` — the sample report used during design
  (name, DOB, MRN, address, phone, email, insurance ID) as a regression
  fixture; assert no raw PII substrings remain in `redacted_text`.
- File validation (oversized file, wrong type, too many pages) rejected
  with friendly errors, not exceptions bubbling up.
- Retry wrapper: mock a transient failure followed by success, assert it
  eventually succeeds; mock a non-transient failure, assert it does NOT
  retry.

---

## 6. Environment variables (`.env.example`)

```
GOOGLE_API_KEY=
TAVILY_API_KEY=
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=health-report-analyser
```

---

## 7. Dependencies (new/changed vs V1)

```
presidio-analyzer
presidio-anonymizer
spacy  (+ en_core_web_sm model)
tenacity
pydantic
# existing: langchain-google-genai, langgraph, langchain-huggingface,
# streamlit, PyPDFLoader dependency, tavily client
# Chroma/embeddings dependency becomes optional — do not require it in the
# default code path (see §2)
```

---

## 8. Explicitly out of scope for this pass

Do not build these now — noted so the agent doesn't scope-creep into them:

- User accounts / login / persistence across sessions
- Multi-report comparison / trend tracking over time
- Optional RAG-over-notes path (leave the extension point per §2, don't build it)
- Anything beyond the reference-range fallback table's general adult baseline
  (no age/sex personalization yet)

---

## 9. Suggested build order

1. `models.py` + `reference_ranges.py` (schema + range parsing/status logic) — foundation everything else depends on.
2. `extraction.py` text extraction + OCR fallback.
3. Wire in `pii_guard.py` (already built) at the correct pipeline point.
4. Structured extraction call + validation, replacing the old free-text `extract_and_label`.
5. `model_config.py` + `prompts.py`.
6. `agent.py`: summary/diet agent, then chat agent (structured-data-in-prompt, no vector DB).
7. Retry logic (`tenacity`) wrapped around all external calls.
8. Tracing config.
9. `app.py` + `ui_components.py` — UI rebuild, dietary restrictions UI, status messaging.
10. `tests/`.
