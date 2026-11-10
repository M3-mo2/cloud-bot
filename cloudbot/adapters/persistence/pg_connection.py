"""The ``aiosqlite`` surface over ``asyncpg``, for call sites already written against it.

Why a facade instead of a rewrite
---------------------------------
The persistence stores were written against a tiny, well-defined surface:
``execute`` / an async cursor context manager / ``fetchone`` / ``fetchall`` /
``rowcount`` / ``lastrowid`` / ``commit`` / ``executemany`` / ``executescript``
and dict-like rows. Measured on this tree when the facade was written: 57
``async with conn.execute(...)`` sites, 76 plain ``await conn.execute(...)``
sites, 35 ``fetchone()`` and 25 ``fetchall()`` call sites, 3 ``executemany`` and
1 ``executescript``, and 41,896 ``?`` placeholders across 12 store modules.

Rewriting ~180 call sites by hand is 180 chances to introduce a bug that only
shows at runtime. One adapter is one place to test: this module is the unit under
test, and the test suite then proves the adapter once instead of proving every
store twice.

What is translated (and only at execution time)
-----------------------------------------------
* ``?`` -> ``$1``, ``$2`` ... in order of appearance, and only outside string
  literals, quoted identifiers and comments (a ``?`` inside a quoted string is
  data, not a placeholder).
* ``INSERT OR IGNORE`` -> ``INSERT ... ON CONFLICT DO NOTHING``.
* ``INSERT OR REPLACE`` -> ``INSERT ... ON CONFLICT (<primary key>) DO UPDATE``,
  with the primary key derived from the live catalog (never hardcoded). If it
  cannot be derived, the statement is refused naming the table.
* ``datetime('now')`` -> the UTC RFC3339 TEXT rendering of ``now()``;
  ``date('now')`` -> ``CURRENT_DATE::text``. Timestamps stay TEXT here (see the
  ADR "one facade over asyncpg"), so the value the domain reads back is still a
  string; ``now()`` itself would return a ``datetime`` and break ``parse_dt``.
* ``sqlite_master`` -> a derived table over ``pg_catalog`` exposing the same
  ``name``/``type`` columns, so the existing ``_applied_versions`` query works.
* ``PRAGMA ...`` -> a no-op. Postgres always enforces foreign keys, and WAL is a
  SQLite journal concept; the intent is kept in a comment rather than deleted.
* ``BEGIN IMMEDIATE`` -> a real ``BEGIN``. SQLite's IMMEDIATE takes the write
  lock up front; Postgres needs no such hint.

Transactions
------------
Every statement is autocommit by default, exactly like the SQLite connection the
stores were written for (``isolation_level=None``), so a write that is followed
by a read on the same handle still sees its own row. An explicit ``BEGIN`` (the
subscription store's ``BEGIN IMMEDIATE``) pins one pooled connection for the
duration of that transaction and is released on ``COMMIT``/``ROLLBACK``; that is
what keeps a purchase (check balance -> debit -> extend -> append) atomic.

Dependencies are imported lazily so SQLite deployments never need ``asyncpg``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

__all__ = [
    "PgConnection",
    "PgCursor",
    "PgTranslationError",
    "ReplaceRequest",
    "parse_rowcount",
    "translate_sql",
]


class PgTranslationError(ValueError):
    """A SQLite statement that cannot be translated faithfully."""


# ---------------------------------------------------------------------------
# statement text translation
# ---------------------------------------------------------------------------

_SQLITE_MASTER = (
    "(SELECT tablename AS name, 'table'::text AS type "
    "FROM pg_catalog.pg_tables "
    "WHERE schemaname = ANY (current_schemas(false)) "
    "UNION ALL "
    "SELECT indexname AS name, 'index'::text AS type "
    "FROM pg_catalog.pg_indexes "
    "WHERE schemaname = ANY (current_schemas(false))) AS sqlite_master"
)

_NOW_TEXT = (
    "to_char(now() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
)

_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\b", re.IGNORECASE)
_INSERT_OR_REPLACE = re.compile(r"\bINSERT\s+OR\s+REPLACE\b", re.IGNORECASE)
_DATETIME_NOW = re.compile(r"\bdatetime\s*\(\s*'now'\s*\)", re.IGNORECASE)
_DATE_NOW = re.compile(r"\bdate\s*\(\s*'now'\s*\)", re.IGNORECASE)
_SQLITE_MASTER_TOKEN = re.compile(r"\bsqlite_master\b", re.IGNORECASE)
_INSERT_COLUMNS = re.compile(
    r"^\s*INSERT\s+INTO\s+(?P<table>[A-Za-z_][\w.\"]*)\s*"
    r"\((?P<columns>[^)]*)\)\s*VALUES\b",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class ReplaceRequest:
    """An ``INSERT OR REPLACE`` whose conflict target must be derived."""

    table: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class Translation:
    sql: str
    placeholders: int
    replace: ReplaceRequest | None = None


def _code_mask(sql: str) -> list[bool]:
    """``True`` for every character that is code, ``False`` inside literals/comments."""
    mask = [True] * len(sql)
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'" or ch == '"':
            quote = ch
            j = i + 1
            while j < n:
                if sql[j] == quote:
                    if j + 1 < n and sql[j + 1] == quote:  # doubled -> escaped
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            for k in range(i, min(j, n)):
                mask[k] = False
            i = j
        elif ch == "-" and sql[i : i + 2] == "--":
            j = sql.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                mask[k] = False
            i = j
        elif ch == "/" and sql[i : i + 2] == "/*":
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, min(j, n)):
                mask[k] = False
            i = j
        else:
            i += 1
    return mask


def _replace_code(
    sql: str,
    pattern: re.Pattern[str],
    replacement: str | Callable[[re.Match[str]], str],
) -> tuple[str, bool]:
    """Apply ``pattern`` only where the match starts in code (outside literals)."""
    mask = _code_mask(sql)
    out: list[str] = []
    last = 0
    changed = False
    for match in pattern.finditer(sql):
        if not mask[match.start()]:
            continue
        out.append(sql[last : match.start()])
        out.append(
            replacement(match) if callable(replacement) else match.expand(replacement)
        )
        last = match.end()
        changed = True
    if not changed:
        return sql, False
    out.append(sql[last:])
    return "".join(out), True


def _append_clause(sql: str, clause: str) -> str:
    body = sql.rstrip()
    suffix = ""
    if body.endswith(";"):
        body, suffix = body[:-1].rstrip(), ";"
    return f"{body} {clause}{suffix}"


def translate_sql(sql: str) -> Translation:
    """Translate one SQLite statement into its Postgres equivalent.

    Pure (no database access): ``INSERT OR REPLACE`` returns a ``ReplaceRequest``
    so the caller can resolve the primary key from the live catalog.
    """
    text = sql
    replace: ReplaceRequest | None = None

    text, had_replace = _replace_code(text, _INSERT_OR_REPLACE, "INSERT")
    if had_replace:
        match = _INSERT_COLUMNS.match(text)
        if match is None:
            raise PgTranslationError(
                "INSERT OR REPLACE without an explicit column list cannot be "
                "translated (the conflict target must be derived from the row)"
            )
        table = match.group("table").strip().strip('"')
        columns = tuple(
            column.strip().strip('"')
            for column in match.group("columns").split(",")
            if column.strip()
        )
        if not columns:
            raise PgTranslationError(
                f"INSERT OR REPLACE into {table!r} names no columns; cannot "
                "derive the conflict target"
            )
        replace = ReplaceRequest(table=table, columns=columns)

    text, _ = _replace_code(text, _INSERT_OR_IGNORE, "INSERT")
    ignored = _is_ignored_insert(sql)
    text, _ = _replace_code(text, _DATETIME_NOW, _NOW_TEXT)
    text, _ = _replace_code(text, _DATE_NOW, "CURRENT_DATE::text")
    text, _ = _replace_code(text, _SQLITE_MASTER_TOKEN, _SQLITE_MASTER)

    # Number placeholders last, so nothing above can shift the numbering.
    mask = _code_mask(text)
    pieces: list[str] = []
    counter = 0
    for index, char in enumerate(text):
        if char == "?" and mask[index]:
            counter += 1
            pieces.append(f"${counter}")
        else:
            pieces.append(char)
    translated = "".join(pieces)

    if ignored:
        translated = _append_clause(translated, "ON CONFLICT DO NOTHING")
    return Translation(sql=translated, placeholders=counter, replace=replace)


def _is_ignored_insert(sql: str) -> bool:
    match = _INSERT_OR_IGNORE.search(sql)
    return bool(match and _code_mask(sql)[match.start()])


def _first_keyword(sql: str) -> str:
    """First keyword of a statement, with leading comments/whitespace skipped."""
    mask = _code_mask(sql)
    for index, char in enumerate(sql):
        if mask[index] and not char.isspace():
            match = re.match(r"[A-Za-z_]+", sql[index:])
            return match.group(0).upper() if match else ""
    return ""


def parse_rowcount(status: str | None) -> int:
    """Row count from asyncpg's status tag (``INSERT 0 3`` / ``UPDATE 2``)."""
    if not status:
        return -1
    parts = status.split()
    if not parts:
        return -1
    if parts[0] == "INSERT":
        try:
            return int(parts[-1])
        except (IndexError, ValueError):
            return -1
    if parts[0] in {"UPDATE", "DELETE", "SELECT", "MOVE", "FETCH", "COPY"}:
        try:
            return int(parts[-1])
        except (IndexError, ValueError):
            return -1
    return -1


