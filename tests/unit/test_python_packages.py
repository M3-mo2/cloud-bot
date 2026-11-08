"""The missing-package offer, end to end.

The feature exists because the most common first failure in a fresh workspace is
``ModuleNotFoundError``, and the fix a user wants is one tap. Three things have to
hold, and each had a way to be quietly wrong:

* the **table** must map a module name to the pip name people actually install —
  ``PIL`` is not a package, ``pillow`` is;
* the **parser** must read a traceback without inventing a module from prose;
* the **offer** must appear on every surface where a command's output is shown,
  and a tap must install exactly one package.
"""
from __future__ import annotations

import pytest

from cloudbot.domain.python_packages import (
    FIX_CALLBACK_PREFIX,
    MODULE_TO_PIP,
    missing_modules,
    pip_name_for,
)
from cloudbot.domain.value_objects import WorkspaceId
from cloudbot.core.locale import Locale

WS = WorkspaceId("ws_01TEST000000000000000000")


# --------------------------------------------------------------------- table --
def test_every_pip_name_is_a_name_the_service_accepts():
    """A table entry the package validator rejects would 500 on tap."""
    from cloudbot.services.packages import validate_package_name

    for module, pip in MODULE_TO_PIP.items():
        assert validate_package_name(pip) == pip, f"{module} -> {pip} is not valid"


def test_no_pip_name_is_dotted():
    """``PIL.Image`` is a module path, never a distributable."""
    for pip in MODULE_TO_PIP.values():
        assert "." not in pip, pip


def test_the_awkward_ones_are_mapped_the_way_pip_spells_them():
    """These are the entries a naive module->name guess gets wrong."""
    assert pip_name_for("PIL") == "pillow"
    assert pip_name_for("yaml") == "pyyaml"
    assert pip_name_for("bs4") == "beautifulsoup4"
    assert pip_name_for("cv2") == "opencv-python"
    assert pip_name_for("sklearn") == "scikit-learn"
    assert pip_name_for("dotenv") == "python-dotenv"
    assert pip_name_for("jwt") == "pyjwt"
    assert pip_name_for("Crypto") == "pycryptodome"


def test_lookup_ignores_case_but_returns_pips_spelling():
    assert pip_name_for("pil") == "pillow"
    assert pip_name_for("PIL") == "pillow"
    assert pip_name_for("YAML") == "pyyaml"


def test_an_unknown_module_is_unknown_rather_than_guessed():
    """No entry may be invented: a wrong pip name is worse than no button."""
    assert pip_name_for("some_private_module") is None
    assert pip_name_for("") is None


# -------------------------------------------------------------------- parser --
def test_reads_the_modern_error():
    assert missing_modules(
        "Traceback (most recent call last):\n"
        "ModuleNotFoundError: No module named 'flask'"
    ) == ("flask",)


def test_reads_the_legacy_error():
    assert missing_modules("ImportError: No module named yaml") == ("yaml",)


def test_a_dotted_module_reports_its_top_level_package():
    """``pip install a.b`` does not work; ``pip install a`` does."""
    assert missing_modules(
        "ModuleNotFoundError: No module named 'a.b'"
    ) == ("a",)


def test_two_different_modules_are_returned_in_order():
    out = (
        "ModuleNotFoundError: No module named 'flask'\n"
        "ModuleNotFoundError: No module named 'PIL'"
    )
    assert missing_modules(out) == ("flask", "PIL")


def test_the_same_module_twice_is_reported_once():
    out = (
        "ModuleNotFoundError: No module named 'flask'\n"
        "ModuleNotFoundError: No module named 'flask'"
    )
    assert missing_modules(out) == ("flask",)


def test_prose_that_mentions_the_word_module_yields_nothing():
    """The bite: a parser keyed on the bare word "module" fires on a traceback
    that is about something else entirely, and offers to install a package that
    does not exist."""
    out = (
        "Traceback (most recent call last):\n"
        '  File "app.py", line 3, in <module>\n'
        "    sys.modules['x'] = None\n"
        "TypeError: 'module' object does not support item assignment"
    )
    assert missing_modules(out) == ()


def test_empty_stderr_yields_nothing():
    assert missing_modules("") == ()
    assert missing_modules("   \n  ") == ()


def test_the_cap_holds():
    out = "\n".join(
        f"ModuleNotFoundError: No module named 'm{i}'" for i in range(9)
    )
    assert len(missing_modules(out)) == 5


# ---------------------------------------------------------------- callbacks --
def test_a_fix_callback_fits_for_the_longest_pip_name():
    """Callback data is capped at 64 bytes by Telegram."""
    longest = max(MODULE_TO_PIP.values(), key=len)
    data = FIX_CALLBACK_PREFIX + longest
    assert len(data.encode()) <= 64, (longest, len(data))


