"""``Screen`` — a RichDoc plus an optional markup.

Screens are pure functions of domain state + locale: no I/O, snapshot-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cloudbot.presentation.rich.document import RichDoc


@dataclass
class Screen:
    doc: RichDoc
    markup: Any | None = None
    #: When set, the handler sends (rather than edits) because force_reply or a
    #: reply keyboard cannot ride an edit.
    send_new: bool = False
