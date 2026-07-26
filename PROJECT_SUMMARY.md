# Project Summary — Health Report Analyser V2

Machine-readable-ish snapshot of the current codebase state, for an LLM
picking up this project cold. Full rationale lives in [README.md](README.md)
(the original build spec); operating rules live in [CLAUDE.md](CLAUDE.md).
This file is a third thing: what's actually built, right now.

## One-line description

Streamlit app: user uploads a blood-test PDF → PII is stripped → an LLM
extracts structured test results (Python computes normal/high/low status,
never the LLM) → the app shows color-coded result cards, a cuisine-aware
diet plan grounded in web search, and a chat interface scoped to the report.
Audience is older, non-technical users. Not medical advice — disclaimed on
every screen.

## Status: functionally complete, manually verified once

All modules in the build spec exist and are wired together. The full
Upload → Process → Summary → Diet Plan → Chat flow has been driven
end-to-end with Playwright against a synthetic sample PDF and confirmed
working, including PII redaction. `pytest` suite (49 tests) passes. Not yet
exercised live: the scanned-PDF (Path B / vision OCR) path.

## File-by-file

| File | Role | Depends on |
|---|---|---|
| `app.py` | Streamlit UI + session-state wiring only. Three screens: upload, summary, chat. No LLM/business logic lives here. | `agent`, `extraction`, `pii_guard`, `ui_components` |
| `agent.py` | Builds the summary/diet ReAct agent and the chat ReAct agent (LangGraph `create_react_agent`, Gemini + Tavily tool). Chat agent carries cross-turn memory via an in-process `MemorySaver` checkpointer keyed on `thread_id`. Code-level dosage guardrail (regex) on chat responses. | `model_config`, `prompts`, `llm_utils`, `retry`, `tracing` |
| `extraction.py` | PDF text extraction (Path A: text layer via PyMuPDF/pypdf) + OCR fallback (Path B: page image → Gemini vision, no PII redaction). Structured extraction via `with_structured_output`. File validation (size/page count). | `model_config`, `models`, `prompts`, `reference_ranges`, `retry`, `llm_utils` |
| `pii_guard.py` | **Pre-existing, do not rewrite.** Presidio + spaCy based PII redaction. Regex layer (MRN, insurance ID, phone, street address) + NER layer (PERSON, LOCATION, etc). DOB redacted only near a "DOB" label; other dates left alone. | presidio-analyzer/anonymizer, spacy |
| `models.py` | Pydantic schemas: `TestResult` (test_name/value/unit/range_raw/range_low/range_high/range_source/status), `ExtractionResult` (tests, free_text_notes, extraction_warnings). | — |
| `reference_ranges.py` | `parse_range_string()` (handles "70-99", "<200", ">40", en/em dashes), `compute_status()` (pure numeric comparison → NORMAL/HIGH/LOW/BORDERLINE/UNVERIFIED), `FALLBACK_RANGES` dict (general adult baseline, not age/sex personalized). | `models` (for the `Status` type) |
| `model_config.py` | `TASK_CONFIG` dict (extraction @ temp 0, summary_diet @ 0.5, chat @ 0.3, all `gemini-2.5-flash`) + `get_llm_for_task()` factory (`lru_cache`d). | langchain-google-genai |
| `prompts.py` | All system prompts as constants/render functions: `EXTRACTION_SYSTEM_PROMPT`, `render_summary_diet_prompt(tests, cuisine, restrictions)`, `render_chat_prompt(tests)`. Chat/summary prompts embed the test-results table directly (markdown). | `models` |
| `retry.py` | `external_call_retry` decorator (tenacity, exponential backoff + jitter, 4 attempts). `is_transient_error()` classifies by exception type/message (timeout/rate-limit/5xx = retry; ValidationError/malformed-request = don't). | tenacity, pydantic |
| `tracing.py` | Loads `.env`, exposes `tracing_enabled()` and `trace_metadata(thread_id, **extra)` for tagging LangSmith runs. Mostly env-var driven (LangSmith reads `LANGCHAIN_*` itself). | python-dotenv |
| `llm_utils.py` | `extract_text(content)` — normalizes a LangChain message's `.content` (which can be a plain string OR a list of content-block dicts from Gemini) into plain text. Used everywhere a `.content` is read, to avoid leaking raw Python reprs into the UI. | — |
| `ui_components.py` | Pure rendering helpers: `render_disclaimer()`, `render_result_card()`/`render_result_cards()` (color-coded by status), `render_pii_summary()`, `render_scanned_document_notice()`, `render_search_warning()`, `render_friendly_error()`. No business logic. | streamlit, `models` |
| `tests/` | pytest suite: `test_reference_ranges.py` (range parsing + status), `test_pii_guard.py` (redaction regression against a synthetic sample report), `test_file_validation.py` (oversized/wrong-type/too-many-pages), `test_retry.py` (transient-vs-not classification, retry/no-retry behavior). | — |

## Data flow (as implemented)

```
PDF upload (app.py)
  -> extraction.validate_upload()          # size/type/page-count, friendly errors
  -> extraction.load_pdf_text()             # per-page text via PyMuPDF; low-yield pages flagged scanned_image
  -> extraction.ocr_fallback()              # scanned pages only: page image -> Gemini vision, NO redaction
  -> pii_guard.PIIGuard().redact()          # digital_text pages only, before anything else touches an LLM
  -> extraction.extract_structured()        # one batched LLM call across all pages (with_structured_output); splits + merges only if text is unusually large
       -> reference_ranges.parse_range_string() + compute_status()   # pure Python, after the LLM call
  -> st.session_state.extraction_result     # held in session, no database
  -> agent.generate_summary()               # ReAct agent, Gemini + Tavily; falls back to ungrounded plan + UI warning if search fails
  -> agent.build_chat_agent() / send_chat_message()   # structured data in system prompt; dosage-guardrail regex on every response
```

## Non-negotiable rules actually enforced in code (cross-check against CLAUDE.md)

- PII redaction (`pii_guard`) runs only on Path A (`extraction.py`'s `document.pages` loop in `app.py` checks `page.path == "digital_text"`).
- Path B pages are explicitly *not* redacted, and `ui_components.render_scanned_document_notice()` is shown before those pages are sent.
- Status is computed in `reference_ranges.compute_status()`, called from `extraction._finalize_test_result()` — never inside the LLM call.
- `agent.build_chat_agent()` raises `ValueError` if `thread_id` is falsy — no hardcoded default. `thread_id` also keys the chat agent's `MemorySaver` checkpointer, so reusing another user's `thread_id` would leak their chat history — another reason the no-hardcoded-default rule matters.
- `agent._contains_dosage_language()` regex check runs on every chat response in `send_chat_message()`, backstopping the prompt instruction in `prompts.CHAT_SYSTEM_PROMPT`.
- All Gemini/Tavily calls go through `retry.external_call_retry` (see `extraction._extract_chunk`, `extraction._vision_extract_page_text`, `agent._invoke_summary_agent`, `agent._invoke_chat_agent`).
- Tavily failure in `generate_summary()` is caught, falls back to an ungrounded Gemini-only call, and returns `SummaryResult(search_grounded=False, search_warning=...)` which `app.py` renders as a visible warning — never silent degradation.

## Known gaps / not yet done

- **Path B (scanned PDF) flow untested live** — code exists (`extraction.ocr_fallback`, `_vision_extract_page_text`) but has not been driven end-to-end with a real scanned/low-text-yield PDF.
- **`.env` has `LANGCHAIN_TRACING_V2=true` with no `LANGCHAIN_API_KEY`** by default in a fresh checkout — will cause LangSmith auth warnings until a key is added or tracing is turned off. Not a code bug, an environment/config setup step.
- **No CI config** — tests are run manually via `uv run pytest`.
- **Chat memory is in-process only** — `agent.build_chat_agent()`'s `MemorySaver` checkpointer holds conversation state in memory, scoped to one `chat_agent` instance (one Streamlit session). It is lost on app restart and isn't shared across processes; acceptable under this app's no-database design (CLAUDE.md) but worth knowing if multi-process/multi-worker deployment is ever considered.

## Commands

```bash
uv sync --extra dev                # install deps
uv run python -m spacy download en_core_web_sm   # or: uv pip install the wheel directly (see below if download fails)
uv run pytest                      # run test suite
uv run streamlit run app.py        # run the app (needs GOOGLE_API_KEY, TAVILY_API_KEY in .env)
```

If `spacy download` fails in a sandboxed/offline environment, install the
model wheel directly:
```bash
uv pip install en_core_web_sm@https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl
```

## Explicitly out of scope (per README.md §8 — don't build)

User accounts/login/persistence, multi-report comparison/trend tracking,
RAG-over-notes retrieval (the `free_text_notes` field on `ExtractionResult`
is a deliberate unused extension point — leave it that way), age/sex
personalization of reference ranges.
