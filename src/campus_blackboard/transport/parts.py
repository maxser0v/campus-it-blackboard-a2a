"""Conversions between project JSON payloads and A2A protobuf parts."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

from a2a.types import Message, Part, Role


def structured_message(payload: dict[str, Any], *, role: int = Role.ROLE_USER) -> Message:
    """Build an A2A message containing a structured JSON data part."""

    return Message(
        role=role,
        message_id=uuid4().hex,
        parts=[Part(data=ParseDict(payload, Value()))],
    )


def extract_structured_payload(message: Message | None) -> dict[str, Any]:
    """Read the first JSON object data part from an A2A message."""

    if message is None:
        raise ValueError("A2A request did not include a message")
    for part in message.parts:
        if part.HasField("data"):
            value = MessageToDict(part.data)
            if isinstance(value, dict):
                return value
            raise ValueError("A2A structured data part must contain a JSON object")
    raise ValueError("A2A request must include a structured JSON data part")


def extract_structured_response(message: Message) -> dict[str, Any]:
    """Read the first JSON object data part from an A2A response message."""

    return extract_structured_payload(message)

