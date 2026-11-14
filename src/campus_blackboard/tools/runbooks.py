"""Keyword retrieval from the actual local Markdown runbook files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_RUNBOOK_ROOT = Path(__file__).resolve().parents[3] / "runbooks"
_TOKEN = re.compile(r"[a-z0-9]+", re.I)


def _slug(text: str) -> str:
    return "-".join(_TOKEN.findall(text.lower()))


class RunbookTools:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _RUNBOOK_ROOT

    def search(self, query: str, *, limit: int = 3) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("runbook query must be a non-empty string of at most 500 characters")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        limit = max(1, min(limit, 10))
        terms = {term.lower() for term in _TOKEN.findall(query) if len(term) > 2}
        results: list[tuple[int, dict[str, str]]] = []
        for path in sorted(self.root.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            runbook_match = re.search(r"^## Runbook ID:\s*`([^`]+)`", text, re.MULTILINE)
            title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
            if not runbook_match:
                continue
            runbook_id = runbook_match.group(1)
            title = title_match.group(1).strip() if title_match else path.stem
            headings = list(re.finditer(r"^###\s+(.+)$", text, re.MULTILINE))
            if not headings:
                headings = [re.match(r"^#\s+(.+)$", text, re.MULTILINE)]  # type: ignore[list-item]
                headings = [heading for heading in headings if heading]
            for index, match in enumerate(headings):
                start = match.end()
                end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
                section_text = text[start:end].strip()
                section_title = match.group(1).strip()
                section_tokens = {term.lower() for term in _TOKEN.findall(section_title + " " + section_text)}
                score = len(terms & section_tokens) + 2 * len(terms & {t.lower() for t in _TOKEN.findall(title)})
                if score:
                    results.append((score, {
                        "runbook_id": runbook_id,
                        "section_id": f"{runbook_id}#{_slug(section_title)}",
                        "title": title,
                        "section_title": section_title,
                        "source": f"runbooks/{path.name}",
                        "text": section_text,
                    }))
        results.sort(key=lambda item: (-item[0], item[1]["runbook_id"], item[1]["section_id"]))
        items = [item for _, item in results[:limit]]
        return {"tool_name": "search_runbooks", "query": query, "results": items}
