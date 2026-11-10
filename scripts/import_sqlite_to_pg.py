"""Copy the bot's SQLite database into Postgres, once, safely, re-runnably.

What it does
------------
1. Reads every user table from the SQLite file (``data/bot.db`` by default) with
   the stdlib ``sqlite3`` module.
2. Inserts into Postgres in **dependency order** (parents before children, from
   ``PRAGMA foreign_key_list``), so the foreign keys the schema declares hold at
   every step.
3. Is **idempotent**: every insert is ``ON CONFLICT DO NOTHING`` against the
   table's real primary key. A failed run is retried by running it again; a
   completed run is a no-op.
4. Compares each table's row count, ``source -> target``, after the copy and
   exits non-zero on any mismatch.
5. Prints counts only. It never prints the DSN, a token, or row content.

Sequences
---------
Rows arrive with their explicit ids, which leaves each ``BIGSERIAL`` sequence
behind the data. After each table is copied the script advances the sequence to
``MAX(id) + 1`` so the first row the bot inserts afterwards does not collide.

Usage::

    python -m scripts.import_sqlite_to_pg --dsn-file ~/.neon_uri
    python -m scripts.import_sqlite_to_pg --database-url "$CLOUDBOT_DATABASE_URL"

``--schema`` puts the bot's tables in a dedicated Postgres schema (the DSN's
default ``public`` is used when it is omitted).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from cloudbot.adapters.persistence.database import Database, is_postgres_url, pg_dsn

#: SQLite's own bookkeeping tables are never copied.
_SQLITE_INTERNAL = {"sqlite_sequence", "sqlite_stat1", "sqlite_stat4"}


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]
    references: tuple[str, ...]


def load_source(path: Path) -> dict[str, list[tuple]]:
    """Every user table's rows, in a stable order, keyed by table name."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            if str(row[0]) not in _SQLITE_INTERNAL
        ]
        data: dict[str, list[tuple]] = {}
        for name in names:
            cursor = connection.execute(f'SELECT * FROM "{name}"')
            data[name] = [tuple(row) for row in cursor.fetchall()]
        return data
    finally:
        connection.close()


