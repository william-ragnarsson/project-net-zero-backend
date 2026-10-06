"""Structured outputs, one model per LLM stage.

The field descriptions become the JSON schema the model fills in, so they are
short and say what goes in the field. Every field is required: a schema
default would leak into the description, and the model should always fill
``notes`` and ``new_imports`` (an empty string or list is fine).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from netzero.events import Rating


class TriageRating(BaseModel):
    function_id: str = Field(description="The function id, copied exactly")
    potential: Rating = Field(description="How much CPU time a faithful rewrite could save")
    testability: Rating = Field(description="How easily deterministic unit tests can call it")
    reason: str = Field(description="At most 15 words")


class TriageOut(BaseModel):
    ratings: list[TriageRating] = Field(description="One rating per function")


class TestFileOut(BaseModel):
    __test__ = False  # not a pytest test class

    code: str = Field(description="The complete pytest file")
    notes: str = Field(description="What the tests cover, at most 30 words")


class TestRepairOut(BaseModel):
    __test__ = False

    diagnosis: str = Field(description="What was wrong, at most 30 words")
    code: str = Field(description="The complete corrected pytest file")
    notes: str = Field(description="What the tests cover, at most 30 words")


class RewriteOut(BaseModel):
    strategy: str = Field(description="At most 8 words")
    rationale: str = Field(description="Why it is faster and still identical, at most 40 words")
    code: str = Field(
        description="The complete new function definition including its decorators, unindented"
    )
    new_imports: list[str] = Field(
        description="Import statements the code needs that the module lacks"
    )


class RewriteRepairOut(BaseModel):
    diagnosis: str = Field(description="What went wrong, at most 30 words")
    strategy: str = Field(description="At most 8 words")
    rationale: str = Field(description="Why it is faster and still identical, at most 40 words")
    code: str = Field(
        description="The complete new function definition including its decorators, unindented"
    )
    new_imports: list[str] = Field(
        description="Import statements the code needs that the module lacks"
    )
