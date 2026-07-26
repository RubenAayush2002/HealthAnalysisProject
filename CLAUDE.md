# CLAUDE.md

Context for Claude Code when working in this repository. Full build spec
lives in [README.md](README.md) — read it for rationale/detail; this file is
a quick-reference index, not a replacement.

## What this is

A Streamlit app for older, non-technical users: upload a health/blood test
report PDF, get PII stripped, results extracted into structured data, a
plain-English color-coded summary, a cuisine-aware diet plan grounded in web
search, and a follow-up chat scoped to the report + general health/diet
questions. **Not medical advice** — must be disclosed on upload, summary, and
chat screens.

## Architecture

Two-tier: UI code and logic code are separate files. `app.py` has **zero**
LLM/business logic. Logic files have **zero** Streamlit dependency so they're
testable headlessly.

| File | Responsibility |
|---|---|
| `app.py` | Streamlit UI, session-state, wiring only |
| `agent.py` | Orchestration: extraction/summary/chat agents, retry, dosage guardrail |
| `pii_guard.py` | PII detection/redaction — **already implemented**, don't rewrite |
| `extraction.py` | PDF text/OCR extraction + structured data extraction & validation |
| `models.py` | Pydantic schemas (`TestResult`, `ExtractionResult`) |
| `model_config.py` | Per-task LLM config (model, temperature) |
| `reference_ranges.py` | Fallback adult reference ranges + range-string parser |
| `tracing.py` | LangSmith tracing setup |
| `prompts.py` | All system prompts, centralized |
| `ui_components.py` | Reusable Streamlit rendering helpers (cards, banners) |
| `tests/` | pytest suite |

Data flow: PDF → `extraction.load_pdf_text()` (OCR fallback if low text yield)
→ `pii_guard` redaction → `extraction.extract_structured()` (LLM extracts
fields, **Python computes status**) → result cards + `agent.generate_summary()`
→ held in `st.session_state` → `agent.build_chat_agent()` (structured data in
system prompt, no vector DB).

## Non-negotiable design rules

- **PII redaction always runs before any external call** — but only on Path A
  (digital PDFs with a text layer).
- **Path B (scanned/photographed PDFs) sends the raw page image to Gemini
  vision with no redaction** — a deliberate tradeoff (image-level PII
  blackout was tried and produced false positives on lab test names). This
  must be disclosed to the user with a distinct status message before that
  page is sent; never merge it into Path A's status messaging.
- **Status (NORMAL/HIGH/LOW/BORDERLINE/UNVERIFIED) is always computed in
  Python** by numeric comparison against a parsed range. The LLM extracts
  values only — never decide status. No range available → `UNVERIFIED`,
  never guess.
- **No vector DB / Chroma by default.** Structured test results go directly
  into the chat agent's system prompt every turn. `free_text_notes` on
  `ExtractionResult` is a deliberate extension point for optional future RAG
  — keep it populated but unused.
- `thread_id` in `build_chat_agent()` is **required**, no hardcoded default —
  a hardcoded default caused a real cross-user data bug in V1.
- Medication dosage guardrail is **code-level** (regex/keyword check on chat
  responses), not prompt-only — prompt instruction is a first layer, the code
  check is the backstop.
- All external calls (Gemini, Tavily) go through a `tenacity` retry wrapper
  with exponential backoff + jitter, retrying only transient errors (timeout/
  rate-limit/5xx) — never retry validation or malformed-request errors.
- If Tavily search fails after retries, still return a diet plan but flag it
  visibly in the UI — never silently degrade.
- Reference ranges in `reference_ranges.py` are a general adult baseline, not
  personalized by age/sex — a known, documented limitation.

## Commands

Fill in once `pyproject.toml` exists:
- Install: `uv sync`
- Run app: `uv run streamlit run app.py`
- Run tests: `uv run pytest`

## Out of scope (do not build)

User accounts/login/persistence across sessions, multi-report comparison or
trend tracking, the RAG-over-notes path (leave the `free_text_notes`
extension point only, don't implement retrieval), age/sex-personalized
reference ranges.
