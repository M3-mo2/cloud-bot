"""``python -m cloudbot.devtools.reproduce`` — turn a bug report into a failing test.

It is **keyword matching, not diagnosis**: it scores screens against the words in
the report using the catalogue keys, the screen names, the button labels and the
callback prefixes, prints the evidence for every match, and writes a stub test
that renders the best-guess screen and fails until the bug is fixed. A wrong guess
is therefore visibly wrong (the evidence does not fit), never confidently wrong.

No model call, no network: it reads the catalogue and the screen builders only.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from cloudbot.devtools import screens as screen_tools

#: Words that carry no signal when matching a report to a screen.
STOPWORDS = frozenset(
    """
    a an the and or but if then else for to of in on at by with from into is are was
    were be been being it its this that these those i we you he she they my our your
    after before when where while not no yes do does did done have has had can could
    should would will just also very really more most less least than as so there here
    about over under up down out off again once same different new old get gets got
    make makes made show shows shown display displays render renders page screen button
    buttons text says say said work works working broken broke bug error issue problem
    when then please fix wrong correct right expected actual instead currently
    """.split()
)

_ARABIC_STOPWORDS = frozenset(
    """
    في من على الى إلى عن مع هذا هذه ذلك التي الذي كان كانت يكون ان أن لا ما هو هي
    عند عندما بعد قبل لكن ثم او أو و يا كل بعض غير حتى بين لم لن قد لقد
    """.split()
)

_WORD = re.compile(r"[\w\u0600-\u06FF][\w\u0600-\u06FF_.-]*", re.UNICODE)

@dataclass(frozen=True)
class Evidence:
    kind: str  # "screen" | "catalog" | "button" | "callback" | "mutation" | "path"
    needle: str
    matched: str
    detail: str = ""

@dataclass
class Candidate:
    screen: str
    score: float = 0.0
    evidence: list[Evidence] = field(default_factory=list)

@dataclass
class Reproduction:
    description: str
    candidates: list[Candidate]
    locale: str = "en"

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for word in _WORD.findall(text.lower()):
        word = word.strip("._-")
        if len(word) < 3:
            continue
        if word in STOPWORDS or word in _ARABIC_STOPWORDS:
            continue
        if word.isdigit():
            continue
        out.append(word)
    return out

def _screen_sources(locale) -> dict[str, tuple[str, list, list[str]]]:
    """name → (html, buttons, callbacks) for every screen in one locale."""
    from cloudbot.presentation import catalog as screen_catalog

    fixtures = screen_catalog.fixtures()
    out: dict[str, tuple[str, list, list[str]]] = {}
    for name in screen_catalog.screen_names():
        try:
            screen = screen_catalog.build(name, locale, fixtures_data=fixtures)
        except Exception:  # noqa: BLE001 - a broken screen is simply not a match
            continue
        out[name] = (
            screen.doc.html,
            screen_tools.buttons(screen),
            screen_tools.callbacks(screen),
        )
    return out

def _catalog_values(locale) -> list[tuple[str, str]]:
    from cloudbot.presentation.i18n.catalog import CATALOGS

    return [(key, value) for key, value in CATALOGS[locale].MESSAGES.items() if value]

def match(description: str, paths: list[str] | None = None, *, locale=None) -> Reproduction:
    from cloudbot.core.locale import Locale

    locale = locale or Locale.EN
    tokens = _tokens(description)
    # A quoted phrase is a stronger needle than a single word.
    quoted = re.findall(r"[\"“”'‘’]([^\"“”'‘’]{3,})[\"“”'‘’]", description)

    candidates: dict[str, Candidate] = {}
    sources = _screen_sources(locale)

    def add(name: str, evidence: Evidence, weight: float) -> None:
        candidate = candidates.setdefault(name, Candidate(screen=name))
        candidate.score += weight
        candidate.evidence.append(evidence)

    catalog_pairs = _catalog_values(locale)

    def screen_renders(value: str) -> list[str]:
        needle = value.lower()
        return [name for name, (html, _b, _c) in sources.items() if needle in html.lower()]

    for needle in tokens:
        # 1. the screen name itself
        for name in sources:
            if needle in name.lower():
                add(name, Evidence("screen", needle, name), 3.0)
        # 2. button labels
        for name, (_html, buttons, _cb) in sources.items():
            for button in buttons:
                if needle in (button.text or "").lower():
                    add(
                        name,
                        Evidence("button", needle, button.text, f"→ {button.callback_data or button.url or ''}"),
                        2.5,
                    )
        # 3. callback prefixes
        for name, (_html, _b, callbacks) in sources.items():
            for action in callbacks:
                prefix = ":".join(action.split(":")[:2])
                if needle in prefix.lower() or needle in action.lower():
                    add(name, Evidence("callback", needle, action), 1.5)
        # 4. catalogue entries whose *value* contains the word, then the screens
        #    that render that value (so a wrong screen cannot claim the match).
        for key, value in catalog_pairs:
            value_hit = needle in value.lower()
            key_hit = needle in key.lower()
            if not (value_hit or key_hit):
                continue
            rendering = screen_renders(value) if len(value) >= 4 else []
            # A word in the *rendered text* is strong evidence for exactly the
            # screens that show it; a namespace name in a key is weak.
            weight = 2.0 if value_hit else 0.25
            for name in rendering[:8]:
                add(name, Evidence("catalog", needle, f"{key} = {value!r}"), weight)
            if not rendering and not candidates:
                # Keep a mapped error/status hit as weak evidence when nothing
                # else matched at all.
                for name in sources:
                    add(name, Evidence("catalog", needle, f"{key} = {value!r}"), 0.1)

    # 5. a quoted phrase is matched exactly.
    for phrase in quoted:
        needle = phrase.lower()
        for name, (html, buttons, callbacks) in sources.items():
            if needle in html.lower():
                add(name, Evidence("catalog", phrase, "rendered text"), 3.0)
            for button in buttons:
                if needle in (button.text or "").lower():
                    add(name, Evidence("button", phrase, button.text), 3.0)

    # 6. an optional path names a module; screens whose builder is defined near it
    #    score a little, and a ``ROUTE`` hint in the path wins strongly.
    for raw in paths or []:
        path = Path(raw)
        stem = path.stem
        if not stem:
            continue
        for name in sources:
            if stem in name or name.startswith(stem.rsplit("_", 1)[0]):
                add(name, Evidence("path", stem, name, "file stem resembles screen"), 1.0)

    ranked = sorted(
        candidates.values(), key=lambda c: (c.score, len(c.evidence)), reverse=True
    )
    return Reproduction(description=description, candidates=ranked, locale=locale.value)

def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (slug[:40] or "bug").strip("_")

def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')

def _heuristic_assertion(description: str, rendered_html: str = "") -> str:
    """A concrete assertion when the named symptom really is on today's screen.

    Otherwise a sentinel: the stub must fail now, and a false concrete assertion
    (the bug is not actually there) would pass — exactly the confidently-wrong
    outcome the tool is honest about avoiding.
    """
    literal = re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", description)
    if literal and literal[0] in rendered_html:
        token = literal[0]
        return (
            "    # The report names a literal placeholder that is on this screen now.\n"
            "    def reported_behaviour_is_present() -> bool:\n"
            f"        return {token!r} in html"
        )
    quoted = re.findall(r"[\"“”'‘’]([^\"“”'‘’]{3,})[\"“”'‘’]", description)
    if quoted and quoted[0].lower() in rendered_html.lower():
        phrase = quoted[0]
        return (
            "    # The report quotes text that is on this screen now.\n"
            "    def reported_behaviour_is_present() -> bool:\n"
            f"        return {phrase!r}.lower() in html.lower()"
        )
    return (
        "    # STUB: fill this in with the concrete observation. It currently\n"
        "    # asserts a sentinel, so it fails on today's tree and turns green when\n"
        "    # the reported behaviour is corrected.\n"
        "    def reported_behaviour_is_present() -> bool:\n"
        "        _ = (html, callbacks)  # the rendered screen, available to the check\n"
        "        return True  # <-- replace with the exact symptom"
    )

def render_test(
    reproduction: Reproduction,
    *,
    screen: str | None = None,
    handler: str | None = None,
    out_path: str | None = None,
) -> str:
    best = reproduction.best
    screen_name = screen or (best.screen if best else "<unknown>")
    evidence_lines = []
    if best is not None:
        for item in best.evidence[:12]:
            detail = f" {item.detail}" if item.detail else ""
            evidence_lines.append(
                f"#   {item.kind}: {item.needle!r} matched {item.matched!r}{detail}"
            )
    if not evidence_lines:
        evidence_lines = ["#   (no evidence — the report named nothing in the catalogue)"]

    runner = out_path or "tests/unit/test_repro_" + _slug(reproduction.description) + ".py"
    rendered_html = ""
    try:
        from cloudbot.core.locale import Locale
        from cloudbot.presentation import catalog as screen_catalog

        rendered_html = screen_catalog.build(screen_name, Locale.EN).doc.html
    except Exception:  # noqa: BLE001 - an unrenderable guess just falls back
        rendered_html = ""
    assertion = _heuristic_assertion(reproduction.description, rendered_html)
    handler_note = (
        f"# Suspected handler: {handler}\n" if handler else "# Suspected handler: (none named)\n"
    )
    description = _escape(reproduction.description)
    candidates = "\n".join(
        f"#   {c.screen} (score {c.score:.1f})" for c in reproduction.candidates[:5]
    )
    return f'''"""Reproduce: {description}

