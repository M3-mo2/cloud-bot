"""TASK digits — the catalog-rewriting script is idempotent and reports counts.

The script is the migration tool, not a one-shot edit, so its contract is tested:
it changes digit characters inside string literals only, reports how many it
changed per file, and a second run changes nothing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "latinize_digits.py"


def _load():
    spec = importlib.util.spec_from_file_location("latinize_digits", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_rewrites_digits_and_punctuation_inside_string_literals(tmp_path):
    module = _load()
    target = tmp_path / "sample.py"
    target.write_text(
        'MESSAGES = {"a": "٥٠ نجمة", "b": "٣٫٩ ميغابايت (٣٪)"}\n'
        "# a comment with ٩ is not content\n"
        "COUNT = 5\n",
        encoding="utf-8",
    )

    # 2 digits in "a" + 3 in "b"; the comment's digit is not content.
    digits, punctuation = module.rewrite(target)
    assert (digits, punctuation) == (5, 2)
    assert "٩" in target.read_text(encoding="utf-8")  # the comment is untouched
    assert 'MESSAGES = {"a": "50 نجمة", "b": "3.9 ميغابايت (3%)"}' in target.read_text(
        encoding="utf-8"
    )


def test_second_run_changes_nothing(tmp_path):
    module = _load()
    target = tmp_path / "sample.py"
    target.write_text('MESSAGES = {"a": "٥٠"}\n', encoding="utf-8")

    assert module.rewrite(target) == (2, 0)
    assert module.rewrite(target) == (0, 0)


def test_identifiers_and_keys_are_untouched(tmp_path):
    module = _load()
    target = tmp_path / "sample.py"
    target.write_text('key5 = "٥"\n', encoding="utf-8")

    module.rewrite(target)
    text = target.read_text(encoding="utf-8")
    assert text.startswith('key5 = "5"')
