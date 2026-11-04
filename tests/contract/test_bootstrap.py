"""Composition root: build the container and dispatcher offline (mock engine)."""

from __future__ import annotations

import pytest

from cloudbot.bootstrap.app import build_bot, build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.bootstrap import lifecycle
from cloudbot.core.config import Settings


@pytest.mark.asyncio
async def test_build_and_startup_with_mock_engine():
    settings = Settings(
        mode="dev",
        bot_token="123:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        _env_file=None,
    )
    container = build(settings)
    try:
        await lifecycle.startup(container)  # migrations + mock /v1/meta
        bot = build_bot(settings)
        dispatcher = build_dispatcher(container, bot)
        # Routers are registered.
        assert dispatcher.sub_routers
        # Meta came from the fake engine.
        meta = await container.engine.meta()
        assert meta.api_version == "v1"
    finally:
        await container.aclose()


def test_fake_engine_implements_port_surface():
    from cloudbot.adapters.cloud_service.fake import FakeEngine

    engine = FakeEngine()
    for method in (
        "meta",
        "create_workspace",
        "list_workspaces",
        "get_workspace",
        "list_files",
        "read_file",
        "exec",
        "create_run",
        "run_logs",
        "purge_runs",
    ):
        assert hasattr(engine, method)
