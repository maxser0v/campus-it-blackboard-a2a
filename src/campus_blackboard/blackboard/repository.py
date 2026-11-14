"""SQLite append-only Blackboard repository with ownership and idempotency."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import ValidationError

from campus_blackboard.contracts import BlackboardRequest, KnowledgeType, ProblemStatus


class _RepositoryError(Exception):
    def __init__(self, code: str, message: str, **details: Any):
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


class Repository:
    """Persist Blackboard problems, entries, revisions, and audit events.

    The repository receives the already authenticated actor as a separate
    argument. It never trusts an actor/source field contained in request data.
    Each mutation runs in a short ``BEGIN IMMEDIATE`` transaction.
    """

    def __init__(self, db_path: str | Path):
        raw_path = str(db_path)
        if raw_path == ":memory:":
            self._path = f"file:campus-blackboard-{uuid4().hex}?mode=memory&cache=shared"
            self._uri = True
            self._keeper = sqlite3.connect(self._path, uri=True, check_same_thread=False)
        else:
            path = Path(raw_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(path)
            self._uri = False
            self._keeper = None
        self._initialize()

    def close(self) -> None:
        """Close the keeper connection used for a shared in-memory database."""

        if self._keeper is not None:
            self._keeper.close()
            self._keeper = None

    def handle(self, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and execute an action, returning a JSON-ready result/error."""

        if not actor or not actor.strip():
            return _error("unauthorized", "An authenticated actor is required.")
        try:
            request = BlackboardRequest.model_validate(payload)
        except ValidationError as exc:
            return _error("invalid_request", "The Blackboard action payload is invalid.", validation_errors=[
                {
                    "loc": ".".join(str(part) for part in item.get("loc", ())),
                    "message": item.get("msg", "invalid value"),
                    "type": item.get("type", "value_error"),
                }
                for item in exc.errors()
            ])

        try:
            action = request.action
            if actor == "dashboard" and action not in {
                "read_blackboard",
                "get_hypotheses",
                "get_solutions",
                "get_problem_status",
            }:
                raise _RepositoryError("forbidden", "The dashboard credential is read-only.")
            if action == "create_problem":
                return self._create_problem(actor, request)
            if action == "read_blackboard":
                return self._read_blackboard(actor, request)
            if action == "write_blackboard":
                return self._write_blackboard(actor, request)
            if action == "update_knowledge":
                return self._update_knowledge(actor, request)
            if action == "get_hypotheses":
                return self._filtered_read(actor, request, KnowledgeType.HYPOTHESIS)
            if action == "get_solutions":
                return self._filtered_read(actor, request, KnowledgeType.SOLUTION)
            if action == "get_problem_status":
                return self._get_problem_status(actor, request)
            if action == "set_problem_status":
                return self._set_problem_status(actor, request)
            return _error("unknown_action", f"Unsupported Blackboard action: {action}")
        except _RepositoryError as exc:
            self._audit_failure(actor, request, exc.code, exc.message)
            return _error(exc.code, exc.message, **exc.details)
        except (ValueError, TypeError, sqlite3.IntegrityError) as exc:
            self._audit_failure(actor, request, "invalid_request", str(exc))
            return _error("invalid_request", str(exc))
        except sqlite3.OperationalError as exc:
            self._audit_failure(actor, request, "storage_error", "The Blackboard storage operation failed.")
            return _error("storage_error", "The Blackboard storage operation failed.", reason=str(exc))

    def _create_problem(self, actor: str, request: BlackboardRequest) -> dict[str, Any]:
        _require_controller(actor)
        if not request.request_id:
            raise _RepositoryError("invalid_request", "create_problem requires request_id.")
        if not request.case_id or not request.title:
            raise _RepositoryError("invalid_request", "create_problem requires case_id and title.")
        problem_id = request.problem_id or f"problem-{uuid4().hex}"
        limits = request.limits or {}
        _json_dumps(limits)

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            now = _now()
            conn.execute(
                "INSERT INTO problems(problem_id, case_id, title, status, board_revision, limits_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
                (problem_id, request.case_id, request.title, ProblemStatus.OPEN.value, _json_dumps(limits), now, now),
            )
            return {
                "problem_id": problem_id,
                "case_id": request.case_id,
                "title": request.title,
                "status": ProblemStatus.OPEN.value,
                "board_revision": 0,
            }

        return self._mutate(actor, request, operation, problem_id=problem_id)

    def _write_blackboard(self, actor: str, request: BlackboardRequest) -> dict[str, Any]:
        if not request.problem_id or not request.knowledge_type or request.data is None:
            raise _RepositoryError("invalid_request", "write_blackboard requires problem_id, knowledge_type, and data.")
        if not request.request_id:
            raise _RepositoryError("invalid_request", "write_blackboard requires request_id.")
        if request.knowledge_type == KnowledgeType.CONTROLLER_DECISION and actor != "controller":
            raise _RepositoryError("forbidden", "Only the controller may write controller_decision entries.")
        data = _clean_data(request.data)
        entry_id = f"entry-{uuid4().hex}"

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            problem = _problem_row(conn, request.problem_id)
            _validate_references(conn, request.problem_id, data)
            now = _now()
            board_revision = int(problem["board_revision"]) + 1
            conn.execute(
                "UPDATE problems SET board_revision = ?, updated_at = ? WHERE problem_id = ?",
                (board_revision, now, request.problem_id),
            )
            conn.execute(
                "INSERT INTO entries(entry_id, problem_id, knowledge_type, source_agent, current_revision, created_at) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (entry_id, request.problem_id, request.knowledge_type.value, actor, now),
            )
            conn.execute(
                "INSERT INTO revisions(entry_id, revision, board_revision, data_json, created_at) VALUES (?, 1, ?, ?, ?)",
                (entry_id, board_revision, _json_dumps(data), now),
            )
            return {
                "entry_id": entry_id,
                "problem_id": request.problem_id,
                "knowledge_type": request.knowledge_type.value,
                "source_agent": actor,
                "revision": 1,
                "board_revision": board_revision,
                "created_at": now,
            }

        return self._mutate(actor, request, operation, problem_id=request.problem_id)

    def _update_knowledge(self, actor: str, request: BlackboardRequest) -> dict[str, Any]:
        if not request.problem_id or not request.entry_id or request.expected_revision is None or request.data is None:
            raise _RepositoryError(
                "invalid_request", "update_knowledge requires problem_id, entry_id, expected_revision, and data."
            )
        if not request.request_id:
            raise _RepositoryError("invalid_request", "update_knowledge requires request_id.")
        data = _clean_data(request.data)

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            problem = _problem_row(conn, request.problem_id)
            row = conn.execute(
                "SELECT entry_id, problem_id, knowledge_type, source_agent, current_revision, created_at "
                "FROM entries WHERE entry_id = ?",
                (request.entry_id,),
            ).fetchone()
            if row is None or row["problem_id"] != request.problem_id:
                raise _RepositoryError("not_found", "The requested entry does not exist in this problem.")
            if row["source_agent"] != actor:
                raise _RepositoryError("forbidden", "An agent may revise only its own entries.")
            if int(row["current_revision"]) != request.expected_revision:
                raise _RepositoryError(
                    "revision_conflict",
                    "The entry changed after it was read; reread before retrying.",
                    expected_revision=request.expected_revision,
                    current_revision=int(row["current_revision"]),
                )
            _validate_references(conn, request.problem_id, data)
            next_revision = int(row["current_revision"]) + 1
            board_revision = int(problem["board_revision"]) + 1
            now = _now()
            conn.execute(
                "UPDATE entries SET current_revision = ? WHERE entry_id = ?",
                (next_revision, request.entry_id),
            )
            conn.execute(
                "UPDATE problems SET board_revision = ?, updated_at = ? WHERE problem_id = ?",
                (board_revision, now, request.problem_id),
            )
            conn.execute(
                "INSERT INTO revisions(entry_id, revision, board_revision, data_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (request.entry_id, next_revision, board_revision, _json_dumps(data), now),
            )
            return {
                "entry_id": request.entry_id,
                "problem_id": request.problem_id,
                "knowledge_type": row["knowledge_type"],
                "source_agent": actor,
                "revision": next_revision,
                "board_revision": board_revision,
                "created_at": now,
            }

        return self._mutate(actor, request, operation, problem_id=request.problem_id)

    def _set_problem_status(self, actor: str, request: BlackboardRequest) -> dict[str, Any]:
        _require_controller(actor)
        if not request.problem_id or request.status is None:
            raise _RepositoryError("invalid_request", "set_problem_status requires problem_id and status.")
        if not request.request_id:
            raise _RepositoryError("invalid_request", "set_problem_status requires request_id.")

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            problem = _problem_row(conn, request.problem_id)
            board_revision = int(problem["board_revision"]) + 1
            now = _now()
            conn.execute(
                "UPDATE problems SET status = ?, board_revision = ?, updated_at = ? WHERE problem_id = ?",
                (request.status.value, board_revision, now, request.problem_id),
            )
            return {
                "problem_id": request.problem_id,
                "status": request.status.value,
                "board_revision": board_revision,
            }

        return self._mutate(actor, request, operation, problem_id=request.problem_id)

    def _read_blackboard(self, actor: str, request: BlackboardRequest) -> dict[str, Any]:
        if not request.problem_id:
            raise _RepositoryError("invalid_request", "read_blackboard requires problem_id.")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")
            problem = _problem_row(conn, request.problem_id)
            types = [item.value for item in request.knowledge_types] if request.knowledge_types else None
            where = ["e.problem_id = ?"]
            args: list[Any] = [request.problem_id]
            if types:
                marks = ",".join("?" for _ in types)
                where.append(f"e.knowledge_type IN ({marks})")
                args.extend(types)
            latest_query = (
                "SELECT e.entry_id, e.problem_id, e.knowledge_type, e.source_agent, r.revision, r.data_json, "
                "r.created_at, r.board_revision FROM entries e "
                "JOIN revisions r ON r.entry_id=e.entry_id AND r.revision=e.current_revision WHERE "
                + " AND ".join(where)
            )
            if request.since_revision is not None:
                latest_query += " AND r.board_revision > ?"
                args.append(request.since_revision)
            latest_query += " ORDER BY r.board_revision, e.entry_id"
            entries = [_entry_dict(row) for row in conn.execute(latest_query, args).fetchall()]

            history_where = ["e.problem_id = ?"]
            history_args: list[Any] = [request.problem_id]
            if types:
                marks = ",".join("?" for _ in types)
                history_where.append(f"e.knowledge_type IN ({marks})")
                history_args.extend(types)
            history_query = (
                "SELECT e.entry_id, e.problem_id, e.knowledge_type, e.source_agent, r.revision, r.data_json, "
                "r.created_at, r.board_revision FROM entries e JOIN revisions r ON r.entry_id=e.entry_id WHERE "
                + " AND ".join(history_where)
            )
            if request.since_revision is not None:
                history_query += " AND r.board_revision > ?"
                history_args.append(request.since_revision)
            history_query += " ORDER BY r.board_revision, e.entry_id, r.revision"
            history = [_entry_dict(row) for row in conn.execute(history_query, history_args).fetchall()]

            # Keep the problem, latest entries, and history in one consistent
            # read snapshot. Finish that snapshot before writing the audit row:
            # in WAL mode, upgrading a read snapshot after another writer has
            # committed can fail immediately with SQLITE_BUSY_SNAPSHOT.
            conn.commit()
            _record_audit(conn, actor, request, "ok", {})
            conn.commit()
            return {
                "problem": _problem_dict(problem),
                "entries": entries,
                "history": history,
            }

    def _filtered_read(
        self, actor: str, request: BlackboardRequest, knowledge_type: KnowledgeType
    ) -> dict[str, Any]:
        return self._read_blackboard(
            actor,
            request.model_copy(update={"knowledge_types": [knowledge_type]}),
        )

    def _get_problem_status(self, actor: str, request: BlackboardRequest) -> dict[str, Any]:
        if not request.problem_id:
            raise _RepositoryError("invalid_request", "get_problem_status requires problem_id.")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")
            problem = _problem_row(conn, request.problem_id)
            request_rows = conn.execute(
                "SELECT e.entry_id, r.data_json FROM entries e JOIN revisions r "
                "ON r.entry_id=e.entry_id AND r.revision=e.current_revision "
                "WHERE e.problem_id=? AND e.knowledge_type='evidence_request'",
                (request.problem_id,),
            ).fetchall()
            observation_rows = conn.execute(
                "SELECT r.data_json FROM entries e JOIN revisions r "
                "ON r.entry_id=e.entry_id AND r.revision=e.current_revision "
                "WHERE e.problem_id=? AND e.knowledge_type='observation'",
                (request.problem_id,),
            ).fetchall()
            request_observation_statuses: dict[str, set[str]] = {}
            for row in observation_rows:
                data = json.loads(row["data_json"])
                request_id = data.get("fulfilled_request_entry_id")
                observation_status = str(data.get("fulfilled_request_status", "")).lower()
                if isinstance(request_id, str) and observation_status:
                    request_observation_statuses.setdefault(request_id, set()).add(observation_status)

            unresolved = []
            for row in request_rows:
                data = json.loads(row["data_json"])
                if str(data.get("status", "open")).lower() in {"open", "pending", "requested", "unresolved"}:
                    linked_statuses = request_observation_statuses.get(row["entry_id"], set())
                    if "fulfilled" in linked_statuses:
                        continue
                    item = {"entry_id": row["entry_id"], **data}
                    if "unavailable" in linked_statuses:
                        # Keep unavailable distinct from fulfilled: the request
                        # has been investigated but the fact remains unknown.
                        item["evidence_status"] = "unavailable"
                    unresolved.append(item)

            status = {
                "problem_id": problem["problem_id"],
                "status": problem["status"],
                "board_revision": int(problem["board_revision"]),
                "unresolved_requests": unresolved,
                "limits": json.loads(problem["limits_json"]),
            }
            # As with read_blackboard, commit the consistent snapshot before
            # recording audit data to avoid a WAL snapshot-upgrade race.
            conn.commit()
            _record_audit(conn, actor, request, "ok", {})
            conn.commit()
            return status

    def _mutate(
        self,
        actor: str,
        request: BlackboardRequest,
        operation: Callable[[sqlite3.Connection], dict[str, Any]],
        *,
        problem_id: str | None,
    ) -> dict[str, Any]:
        request_id = request.request_id
        assert request_id is not None
        digest = hashlib.sha256(_json_dumps(request.model_dump(mode="json", exclude_none=True)).encode()).hexdigest()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT payload_hash, response_json FROM idempotency WHERE actor=? AND request_id=?",
                (actor, request_id),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != digest:
                    raise _RepositoryError(
                        "idempotency_conflict",
                        "The request_id was already used by this actor for a different request.",
                        request_id=request_id,
                    )
                result = json.loads(existing["response_json"])
                result["duplicate"] = True
                _record_audit(conn, actor, request, "duplicate", {"request_id": request_id})
                conn.commit()
                return result

            result = operation(conn)
            conn.execute(
                "INSERT INTO idempotency(actor, request_id, payload_hash, response_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (actor, request_id, digest, _json_dumps(result), _now()),
            )
            _record_audit(conn, actor, request, "ok", {"result": result}, problem_id=problem_id)
            conn.commit()
            return result

    def _audit_failure(self, actor: str, request: BlackboardRequest, code: str, message: str) -> None:
        try:
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                _record_audit(conn, actor, request, "error", {"code": code, "message": message})
                conn.commit()
        except sqlite3.Error:
            return

    def _initialize(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS problems (
                    problem_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    board_revision INTEGER NOT NULL DEFAULT 0,
                    limits_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entries (
                    entry_id TEXT PRIMARY KEY,
                    problem_id TEXT NOT NULL REFERENCES problems(problem_id),
                    knowledge_type TEXT NOT NULL,
                    source_agent TEXT NOT NULL,
                    current_revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS entries_by_problem ON entries(problem_id, knowledge_type);
                CREATE TABLE IF NOT EXISTS revisions (
                    entry_id TEXT NOT NULL REFERENCES entries(entry_id),
                    revision INTEGER NOT NULL,
                    board_revision INTEGER NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(entry_id, revision)
                );
                CREATE INDEX IF NOT EXISTS revisions_by_board ON revisions(board_revision);
                CREATE TABLE IF NOT EXISTS idempotency (
                    actor TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(actor, request_id)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    audit_id TEXT PRIMARY KEY,
                    request_id TEXT,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    problem_id TEXT,
                    outcome TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=15.0, uri=self._uri)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn


def _require_controller(actor: str) -> None:
    if actor != "controller":
        raise _RepositoryError("forbidden", "Only the controller may perform this action.")


def _problem_row(conn: sqlite3.Connection, problem_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM problems WHERE problem_id=?", (problem_id,)).fetchone()
    if row is None:
        raise _RepositoryError("not_found", "The requested problem does not exist.", problem_id=problem_id)
    return row


def _validate_references(conn: sqlite3.Connection, problem_id: str, data: Any) -> None:
    references: list[str] = []

    def visit(value: Any, parent_key: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).lower()
                is_reference = (
                    normalized.endswith(("_entry_id", "_entry_ids", "_evidence_id", "_evidence_ids", "_observation_id", "_observation_ids", "_hypothesis_id", "_hypothesis_ids"))
                    or normalized in {"entry_id", "entry_ids", "evidence_id", "evidence_ids", "observation_id", "observation_ids", "hypothesis_id", "hypothesis_ids"}
                )
                if is_reference:
                    if isinstance(child, list):
                        references.extend(item for item in child if isinstance(item, str))
                    elif isinstance(child, str):
                        references.append(child)
                else:
                    visit(child, normalized)
        elif isinstance(value, list):
            for child in value:
                visit(child, parent_key)

    visit(data)
    for entry_id in set(references):
        row = conn.execute(
            "SELECT problem_id FROM entries WHERE entry_id=?",
            (entry_id,),
        ).fetchone()
        if row is None:
            raise _RepositoryError(
                "invalid_reference", "A knowledge reference does not point to a stored entry.", entry_id=entry_id
            )
        if row["problem_id"] != problem_id:
            raise _RepositoryError(
                "cross_case_reference",
                "Knowledge entries may reference only entries from the same problem.",
                entry_id=entry_id,
            )


def _record_audit(
    conn: sqlite3.Connection,
    actor: str,
    request: BlackboardRequest,
    outcome: str,
    details: dict[str, Any],
    *,
    problem_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_events(audit_id, request_id, actor, action, problem_id, outcome, details_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            uuid4().hex,
            request.request_id,
            actor,
            request.action,
            problem_id or request.problem_id,
            outcome,
            _json_dumps(details),
            _now(),
        ),
    )


def _entry_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "entry_id": row["entry_id"],
        "problem_id": row["problem_id"],
        "knowledge_type": row["knowledge_type"],
        "source_agent": row["source_agent"],
        "revision": int(row["revision"]),
        "data": json.loads(row["data_json"]),
        "created_at": row["created_at"],
        "board_revision": int(row["board_revision"]),
    }


def _problem_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "problem_id": row["problem_id"],
        "case_id": row["case_id"],
        "title": row["title"],
        "status": row["status"],
        "board_revision": int(row["board_revision"]),
        "limits": json.loads(row["limits_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _clean_data(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise _RepositoryError("invalid_request", "knowledge data must be a JSON object.")
    try:
        return json.loads(_json_dumps(data))
    except (ValueError, TypeError) as exc:
        raise _RepositoryError("invalid_request", "knowledge data must contain only JSON values.") from exc


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}

