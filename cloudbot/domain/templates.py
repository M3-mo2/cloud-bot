"""Starter project templates for the Python profile.

A template is **static data**: the exact files a person would start a small
Python project with, written into a workspace without typing anything. It lives
in the domain layer so the catalog is not tied to a screen, and so a test can
walk it without rendering anything.

Rules this catalog keeps (TASK python-templates):

* Python only, and only the **standard library** — except where the whole point
  of the template is a dependency (``flask-api`` imports flask, ``async-fetch``
  imports httpx). A template that needs a package says so in its own README;
  nothing here installs anything.
* Every file is **runnable as written**: no import of a module the template does
  not create, no placeholder that raises, no ``../`` or absolute file path.
* Every template ships a short ``README.md``: what it is, how to run it.
* Paths are relative to the workspace root and always stay inside it.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "TEMPLATES",
    "Template",
    "TemplateFile",
    "by_key",
    "keys",
    "paths",
    "template_path",
]

#: The profile a template applies to. The engine's ``/v1/meta`` default profile.
TEMPLATE_PROFILE = "python"


@dataclass(frozen=True)
class TemplateFile:
    """One file a template writes, ``path`` relative to the workspace root."""

    path: str
    content: str


@dataclass(frozen=True)
class Template:
    """A starter project.

    ``title_key`` / ``detail_key`` / ``notes_key`` are catalog keys — the
    presentation layer owns every human word, so no prose lives here.
    """

    key: str
    title_key: str
    detail_key: str
    files: tuple[TemplateFile, ...]
    notes_key: str

    def paths(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files)


def template_path(template: Template, file: TemplateFile) -> str:
    """The workspace-absolute path for ``file`` (root is ``/``).

    A template's paths are relative; this is the one place the leading ``/`` is
    added, so the screen and the write path cannot disagree.
    """
    return "/" + file.path


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
            """# hello

One Python script that prints a greeting.

Run it with:

