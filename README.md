# 🩺 Health Report Analyser

**Upload a blood test PDF. Get a plain-English summary, a diet plan built for your cuisine, and a chatbot that already knows your results — without opening a single new tab to Google a medical term.**

Blood test reports are full of numbers and jargon that mean nothing to most people. This app reads the report for you, tells you what's actually normal vs. what needs attention, and turns that into food advice grounded in real web search — all while stripping your personal details before any of it touches a third-party AI.

Built for non-technical users. **Not a medical advice tool** — it's a reading-and-organizing aid, disclaimed on every screen.

**🔗 Try it live: [explainmyreport.streamlit.app](https://explainmyreport.streamlit.app)**

---

## What it does

1. **Reads** the PDF — real text layer if available, OCR/vision fallback if it's a scanned photo.
2. **Redacts** personal details (name, DOB, address, phone, email, MRN, insurance ID) locally, before anything leaves for an external API.
3. **Extracts** every test result into structured data via an LLM — the model only reads what's printed; it never decides what's normal.
4. **Judges** each result — Normal / High / Low / Borderline — using plain numeric comparison in Python, not an LLM guess.
5. **Summarizes** the findings in a short, non-alarming paragraph instead of a wall of bullet points.
6. **Builds a diet plan** for a chosen cuisine and any dietary restrictions, using an agent that searches the web for current, relevant advice instead of guessing from memory.
7. **Chats** — a report-scoped assistant that already has your results in context and can pull in live web search for anything beyond the report.

---

## Under the hood: agentic, not just prompted

- **Tool-using agents with real judgment split** — the diet-plan and chat agents are LangGraph ReAct agents that decide *when* to call Tavily web search; but the LLM never decides your test status — that's computed deterministically in Python, with the model restricted to structured extraction only (Pydantic-validated, no prose parsing).
- **Privacy and resilience built into the architecture** — PII is redacted locally (Presidio + spaCy) before any text reaches an external API, every external call has classified retry logic (transient failures retry, validation failures don't), and a code-level safety backstop double-checks chat output for medication-dosage language before it's shown.
- **Session-scoped memory and full observability** — the chat agent persists per-session context via a LangGraph checkpointer with no cross-user leakage, and every run is traced (LangSmith) without ever logging raw PII.

---

## Architecture

```
PDF upload
   │
   ▼
Text extraction (digital text layer, or OCR/vision fallback for scans)
   │
   ▼
PII redaction (local, Presidio + spaCy) ── always before any external call
   │
   ▼
Structured extraction (Gemini, schema-constrained)
   │
   ▼
Status computation (pure Python — Normal / High / Low / Borderline)
   │
   ├──► Color-coded result cards
   │
   ▼
Summary + diet agent (Gemini + Tavily, ReAct)
   │
   ▼
Session state (in-memory only — nothing persisted to a database)
   │
   ▼
Chat agent (structured results in context, Tavily for open-ended questions,
             LangGraph checkpointer for cross-turn memory)
```

No vector database by design — a lab report is a few dozen structured values, small enough to inject directly into the chat agent's context every turn. That keeps the default path simple; a free-text-notes field is kept separate in the schema as an extension point for RAG later, without needing it now.

---

## Tech stack

- **UI**: Streamlit
- **LLM**: Google Gemini (structured extraction, summary/diet agent, chat agent)
- **Agent framework**: LangGraph (ReAct agents, checkpointer-based memory)
- **Web search tool**: Tavily
- **PII redaction**: Microsoft Presidio + spaCy (local, no cloud PII API — health data never makes a second hop to a third vendor)
- **Validation**: Pydantic
- **Resilience**: Tenacity (retry with exponential backoff + jitter)
- **Observability**: LangSmith tracing
- **Deployment**: Streamlit Community Cloud

---

## Privacy notes

- Digital PDFs are fully redacted (name, DOB, address, phone, email, MRN, insurance ID) before any text is sent externally.
- Scanned/photographed PDFs can't be redacted the same way (no reliable region-based redaction without false-positives on the report itself), so those pages are sent as images with the raw content — the app discloses this to the user at the point it happens, rather than treating all uploads the same.
- Nothing is stored in a database. Report data lives only in the browser session; closing the tab clears it.

---

## Running locally

```bash
uv sync
cp .env.example .env   # fill in GOOGLE_API_KEY, TAVILY_API_KEY, (optional) LangSmith keys
streamlit run app.py
```

---

## Disclaimer

This tool organizes and explains information from a report you provide. It does not diagnose, does not replace a doctor, and should never be the basis for a medical decision. Always confirm results and recommendations with a qualified healthcare professional.