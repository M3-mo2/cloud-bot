"""Starter project templates for the Python profile.

A template is **static data**: the exact files a person would start a small
Python project with, written into a workspace without typing anything. It lives
in the domain layer so the catalog is not tied to a screen, and so a test can
walk it without rendering anything.

Rules this catalog keeps (TASK python-templates, TASK template-folders, TASK
template-catalog-v2):

* Python only, and only the **standard library** — except three templates whose
  entire point is a package: ``flask-api`` imports flask, ``async-fetch`` imports
  httpx, and ``pytest-suite`` needs pytest. Each of those says so in its own
  README, and the bot's missing-package button covers the install. Every other
  template runs on a bare Python.
* Every file is **runnable as written**: no import of a module the template does
  not create, no placeholder that raises, no ``../`` or absolute file path.
* Every template ships a short ``README.md``: what it is, how to run it, what it
  needs.
* Each template writes into **its own folder**, named after its ``key``, so two
  different starters can live in one workspace side by side. The declared
  ``TemplateFile.path`` values stay relative to that folder; one function
  (:func:`template_relative_path`) composes the final path.
* A ``key`` is ``[a-z][a-z0-9-]*`` — it names a directory, so a key that could be
  a path (a slash, ``..``, an absolute path) is refused.
* ``icon`` is the one-line mark the list shows. It is an emoji carried by the
  template itself (TASK template-catalog-v2): the value is catalog data, chosen
  once for every locale, so the domain owns it and the screen only prints it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "GROUPS",
    "TEMPLATES",
    "Template",
    "TemplateFile",
    "by_key",
    "keys",
    "paths",
    "relative_paths",
    "template_path",
    "template_relative_path",
    "template_root",
]

#: The three groups the list screen draws, in display order. A group is
#: template data (``Template.group``), not a mapping invented by the screen:
#: adding a template means choosing one of these, and the list cannot silently
#: lose it. The headings are catalog keys (``tpl.group.<group>``), so the domain
#: carries only the locale-independent name.
GROUPS: tuple[str, ...] = ("starts", "projects", "web")

#: A key is a single lowercase path segment; it becomes a directory name.
_KEY_RE = re.compile(r"[a-z][a-z0-9-]*\Z")

#: The profile a template applies to. The engine's ``/v1/meta`` default profile.
TEMPLATE_PROFILE = "python"


@dataclass(frozen=True)
class TemplateFile:
    """One file a template writes, ``path`` relative to the template's folder."""

    path: str
    content: str


@dataclass(frozen=True)
class Template:
    """A starter project.

    ``title_key`` / ``detail_key`` / ``notes_key`` are catalog keys — the
    presentation layer owns every human word, so no prose lives here. ``icon`` is
    the one exception: it is a locale-independent emoji, carried by the template
    so the list can print its mark without a second catalog lookup.

    ``group`` is required and one of :data:`GROUPS`. It is the list's section, so
    a template added without one is a construction error and a test failure —
    never a card that quietly disappears from the screen. ``short_key`` is
    derived from ``key`` (``tpl.<key>.short``) so the one-line description a
    picker needs cannot be spelled differently in a second place.
    """

    key: str
    title_key: str
    detail_key: str
    notes_key: str
    icon: str
    group: str
    files: tuple[TemplateFile, ...]

    @property
    def short_key(self) -> str:
        """The one-line description the list screen prints (``tpl.<key>.short``).

        A template key is a folder name (``script-with-args``) while a catalog key
        is underscored (``tpl.script_with_args.detail``), so the same rule that
        names every other template key maps ``-`` to ``_`` here. Deriving it from
        ``key`` keeps one spelling: a template cannot carry a short line under a
        second name the screen would never read.
        """
        return f"tpl.{self.key.replace('-', '_')}.short"

    def paths(self) -> tuple[str, ...]:
        """Every workspace-absolute path this template writes, in order."""
        return tuple(template_path(self, f) for f in self.files)

    def relative_paths(self) -> tuple[str, ...]:
        """Every path as the user sees it on disk (``hello/hello.py``)."""
        return tuple(template_relative_path(self, f) for f in self.files)


