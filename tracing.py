"""
tracing.py

LangSmith tracing setup. Mostly environment configuration -- LangSmith reads
LANGCHAIN_TRACING_V2 / LANGCHAIN_API_KEY / LANGCHAIN_PROJECT directly from
the environment, so this module's job is just to load .env and provide a
small helper for tagging traces with the session's thread_id.

This module is also where GOOGLE_API_KEY / TAVILY_API_KEY end up in
os.environ. Neither is read explicitly anywhere in this codebase --
ChatGoogleGenerativeAI (model_config.py) and TavilySearch (agent.py) both
pull their key from os.environ internally. Locally, load_dotenv() populates
os.environ from .env. On Streamlit Community Cloud there is no .env file;
secrets are injected via st.secrets instead, which does NOT automatically
populate os.environ. _sync_secrets_to_environ() below bridges that gap by
copying any st.secrets entries into os.environ (without overwriting a value
already set, e.g. from a real local .env) -- this must run before
model_config.get_llm_for_task() or agent._build_tavily_tool() are ever
called, so it happens at import time here, same as load_dotenv().

IMPORTANT: since pii_guard redaction always runs before any LLM call (see
extraction.py / CLAUDE.md), traces never see raw pre-redaction text. Never
pass raw, un-redacted report text into trace metadata here or anywhere else
-- only redacted text and structured (already-PII-free) data should ever
reach a traced call.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _sync_secrets_to_environ() -> None:
    """
    Mirrors st.secrets into os.environ for any key not already set there.
    A no-op locally when no .streamlit/secrets.toml exists (or when running
    outside Streamlit) -- st.secrets raises in that case, which we swallow,
    since .env via load_dotenv() is the local source of truth.
    """
    try:
        import streamlit as st

        secrets = dict(st.secrets)
    except Exception:
        return

    for key, value in secrets.items():
        if isinstance(value, str) and key not in os.environ:
            os.environ[key] = value


_sync_secrets_to_environ()


def tracing_enabled() -> bool:
    return os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"


def trace_metadata(thread_id: str, **extra: str) -> dict[str, str]:
    """
    Standard metadata to attach to a traced run (via `config={"metadata": ...}`
    on .invoke()/.stream() calls) so a specific user's session can be
    isolated when debugging in LangSmith.
    """
    return {"thread_id": thread_id, **extra}
