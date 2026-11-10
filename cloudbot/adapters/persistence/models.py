"""Row ↔ entity mapping for the persistence adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cloudbot.core.locale import coerce_locale
from cloudbot.domain.entities import User


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _optional(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def row_to_user(row: Any) -> User:
    last_seen = _optional(row, "last_seen_at")
    return User(
        tg_id=int(row["tg_id"]),
        locale=coerce_locale(row["locale"]),
        created_at=parse_dt(row["created_at"]),
        display_name=str(_optional(row, "display_name") or ""),
        last_seen_at=parse_dt(last_seen) if last_seen else None,
    )


def row_to_event(row: Any) -> "EventRecord":
    import json

    from cloudbot.domain.events import EventCategory, EventRecord, Severity

    try:
        params = json.loads(row["params"] or "{}")
        if not isinstance(params, dict):
            params = {}
    except (ValueError, TypeError):
        params = {}
    category = EventCategory(str(row["category"]))
    try:
        severity = Severity(str(row["severity"]))
    except ValueError:
        severity = category.default_severity
    return EventRecord(
        id=int(row["id"]),
        ts=parse_dt(row["ts"]),
        category=category,
        severity=severity,
        tg_id=int(row["tg_id"]) if row["tg_id"] is not None else None,
        ws_id=str(row["ws_id"]) if row["ws_id"] is not None else None,
        summary_key=str(row["summary_key"]),
        params=params,
        actor=str(row["actor"]),
    )


@dataclass(frozen=True)
class SessionRow:
    tg_id: int
    ws_id: str
    ses_id: str
    cols: int
    rows: int
    out_offset: int
    created_at: str
    last_seen_at: str
    ended_at: str | None = None

def row_to_session(row: Any) -> SessionRow:
    return SessionRow(
        tg_id=int(row["tg_id"]),
        ws_id=str(row["ws_id"]),
        ses_id=str(row["ses_id"]),
        cols=int(row["cols"]),
        rows=int(row["rows"]),
        out_offset=int(row["out_offset"]),
        created_at=str(row["created_at"]),
        last_seen_at=str(row["last_seen_at"]),
        ended_at=str(_optional(row, "ended_at")) if _optional(row, "ended_at") else None,
    )
