"""Phase 2a/2b/2c unit tests: package, tunnel and port use-cases.

Covers ownership refusal, local validation, the install-failure path (honest,
with the ``allow_net`` explanation), the three tunnel gates distinguished, and
port-report passthrough (reachability, partial view, unix section).
"""

from __future__ import annotations

import pytest

from cloudbot.core.errors import EngineRefused, InvalidInput
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import Limits, WorkspaceId
from cloudbot.presentation.mapping import errors as error_mapping
from cloudbot.services.packages import parse_package_input, validate_package_name
from cloudbot.services.tunnels import parse_port, validate_port

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")


async def _own(users, tg_id: int, ws: WorkspaceId = WS):
    await users.get_or_create(tg_id, default_locale=Locale.EN)
    await users.add_workspace(tg_id, ws)


# -- packages ----------------------------------------------------------------
async def test_packages_install_refused_for_foreign_user(services, users):
    await _own(users, 1)
    with pytest.raises(Exception) as exc:
        await services.packages.install(2, WS, ["six"])
    assert exc.value.code == "not_owned"


async def test_packages_install_ok_when_net(services, users):
    await _own(users, 1)
    # demo is seeded with default limits (allow_net False); enable it.
    await services.workspaces.patch(1, WS, limits=Limits(allow_net=True))
    outcome = await services.packages.install(1, WS, ["six"])
    assert outcome.ok is True
    assert outcome.allow_net is True


async def test_packages_install_failure_is_honest_without_net(services, users):
    await _own(users, 1)
    # The default workspace has allow_net False; the fake returns exit_code 1.
    outcome = await services.packages.install(1, WS, ["six"])
    assert outcome.result.exit_code == 1
    assert outcome.ok is False
    assert outcome.allow_net is False
    # The failed install always carries output to explain (pip's own text).
    assert outcome.result.output


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_install_screen_renders_nonzero_exit_as_failure(locale):
    """The result screen must treat a non-zero exit_code as a FAILURE.

    This is the screen-level guard the bite test flips: if the screen ignores
    ``exit_code`` and always shows success, this goes red.
    """
    from cloudbot.domain.value_objects import InstallResult
    from cloudbot.presentation.screens import packages as pkg_screens
    from cloudbot.services.packages import InstallOutcome

    failed = InstallOutcome(
        result=InstallResult(
            exit_code=1,
            output="pip: error",
            stdout="",
            duration_ms=100,
            timed_out=False,
            truncated=False,
            target="/opt/python/private",
        ),
        allow_net=False,
    )
    html = pkg_screens.install_result_screen(failed, "six", WS, locale).doc.html
    ok_title = __import__("cloudbot.presentation.i18n.catalog", fromlist=["catalog"]).catalog.t(
        "package.result_ok_title", locale
    )
    fail_title = __import__("cloudbot.presentation.i18n.catalog", fromlist=["catalog"]).catalog.t(
        "package.result_failed_title", locale
    )
    assert fail_title in html, "a non-zero exit_code did not render as a failure"
    assert ok_title not in html, "a failed install was rendered as success"
    # The no-egress cause is explained in human words.
    assert "internet" in html.lower() or "إنترنت" in html


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_install_screen_renders_zero_exit_as_success(locale):
    from cloudbot.domain.value_objects import InstallResult
    from cloudbot.presentation.screens import packages as pkg_screens
    from cloudbot.services.packages import InstallOutcome

    ok = InstallOutcome(
        result=InstallResult(
            exit_code=0,
            output="ok",
            stdout="ok",
            duration_ms=100,
            timed_out=False,
            truncated=False,
            target="/opt/python/private",
        ),
        allow_net=True,
    )
    html = pkg_screens.install_result_screen(ok, "six", WS, locale).doc.html
    assert "✅" in html


@pytest.mark.parametrize("bad", ["", "   ", "six;rm -rf /", "$(reboot)", "a b", "six|cat"])
def test_package_name_validation_refuses_hostile_input(bad):
    with pytest.raises(InvalidInput):
        validate_package_name(bad)


@pytest.mark.parametrize("good", ["six", "requests", "scikit-learn", "pkg>=1.2", "pkg==1.0.0", "a.b_c-d"])
def test_package_name_validation_accepts_requirements(good):
    assert validate_package_name(good) == good.strip()


def test_parse_package_input_splits_on_space_and_comma():
    assert parse_package_input("requests six") == ["requests", "six"]
    assert parse_package_input("requests, six ,urllib3") == ["requests", "six", "urllib3"]
    assert parse_package_input("  ") == []


