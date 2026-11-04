"""Safe HTML escaping and element helpers.

This is the **only** module allowed to concatenate raw HTML. Every user- or
engine-derived value passes through ``esc``.
"""

from __future__ import annotations

import re

#: Bidirectional control characters that could visually reorder text.
_BIDI_CONTROLS = re.compile(
    "[\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c]"
)


class Html(str):
    """A fragment already assembled and escaped by this module.

    The document builder inserts an ``Html`` verbatim and escapes every other
    ``str``. Only this module constructs ``Html`` values, so raw markup cannot
    enter a document from an untrusted source.
    """


def esc(text: object) -> str:
    """Escape ``&``, ``<``, ``>`` and quotes for safe HTML embedding."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def neutralize_bidi(text: str) -> str:
    """Strip bidi override characters so a filename cannot reverse a sentence."""
    return _BIDI_CONTROLS.sub("", text)


def safe(text: object) -> str:
    """Escape and neutralize bidi controls in one step."""
    return esc(neutralize_bidi(str(text)))


def fragment(parts: object) -> Html:
    """Join pre-escaped parts (``Html`` or plain ``str``) into one fragment.

    Plain strings are escaped; ``Html`` parts are inserted as-is.
    """
    if isinstance(parts, (list, tuple)):
        return Html("".join(str(p) if isinstance(p, Html) else safe(p) for p in parts))
    return Html(str(parts) if isinstance(parts, Html) else safe(parts))


def code(text: object) -> Html:
    return Html(f"<code>{safe(text)}</code>")


def path(text: object) -> Html:
    """Paths are always inline code."""
    return code(text)


def pre(text: object, language: str | None = None) -> Html:
    cls = f' class="language-{esc(language)}"' if language else ""
    return Html(f"<pre><code{cls}>{safe(text)}</code></pre>")


def bold(text: object) -> Html:
    inner = str(text) if isinstance(text, Html) else safe(text)
    return Html(f"<b>{inner}</b>")


def italic(text: object) -> Html:
    inner = str(text) if isinstance(text, Html) else safe(text)
    return Html(f"<i>{inner}</i>")


def blockquote(text: object) -> Html:
    inner = str(text) if isinstance(text, Html) else safe(text)
    return Html(f"<blockquote>{inner}</blockquote>")