# ------------------------------------------------------------------- screens --
def _offer(stderr: str, locale=Locale.EN):
    from cloudbot.presentation.screens.packages import missing_section

    return missing_section(stderr, WS, locale)


def test_a_known_module_offers_its_pip_name_and_names_the_module():
    html, rows = _offer("ModuleNotFoundError: No module named 'PIL'")
    assert "pillow" in html
    # The user's error said PIL; the button says pillow. Both must be visible.
    assert "PIL" in html
    assert rows, "no button for a known module"
    assert any("pillow" in b.callback_data for r in rows for b in r)


def test_an_unknown_module_offers_no_install_button():
    html, rows = _offer("ModuleNotFoundError: No module named 'weird_thing'")
    assert html, "the screen said nothing about a missing module"
    flat = [b.callback_data for r in rows for b in r]
    assert not any("weird_thing" in d for d in flat), "invented a pip name"


def test_a_clean_traceback_produces_no_offer_at_all():
    """A run that failed for another reason must not grow a package section."""
    html, rows = _offer("ValueError: nope")
    assert html == ""
    assert rows == []


def test_the_offer_is_arabic_when_the_locale_is():
    html, _ = _offer("ModuleNotFoundError: No module named 'flask'", Locale.AR)
    assert any("\u0600" <= c <= "\u06ff" for c in html), "not Arabic"


# ------------------------------------------------------- every surface wired --
def test_the_run_result_screen_carries_the_offer():
    """The screen a plain `run` lands on."""
    import inspect

    from cloudbot.presentation.screens import run as run_screens

    params = inspect.signature(run_screens.exec_result).parameters
    assert "missing_offer" in params, "exec_result cannot show the offer"


def test_the_live_terminal_carries_the_offer():
    """Where `python3 app.py` typed by hand lands. This is the surface he
    complained was missing the button, so it gets its own assertion."""
    import inspect

    from cloudbot.presentation.screens import terminal as terminal_screens

    src = inspect.getsource(terminal_screens)
    assert "missing_section" in src, "the terminal never builds the offer"
    params = inspect.signature(terminal_screens.terminal_view_markup).parameters
    assert "extra_rows" in params, "terminal markup cannot carry the offer rows"


def test_the_fix_callback_handler_exists():
    """A button nobody handles is a dead end.

    Assert on the **registered route**, not on a literal string: the handler
    filters on ``pkg_screens.FIX_PREFIX``, so grepping for ``"pkg:fix"`` misses a
    working handler (and would pass on a comment).
    """
    from cloudbot.handlers import features

    routes = [
        getattr(h, "callback", None)
        for h in features.router.callback_query.handlers
    ]
    assert routes, "the features router registered no callbacks at all"
    src = __import__("inspect").getsource(features)
    assert "on_fix_missing_package" in src, "no handler function"
    assert "FIX_PREFIX" in src, "the handler is not bound to the fix prefix"

def test_the_file_run_logs_carry_the_offer():
    """The surface a *file* run lands on.

    This is the path he reported: he opened a file, ran it, it failed on a missing
    package, and there was no button. ``live_logs`` renders a run's stderr and had
    no offer at all, so the feature existed and was unreachable from here.
    """
    import inspect

    from cloudbot.presentation.screens import run as run_screens

    src = inspect.getsource(run_screens.live_logs)
    assert "missing_section" in src, "live_logs never builds the offer"
    params = inspect.signature(run_screens.live_logs_markup).parameters
    assert "extra_rows" in params, "live_logs markup cannot carry the offer rows"


def test_live_logs_really_renders_the_button_for_a_missing_module():
    """End of the argument: build the screen and look for the button."""
    from cloudbot.domain.entities import Run, RunStatus
    from cloudbot.domain.value_objects import RunId
    from cloudbot.presentation.screens.run import live_logs

    run = Run(
        id=RunId("run_01TEST000000000000000000"),
        workspace_id=WS,
        kind="file",
        argv=["app.py"],
        cwd="/app",
        status=RunStatus.FAILED,
        exit_code=1,
        interpreter="python3",
        timeout_sec=60,
        started_at=None,
        ended_at=None,
        stderr_bytes=90,
    )
    screen = live_logs(
        run, Locale.EN,
        stdout="",
        stderr="ModuleNotFoundError: No module named 'PIL'",
    )
    doc_html = screen.doc.html or ""
    built = screen.doc.build()
    if not doc_html and built.blocks:
        doc_html = " ".join(str(getattr(b, "text", "")) for b in built.blocks)
    assert "pillow" in doc_html, f"no offer in the run logs screen: {doc_html[:300]}"