def template_root(key: str) -> str:
    """The folder a template owns: its ``key``, validated as one path segment.

    The key names a directory, so a value that is not ``[a-z][a-z0-9-]*`` — a
    slash, a leading dot, an absolute path, ``..`` — is refused here rather than
    becoming a path the engine would have to reject later.
    """
    if not _KEY_RE.match(key):
        raise ValueError(f"invalid template key: {key!r}")
    return key


def template_relative_path(template: Template, file: TemplateFile) -> str:
    """The path a user sees on disk: the template folder plus the file path.

    This is the one place the folder prefix is composed, so the catalog keeps
    readable template-relative paths and the screen and the write cannot
    disagree.
    """
    root = template_root(template.key)
    path = file.path
    if not path or path.startswith("/"):
        raise ValueError(f"invalid template file path: {path!r} in {template.key!r}")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"invalid template file path: {path!r} in {template.key!r}")
    return f"{root}/{path}"


def template_path(template: Template, file: TemplateFile) -> str:
    """The workspace-absolute path for ``file`` (root is ``/``).

    Composed from :func:`template_relative_path`, so the folder rule is owned by
    exactly one function; the leading ``/`` is added only here.
    """
    return "/" + template_relative_path(template, file)


def _f(path: str, content: str) -> TemplateFile:
    return TemplateFile(path=path, content=content)


