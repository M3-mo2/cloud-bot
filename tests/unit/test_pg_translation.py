"""Unit tests for the SQLite -> Postgres statement translator (no database).

The facade's contract is proved here without a server: placeholder numbering,
the "``?`` inside a literal or a comment is data" rule, and the statement forms
the stores actually use. The live-server counterpart is
``tests/contract/test_pg_facade.py``.
"""

from __future__ import annotations

import pytest

from cloudbot.adapters.persistence.pg_connection import (
    PgTranslationError,
    parse_rowcount,
    translate_sql,
)


def test_placeholders_numbered_in_order() -> None:
    result = translate_sql(
        "SELECT * FROM users WHERE tg_id = ? AND locale = ? ORDER BY created_at LIMIT ?"
    )
    assert result.sql == (
        "SELECT * FROM users WHERE tg_id = $1 AND locale = $2 "
        "ORDER BY created_at LIMIT $3"
    )
    assert result.placeholders == 3


def test_question_mark_inside_a_string_literal_is_data() -> None:
    result = translate_sql("SELECT 'what?' AS q, ? AS value")
    assert result.sql == "SELECT 'what?' AS q, $1 AS value"
    assert result.placeholders == 1


def test_question_mark_inside_a_comment_is_data() -> None:
    sql = "SELECT 1 AS a -- why? because\nFROM users WHERE tg_id = ?"
    result = translate_sql(sql)
    assert "why? because" in result.sql
    assert result.placeholders == 1
    assert "$1" in result.sql


def test_doubled_quote_escape_stays_inside_the_literal() -> None:
    result = translate_sql("INSERT INTO t (note) VALUES ('it''s ? fine') RETURNING ?")
    assert result.placeholders == 1
    assert "'it''s ? fine'" in result.sql


def test_insert_or_ignore_becomes_conflict_do_nothing() -> None:
    result = translate_sql(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)"
    )
    assert result.sql == (
        "INSERT INTO schema_migrations (version, applied_at) VALUES ($1, $2) "
        "ON CONFLICT DO NOTHING"
    )
    assert result.placeholders == 2


def test_insert_or_replace_asks_for_the_primary_key() -> None:
    result = translate_sql(
        "INSERT OR REPLACE INTO runtime_flags (key, value, updated_at) VALUES (?, ?, ?)"
    )
    assert result.replace is not None
    assert result.replace.table == "runtime_flags"
    assert result.replace.columns == ("key", "value", "updated_at")
    # The conflict target is derived at execution time, never hardcoded here.
    assert result.sql.startswith("INSERT INTO runtime_flags")
    assert "ON CONFLICT" not in result.sql


def test_insert_or_replace_without_columns_is_refused() -> None:
    with pytest.raises(PgTranslationError):
        translate_sql("INSERT OR REPLACE INTO t VALUES (?, ?)")


def test_datetime_now_becomes_the_utc_text_rendering() -> None:
    result = translate_sql(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))"
    )
    assert "datetime('now')" not in result.sql
    assert "to_char(now() at time zone 'utc'" in result.sql
    assert result.placeholders == 1


def test_date_now_becomes_current_date_text() -> None:
    result = translate_sql("SELECT date('now') AS d")
    assert result.sql == "SELECT CURRENT_DATE::text AS d"


def test_sqlite_master_becomes_a_pg_catalog_relation() -> None:
    result = translate_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    )
    assert "FROM sqlite_master" not in result.sql
    assert "pg_catalog.pg_tables" in result.sql
    assert "SELECT name FROM" in result.sql


def test_datetime_now_inside_a_literal_is_untouched() -> None:
    result = translate_sql("SELECT 'datetime(''now'')' AS text")
    assert "datetime(''now'')" in result.sql
    assert "to_char" not in result.sql


def test_expression_inside_a_literal_keeps_its_question_mark() -> None:
    # A literal that happens to contain a placeholder-looking character must not
    # consume a parameter slot: the count is what the caller binds.
    result = translate_sql("SELECT '?' AS marker, ? AS bound")
    assert result.placeholders == 1
    assert result.sql == "SELECT '?' AS marker, $1 AS bound"


@pytest.mark.parametrize(
    "status,expected",
    [
        ("INSERT 0 1", 1),
        ("INSERT 0 7", 7),
        ("INSERT 0 0", 0),
        ("UPDATE 3", 3),
        ("UPDATE 0", 0),
        ("DELETE 2", 2),
        ("DELETE 0", 0),
        ("SELECT 5", 5),
        (None, -1),
        ("", -1),
        ("nonsense", -1),
    ],
)
def test_parse_rowcount(status, expected) -> None:
    assert parse_rowcount(status) == expected


def test_pg_dsn_strips_channel_binding_and_keeps_sslmode() -> None:
    from cloudbot.adapters.persistence.database import pg_dsn

    dsn = pg_dsn(
        "postgresql://user:secret@db.example/cloudbot"
        "?channel_binding=require&sslmode=require"
    )
    assert "channel_binding" not in dsn
    assert "sslmode=require" in dsn


def test_pg_dsn_normalises_the_postgres_alias() -> None:
    from cloudbot.adapters.persistence.database import pg_dsn

    assert pg_dsn("postgres://u:p@h/db").startswith("postgresql://")
    assert pg_dsn("postgresql+asyncpg://u:p@h/db").startswith("postgresql://")


def test_backend_is_picked_from_the_dsn() -> None:
    from cloudbot.adapters.persistence.database import Database, is_postgres_url

    assert is_postgres_url("postgresql://u:p@h/db")
    assert is_postgres_url("postgres://u:p@h/db")
    assert not is_postgres_url("sqlite+aiosqlite:///./data/bot.db")
    assert Database("postgresql://u:p@h/db").is_postgres is True
    assert Database("postgresql://u:p@h/db").path == ""
    assert Database(":memory:").is_postgres is False
    assert Database(":memory:").migrations_dir.name == "migrations"


def test_search_path_must_be_a_plain_identifier() -> None:
    from cloudbot.adapters.persistence.database import Database

    with pytest.raises(ValueError):
        Database("postgresql://u:p@h/db", search_path='x"; DROP SCHEMA public; --')
