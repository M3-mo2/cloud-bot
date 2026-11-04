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


def row_to_user(row: Any) -> User:
    return User(
        tg_id=int(row["tg_id"]),
        locale=coerce_locale(row["locale"]),
        created_at=parse_dt(row["created_at"]),
        plan=str(row["plan"]),
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
    )
