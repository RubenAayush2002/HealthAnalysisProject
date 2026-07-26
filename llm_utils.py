"""
llm_utils.py

Small shared helpers for working with LangChain message objects.
"""

from __future__ import annotations


def extract_text(content) -> str:
    """
    Normalizes a LangChain message's .content into plain text. Gemini
    responses sometimes come back as a list of content blocks (dicts with a
    "text" key) rather than a plain string -- naively str()-ing that list
    would leak Python repr syntax straight into the UI.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        if parts:
            return "".join(parts)
    return str(content)