def describe_source(path: Path) -> dict[str, Table]:
    """Column list, primary key and parent tables, read from SQLite itself."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            if str(row[0]) not in _SQLITE_INTERNAL
        ]
        tables: dict[str, Table] = {}
        for name in names:
            info = connection.execute(f'PRAGMA table_info("{name}")').fetchall()
            columns = tuple(str(row[1]) for row in info)
            primary_key = tuple(
                str(row[1]) for row in sorted(info, key=lambda row: row[5]) if row[5]
            )
            parents = tuple(
                sorted(
                    {
                        str(row[2])
                        for row in connection.execute(
                            f'PRAGMA foreign_key_list("{name}")'
                        )
                        if str(row[2]) in names and str(row[2]) != name
                    }
                )
            )
            tables[name] = Table(
                name=name,
                columns=columns,
                primary_key=primary_key,
                references=parents,
            )
        return tables
    finally:
        connection.close()


def dependency_order(tables: dict[str, Table]) -> list[str]:
    """Parents before children; an unrelated table keeps alphabetical order."""
    ordered: list[str] = []
    remaining = {name: set(table.references) for name, table in tables.items()}
    while remaining:
        ready = sorted(name for name, parents in remaining.items() if not parents)
        if not ready:  # a cycle: fall back to alphabetical rather than stop
            ready = sorted(remaining)
        for name in ready:
            ordered.append(name)
            remaining.pop(name)
        for parents in remaining.values():
            parents.difference_update(ready)
    return ordered


async def copy(
    source_path: Path,
    database_url: str,
    *,
    schema: str | None = None,
) -> dict[str, tuple[int, int]]:
    """Perform the copy; return ``{table: (source_count, target_count)}``."""
    tables = describe_source(source_path)
    rows = load_source(source_path)

    if schema is not None:
        await _ensure_schema(database_url, schema)
    database = Database(database_url, search_path=schema)
    try:
        await database.connect()
        await database.migrate()
        connection = await database.connect()

        for name in dependency_order(tables):
            table = tables[name]
            source_rows = rows.get(name, [])
            if not table.primary_key and source_rows:
                async with connection.execute(
                    f'SELECT COUNT(*) AS n FROM "{name}"'
                ) as cur:
                    existing = int((await cur.fetchone())["n"])
                if existing:
                    raise SystemExit(
                        f"{name}: no primary key and the target already has "
                        f"{existing} row(s); cannot copy idempotently"
                    )

            column_sql = ", ".join(f'"{column}"' for column in table.columns)
            placeholders = ", ".join("?" for _ in table.columns)
            conflict = (
                " ON CONFLICT DO NOTHING" if table.primary_key else ""
            )
            sql = (
                f'INSERT INTO "{name}" ({column_sql}) VALUES ({placeholders})'
                f"{conflict}"
            )
            for row in source_rows:
                await connection.execute(sql, row)

            await _resync_sequence(connection, name)

        counts: dict[str, tuple[int, int]] = {}
        for name in dependency_order(tables):
            async with connection.execute(
                f'SELECT COUNT(*) AS n FROM "{name}"'
            ) as cur:
                target = int((await cur.fetchone())["n"])
            counts[name] = (len(rows.get(name, [])), target)
        return counts
    finally:
        await database.close()


async def _ensure_schema(database_url: str, schema: str) -> None:
    """Create ``schema`` when it is missing, so ``--schema`` works on a fresh DB."""
    import asyncpg

    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", schema):
        raise SystemExit(f"invalid schema name {schema!r}")
    connection = await asyncpg.connect(pg_dsn(database_url))
    try:
        await connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    finally:
        await connection.close()


async def _resync_sequence(connection, table: str) -> None:
    """Move a table's identity sequence past the imported ids."""
    async with connection.execute(
        "SELECT a.attname AS column, pg_get_serial_sequence(?::text, a.attname) AS seq "
        "FROM pg_attribute a "
        "WHERE a.attrelid = to_regclass(?) AND a.attnum > 0 AND NOT a.attisdropped",
        (table, table),
    ) as cur:
        sequences = [
            (str(row["column"]), str(row["seq"]))
            for row in await cur.fetchall()
            if row["seq"] is not None
        ]
    for column, sequence in sequences:
        await connection.execute(
            f'SELECT setval(?, COALESCE((SELECT MAX("{column}") FROM "{table}"), 0) + 1, false)',
            (sequence,),
        )


def report(counts: dict[str, tuple[int, int]]) -> bool:
    """Print ``source -> target`` per table; return whether every count matched."""
    width = max((len(name) for name in counts), default=4)
    ok = True
    for name in sorted(counts):
        source, target = counts[name]
        matched = source == target
        ok = ok and matched
        print(f"{name:<{width}}  {source:>6} -> {target:>6}  {'ok' if matched else 'MISMATCH'}")
    return ok


def _dsn_from(args: argparse.Namespace) -> str:
    if args.database_url:
        return args.database_url
    if args.dsn_file:
        text = Path(args.dsn_file).expanduser().read_text(encoding="utf-8").strip()
        if text:
            return text
    import os

    value = os.environ.get("CLOUDBOT_DATABASE_URL", "")
    if value and is_postgres_url(value):
        return value
    raise SystemExit(
        "no Postgres DSN: pass --dsn-file or --database-url, or set "
        "CLOUDBOT_DATABASE_URL to a postgresql:// URL"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sqlite", default="data/bot.db", help="source SQLite file")
    parser.add_argument("--dsn-file", help="file holding the Postgres DSN")
    parser.add_argument("--database-url", help="Postgres DSN (avoid: shell history)")
    parser.add_argument("--schema", help="Postgres schema (default: the DSN's)")
    args = parser.parse_args(argv)

    source_path = Path(args.sqlite)
    if not source_path.exists():
        print(f"source database not found: {source_path}", file=sys.stderr)
        return 2
    url = _dsn_from(args)
    if not is_postgres_url(url):
        print("the target DSN is not a postgresql:// URL", file=sys.stderr)
        return 2
    # Normalise (and validate) the DSN without ever printing it.
    pg_dsn(url)

    counts = asyncio.run(copy(source_path, url, schema=args.schema))
    print(f"\n{len(counts)} tables copied\n")
    if not report(counts):
        print("\nrow counts do not match; nothing was deleted", file=sys.stderr)
        return 1
    total = sum(source for source, _ in counts.values())
    print(f"\n{total} rows verified in {len(counts)} tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