# ---------------------------------------------------------------------------
# the cursor
# ---------------------------------------------------------------------------


class PgCursor:
    """A cursor that is its own async context manager and its own awaitable.

    ``await conn.execute(...)`` awaits one of these (76 call sites) and
    ``async with conn.execute(...) as cur:`` enters the same object (57 sites).
    The work happens once, on the first await/enter, so the two spellings share
    one path. ``async for`` is not used anywhere in the tree, so it is not
    offered.
    """

    def __init__(self, runner: Callable[[], Any] | None = None) -> None:
        self._runner = runner
        self._rows: list[Any] = []
        self._status: str | None = None
        self._lastrowid = 0
        self._executed = runner is None
        self._index = 0

    @classmethod
    def ready(
        cls,
        rows: Sequence[Any] = (),
        status: str | None = None,
        *,
        lastrowid: int = 0,
    ) -> "PgCursor":
        cursor = cls()
        cursor._rows = list(rows)
        cursor._status = status
        cursor._lastrowid = int(lastrowid or 0)
        return cursor

    async def _run(self) -> "PgCursor":
        if not self._executed:
            self._executed = True
            result = await self._runner()
            if isinstance(result, PgCursor):
                self._rows = result._rows
                self._status = result._status
                self._lastrowid = result._lastrowid
            else:
                rows, status, lastrowid = result
                self._rows = list(rows)
                self._status = status
                self._lastrowid = int(lastrowid or 0)
        return self

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self) -> "PgCursor":
        return await self._run()

    async def __aexit__(self, *exc_info: object) -> None:
        # Rows are already materialised and the pooled connection is released by
        # the runner; there is nothing to close.
        return None

    @property
    def rowcount(self) -> int:
        if self._status is None:
            return len(self._rows)
        return parse_rowcount(self._status)

    @property
    def lastrowid(self) -> int:
        return self._lastrowid

    @property
    def description(self) -> None:
        return None

    async def fetchone(self) -> Any | None:
        await self._run()
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    async def fetchall(self) -> list[Any]:
        await self._run()
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows

    async def fetchmany(self, size: int = 1) -> list[Any]:
        await self._run()
        rows = self._rows[self._index : self._index + size]
        self._index += len(rows)
        return rows

    async def close(self) -> None:
        self._rows = []
        self._index = 0


