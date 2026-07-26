# How This Project Works (Plain English Guide)

This is a walkthrough for anyone who isn't deep into the codebase yet.
No jargon assumed — if a term needs explaining, it's explained inline.

## What does this app actually do?

You upload a blood test report (a PDF) to a web page. The app:

1. Reads the text out of the PDF.
2. Blacks out your personal details (name, address, phone number, etc.)
   before showing the data to any AI.
3. Pulls out every test result (like "Glucose: 112 mg/dL") into a clean,
   structured list.
4. Figures out — with plain math, not AI guesswork — whether each result is
   Normal, High, Low, or Borderline.
5. Shows you all of that in color-coded cards, grouped so the important
   stuff (High/Low results) is easy to spot.
6. Builds you a diet plan (for a cuisine you pick, respecting allergies you
   list) using an AI that can also search the web for good sources.
7. Lets you chat and ask follow-up questions about your results.

It is explicitly **not medical advice** — that's said on every screen.

## The big picture, in one sentence

**A user's PDF goes in one end, gets its private info stripped out, gets
turned into structured data, and comes out the other end as friendly cards,
a diet plan, and a chatbot — all without ever storing anything in a
database.**

## Walking through a real user session, step by step

### Step 1 — You land on the upload screen

This is `app.py`. Think of `app.py` as the "front desk" — it's the only file
that knows about buttons, screens, and what you see on the page. It doesn't
do any of the smart AI work itself; it just calls out to other files to do
that work, and displays whatever comes back.

### Step 2 — You upload a PDF

`app.py` hands the file to `extraction.py`, which is the "document reader."
First it checks the file isn't too big or too long (`validate_upload`) —
if it is, you get a friendly error message, never a scary crash.

Then it tries to read the text out of the PDF page by page. Most PDFs
downloaded from a lab's website have real, selectable text in them — easy to
read directly. But sometimes a report is actually a **photo or scan** of a
paper document, which has no real text at all, just an image. `extraction.py`
detects this automatically: if a page doesn't have enough readable text, it
treats that page as a scanned image instead.

### Step 3 — Personal details get removed (for normal PDFs)

This is `pii_guard.py` — the "privacy filter." Before any of your report's
text is sent to an AI over the internet, this file scans it and blacks out
things like:

- Your name
- Date of birth
- Address
- Phone number
- Email
- Medical record number / insurance ID

It leaves your **test date** alone on purpose, since that's medically useful
and not really identifying on its own.

**Important exception:** if a page was a scanned image (from Step 2), this
filter *can't* run on it — you can't black out text in a photo the same way.
So for scanned pages, the app sends the raw image straight to the AI,
personal details and all, but it clearly warns you on screen first that
this is happening and why. Typed/digital PDFs always get the privacy filter;
scanned photos don't.

### Step 4 — The AI reads your results (but doesn't judge them)

