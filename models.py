"""
models.py

Pydantic schemas for structured test-result data. This is the contract
between the LLM extraction step (extraction.py) and everything downstream
(status computation, UI rendering, the chat agent's system prompt).

The LLM only ever populates test_name/value/unit/range_raw. range_low,
range_high, range_source, and status are always filled in afterward by pure
Python code in reference_ranges.py / extraction.py -- never by the LLM.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RangeSource = Literal["report", "fallback_table", "unavailable"]
Status = Literal["NORMAL", "HIGH", "LOW", "BORDERLINE", "UNVERIFIED"]


class TestResult(BaseModel):
    test_name: str
    value: float
    unit: str
    range_raw: str = Field(description="Reference range exactly as printed on the report")
    range_low: float | None = None
    range_high: float | None = None
    range_source: RangeSource = "unavailable"
    status: Status = "UNVERIFIED"


class ExtractionResult(BaseModel):
    tests: list[TestResult] = Field(default_factory=list)
    free_text_notes: str | None = Field(
        default=None,
        description=(
            "Doctor's notes / other free text kept separate from structured "
            "tests. Extension point for optional future RAG -- not used by "
            "default."
        ),
    )
    extraction_warnings: list[str] = Field(default_factory=list)
