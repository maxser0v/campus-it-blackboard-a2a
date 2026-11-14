"""Process fixture that exposes a deterministic A2A task service for tests."""

import os

from campus_blackboard.config import Settings
from campus_blackboard.transport.service import create_a2a_app


async def _echo(payload: dict) -> dict:
    return {"accepted": True, "payload": payload}


settings = Settings(
    agent_credentials={
        "controller": "test-controller-token",
        "evidence": "test-evidence-token",
    }
)
app = create_a2a_app(
    "evidence",
    _echo,
    settings=settings,
    base_url=os.environ.get("A2A_TEST_BASE_URL", "http://127.0.0.1:8139"),
)