Back in `extraction.py`, the now-privacy-safe text gets sent to an AI model
(Google's Gemini) with instructions from `prompts.py` — think of
`prompts.py` as a big folder of exact scripts telling the AI what to do and
how to behave at each step.

The AI's only job here is to read off what's printed: test name, the number,
the unit, and the reference range shown on the report (e.g. "Glucose: 112
mg/dL, normal range 70-99"). The AI is deliberately *not* asked to decide
whether that's good or bad — because AI can be inconsistent at that kind of
judgment call, and getting it wrong on a health report is a bad place to be
wrong.

If your report has several pages, each page's text is sent to the AI at the
same time (in parallel) rather than one at a time, so you're not waiting
around for page 1 to finish before page 2 even starts.

### Step 5 — Plain math decides Normal/High/Low, not the AI

This is `reference_ranges.py` — the "calculator." Once the AI has reported
the raw numbers, this file does the actual judging, using simple math: is
112 higher than the top of the range (99)? Yes → High. This is deliberately
*not* left up to the AI. A number comparison is something code can do
perfectly and reliably every time; an AI might not.

If a report doesn't print a reference range at all, this file has a small
built-in table of typical adult ranges (glucose, cholesterol, etc.) to fall
back on. If it truly can't find any range anywhere, it honestly marks the
result "Unverified" rather than guessing.

`models.py` is the file that defines exactly what a "test result" and a
"full report" look like as data — think of it as the blank form template
that gets filled in as this process runs. Every other file agrees to use
that same shape of data, so everything fits together.

### Step 6 — You see your results

Back in `app.py`, using helpers from `ui_components.py` (the "visual design
toolkit" — cards, colors, banners, warnings), your results appear as
color-coded cards, grouped into:

- 🔴 **Needs attention** (High/Low) — shown open by default
- 🟡 **Borderline** — shown open by default
- ✅ **Normal** — collapsed, since it's usually the least urgent to read
- ⚪ **Couldn't verify** — collapsed

### Step 7 — You ask for a diet plan

You pick a cuisine and any dietary restrictions/allergies, and click
"Generate." This calls into `agent.py` — the "AI coordinator." It builds an
AI "agent" (basically: an AI that's allowed to use a tool, in this case web
search) and hands it your test results plus your preferences, following
instructions from `prompts.py`.

The AI can search the web (using a service called Tavily) for
up-to-date, relevant food advice — e.g. "vegetarian diabetic-friendly
Indian foods" — rather than just making things up from memory. It comes
back with one unified plan: a short overview, one list of foods to
prioritize, one list of foods to limit, and a few sample meals — not a
separate mini-plan for every single flagged result.

If web search fails for some reason, the app doesn't just silently give you
a lower-quality answer and pretend it's the same — it tells you plainly
that this plan wasn't checked against live sources.

### Step 8 — You chat about your results

Also handled in `agent.py`. This builds a second, separate AI agent — one
that's given your full results table directly (so it can answer "what was
my glucose?" instantly, without needing to search anything) and can also
search the web for general health/diet questions beyond your report.

This chat agent is built once, the first time you open the chat screen, and
then reused for every message you send afterward in that same session —
it remembers the conversation so far.

One safety rule enforced here: **the AI is never allowed to tell you a
specific medication dosage** (like "take 500mg twice a day"). This isn't
just a polite instruction to the AI — the code also double-checks every
single response with a pattern-matching safety net, and if anything looks
like a dosage recommendation slipped through, it gets swapped out for a
"please ask your doctor" message before you ever see it.

## What each file's job is, at a glance

| File | Plain-English job |
|---|---|
| `app.py` | The "front desk" — screens, buttons, what you see. No smart logic of its own. |
| `extraction.py` | The "document reader" — gets text out of your PDF, handles scanned photos differently, sends text to the AI to pull out test results. |
| `pii_guard.py` | The "privacy filter" — blacks out your name/address/etc. before anything leaves for the AI (for typed PDFs). |
| `reference_ranges.py` | The "calculator" — plain math that decides Normal/High/Low/Borderline, with a backup table of typical ranges. |
| `models.py` | The "blank form template" — defines what a test result and a full report look like as data. |
| `model_config.py` | The "settings sheet" — which AI model to use, and how creative vs. precise it should be, for each different task. |
| `prompts.py` | The "script folder" — the exact instructions given to the AI at each step (reading results, writing a diet plan, chatting). |
| `agent.py` | The "AI coordinator" — builds the diet-plan AI and the chat AI, and enforces the no-medication-dosage safety rule. |
| `retry.py` | The "try again" logic — if a call to the AI fails because of a temporary hiccup (like a timeout), retry it automatically a few times before giving up. |
| `llm_utils.py` | A small helper that cleans up the AI's raw response so it always comes back as normal, readable text. |
| `tracing.py` | Optional behind-the-scenes logging (via a service called LangSmith) so a developer can debug what happened in a session, if they choose to turn it on. |
| `ui_components.py` | The "visual design toolkit" — the reusable building blocks for cards, colors, and warning banners you see on screen. |
| `tests/` | Automated checks that make sure the important logic (like the Normal/High/Low math, and the privacy filter) keeps working correctly as the code changes. |

## A few deliberate design choices worth knowing

- **Nothing is saved to a database.** Your report data only exists for the
  length of your browser session. Close the tab (or restart the app), and
  it's gone — you'd need to re-upload.
- **The AI never decides your health status.** It only reads numbers off
  the page; plain code decides what those numbers mean.
- **Scanned photos are handled differently, on purpose**, and the app tells
  you when that's happening — it's a real difference in how your privacy is
  protected, not a hidden detail.
- **This is not a medical advice tool.** It's a reading-and-organizing aid,
  clearly labeled as such throughout.

## Where to look next

- [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) — a more technical snapshot of
  the codebase (file dependencies, data flow, known gaps).
- [CLAUDE.md](CLAUDE.md) — the non-negotiable rules this codebase follows,
  written for an AI coding assistant working on this repo.
- [README.md](README.md) — the original detailed build specification.
