"""Escaping/injection: adversarial values render literally."""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.presentation.rich import tags
from cloudbot.presentation.rich.document import RichDoc


def test_esc_escapes_html():
    assert tags.esc("<script>") == "&lt;script&gt;"
    assert tags.esc("</code>") == "&lt;/code&gt;"
    assert tags.esc("a & b") == "a &amp; b"
    assert tags.esc('"q"') == "&quot;q&quot;"


def test_rtl_override_is_neutralized():
    malicious = "\u202eevil.txt"
    assert "\u202e" not in tags.safe(malicious)
    assert "evil.txt" in tags.safe(malicious)


def test_document_escapes_dynamic_values():
    doc = RichDoc(Locale.EN)
    doc.p("<script>alert(1)</script>")
    html = doc.html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_code_and_path_escape():
    doc = RichDoc(Locale.EN)
    doc.p(tags.code("</code><script>"))
    assert "<script>" not in doc.html


def test_filename_with_rtl_override_in_codeblock():
    doc = RichDoc(Locale.AR)
    doc.p(tags.code("a\u202eb.py"))
    assert "\u202e" not in doc.html


def test_build_sets_rtl_from_locale():
    en = RichDoc(Locale.EN).p("hello").build()
    ar = RichDoc(Locale.AR).p("مرحبا").build()
    assert en.is_rtl is False
    assert ar.is_rtl is True


def test_adversarial_in_workspace_name():
    from cloudbot.adapters.cloud_service.fake import _ws
    from cloudbot.domain.value_objects import WorkspaceStatus
    from cloudbot.presentation.screens.workspaces import workspace_detail

    ws = _ws("ws_x", "<b>hack</b>", WorkspaceStatus.ACTIVE)
    html = workspace_detail(ws, Locale.EN).doc.html
    assert "<b>hack</b>" not in html
    assert "&lt;b&gt;hack&lt;/b&gt;" in html
