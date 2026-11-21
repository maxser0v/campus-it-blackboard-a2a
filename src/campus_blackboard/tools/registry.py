"""Allowlisted LangChain structured tools over read-only incident sources."""

from __future__ import annotations

from typing import Any

from langchain.tools import tool
from pydantic import BaseModel, ConfigDict

from .fixtures import FixtureTools, validate_fixture_arguments
from .runbooks import RunbookTools


class _Arguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class _Metrics(_Arguments):
    case_id: str
    metric_names: list[str] | tuple[str, ...] | None = None
    contains: str | None = None


class _Logs(_Arguments):
    case_id: str
    contains: str | None = None
    service: str | None = None
    level: str | None = None
    limit: int = 20


class _Changes(_Arguments):
    case_id: str
    service: str | None = None
    limit: int = 20


class _Runbooks(_Arguments):
    query: str
    limit: int = 3


class ToolRegistry:
    ALLOWED_FIXTURE_TOOLS = frozenset({"query_metrics", "query_logs", "get_change_history"})

    def __init__(self, fixture_tools: FixtureTools | None = None, runbook_tools: RunbookTools | None = None) -> None:
        self.fixtures = fixture_tools or FixtureTools()
        self.runbooks = runbook_tools or RunbookTools()
        self.langchain_tools = {
            "query_metrics": tool("query_metrics", description="Read bounded incident metrics with source provenance.", args_schema=_Metrics)(self.fixtures.query_metrics),
            "query_logs": tool("query_logs", description="Read bounded incident logs; contains matches all supplied words.", args_schema=_Logs)(self.fixtures.query_logs),
            "get_change_history": tool("get_change_history", description="Read bounded incident change history with source provenance.", args_schema=_Changes)(self.fixtures.get_change_history),
            "search_runbooks": tool("search_runbooks", description="Search local recovery runbooks by query.", args_schema=_Runbooks)(self.runbooks.search),
        }

    def execute(self, name: str, *, case_id: str | None = None, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        if name in self.ALLOWED_FIXTURE_TOOLS:
            if not case_id:
                raise ValueError("case_id is required for fixture tools")
            validate_fixture_arguments(name, args)
            args["case_id"] = case_id
        elif name == "search_runbooks":
            if "case_id" in args:
                raise ValueError("runbook retrieval is not case-specific")
            if args.keys() - {"query", "limit"}:
                raise ValueError("unsupported runbook search arguments")
        else:
            raise ValueError(f"tool is not allowlisted: {name}")
        return self.langchain_tools[name].invoke(args)
