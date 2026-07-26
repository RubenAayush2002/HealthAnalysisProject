"""
model_config.py

Per-task LLM configuration. Extraction is deliberately temperature 0 --
precision matters and it's paired with code-side range/status validation
(reference_ranges.py). Summary/diet and chat have some creative slack since
they produce prose, not structured facts that get compared numerically.

get_llm_for_task() is cheap to call repeatedly (langchain client construction
is not free but not huge either); wrap it in @st.cache_resource at the call
site in app.py, same pattern V1 used for get_llm().
"""

from __future__ import annotations

from functools import lru_cache
from typing import TypedDict

from langchain_google_genai import ChatGoogleGenerativeAI


class TaskConfig(TypedDict):
    model: str
    temperature: float


TASK_CONFIG: dict[str, TaskConfig] = {
    "extraction": {"model": "gemini-2.5-flash", "temperature": 0},
    "summary_diet": {"model": "gemini-2.5-flash", "temperature": 0.5},
    "chat": {"model": "gemini-2.5-flash", "temperature": 0.3},
}


@lru_cache(maxsize=len(TASK_CONFIG))
def get_llm_for_task(task: str) -> ChatGoogleGenerativeAI:
    """
    Returns a cached ChatGoogleGenerativeAI configured for the given task.
    Raises KeyError for an unknown task name -- fail loudly, don't silently
    fall back to a default config since temperature/model choice is
    deliberate per task.
    """
    config = TASK_CONFIG[task]
    return ChatGoogleGenerativeAI(model=config["model"], temperature=config["temperature"])
