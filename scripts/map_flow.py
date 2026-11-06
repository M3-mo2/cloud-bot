"""``python -m scripts.map_flow`` / ``python scripts/map_flow.py``.

Regenerates ``docs/FLOW-MAP.md`` from the code. This script is only a thin entry
point: the derivation lives in ``cloudbot.devtools.flow`` so the same code is
reused by the test that proves the committed document is current.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: The repository root, so the script works as a module or as a plain file.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    from cloudbot.devtools import flow

    target = flow.write(ROOT)
    model = flow.build_model(ROOT)
    print(
        f"wrote {target.relative_to(ROOT)} — "
        f"{model.screen_count} screens, {model.edge_count} buttons, "
        f"{len(model.destinations)} destinations"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
