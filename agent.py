"""
agent.py

Orchestration: builds the summary/diet agent and the chat agent.

- generate_summary(): a ReAct agent (Gemini + Tavily) that produces a health
  snapshot and a cuisine + restriction-aware diet plan grounded in web
  search. If Tavily fails after retries, still returns a diet plan but flags
  it as ungrounded -- never silently degrades.
- build_chat_agent(): a LangGraph ReAct agent with the user's structured
  test data injected into the system prompt (no vector DB, no retriever
  tool -- see README.md section 2 / CLAUDE.md). Cross-turn memory is a
  per-agent-instance MemorySaver checkpointer keyed on thread_id. thread_id
  is required, no hardcoded default (a hardcoded default caused a real
  cross-user bug in V1 -- don't regress it).

Both are wrapped in the standard retry policy (retry.py) and traced
(tracing.py). Chat responses additionally pass through a code-level dosage
guardrail -- a blunt backstop to the prompt-level instruction in prompts.py,
not a replacement for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_tavily import TavilySearch
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from llm_utils import extract_text
from model_config import get_llm_for_task
from models import TestResult
from prompts import render_chat_prompt, render_summary_diet_prompt
from retry import external_call_retry
from tracing import trace_metadata

DOSAGE_REDIRECT_MESSAGE = (
    "I can't recommend specific medication dosages -- that needs to come "
    "from your doctor or pharmacist, since it depends on things I don't "
    "have full visibility into (your full history, other medications, "
    "etc). Please check with them directly."
)

# Blunt keyword/regex backstop for dosage-pattern language. Looks for a
# numeric dose unit (mg, mcg, ml, IU, etc.) combined with typical dosing
# phrasing ("twice daily", "every X hours", "take X"). Intentionally
# over-broad -- false positives here just mean an occasional unnecessary
# redirect, which is an acceptable tradeoff for a safety backstop.
_DOSAGE_PATTERN = re.compile(
    r"\b\d+(\.\d+)?\s*(mg|mcg|g|ml|iu|units?)\b.{0,40}"
    r"(once|twice|three times|daily|per day|every\s+\d+\s*(hours?|hrs?)|"
    r"a day|/day)"
    r"|"
    r"\btake\s+\d+(\.\d+)?\s*(mg|mcg|g|ml|iu|units?)\b",
    re.IGNORECASE | re.DOTALL,
)


def _contains_dosage_language(text: str) -> bool:
    return bool(_DOSAGE_PATTERN.search(text))


@dataclass
class SummaryResult:
    text: str
    search_grounded: bool
    search_warning: str | None = None


def _build_tavily_tool() -> TavilySearch:
    return TavilySearch(max_results=5)


@external_call_retry
def _invoke_summary_agent(prompt: str, thread_id: str) -> str:
    llm = get_llm_for_task("summary_diet")
    tools = [_build_tavily_tool()]
    agent = create_react_agent(llm, tools)
    result = agent.invoke(
        {"messages": [SystemMessage(content=prompt), HumanMessage(content="Please generate my summary and diet plan.")]},
        config={"metadata": trace_metadata(thread_id, task="summary_diet")},
    )
    final_message = result["messages"][-1]
    return extract_text(final_message.content)


def generate_summary(
    tests: list[TestResult],
    cuisine: str,
    restrictions: list[str],
    thread_id: str,
) -> SummaryResult:
    """
    ReAct agent: Gemini (summary_diet task config) + Tavily search tool.
    Produces health snapshot, normal/attention breakdown, cuisine+
    restriction-aware diet plan, next steps.

    If Tavily search ultimately fails after retries, still returns a diet
    plan (ungrounded) with search_grounded=False and a warning the UI must
    surface -- never silently degrade without telling the user.
    """
    prompt = render_summary_diet_prompt(tests, cuisine, restrictions)
    try:
        text = _invoke_summary_agent(prompt, thread_id)
        return SummaryResult(text=text, search_grounded=True)
    except Exception:
        # Search (or the agent loop using it) failed after retries. Fall back
        # to a plain, ungrounded LLM call so the user still gets a plan.
        llm = get_llm_for_task("summary_diet")
        fallback_prompt = prompt + (
            "\n\nNote: live web search is unavailable right now. Produce your "
            "best plan from general knowledge, and do not claim it is based "
            "on current sources."
        )
        response = llm.invoke(
            [SystemMessage(content=fallback_prompt), HumanMessage(content="Please generate my summary and diet plan.")]
        )
        text = extract_text(response.content)
        return SummaryResult(
            text=text,
            search_grounded=False,
            search_warning=(
                "Live search unavailable — this diet plan is based on general "
                "knowledge, not verified current sources."
            ),
        )


def build_chat_agent(tests: list[TestResult], thread_id: str):
    """
    LangGraph ReAct agent, Gemini (chat task config), Tavily tool only (no
    retriever tool by default -- see README.md section 2). Structured data
    is injected via the system prompt, not a tool.

    Conversation memory across turns is held by an in-process MemorySaver
    checkpointer, keyed on thread_id -- _invoke_chat_agent() only ever sends
    the new HumanMessage each turn, so without a checkpointer the agent
    would have no way to see prior turns. MemorySaver is in-memory only (not
    persisted across app restarts); that's fine here since Streamlit session
    state already scopes a chat_agent instance to one browser session.

    thread_id is required -- callers must pass a per-session UUID, never a
    hardcoded default (see module docstring). It doubles as the
    checkpointer's conversation key, so reusing another user's thread_id
    would leak their chat history into this agent -- another reason the
    no-hardcoded-default rule matters.
    """
    if not thread_id:
        raise ValueError("thread_id is required and must be a per-session identifier")

    llm = get_llm_for_task("chat")
    tools = [_build_tavily_tool()]
    system_prompt = render_chat_prompt(tests)
    agent = create_react_agent(llm, tools, prompt=system_prompt, checkpointer=MemorySaver())
    return agent


@external_call_retry
def _invoke_chat_agent(agent, message: str, thread_id: str) -> str:
    result = agent.invoke(
        {"messages": [HumanMessage(content=message)]},
        config={
            "configurable": {"thread_id": thread_id},
            "metadata": trace_metadata(thread_id, task="chat"),
        },
    )
    final_message = result["messages"][-1]
    return extract_text(final_message.content)


def send_chat_message(agent, message: str, thread_id: str) -> str:
    """
    Invokes the chat agent and applies the code-level dosage guardrail to
    the response. This is a backstop, not a replacement for the
    prompt-level instruction in prompts.py -- both layers run.
    """
    response_text = _invoke_chat_agent(agent, message, thread_id)
    if _contains_dosage_language(response_text):
        return DOSAGE_REDIRECT_MESSAGE
    return response_text
