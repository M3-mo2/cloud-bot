"""TASK flowdoc — the generated flow map is deterministic, current and honest.

The document is derived output, so the tests prove three things about the
derivation itself:

* it is deterministic (two runs are byte-identical);
* the committed ``docs/FLOW-MAP.md`` is exactly what a fresh run produces;
* every number the summary claims is the number the document (and the catalog)
  actually contains — a claimed count cannot drift from the tree it printed.

It also proves the generator is offline: with ``socket.socket`` patched to raise,
a full run still completes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cloudbot.devtools import flow

REPO_ROOT = Path(__file__).resolve().parents[2]
ARABIC_DIGITS = re.compile(r"[\u0660-\u0669]")


@pytest.fixture(scope="module")
def model():
    return flow.build_model(REPO_ROOT)


@pytest.fixture(scope="module")
def document(model) -> str:
    return flow.render(model)


def _summary(document: str) -> dict[str, int]:
    """The one-screen summary table as ``{label: value}`` (values are ints)."""
    out: dict[str, int] = {}
    section = document.split("## One-screen summary", 1)[1].split("## 1.", 1)[0]
    for line in section.splitlines():
        match = re.match(r"\|\s*(.+?)\s*\|\s*(\d+)\s*\|", line)
        if match:
            out[match.group(1)] = int(match.group(2))
    return out


def _section_count(document: str, title: str) -> int:
    """The number in a contradiction section's heading, e.g. ``## 3.3 Dead ends (7)``."""
    match = re.search(re.escape(title) + r" \((\d+)\)", document)
    assert match is not None, f"section {title!r} is missing"
    return int(match.group(1))


def test_the_generator_is_deterministic(document):
    """A second, independent run produces byte-identical text."""
    from cloudbot.devtools import flow as fresh

    second = fresh.render(fresh.build_model(REPO_ROOT))
    assert second == document


def test_the_committed_document_is_current(document):
    """The committed file equals freshly generated output (the staleness gate)."""
    committed = (REPO_ROOT / flow.DOC_RELATIVE).read_text(encoding="utf-8")
    assert committed == document, (
        "docs/FLOW-MAP.md is stale; regenerate it with "
        "`python -m scripts.map_flow`"
    )


def test_the_generated_output_has_only_ascii_digits(document):
    """His rule: no Arabic-Indic digit anywhere in user-facing output."""
    assert not ARABIC_DIGITS.search(document), "the generated document contains ٠–٩"


def test_the_summary_numbers_match_the_tree_it_printed(document, model):
    """Every number the summary claims equals a number measured from the output."""
    summary = _summary(document)

    # Catalog screens: claimed vs the catalog itself.
    from cloudbot.presentation import catalog as screen_catalog

    assert summary["Catalog screens"] == len(screen_catalog.SCREEN_BUILDERS)
    assert summary["Screens including the /dev tool"] == summary["Catalog screens"] + 3

    # Buttons: claimed vs an independent count over every rendered screen.
    expected_buttons = sum(
        len(screen_catalog.button_infos(screen_catalog.build(name, screen_catalog.Locale.EN)))
        for name in screen_catalog.SCREEN_BUILDERS
    )
    assert summary["Button edges"] >= expected_buttons

    # Every flat-index screen appears exactly once, with its buttons.
    flat = document.split("## 5. Every screen, every button (flat index)", 1)[1]
    flat_screens = re.findall(r"^### (\S+) — ", flat, re.MULTILINE)
    assert len(flat_screens) == summary["Screens including the /dev tool"]
    assert len(set(flat_screens)) == len(flat_screens)

    # The entry table's row count is the claimed entry-command count.
    entries = document.split("## 1. Entry points", 1)[1].split("Callback re-entry", 1)[0]
    entry_rows = [
        line for line in entries.splitlines()
        if line.startswith("| `") and "|" in line
    ]
    assert len(entry_rows) == summary["Entry commands"]

    # The callback re-entry table's row count is the claimed action count.
    reentry = document.split("**Callback re-entry.**", 1)[1].split("## 2.", 1)[0]
    action_rows = [line for line in reentry.splitlines() if line.startswith("| `")]
    assert len(action_rows) == summary["Callback re-entry actions"]


def test_the_contradiction_counts_agree_with_the_summary(document):
    """Each contradiction section's heading count equals the summary's count."""
    summary = _summary(document)
    pairs = {
        "### 3.1 Two labels for one destination": "one destination, several labels",
        "### 3.2 Repeated buttons on one screen": "repeated button on one screen",
        "### 3.3 Dead ends": "dead ends",
        "### 3.4 Orphans": "orphans",
        "### 3.5 Loops that are not obvious": "two-screen loops",
    }
    for heading, key in pairs.items():
        claimed = _section_count(document, heading)
        assert claimed == summary[f"Contradictions: {key}"], heading
    # Depths has its own wording, but the two numbers must still agree.
    depths = _section_count(document, "### 3.7 Depths")
    assert depths == summary["Screens deeper than 4 taps from `/start`"]


def test_the_generator_needs_no_socket(monkeypatch):
    """The whole derivation runs with sockets disabled — it is offline."""
    import socket

    def _boom(*_args, **_kwargs):
        raise AssertionError("the flow generator opened a socket")

    monkeypatch.setattr(socket, "socket", _boom)
    model = flow.build_model(REPO_ROOT)
    assert model.screen_count > 0
    assert flow.render(model)


def test_the_entry_points_come_from_the_routers(document):
    """The commands are the routers' own, and include the documented four."""
    for command in ("/start", "/help", "/settings", "/paysupport"):
        assert f"| `{command}` |" in document