Generated by ``python -m cloudbot.devtools.reproduce``. This is a STUB, not a
diagnosis: it narrows the search to the screen below and fails until the reported
behaviour is fixed. Confirm the evidence before trusting the guess, then replace
the stubbed observation with the exact symptom.

Best-guess screen: {screen_name}
Other candidates:
{candidates}
Evidence behind the guess:
{chr(10).join(evidence_lines)}
{handler_note}"""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.presentation import catalog as screen_catalog

def test_{_slug(screen_name)}_does_not_reproduce_the_report():
    screen = screen_catalog.build("{screen_name}", Locale.EN)
    html = screen.doc.html
    callbacks = [
        b.callback_data
        for row in (getattr(screen.markup, "inline_keyboard", None) or [])
        for b in row
        if getattr(b, "callback_data", None)
    ]

    # The reported behaviour, in the reporter's words:
    #   {description}
    #
{assertion}
    assert not reported_behaviour_is_present(), (
        "reported behaviour reproduced on screen {screen_name}: {description}"
    )
'''

def command_for(out_path: str) -> str:
    return f"/home/zerops/cloud-bot-venv/bin/python -m pytest -q {out_path}"

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cloudbot.devtools.reproduce",
        description="Turn a plain-language bug report into a stub failing test.",
    )
    parser.add_argument("description", help="the bug, in plain language")
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="a file path mentioned in the report (repeatable)",
    )
    parser.add_argument("--screen", help="force the screen under suspicion")
    parser.add_argument("--handler", help="force the handler under suspicion")
    parser.add_argument(
        "--write",
        metavar="FILE",
        help="write the stub test to FILE instead of printing it",
    )
    args = parser.parse_args(argv)

    reproduction = match(args.description, args.path)
    if not reproduction.candidates:
        print("reproduce: no screen matched the report; try naming a screen/button.")
        return 2

    out_path = args.write or f"tests/unit/test_repro_{_slug(args.description)}.py"
    test_source = render_test(
        reproduction, screen=args.screen, handler=args.handler, out_path=out_path
    )
    if args.write:
        Path(args.write).write_text(test_source, encoding="utf-8")
        print(f"wrote {args.write}")
    else:
        print(test_source)
    print()
    print("evidence (keyword → match):")
    for candidate in reproduction.candidates[:3]:
        print(f"  {candidate.screen} ({candidate.score:.1f})")
        for item in candidate.evidence[:8]:
            detail = f" {item.detail}" if item.detail else ""
            print(f"    {item.kind}: {item.needle!r} → {item.matched!r}{detail}")
    print()
    print("run it with:")
    print("  " + command_for(out_path))
    return 0

if __name__ == "__main__":
    sys.exit(main())
