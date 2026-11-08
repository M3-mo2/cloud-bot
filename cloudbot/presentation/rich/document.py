"""``RichDoc`` — the builder that composes an ``InputRichMessage``.

``build()`` returns exactly one of html/markdown/blocks (html) and derives
``is_rtl`` from the locale. Every dynamic value is escaped by ``tags``.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from aiogram.types import InputRichMessage

from cloudbot.core.locale import Locale, is_rtl
from cloudbot.presentation.rich import tags
from cloudbot.presentation.rich.tags import Html  # noqa: F401  (re-export)

Element = str | Html

#: Rich-message text length cap (Bot API).
MAX_TEXT_LEN = 32768


class RichDoc:
    def __init__(self, locale: Locale) -> None:
        self._locale = locale
        self._parts: list[str] = []
        self._blocks: list = []

    @property
    def locale(self) -> Locale:
        return self._locale

    def _add(self, raw: str) -> "RichDoc":
        self._parts.append(raw)
        return self

    def _value(self, value: Element) -> str:
        return str(value) if isinstance(value, Html) else tags.safe(value)

    # -- block elements ------------------------------------------------------
    def h(self, level: int, text: Element) -> "RichDoc":
        level = max(1, min(6, level))
        return self._add(f"<h{level}>{self._value(text)}</h{level}>")

    def p(self, text: Element) -> "RichDoc":
        return self._add(f"<p>{self._value(text)}</p>")

    def bold(self, text: Element) -> "RichDoc":
        return self.p(tags.bold(text))

    def code(self, text: Element) -> "RichDoc":
        return self.p(tags.code(text))

    def pre(self, text: Element, language: str | None = None) -> "RichDoc":
        inner = str(text) if isinstance(text, Html) else tags.safe(text)
        cls = f' class="language-{tags.esc(language)}"' if language else ""
        return self._add(f"<pre><code{cls}>{inner}</code></pre>")

    def raw(self, html: str) -> "RichDoc":
        """Insert trusted, already-built HTML (used only by this module's peers)."""
        return self._add(html)

    def prepend_raw(self, html: str) -> "RichDoc":
        """Insert trusted HTML at the very top (the entered-mode banner seam)."""
        self._parts.insert(0, html)
        return self

    def prepend_text(self, text: str) -> "RichDoc":
        """Insert a **quoted** notice at the very top, in the form this doc uses.

        An html document gets a ``<blockquote>``; a blocks document gets a
        paragraph block. The admin panel prefixes a banner or a one-line result to
        screens that may build in either form, and mixing the two would drop the
        html half (see :meth:`build`).
        """
        if self._blocks:
            from aiogram.types import InputRichBlockBlockQuotation

            self._blocks.insert(0, InputRichBlockBlockQuotation(blocks=[
                self._paragraph_block(text)
            ]))
        else:
            self._parts.insert(0, f"<blockquote>{tags.safe(text)}</blockquote>")
        return self

    @staticmethod
    def _paragraph_block(text: str):
        from aiogram.types import InputRichBlockParagraph

        return InputRichBlockParagraph(text=text)

    def div(self) -> "RichDoc":
        return self._add("<hr>")

    def blockquote(self, text: Element) -> "RichDoc":
        return self._add(f"<blockquote>{self._value(text)}</blockquote>")

    def quote(self, text: str) -> "RichDoc":
        """A quoted notice in whichever form this document already uses.

        An html document gets ``<blockquote>``; a blocks document gets a
        block-quotation block. Screens that build in blocks cannot add html (see
        :meth:`build`), and a warning must not be the thing that silently drops
        the rest of the message.
        """
        if self._blocks:
            from aiogram.types import InputRichBlockBlockQuotation

            self._blocks.append(
                InputRichBlockBlockQuotation(blocks=[self._paragraph_block(text)])
            )
            return self
        return self._add(f"<blockquote>{tags.safe(text)}</blockquote>")

    def ul(self, items: Sequence[Element]) -> "RichDoc":
        body = "".join(f"<li>{self._value(item)}</li>" for item in items)
        return self._add(f"<ul>{body}</ul>")

    def details(
        self, summary: Element, blocks: Sequence[Element], *, open: bool = False
    ) -> "RichDoc":
        attr = " open" if open else ""
        body = "".join(self._value(b) for b in blocks)
        return self._add(
            f"<details{attr}><summary>{self._value(summary)}</summary>{body}</details>"
        )

    def table(
        self,
        headers: Sequence[Element] | None,
        rows: Sequence[Sequence[Element]],
        *,
        bordered: bool = True,
        striped: bool = False,
    ) -> "RichDoc":
        attrs = []
        if bordered:
            attrs.append('is_bordered="true"')
        if striped:
            attrs.append('is_striped="true"')
        attr = (" " + " ".join(attrs)) if attrs else ""
        out = [f"<table{attr}>"]
        if headers:
            out.append("<tr>" + "".join(f"<th>{self._value(h)}</th>" for h in headers) + "</tr>")
        for row in rows:
            out.append("<tr>" + "".join(f"<td>{self._value(c)}</td>" for c in row) + "</tr>")
        out.append("</table>")
        return self._add("".join(out))

    def kv(self, pairs: Mapping[Element, Element]) -> "RichDoc":
        rows = [[tags.bold(k), v] for k, v in pairs.items()]
        return self.table(None, rows)

    def kv_table(self, pairs: Sequence[tuple[Element, Element]], *, bordered: bool = True) -> "RichDoc":
        """A two-column table with <th> labels on the left."""
        attr = ' is_bordered="true"' if bordered else ""
        body = "".join(
            f"<tr><th>{self._value(k)}</th><td>{self._value(v)}</td></tr>" for k, v in pairs
        )
        return self._add(f"<table{attr}>{body}</table>")

    def paragraph(self, text: str) -> "RichDoc":
        """A body paragraph as an ``InputRichBlock`` — the blocks form of a message.

        A screen that needs **buttons inside its body** cannot use html: Telegram
        refuses ``<tg-button callback_data=...>`` with ``BUTTON_URL_INVALID`` (the
        tag only accepts a ``url``), while the ``blocks`` form accepts
        ``callback_data`` and delivers a normal ``CallbackQuery``. Measured.

        ``InputRichMessage`` allows *either* ``html`` or ``blocks``, never both — so
        a document that has any block emits **only** blocks. That is why this is an
        all-or-nothing seam: a screen either builds entirely in blocks or not at all.
        """
        from aiogram.types import InputRichBlockParagraph

        self._blocks.append(InputRichBlockParagraph(text=text))
        return self

    def heading(self, text: str, *, size: int = 2) -> "RichDoc":
        """A heading block (``size`` 1-6, 1 largest) — the blocks form's <h2>."""
        from aiogram.types import InputRichBlockSectionHeading

        self._blocks.append(InputRichBlockSectionHeading(text=text, size=max(1, min(6, size))))
        return self

    def pre_block(self, text: str, language: str | None = None) -> "RichDoc":
        """A preformatted block (a fixed-width body) — the blocks form of <pre>.

        Used where alignment is the content: a tree's branch glyphs only line up
        in a fixed-width block, and block paragraph text has no line breaks.
        """
        from aiogram.types import InputRichBlockPreformatted

        self._blocks.append(InputRichBlockPreformatted(text=text, language=language))
        return self

    def footer(self, text: str) -> "RichDoc":
        from aiogram.types import InputRichBlockFooter

        self._blocks.append(InputRichBlockFooter(text=text))
        return self

    def kv_block(self, pairs: Sequence[tuple[str, str]]) -> "RichDoc":
        """A two-column ``label`` / ``value`` table in the **blocks** form.

        A table is allowed only for a label and its value (identity blocks), never
        for a list of subjects. This is the block-document sibling of
        :meth:`kv_table`, for screens that build in blocks.
        """
        from aiogram.types import InputRichBlockTable, RichBlockTableCell

        cells = [
            [
                RichBlockTableCell(align="left", valign="middle", text=label, is_header=True),
                RichBlockTableCell(align="left", valign="middle", text=value),
            ]
            for label, value in pairs
        ]
        self._blocks.append(InputRichBlockTable(cells=cells, is_bordered=True))
        return self

    def buttons(
        self,
        row: Sequence[tuple[str, str, str | None]],
        *,
        align: str | None = None,
    ) -> "RichDoc":
        """A row of 1-8 buttons inside the body: ``(label, callback_data, style)``.

        ``align`` is ``left`` / ``center`` / ``right`` — an alignment the keyboard
        below the message simply does not have.
        """
        from aiogram.types import InputRichBlockButtons, RichMessageButton

        built = []
        for label, action, style in row:
            if len(str(action).encode("utf-8")) > 64:
                raise ValueError(f"callback_data over 64 bytes: {action!r}")
            built.append(RichMessageButton(text=label, callback_data=action, style=style))
        self._blocks.append(InputRichBlockButtons(buttons=built, align=align))
        return self

    def raw_buttons(
        self,
        rows: Sequence[dict | Sequence[dict]],
        *,
        align: str | None = None,
    ) -> "RichDoc":
        """A row of buttons given as ready-made dicts.

        The sibling of :meth:`buttons` for callers that build their own entries —
        a button whose ``url`` comes from a chat, or whose label is composed
        elsewhere. Each dict needs at least ``text`` and one action
        (``callback_data`` / ``url``); ``style`` is optional.
        """
        from aiogram.types import InputRichBlockButtons, RichMessageButton

        entries = list(rows)
        if entries and isinstance(entries[0], dict):
            entries = [entries]
        for row in entries:
            built = []
            for e in row:
                action = e.get("callback_data")
                if action is not None and len(str(action).encode("utf-8")) > 64:
                    raise ValueError(f"callback_data over 64 bytes: {action!r}")
                built.append(
                    RichMessageButton(
                        text=e.get("text", ""),
                        callback_data=action,
                        url=e.get("url"),
                        style=e.get("style"),
                    )
                )
            self._blocks.append(InputRichBlockButtons(buttons=built, align=align))
        return self

    def alert(self, kind: str, title: Element, body: Element) -> "RichDoc":
        icon = {"warning": "⚠️", "error": "❌", "success": "✅", "info": "ℹ️"}.get(kind, "⚠️")
        return self.h(2, tags.fragment([icon, " ", tags.bold(title)])).p(body)

    # -- assembly ------------------------------------------------------------
    @property
    def html(self) -> str:
        return "".join(self._parts)

    def build(self) -> InputRichMessage:
        """``InputRichMessage`` allows *either* html or blocks, never both.

        A document that wrote to **both** is a programming error, and a silent one:
        returning the blocks would drop every html part (an identity table, a
        divider) from the message with no exception anywhere. It is raised instead,
        because the alternative is a screen that quietly loses content.
        """
        if self._blocks and self._parts:
            raise ValueError(
                "a RichDoc cannot mix html parts and blocks: Telegram allows one form. "
                f"{len(self._parts)} html part(s) would be silently dropped."
            )
        if self._blocks:
            return InputRichMessage(blocks=self._blocks, is_rtl=is_rtl(self._locale))
        return InputRichMessage(html=self.html, is_rtl=is_rtl(self._locale))

    def __len__(self) -> int:
        return len(self.html)
