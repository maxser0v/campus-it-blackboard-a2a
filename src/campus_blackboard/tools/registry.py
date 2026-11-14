"""Explicit allowlist connecting model-selected names to local tool methods."""

from __future__ import annotations

from typing import Any

from .fixtures import FixtureTools
from .runbooks import RunbookTools


class ToolRegistry:
    ALLOWED_FIXTURE_TOOLS = frozenset({"query_metrics", "query_logs", "get_change_history"})

    def __init__(self, fixture_tools: FixtureTools | None = None, runbook_tools: RunbookTools | None = None) -> None:
        self.fixtures = fixture_tools or FixtureTools()
        self.runbooks = runbook_tools or RunbookTools()

    def execute(self, name: str, *, case_id: str | None = None, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        if name in self.ALLOWED_FIXTURE_TOOLS:
            if not case_id:
                raise ValueError("case_id is required for fixture tools")
            return getattr(self.fixtures, name)(case_id, **args)
        if name == "search_runbooks":
            if "case_id" in args:
                raise ValueError("runbook retrieval is not case-specific")
            query = args.pop("query", None)
            if args.keys() - {"limit"}:
                raise ValueError("unsupported runbook search arguments")
            return self.runbooks.search(query, **args)
        raise ValueError(f"tool is not allowlisted: {name}")
