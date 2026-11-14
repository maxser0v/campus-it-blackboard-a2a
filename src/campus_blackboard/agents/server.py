"""A2A launch factories for the four independently hosted specialists."""

from __future__ import annotations

from typing import Any

from ..config import Settings, get_settings
from ..transport.service import create_a2a_app
from .runner import SpecialistRunner, build_specialist_handler


def create_app(
    role: str,
    settings: Settings | None = None,
    *,
    blackboard: Any | None = None,
    model: Any | None = None,
    tools: Any | None = None,
):
    """Create the real A2A task service for one specialist role."""
    settings = settings or get_settings()
    handler = build_specialist_handler(role, settings, blackboard=blackboard, model=model, tools=tools)
    return create_a2a_app(role, handler, settings=settings)


def create_evidence_app(settings: Settings | None = None):
    return create_app("evidence", settings)


def create_diagnosis_app(settings: Settings | None = None):
    return create_app("diagnosis", settings)


def create_review_app(settings: Settings | None = None):
    return create_app("review", settings)


def create_response_app(settings: Settings | None = None):
    return create_app("response", settings)


create_specialist_handler = build_specialist_handler


__all__ = [
    "SpecialistRunner", "build_specialist_handler", "create_specialist_handler",
    "create_app", "create_evidence_app", "create_diagnosis_app", "create_review_app", "create_response_app",
]
