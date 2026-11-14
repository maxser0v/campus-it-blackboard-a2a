"""Typed asynchronous action client for the Blackboard A2A service."""

from __future__ import annotations

from typing import Any

from campus_blackboard.contracts import BlackboardRequest
from campus_blackboard.transport.client import BlackboardActionError, call_agent


class BlackboardClient:
    """Call Blackboard actions over the real A2A JSON-RPC transport.

    ``actor`` is used only to select a locally configured credential. It is
    never copied into the message payload; the Blackboard service derives the
    canonical actor from the authenticated credential.
    """

    def __init__(
        self,
        base_url: str,
        actor: str,
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.actor = actor
        self.api_key = api_key
        self.timeout = timeout

    async def call(self, action: str, **params: Any) -> dict[str, Any]:
        """Validate and execute one named Blackboard action."""

        request = BlackboardRequest.model_validate({"action": action, **params})
        payload = request.model_dump(mode="json", exclude_none=True)
        return await call_agent(
            self.base_url,
            payload,
            actor=self.actor,
            api_key=self.api_key,
            timeout=self.timeout,
        )


__all__ = ["BlackboardActionError", "BlackboardClient"]

