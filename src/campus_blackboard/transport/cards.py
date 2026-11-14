"""A2A Agent Card construction for local Blackboard and agent services."""

from __future__ import annotations

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill


ROLE_DESCRIPTIONS = {
    "blackboard": "Validates and stores shared incident knowledge with immutable revisions.",
    "controller": "Coordinates incident investigation and records scheduling decisions.",
    "evidence": "Queries synthetic campus telemetry and records sourced observations.",
    "diagnosis": "Compares evidence-backed incident hypotheses and identifies open questions.",
    "review": "Checks findings and response proposals against their cited evidence.",
    "response": "Builds a bounded response proposal grounded in reviewed findings and runbooks.",
}


def create_agent_card(role: str, base_url: str) -> AgentCard:
    """Build a protocol 1.0 Agent Card advertising the JSON-RPC binding."""

    if role not in ROLE_DESCRIPTIONS:
        raise ValueError(f"unsupported A2A role: {role}")
    return AgentCard(
        name=f"Campus {role.title()} Service",
        description=ROLE_DESCRIPTIONS[role],
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=f"{base_url.rstrip('/')}/a2a/jsonrpc/",
            )
        ],
        version="0.1.0",
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=(role != "blackboard")),
        skills=[
            AgentSkill(
                id=f"{role}_service",
                name=f"{role.title()} service",
                description=ROLE_DESCRIPTIONS[role],
                tags=[role, "campus-it", "blackboard"],
                input_modes=["application/json"],
                output_modes=["application/json"],
                examples=['{"action":"read_blackboard"}'] if role == "blackboard" else [],
            )
        ],
    )

