"""Shared, JSON-friendly contracts for Blackboard requests and findings."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeType(StrEnum):
    OBSERVATION = "observation"
    HYPOTHESIS = "hypothesis"
    EVIDENCE_REQUEST = "evidence_request"
    REVIEW = "review"
    SOLUTION = "solution"
    CONTROLLER_DECISION = "controller_decision"


class ProblemStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    COMPLETED = "completed"
    UNRESOLVED = "unresolved"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class BlackboardRequest(BaseModel):
    """Envelope carried in an A2A structured data part."""

    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "create_problem",
        "read_blackboard",
        "write_blackboard",
        "update_knowledge",
        "get_hypotheses",
        "get_solutions",
        "get_problem_status",
        "set_problem_status",
    ]
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    problem_id: str | None = Field(default=None, min_length=1, max_length=128)
    case_id: str | None = Field(default=None, min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    limits: dict[str, Any] | None = None
    knowledge_type: KnowledgeType | None = None
    data: dict[str, Any] | None = None
    entry_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=1)
    status: ProblemStatus | None = None
    knowledge_types: list[KnowledgeType] | None = None
    since_revision: int | None = Field(default=None, ge=0)

    @field_validator("request_id", "problem_id", "case_id", "entry_id")
    @classmethod
    def strip_ids(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ProblemRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_id: str
    case_id: str
    title: str
    status: ProblemStatus
    board_revision: int = Field(ge=0)
    limits: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class KnowledgeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    problem_id: str
    knowledge_type: KnowledgeType
    source_agent: str
    revision: int = Field(ge=1)
    data: dict[str, Any]
    created_at: str


class ActionError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


def json_object(value: Any) -> dict[str, Any]:
    """Reject non-object or non-JSON values for knowledge payloads."""

    if not isinstance(value, dict):
        raise ValueError("knowledge data must be a JSON object")
    return value