# -- tunnels -----------------------------------------------------------------
async def test_tunnels_publish_refused_for_foreign_user(services, users):
    await _own(users, 1)
    with pytest.raises(Exception) as exc:
        await services.tunnels.publish(2, WS, 8080)
    assert exc.value.code == "not_owned"


async def test_tunnels_publish_gate_allow_tunnels(services, users):
    """allow_net on, allow_tunnels off ⇒ the allow_tunnels gate."""
    await _own(users, 1)
    await services.workspaces.patch(1, WS, limits=Limits(allow_net=True, allow_tunnels=False))
    with pytest.raises(EngineRefused) as exc:
        await services.tunnels.publish(1, WS, 8080)
    assert exc.value.engine_code == "tunnels_disabled"
    assert exc.value.details.get("field") == "allow_tunnels"


async def test_tunnels_publish_gate_allow_net(services, users):
    """allow_net off ⇒ the allow_net gate, even with allow_tunnels on."""
    await _own(users, 1)
    await services.workspaces.patch(1, WS, limits=Limits(allow_net=False, allow_tunnels=True))
    with pytest.raises(EngineRefused) as exc:
        await services.tunnels.publish(1, WS, 8080)
    assert exc.value.engine_code == "tunnels_disabled"
    assert exc.value.details.get("field") == "allow_net"


async def test_tunnels_publish_ok_when_both_on(services, users):
    await _own(users, 1)
    await services.workspaces.patch(1, WS, limits=Limits(allow_net=True, allow_tunnels=True))
    tunnel = await services.tunnels.publish(1, WS, 8080)
    assert tunnel.url.startswith("https://")
    assert tunnel.port == 8080


@pytest.mark.parametrize("bad", [0, 80, 1023, 65536, 99999, -1])
def test_port_validation_range(bad):
    with pytest.raises(InvalidInput):
        validate_port(bad)


@pytest.mark.parametrize("good", [1024, 8080, 65535])
def test_port_validation_accepts_range(good):
    assert validate_port(good) == good


@pytest.mark.parametrize("bad", ["", "abc", "80x", "1.2"])
def test_parse_port_rejects_non_numeric(bad):
    with pytest.raises(InvalidInput):
        parse_port(bad)


def test_parse_port_accepts_documented_range():
    assert parse_port("8080") == 8080
    assert parse_port(" 65535 ") == 65535


# -- the three gates, each with its own user-facing message ------------------
@pytest.mark.parametrize(
    "details,expected_key",
    [
        ({"config": "tunnels.enabled", "field": "enabled"}, "err.tunnels_disabled.server"),
        ({"config": "limits.allow_tunnels", "field": "allow_tunnels"}, "err.tunnels_disabled.allow_tunnels"),
        ({"config": "limits.allow_net", "field": "allow_net"}, "err.tunnels_disabled.allow_net"),
    ],
)
def test_three_tunnel_gates_map_to_three_messages(details, expected_key):
    view = error_mapping.view_for_error(EngineRefused("tunnels_disabled", details=details))
    assert view.message_key == expected_key
    for locale in (Locale.EN, Locale.AR):
        html = error_mapping.error_screen(
            EngineRefused("tunnels_disabled", details=details), locale
        ).doc.html
        assert "{" not in html


def test_three_gate_messages_are_distinct():
    keys = {
        error_mapping.view_for_error(
            EngineRefused("tunnels_disabled", details=d)
        ).message_key
        for d in (
            {"config": "tunnels.enabled"},
            {"config": "limits.allow_tunnels"},
            {"config": "limits.allow_net"},
        )
    }
    assert len(keys) == 3


# -- ports -------------------------------------------------------------------
async def test_ports_list_refused_for_foreign_user(services, users):
    await _own(users, 1)
    with pytest.raises(Exception) as exc:
        await services.ports.list(2, WS)
    assert exc.value.code == "not_owned"


async def test_ports_list_returns_report_with_reachability(services, users):
    await _own(users, 1)
    report = await services.ports.list(1, WS)
    assert report.reachability
    assert report.count == len(report.ports)
    # The unix section is separate from ports.
    assert report.unix and report.unix_count == len(report.unix)
    # A UDP entry's state is the empty string, never invented.
    udp = [p for p in report.ports if p.protocol == "udp"]
    assert udp and udp[0].state == ""