# ---------------------------------------------------------------------------
# hello — one script, one output line.
# ---------------------------------------------------------------------------
_HELLO = Template(
    key="hello",
    title_key="tpl.hello.title",
    detail_key="tpl.hello.detail",
    notes_key="tpl.hello.notes",
    icon="📄",
    group="starts",
    files=(
        _f(
            "hello.py",
            '''"""A one-file Python script: prints a greeting and exits."""


def main() -> None:
    print("Hello from your new workspace!")


if __name__ == "__main__":
    main()
''',
        ),
        _f(
            "README.md",
            """
# hello

One Python script that prints a greeting.
Run it with `python3 hello.py`.
Needs nothing beyond Python.
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# script-with-args — argparse, a real entry point and a real exit code.
# ---------------------------------------------------------------------------
_SCRIPT_WITH_ARGS = Template(
    key="script-with-args",
    title_key="tpl.script_with_args.title",
    detail_key="tpl.script_with_args.detail",
    notes_key="tpl.script_with_args.notes",
    icon="📝",
    group="starts",
    files=(
        _f(
            "greet.py",
            '''"""A small command-line tool built with argparse."""

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Greet someone by name.")
    parser.add_argument("name", help="who to greet")
    parser.add_argument(
        "--times",
        type=int,
        default=1,
        help="how many times to repeat the greeting (default: 1)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print what the tool is doing to stderr",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.verbose:
        print(f"greeting {args.name!r} {args.times} time(s)", file=sys.stderr)
    for _ in range(max(1, args.times)):
        print(f"Hello, {args.name}!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
        ),
        _f(
            "README.md",
            """
# script-with-args

A command-line script with `argparse`, `--verbose` and a real exit code.
Run it with `python3 greet.py World --times 3 --verbose`.
Needs nothing beyond Python.
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# module — a package you can run as a module.
# ---------------------------------------------------------------------------
_MODULE = Template(
    key="module",
    title_key="tpl.module.title",
    detail_key="tpl.module.detail",
    notes_key="tpl.module.notes",
    icon="🧩",
    group="starts",
    files=(
        _f(
            "mypkg/__init__.py",
            '''"""A tiny package you can grow from.

The metadata this package would carry in a pyproject.toml:

    [project]
    name = "mypkg"
    version = "0.1.0"
    description = "A tiny package you can grow from"
    requires-python = ">=3.12"
"""

from .mathx import add

__all__ = ["add"]
''',
        ),
        _f(
            "mypkg/mathx.py",
            '''"""One small, testable module."""


def add(left: int, right: int) -> int:
    """Return the sum of two integers."""
    return left + right
''',
        ),
        _f(
            "mypkg/__main__.py",
            '''"""Run the package with `python3 -m mypkg`."""

from .mathx import add


def main() -> int:
    print(f"2 + 3 = {add(2, 3)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
        ),
        _f(
            "tests/test_mathx.py",
            '''"""A first test: run it with `python3 -m unittest discover -s tests`."""

import unittest

from mypkg.mathx import add


class AddTests(unittest.TestCase):
    def test_adds_two_numbers(self) -> None:
        self.assertEqual(add(2, 3), 5)

    def test_adds_negatives(self) -> None:
        self.assertEqual(add(-4, 1), -3)


if __name__ == "__main__":
    unittest.main()
''',
        ),
        _f(
            "README.md",
            """
# module

A small package `mypkg` with a test and pyproject-shaped metadata.
Run it with `python3 -m mypkg`, and test it with `python3 -m unittest discover -s tests`.
Needs nothing beyond Python.
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# pytest-suite — the layout a person copies to start testing.
# ---------------------------------------------------------------------------
_PYTEST_SUITE = Template(
    key="pytest-suite",
    title_key="tpl.pytest_suite.title",
    detail_key="tpl.pytest_suite.detail",
    notes_key="tpl.pytest_suite.notes",
    icon="🧪",
    group="projects",
    files=(
        _f("src/__init__.py", ""),
        _f(
            "src/calculator.py",
            '''"""The code under test."""


def multiply(left: int, right: int) -> int:
    """Return the product of two integers."""
    return left * right
''',
        ),
        _f(
            "conftest.py",
            '''"""Shared fixtures for this test suite."""

import pytest


@pytest.fixture
def small_numbers() -> tuple[int, int]:
    return 3, 4
''',
        ),
        _f(
            "tests/test_calculator.py",
            '''"""Tests to start from: run them with `pytest`.

One plain test, one fixture test and one parametrised test: the three shapes a
suite uses most.
"""

import pytest

from src.calculator import multiply


def test_multiply_positive_numbers() -> None:
    assert multiply(3, 4) == 12


def test_multiply_by_zero() -> None:
    assert multiply(7, 0) == 0


def test_multiply_with_the_fixture(small_numbers: tuple[int, int]) -> None:
    left, right = small_numbers
    assert multiply(left, right) == 12


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [(2, 3, 6), (-2, 5, -10), (0, 9, 0)],
)
def test_multiply_with_parameters(left: int, right: int, expected: int) -> None:
    assert multiply(left, right) == expected
''',
        ),
        _f(
            "pytest.ini",
            "[pytest]\npythonpath = .\ntestpaths = tests\n",
        ),
        _f(
            "README.md",
            """
# pytest-suite

The layout to copy for tests from the first minute: `src/`, `tests/`, `conftest.py` fixtures.
Run it with `pytest`.
Needs the **pytest** package; install it yourself with `pip install pytest`.
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# data-csv — read a CSV, aggregate, print.
# ---------------------------------------------------------------------------
_DATA_CSV = Template(
    key="data-csv",
    title_key="tpl.data_csv.title",
    detail_key="tpl.data_csv.detail",
    notes_key="tpl.data_csv.notes",
    icon="📈",
    group="projects",
    files=(
        _f(
            "sales.csv",
            "region,amount\nnorth,120\nsouth,80\nnorth,60\neast,50\nsouth,40\n",
        ),
        _f(
            "summarize.py",
            '''"""Read a CSV, group by one column, and print the totals."""

import argparse
import csv
import sys
from collections import defaultdict


def totals_by(path: str, column: str, amount_column: str = "amount") -> dict[str, float]:
    """Sum ``amount_column`` for every value in ``column``."""
    totals: dict[str, float] = defaultdict(float)
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if column not in row or amount_column not in row:
                raise KeyError(
                    f"the CSV needs the columns {column!r} and {amount_column!r}"
                )
            totals[row[column]] += float(row[amount_column])
    return dict(totals)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Group a CSV by one column and print the totals."
    )
    parser.add_argument(
        "path", nargs="?", default="sales.csv", help="the CSV file (default: sales.csv)"
    )
    parser.add_argument(
        "--column", default="region", help="the column to group by (default: region)"
    )
    parser.add_argument(
        "--amount", default="amount", help="the numeric column to sum (default: amount)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        totals = totals_by(args.path, args.column, args.amount)
    except FileNotFoundError:
        print(f"No such file: {args.path}", file=sys.stderr)
        return 2
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    grand_total = sum(totals.values())
    for label, amount in sorted(totals.items()):
        print(f"{label:>8}: {amount:8.2f}")
    print(f"{'total':>8}: {grand_total:8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
        ),
        _f(
            "README.md",
            """
# data-csv

Reads a CSV, groups the amounts by `--column` and prints the totals.
Run it with `python3 summarize.py` or `python3 summarize.py sales.csv --column region`.
Needs nothing beyond Python.
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# flask-api — one file, two routes, a JSON 404.  (needs flask)
# ---------------------------------------------------------------------------
_FLASK_API = Template(
    key="flask-api",
    title_key="tpl.flask_api.title",
    detail_key="tpl.flask_api.detail",
    notes_key="tpl.flask_api.notes",
    icon="🌐",
    group="web",
    files=(
        _f(
            "app.py",
            '''"""A two-route JSON API with an honest 404 and a --port flag.

This template needs Flask. Install it first (the bot does not install anything
for you):

    pip install flask
"""

import argparse

from flask import Flask, jsonify

app = Flask(__name__)


@app.get("/")
def index():
    return jsonify({"message": "hello", "status": "ok"})


@app.get("/health")
def health():
    return jsonify({"status": "healthy"})


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "not_found", "status": 404}), 404


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the JSON API.")
    parser.add_argument(
        "--port", type=int, default=5000, help="port to listen on (default: 5000)"
    )
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    app.run(host="0.0.0.0", port=args.port)
''',
        ),
        _f(
            "README.md",
            """
# flask-api

A single-file Flask app with two JSON routes, a JSON 404 and a `--port` flag.
Run it with `python3 app.py` (or `python3 app.py --port 8000`), then open `http://localhost:5000/`.
Needs the **flask** package; install it yourself with `pip install flask`.
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# cli-app — argparse subcommands that store data in a JSON file.
# ---------------------------------------------------------------------------
_CLI_APP = Template(
    key="cli-app",
    title_key="tpl.cli_app.title",
    detail_key="tpl.cli_app.detail",
    notes_key="tpl.cli_app.notes",
    icon="🧰",
    group="projects",
    files=(
        _f(
            "cli.py",
            '''"""A two-command CLI that stores notes in a JSON file, standard library only."""

import argparse
import json

DEFAULT_FILE = "items.json"


def load_items(path: str) -> list[str]:
    """Read the stored notes; a missing file is an empty list, not an error."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return []
    if not isinstance(data, list):
        raise ValueError(f"{path} does not contain a JSON list")
    return [str(item) for item in data]


def save_items(path: str, items: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(items, handle, indent=2)
        handle.write("\\n")


def cmd_add(args: argparse.Namespace) -> int:
    items = load_items(args.file)
    items.append(args.text)
    save_items(args.file, items)
    print(f"added: {args.text}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    items = load_items(args.file)
    if not items:
        print("no items yet")
        return 0
    for index, item in enumerate(items, start=1):
        print(f"{index:>3}. {item}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli", description="Store and list short notes.")
    parser.add_argument(
        "--file", default=DEFAULT_FILE, help="where the notes live (default: items.json)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="append one note")
    add.add_argument("text")
    add.set_defaults(func=cmd_add)

    listing = sub.add_parser("list", help="print every note")
    listing.set_defaults(func=cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
''',
        ),
        _f(
            "README.md",
            """
# cli-app

Two `argparse` subcommands (`add` and `list`) keeping their notes in a JSON file.
Run it with `python3 cli.py add "buy milk"` and `python3 cli.py list`.
Needs nothing beyond Python.
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# async-fetch — httpx, a timeout, one retry, honest errors.  (needs httpx)
# ---------------------------------------------------------------------------
_ASYNC_FETCH = Template(
    key="async-fetch",
    title_key="tpl.async_fetch.title",
    detail_key="tpl.async_fetch.detail",
    notes_key="tpl.async_fetch.notes",
    icon="🛰️",
    group="web",
    files=(
        _f(
            "fetch.py",
            '''"""Fetch one URL with httpx: a timeout, one retry, an honest message.

This template needs httpx. Install it first (the bot does not install anything
for you):

    pip install httpx
"""

import argparse
import asyncio

import httpx


async def fetch_once(url: str, timeout: float) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.get(url)


async def fetch(url: str, timeout: float = 10.0) -> tuple[str | None, str | None]:
    """Return ``(body, error)``: exactly one of the two is None.

    A timeout or a connection error is retried **once**; an HTTP status error is
    explained with its code and never retried, because the server answered.
    """
    last_error = ""
    for _attempt in range(2):
        try:
            response = await fetch_once(url, timeout)
        except httpx.TimeoutException:
            last_error = f"the request timed out after {timeout:g}s"
        except httpx.ConnectError:
            last_error = "could not connect (check the URL and the network)"
        except httpx.HTTPError as exc:
            last_error = f"the request failed: {exc}"
        else:
            if response.status_code >= 400:
                return None, f"the server answered {response.status_code}"
            return response.text, None
    return None, last_error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch one URL and print its body.")
    parser.add_argument(
        "url", nargs="?", default="https://example.com", help="the URL to fetch"
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="seconds per attempt (default: 10)"
    )
    return parser


async def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    body, error = await fetch(args.url, args.timeout)
    if error is not None:
        print(f"error: {error}")
        return 1
    assert body is not None
    print(body[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
''',
        ),
        _f(
            "README.md",
            """
# async-fetch

Fetches one URL with `httpx`, with `--timeout`, one retry and a clear message per failure.
Run it with `python3 fetch.py https://example.com`.
Needs the **httpx** package; install it yourself with `pip install httpx`.
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# telegram-bot — a working bot with urllib only, no dependency at all.
# ---------------------------------------------------------------------------
_TELEGRAM_BOT = Template(
    key="telegram-bot",
    title_key="tpl.telegram_bot.title",
    detail_key="tpl.telegram_bot.detail",
    notes_key="tpl.telegram_bot.notes",
    icon="🤖",
    group="web",
    files=(
        _f(
            "bot.py",
            '''"""A working Telegram bot using only the Python standard library.

The Bot API is plain HTTPS, so `urllib.request` is enough: no pip install, no
framework, nothing to learn before the bot answers its first message.

Set TELEGRAM_BOT_TOKEN in the environment (see .env.example), then run:

    python3 bot.py
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"
LONG_POLL_SECONDS = 25
START_REPLY = (
    "Hello! This bot is a template. Send /start again, or read bot.py to see how "
    "the long-poll loop works."
)


def api(method: str, token: str, **params):
    """Call one Bot API method and return its ``result``."""
    data = json.dumps(params).encode("utf-8")
    request = urllib.request.Request(
        API.format(token=token, method=method),
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=LONG_POLL_SECONDS + 10) as response:
        payload = json.load(response)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", "Telegram refused the call"))
    return payload.get("result")


def send(token: str, chat_id: int, text: str) -> None:
    """The one place a message goes out."""
    api("sendMessage", token, chat_id=chat_id, text=text)


def handle(token: str, update: dict) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if chat_id is None:
        return
    if text.startswith("/start"):
        send(token, chat_id, START_REPLY)


def run(token: str) -> int:
    offset = 0
    print("bot is polling; press Ctrl+C to stop", flush=True)
    while True:
        try:
            updates = api("getUpdates", token, offset=offset, timeout=LONG_POLL_SECONDS)
        except urllib.error.HTTPError as exc:
            print(f"Telegram answered {exc.code}; retrying in 3s", file=sys.stderr)
            time.sleep(3)
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"network error: {exc}; retrying in 3s", file=sys.stderr)
            time.sleep(3)
            continue
        for update in updates or []:
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            handle(token, update)


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print(
            "No token: set TELEGRAM_BOT_TOKEN in the environment before running "
            "this bot (see .env.example).",
            file=sys.stderr,
        )
        return 2
    try:
        return run(token)
    except KeyboardInterrupt:
        print("stopped", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
        ),
        _f(
            ".env.example",
            "# The token BotFather gives you. Never commit a real token.\n"
            "TELEGRAM_BOT_TOKEN=123456789:replace-this-with-your-token\n",
        ),
        _f(
            "README.md",
            """
# telegram-bot

A working Telegram bot: `getUpdates` long-poll, `/start` replies and a small `send()` helper, `urllib` only.
Run it with `TELEGRAM_BOT_TOKEN=... python3 bot.py` after copying the token from BotFather (`.env.example` shows the name).
Needs a bot token in `TELEGRAM_BOT_TOKEN`, and nothing else.
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# web-page — a static site served by the standard library.
# ---------------------------------------------------------------------------
_HTML_INDEX = '''<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>A small web page</title>
    <link rel="stylesheet" href="style.css" />
  </head>
  <body>
    <main>
      <h1>A small web page</h1>
      <p>This page is served by <code>server.py</code>, using Python's own HTTP server.</p>
      <button id="count">Clicked 0 times</button>
    </main>
    <script src="app.js"></script>
  </body>
</html>
'''

_CSS_STYLE = '''body {
  margin: 0;
  font-family: system-ui, sans-serif;
  background: #f6f7f9;
  color: #1c1e21;
}

main {
  max-width: 40rem;
  margin: 4rem auto;
  padding: 0 1.5rem;
}

h1 {
  font-size: 2rem;
}

button {
  padding: 0.6rem 1rem;
  border: 0;
  border-radius: 0.5rem;
  background: #2563eb;
  color: white;
  font-size: 1rem;
  cursor: pointer;
}
'''

_JS_APP = '''const button = document.getElementById("count");
let clicks = 0;

button.addEventListener("click", () => {
  clicks += 1;
  button.textContent = `Clicked ${clicks} time${clicks === 1 ? "" : "s"}`;
});
'''

_WEB_PAGE = Template(
    key="web-page",
    title_key="tpl.web_page.title",
    detail_key="tpl.web_page.detail",
    notes_key="tpl.web_page.notes",
    icon="🖥️",
    group="web",
    files=(
        _f(
            "server.py",
            '''"""Serve the static site in public/ using only the standard library."""

import argparse
import functools
import http.server
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "public"


class SiteHandler(http.server.SimpleHTTPRequestHandler):
    """A quiet handler: the default one prints a line for every request."""

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the public/ folder over HTTP.")
    parser.add_argument(
        "--port", type=int, default=8080, help="port to listen on (default: 8080)"
    )
    parser.add_argument(
        "--check", action="store_true", help="verify the site files exist and exit"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    index = ROOT / "index.html"
    if not index.is_file():
        print(f"missing site file: {index}", file=sys.stderr)
        return 2
    if args.check:
        files = sorted(p.name for p in ROOT.iterdir() if p.is_file())
        print(f"site is ready in {ROOT}: {', '.join(files)}")
        return 0
    handler = functools.partial(SiteHandler, directory=str(ROOT))
    with http.server.ThreadingHTTPServer(("0.0.0.0", args.port), handler) as httpd:
        print(f"serving {ROOT} at http://localhost:{args.port}/", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
        ),
        _f("public/index.html", _HTML_INDEX),
        _f("public/style.css", _CSS_STYLE),
        _f("public/app.js", _JS_APP),
        _f(
            "README.md",
            """
# web-page

A static site (`public/index.html`, `style.css`, `app.js`) served by Python's `http.server`.
Run it with `python3 server.py --port 8080` and open `http://localhost:8080/`; `--check` only verifies the files.
Needs nothing beyond Python.
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# telegram-checker — check whether usernames exist, stdlib only.
# ---------------------------------------------------------------------------
_TELEGRAM_CHECKER = Template(
    key="telegram-checker",
    title_key="tpl.telegram_checker.title",
    detail_key="tpl.telegram_checker.detail",
    notes_key="tpl.telegram_checker.notes",
    icon="🔎",
    group="web",
    files=(
        _f(
            "check.py",
            '''"""Check whether Telegram usernames are taken, using only the standard library.

Telegram answers a username URL with 200 (taken) or 404 (free). A small delay
between requests matters: a burst is what earns a 429 and a temporary ban.
"""

import argparse
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "https://t.me/"

#: Seconds between requests. Telegram 429s a burst, so this pause is not optional.
DELAY_SECONDS = 1.0
TIMEOUT_SECONDS = 10.0

_TITLE = re.compile(
    r\'<[^>]*class="tgme_page_title"[^>]*>(.*?)</\', re.IGNORECASE | re.DOTALL
)
_TAGS = re.compile(r"<[^>]+>")


def page_title(html: str) -> str | None:
    """The name Telegram prints on a profile page, or None when it is absent."""
    match = _TITLE.search(html or "")
    if not match:
        return None
    text = _TAGS.sub("", match.group(1)).strip()
    return text or None


def classify(status: int, html: str) -> str:
    """Map one response to ``exists``, ``available`` or ``unknown``.

    Pure on purpose: the parser is the part worth testing, and a fixture page
    exercises it without a single network call.
    """
    if status == 404:
        return "available"
    if status == 200 and page_title(html):
        return "exists"
    return "unknown"


def fetch(name: str) -> tuple[int, str]:
    request = urllib.request.Request(
        BASE + name, headers={"User-Agent": "telegram-checker/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, ""


def check(names: list[str]) -> list[tuple[str, str, str | None]]:
    rows: list[tuple[str, str, str | None]] = []
    for index, name in enumerate(names):
        if index:
            # Rate limit: Telegram 429s a burst of requests from one client.
            time.sleep(DELAY_SECONDS)
        status, html = fetch(name)
        rows.append((name, classify(status, html), page_title(html)))
    return rows


def read_names(args: argparse.Namespace) -> list[str]:
    if args.names:
        return args.names
    try:
        with open(args.file, encoding="utf-8") as handle:
            return [
                line.strip()
                for line in handle
                if line.strip() and not line.startswith("#")
            ]
    except FileNotFoundError:
        print(f"No such file: {args.file}", file=sys.stderr)
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Telegram usernames.")
    parser.add_argument("names", nargs="*", help="usernames to check (without @)")
    parser.add_argument(
        "--file", default="usernames.txt", help="a file with one username per line"
    )
    args = parser.parse_args(argv)
    names = read_names(args)
    if not names:
        print("no usernames to check", file=sys.stderr)
        return 2
    print(f"{'username':<24} {'status':<10} title")
    for name, label, title in check(names):
        print(f"{'@' + name:<24} {label:<10} {title or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
        ),
        _f(
            "usernames.txt",
            "# One username per line, without the @.\n"
            "# Placeholders only: replace them with the names you want to check.\n"
            "durov\n"
            "telegram\n",
        ),
        _f(
            "README.md",
            """
# telegram-checker

Checks whether Telegram usernames are taken by reading `https://t.me/<name>`, one second apart.
Run it with `python3 check.py durov telegram`, or put the names in `usernames.txt` and run `python3 check.py`.
Needs nothing beyond Python (do not burst it; Telegram 429s).
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# http-api-client — talk to a JSON API properly, stdlib only.
# ---------------------------------------------------------------------------
_HTTP_API_CLIENT = Template(
    key="http-api-client",
    title_key="tpl.http_api_client.title",
    detail_key="tpl.http_api_client.detail",
    notes_key="tpl.http_api_client.notes",
    icon="📡",
    group="web",
    files=(
        _f(
            "client.py",
            '''"""Call a JSON API and print its status and a few useful fields.

Only the standard library is used, so there is nothing to install:

    python3 client.py --url https://api.github.com/repos/python/cpython
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 15.0
STATUS_LINES = 6


def parse_json(text: str):
    """Parse a JSON body or raise ValueError with a readable reason."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"the response is not JSON: {exc.msg}") from exc


def default_fields(payload: dict, limit: int = STATUS_LINES) -> list[str]:
    """The first few scalar top-level fields, in the order the API sent them."""
    keys = [
        key
        for key, value in payload.items()
        if isinstance(value, (str, int, float, bool))
    ]
    return keys[:limit]


def pick_fields(payload: dict, fields: list[str]) -> list[tuple[str, object]]:
    """The requested fields that exist, as ``(name, value)`` pairs."""
    return [(field, payload[field]) for field in fields if field in payload]


def fetch(url: str) -> tuple[int, str]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "http-api-client/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"network error: {exc}", file=sys.stderr)
        return 0, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call a JSON API.")
    parser.add_argument("--url", required=True, help="the URL to GET")
    parser.add_argument(
        "--fields", default="", help="comma-separated top-level fields to print"
    )
    args = parser.parse_args(argv)

    status, body = fetch(args.url)
    if status == 0:
        return 1
    print(f"status: {status}")
    if status >= 400:
        print("body:")
        print(body[:2000])
        return 1
    try:
        payload = parse_json(body)
    except ValueError as exc:
        print(f"error: {exc}")
        print(body[:2000])
        return 1
    if not isinstance(payload, dict):
        print(json.dumps(payload, indent=2)[:2000])
        return 0
    fields = [field.strip() for field in args.fields.split(",") if field.strip()]
    for key, value in pick_fields(payload, fields or default_fields(payload)):
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
        ),
        _f(
            "README.md",
            """
# http-api-client

Calls a JSON API with `urllib`, prints the status and selected fields, and the body on an error.
Run it with `python3 client.py --url https://api.github.com/repos/python/cpython` (add `--fields name,stars` to choose).
Needs nothing beyond Python.
""",
        ),
    ),
)


#: Display order is the catalog order — simple to more complete, dependency-free
#: first so a newcomer's first pick never needs pip.
TEMPLATES: tuple[Template, ...] = (
    _HELLO,
    _SCRIPT_WITH_ARGS,
    _MODULE,
    _PYTEST_SUITE,
    _DATA_CSV,
    _FLASK_API,
    _CLI_APP,
    _ASYNC_FETCH,
    _TELEGRAM_BOT,
    _WEB_PAGE,
    _TELEGRAM_CHECKER,
    _HTTP_API_CLIENT,
)

_BY_KEY: dict[str, Template] = {t.key: t for t in TEMPLATES}


def keys() -> tuple[str, ...]:
    return tuple(t.key for t in TEMPLATES)


def by_key(key: str) -> Template | None:
    """The template named ``key``, or ``None`` for an unknown/foreign value."""
    return _BY_KEY.get(key)


def paths(template: Template) -> tuple[str, ...]:
    """Every workspace-absolute path ``template`` will write, in order."""
    return template.paths()


def relative_paths(template: Template) -> tuple[str, ...]:
    """Every path as the user sees it on disk (``hello/hello.py``), in order."""
    return template.relative_paths()
