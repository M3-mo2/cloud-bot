"""Higher-level reusable rich components.

``RawPayloadDetails`` is the *only* component that can emit raw engine content,
and it asserts DEBUG + admin at construction so it cannot be called by accident.
"""

from __future__ import annotations

from dataclasses import dataclass

from cloudbot.core.errors import DomainError
from cloudbot.core.modes import RunMode
from cloudbot.presentation.rich.document import Element, RichDoc
from cloudbot.presentation.rich import tags


class RawPayloadNotAllowed(DomainError):
    code = "raw_payload_not_allowed"


@dataclass(frozen=True)
class RawPayloadDetails:
    """A collapsed raw engine payload — DEBUG + admin only.

    Constructing one outside that context raises, so a normal screen cannot leak
    raw output by mistake.
    """

    payload: str

    def __init__(self, payload: str, *, mode: RunMode, is_admin: bool) -> None:
        if mode is not RunMode.DEBUG or not is_admin:
            raise RawPayloadNotAllowed("raw payloads are DEBUG + admin only")
        object.__setattr__(self, "payload", payload)

    def render(self, doc: RichDoc) -> RichDoc:
        return doc.raw(
            "<details><summary>🧪 Raw engine response (admin)</summary>"
            f'<pre><code class="language-json">{tags.esc(self.payload)}</code></pre>'
            "</details>"
        )


def usage_bar(percent: float, *, width: int = 12) -> str:
    """A language-neutral ``[████░░░░] 50%`` bar."""
    filled = max(0, min(width, round(percent / 100 * width)))
    return "[" + "█" * filled + "░" * (width - filled) + f"] {round(percent)}%"


def status_row(label: Element, value: Element) -> list[Element]:
    return [label, value]


__all__ = ["RawPayloadDetails", "RawPayloadNotAllowed", "usage_bar", "status_row"]