```
python3 hello.py
```
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# script-with-args — argparse, a real entry point.
# ---------------------------------------------------------------------------
_SCRIPT_WITH_ARGS = Template(
    key="script-with-args",
    title_key="tpl.script_with_args.title",
    detail_key="tpl.script_with_args.detail",
    notes_key="tpl.script_with_args.notes",
    files=(
        _f(
            "greet.py",
            '''"""A small command-line tool built with argparse."""

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Greet someone by name.")
    parser.add_argument("name", help="who to greet")
    parser.add_argument(
        "--times",
        type=int,
        default=1,
        help="how many times to repeat the greeting (default: 1)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    for _ in range(max(1, args.times)):
        print(f"Hello, {args.name}!")


if __name__ == "__main__":
    main()
''',
        ),
        _f(
            "README.md",
            """# script-with-args

A command-line script that takes arguments with `argparse`.

Run it with:

```
python3 greet.py World
python3 greet.py World --times 3
```
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# module — a package, one module, one test, a README.
# ---------------------------------------------------------------------------
_MODULE = Template(
    key="module",
    title_key="tpl.module.title",
    detail_key="tpl.module.detail",
    notes_key="tpl.module.notes",
    files=(
        _f(
            "mypkg/__init__.py",
            '''"""A tiny package you can grow from."""

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
            """# module

A small Python package (`mypkg`) with one module and one test.

Run the test with:

```
python3 -m unittest discover -s tests
```
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# flask-api — one file, two routes, no database.  (needs flask)
# ---------------------------------------------------------------------------
_FLASK_API = Template(
    key="flask-api",
    title_key="tpl.flask_api.title",
    detail_key="tpl.flask_api.detail",
    notes_key="tpl.flask_api.notes",
    files=(
        _f(
            "app.py",
            '''"""A two-route JSON API.

This template needs Flask. Install it first (the bot does not install anything
for you):

    pip install flask
"""

from flask import Flask, jsonify

app = Flask(__name__)


@app.get("/")
def index():
    return jsonify({"message": "hello", "status": "ok"})


@app.get("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
''',
        ),
        _f(
            "README.md",
            """# flask-api

A single-file Flask app with two JSON routes: `/` and `/health`.

This needs the **flask** package; install it yourself with `pip install flask`.

Run it with:

```
python3 app.py
```

Then open `http://localhost:5000/`.
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# cli-typer-or-argparse-app — argparse based CLI (kept deliberately dependency-free; the
# `cli-typer-or-argparse-app` slot in the task, using argparse so it runs as
# written without installing typer).
# ---------------------------------------------------------------------------
_CLI_APP = Template(
    key="cli-typer-or-argparse-app",
    title_key="tpl.cli_app.title",
    detail_key="tpl.cli_app.detail",
    notes_key="tpl.cli_app.notes",
    files=(
        _f(
            "cli.py",
            '''"""A small multi-command CLI, standard library only.

Uses argparse subcommands so it runs anywhere Python 3 is installed. If you
prefer typer's look, `pip install typer` and swap the parser in `main`.
"""

import argparse


def cmd_greet(args: argparse.Namespace) -> int:
    print(f"Hello, {args.name}!")
    return 0


def cmd_sum(args: argparse.Namespace) -> int:
    print(sum(args.numbers))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli", description="A tiny CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    greet = sub.add_parser("greet", help="greet someone")
    greet.add_argument("name")
    greet.set_defaults(func=cmd_greet)

    total = sub.add_parser("sum", help="add a list of numbers")
    total.add_argument("numbers", type=int, nargs="+")
    total.set_defaults(func=cmd_sum)
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
            """# cli-app

A small multi-command CLI using `argparse` (standard library only).

Run it with:

```
python3 cli.py greet World
python3 cli.py sum 1 2 3
```
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
    files=(
        _f(
            "sales.csv",
            "region,amount\nnorth,120\nsouth,80\nnorth,60\neast,50\nsouth,40\n",
        ),
        _f(
            "summarize.py",
            '''"""Read a CSV, group by a column, and print the totals."""

import csv
from collections import defaultdict


def totals_by(path: str, column: str) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            totals[row[column]] += float(row["amount"])
    return dict(totals)


def main() -> None:
    totals = totals_by("sales.csv", "region")
    grand_total = sum(totals.values())
    for region, amount in sorted(totals.items()):
        print(f"{region:>8}: {amount:8.2f}")
    print(f"{'total':>8}: {grand_total:8.2f}")


if __name__ == "__main__":
    main()
''',
        ),
        _f(
            "README.md",
            """# data-csv

Reads `sales.csv`, groups the amounts by region, and prints the totals.

Run it with:

```
python3 summarize.py
```
""",
        ),
    ),
)

# ---------------------------------------------------------------------------
# async-fetch — httpx, one request, timeout and error handled.  (needs httpx)
# ---------------------------------------------------------------------------
_ASYNC_FETCH = Template(
    key="async-fetch",
    title_key="tpl.async_fetch.title",
    detail_key="tpl.async_fetch.detail",
    notes_key="tpl.async_fetch.notes",
    files=(
        _f(
            "fetch.py",
            '''"""Fetch one URL with httpx, with a timeout and honest errors.

This template needs httpx. Install it first (the bot does not install anything
for you):

    pip install httpx
"""

import asyncio
import sys

import httpx


async def fetch(url: str) -> str:
    """GET one URL and return the body, or explain what went wrong."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
    except httpx.TimeoutException:
        return "The request timed out."
    except httpx.HTTPStatusError as exc:
        return f"The server answered {exc.response.status_code}."
    except httpx.HTTPError as exc:
        return f"The request failed: {exc}"


async def main(url: str) -> int:
    body = await fetch(url)
    print(body[:2000])
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    raise SystemExit(asyncio.run(main(target)))
''',
        ),
        _f(
            "README.md",
            """# async-fetch

Fetches one URL with `httpx`, with a 10-second timeout and clear error messages.

This needs the **httpx** package; install it yourself with `pip install httpx`.

Run it with:

```
python3 fetch.py https://example.com
```
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
            "tests/test_calculator.py",
            '''"""One passing test to start from: run it with `pytest`."""

from src.calculator import multiply


def test_multiply_positive_numbers() -> None:
    assert multiply(3, 4) == 12


def test_multiply_by_zero() -> None:
    assert multiply(7, 0) == 0
''',
        ),
        _f(
            "pytest.ini",
            "[pytest]\npythonpath = .\ntestpaths = tests\n",
        ),
        _f(
            "README.md",
            """# pytest-suite

The layout to copy when you want tests from the first minute: `src/` holds the
code, `tests/` holds the tests, and `pytest.ini` makes imports work from the
project root.

This needs the **pytest** package; install it yourself with `pip install pytest`.

Run the tests with:

```
pytest
```
""",
        ),
    ),
)


#: Display order is the catalog order — simple to more complete.
TEMPLATES: tuple[Template, ...] = (
    _HELLO,
    _SCRIPT_WITH_ARGS,
    _MODULE,
    _FLASK_API,
    _CLI_APP,
    _DATA_CSV,
    _ASYNC_FETCH,
    _PYTEST_SUITE,
)

_BY_KEY: dict[str, Template] = {t.key: t for t in TEMPLATES}


def keys() -> tuple[str, ...]:
    return tuple(t.key for t in TEMPLATES)


def by_key(key: str) -> Template | None:
    """The template named ``key``, or ``None`` for an unknown/foreign value."""
    return _BY_KEY.get(key)


def paths(template: Template) -> tuple[str, ...]:
    """Every workspace-absolute path ``template`` will write, in order."""
    return tuple(template_path(template, f) for f in template.files)