# ---------------------------------------------------------------------------
# the connection
# ---------------------------------------------------------------------------


class PgConnection:
    """The aiosqlite surface over asyncpg, for call sites already written against it."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._tx: Any | None = None
        self._closed = False

    # -- statement execution -------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> PgCursor:
        """Run one statement; the returned cursor is awaitable *and* a context manager."""
        keyword = _first_keyword(sql)
        if keyword == "PRAGMA":
            # No-op: Postgres always enforces foreign keys, and WAL is a SQLite
            # journal concept. Keeping the call site unchanged preserves intent.
            return PgCursor.ready([], "PRAGMA")
        if keyword == "BEGIN":
            return PgCursor(self._begin)
        if keyword == "COMMIT":
            return PgCursor(lambda: self._end(commit=True))
        if keyword == "ROLLBACK":
            return PgCursor(lambda: self._end(commit=False))

        translation = translate_sql(sql)
        args = _coerce_params(params)
        self._check_arity(translation, len(args))
        select = keyword in {"SELECT", "WITH", "VALUES", "TABLE", "SHOW", "EXPLAIN"}

        async def run() -> tuple[list[Any], str | None, int]:
            replace = translation.replace
            statement = translation.sql
            if replace is not None:
                statement = await self._resolve_replace(replace, translation)

            async def inner(con: Any) -> tuple[list[Any], str | None, int]:
                if select:
                    rows = await con.fetch(statement, *args)
                    return list(rows), f"SELECT {len(rows)}", 0
                status = await con.execute(statement, *args)
                lastrowid = 0
                if status.startswith("INSERT ") and parse_rowcount(status) > 0:
                    lastrowid = await self._lastrowid(con)
                return [], status, lastrowid

            return await self._with_connection(inner)

        return PgCursor(run)

    async def executemany(
        self, sql: str, seq: Sequence[Sequence[Any]]
    ) -> PgCursor:
        translation = translate_sql(sql)
        if translation.replace is not None:
            raise PgTranslationError(
                "INSERT OR REPLACE is not supported through executemany"
            )
        statement = translation.sql
        rows = [_coerce_params(row) for row in seq]
        if not rows:
            return PgCursor.ready([], "EXECUTEMANY 0")
        for row in rows:
            self._check_arity(translation, len(row))

        async def run(con: Any) -> PgCursor:
            await con.executemany(statement, rows)
            return PgCursor.ready([], f"EXECUTEMANY {len(rows)}")

        return await self._with_connection(run)

    async def executescript(self, script: str) -> None:
        if not script.strip():
            return

        async def run(con: Any) -> None:
            await con.execute(script)

        await self._with_connection(run)

    # -- transactions --------------------------------------------------------
    async def commit(self) -> None:
        if self._tx is not None:
            await self._end(commit=True)

    async def rollback(self) -> None:
        if self._tx is not None:
            await self._end(commit=False)

    async def close(self) -> None:
        """Release this handle's transaction; the pool itself is owned by ``Database``."""
        if self._closed:
            return
        self._closed = True
        con, self._tx = self._tx, None
        if con is not None:
            try:
                await con.execute("ROLLBACK")
            except Exception:  # noqa: BLE001 - best effort
                pass
            await self._pool.release(con)

    # -- internals -----------------------------------------------------------
    async def _with_connection(self, run: Callable[[Any], Any]) -> Any:
        if self._tx is not None:
            return await run(self._tx)
        async with self._pool.acquire() as con:
            return await run(con)

    async def _begin(self) -> PgCursor:
        if self._tx is not None:
            # Already inside a transaction on the shared facade; a nested BEGIN is
            # what the autocommit SQLite connection would refuse too.
            raise PgTranslationError("cannot start a transaction within a transaction")
        con = await self._pool.acquire()
        try:
            await con.execute("BEGIN")
        except BaseException:
            await self._pool.release(con)
            raise
        self._tx = con
        return PgCursor.ready([], "BEGIN")

    async def _end(self, *, commit: bool) -> PgCursor:
        con = self._tx
        if con is None:
            # Autocommit mode: COMMIT/ROLLBACK are no-ops, as on the SQLite handle
            # opened with isolation_level=None.
            return PgCursor.ready([], "COMMIT" if commit else "ROLLBACK")
        self._tx = None
        try:
            await con.execute("COMMIT" if commit else "ROLLBACK")
        finally:
            await self._pool.release(con)
        return PgCursor.ready([], "COMMIT" if commit else "ROLLBACK")

    def _check_arity(self, translation: Translation, given: int) -> None:
        if translation.placeholders == given:
            return
        raise PgTranslationError(
            f"placeholder/parameter count mismatch: {translation.placeholders} "
            f"placeholder(s) but {given} parameter(s) in SQL: {translation.sql}"
        )

    async def _resolve_replace(
        self, request: ReplaceRequest, translation: Translation
    ) -> str:
        primary_key = await self._primary_key(request.table)
        if not primary_key:
            raise PgTranslationError(
                f"INSERT OR REPLACE into table {request.table!r} but that table "
                "has no primary key, so the conflict target cannot be derived"
            )
        updates = [
            f"{column} = EXCLUDED.{column}"
            for column in request.columns
            if column not in primary_key
        ]
        conflict = ", ".join(primary_key)
        if not updates:
            clause = f"ON CONFLICT ({conflict}) DO NOTHING"
        else:
            clause = f"ON CONFLICT ({conflict}) DO UPDATE SET " + ", ".join(updates)
        return _append_clause(translation.sql, clause)

    async def _lastrowid(self, con: Any) -> int:
        """``lastrowid`` from the row an INSERT just made.

        SQLite's ``lastrowid`` is the rowid of the last insert; Postgres's
        equivalent is ``lastval()`` (the last value a sequence produced). A table
        without a sequence raises "lastval is not yet defined in this session",
        and *inside a transaction any failed statement aborts it* -- so in a
        transaction the probe runs under a savepoint, and a miss rolls back to
        that savepoint instead of poisoning the caller's transaction.
        """
        if self._tx is None:
            try:
                return int(await con.fetchval("SELECT lastval()"))
            except Exception:  # noqa: BLE001 - no sequence used: lastrowid is 0
                return 0
        try:
            await con.execute("SAVEPOINT cloudbot_lastrowid")
        except Exception:  # noqa: BLE001 - defensive: no savepoint support
            return 0
        try:
            value = int(await con.fetchval("SELECT lastval()"))
        except Exception:  # noqa: BLE001 - no sequence used: lastrowid is 0
            try:
                await con.execute("ROLLBACK TO SAVEPOINT cloudbot_lastrowid")
            except Exception:  # noqa: BLE001
                pass
            value = 0
        try:
            await con.execute("RELEASE SAVEPOINT cloudbot_lastrowid")
        except Exception:  # noqa: BLE001
            pass
        return value

    async def _primary_key(self, table: str) -> list[str]:
        sql = (
            "SELECT a.attname FROM pg_index i "
            "CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
            "WHERE i.indrelid = to_regclass($1) AND i.indisprimary "
            "ORDER BY k.ord"
        )
        async def run(con: Any) -> list[str]:
            return [str(row[0]) for row in await con.fetch(sql, table)]

        return await self._with_connection(run)


def _coerce_params(params: Iterable[Any]) -> tuple[Any, ...]:
    """Match SQLite's parameter typing: ``bool`` is an integer there, not a bool.

    Every boolean in this schema is stored as INTEGER 0/1, and asyncpg refuses a
    Python bool for an INTEGER column, so the facade keeps SQLite's typing.
    """
    return tuple(1 if isinstance(value, bool) else value for value in params)
