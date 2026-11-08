"""Python module name → pip package name, and the parser for import failures.

A user sees ``No module named 'PIL'``; ``pip install PIL`` does not exist. The
mapping is **knowledge, not derivation** — a wrong guess is worse than offering
no button at all — so it is a small, frozen, explicit table. Modules outside the
table return ``None`` and the screens deliberately offer no install button.

This module is pure domain: no network, no engine, no presentation. It is the
one place the two facts live:

* :data:`MODULE_TO_PIP` / :func:`pip_name_for` — the honest name mapping;
* :func:`missing_modules` — which imports a failed traceback is missing.

The callback the screens emit is ``pkg:fix:<pip-name>``. Telegram caps
``callback_data`` at 64 bytes, so the table refuses to import if any pip name
plus that prefix cannot fit.
"""

from __future__ import annotations

import re
from types import MappingProxyType

#: The callback prefix a "fix this missing package" button carries. Kept here so
#: the import-time size assertion is next to the names it constrains.
FIX_CALLBACK_PREFIX = "pkg:fix:"

#: Telegram's hard cap on ``callback_data`` (bytes, UTF-8).
_TELEGRAM_CALLBACK_MAX = 64

#: Module name (lowercase, exact Python spelling is case-insensitive) → the name
#: ``pip`` is given, verbatim. Only well-known, verified mappings: a speculative
#: entry is worse than an unknown one because it installs the wrong package.
MODULE_TO_PIP = MappingProxyType(
    {
        # -- web frameworks / servers -------------------------------------
        "flask": "flask",
        "django": "django",
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        # -- HTTP ----------------------------------------------------------
        "requests": "requests",
        "httpx": "httpx",
        "aiohttp": "aiohttp",
        "urllib3": "urllib3",
        # -- data / science ------------------------------------------------
        "pandas": "pandas",
        "numpy": "numpy",
        "scipy": "scipy",
        "matplotlib": "matplotlib",
        "sklearn": "scikit-learn",
        "pil": "pillow",
        "cv2": "opencv-python",
        "seaborn": "seaborn",
        # -- parsing / templating -----------------------------------------
        "yaml": "pyyaml",
        "bs4": "beautifulsoup4",
        "lxml": "lxml",
        "jinja2": "jinja2",
        # -- config / auth / dates ----------------------------------------
        "dotenv": "python-dotenv",
        "jwt": "pyjwt",
        "crypto": "pycryptodome",
        "dateutil": "python-dateutil",
        # -- models / storage ---------------------------------------------
        "pydantic": "pydantic",
        "sqlalchemy": "sqlalchemy",
        "redis": "redis",
        "psycopg2": "psycopg2-binary",
        "pymongo": "pymongo",
        "boto3": "boto3",
        # -- Telegram bots -------------------------------------------------
        "telebot": "pytelegrambotapi",
        "telegram": "python-telegram-bot",
        "aiogram": "aiogram",
        # -- tooling -------------------------------------------------------
        "pytest": "pytest",
        "tqdm": "tqdm",
        "rich": "rich",
        "click": "click",
        "openpyxl": "openpyxl",
        "pytz": "pytz",
        "markdown": "markdown",
        "typer": "typer",
    }
)

#: The longest callback this table can produce, checked at import so a future
#: addition cannot ship a button Telegram refuses before its handler ever runs.
_LONGEST_FIX_CALLBACK = FIX_CALLBACK_PREFIX + max(MODULE_TO_PIP.values(), key=len)
if len(_LONGEST_FIX_CALLBACK.encode("utf-8")) > _TELEGRAM_CALLBACK_MAX:
    raise RuntimeError(
        f"a fix callback ({_LONGEST_FIX_CALLBACK!r}) exceeds Telegram's "
        f"{_TELEGRAM_CALLBACK_MAX}-byte callback_data cap"
    )


def pip_name_for(module: str) -> str | None:
    """Return the pip name for ``module`` (case-insensitive), or ``None``.

    ``None`` means *we do not know* and the screens must not offer an install
    button: an invented name installs the wrong thing (or nothing). Dotted module
    paths are looked up by their top-level part.
    """
    cleaned = (module or "").strip()
    if not cleaned:
        return None
    top_level = cleaned.split(".", 1)[0]
    return MODULE_TO_PIP.get(top_level.lower())


#: A real Python import failure. The prefix is mandatory — matching the bare
#: word "module" would fire on every traceback (`in <module>`).
_MISSING_MODULE = re.compile(
    r"(?:ModuleNotFoundError|ImportError):\s*No module named\s+"
    r"(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z_][\w.]*))"
)

#: A broken loop must not produce a wall of buttons.
MAX_MISSING_MODULES = 5


def missing_modules(stderr: str) -> tuple[str, ...]:
    """Return the distinct missing modules named in ``stderr``, in order, max 5.

    Recognises ``ModuleNotFoundError: No module named 'X'`` and the older
    ``ImportError: No module named X`` form; a dotted module (``a.b``) yields its
    top-level ``a``. Empty or absent stderr yields ``()``.
    """
    if not stderr:
        return ()
    seen: set[str] = set()
    found: list[str] = []
    for match in _MISSING_MODULE.finditer(stderr):
        raw = next((group for group in match.groups() if group), "")
        if not raw:
            continue
        top_level = raw.split(".", 1)[0]
        key = top_level.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        found.append(top_level)
        if len(found) >= MAX_MISSING_MODULES:
            break
    return tuple(found)


__all__ = [
    "FIX_CALLBACK_PREFIX",
    "MAX_MISSING_MODULES",
    "MODULE_TO_PIP",
    "missing_modules",
    "pip_name_for",
]
