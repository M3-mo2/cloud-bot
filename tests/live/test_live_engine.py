"""One live contract test against a real engine, gated behind an env var.

Creates a workspace, writes and reads a file, runs ``exec``, then deletes the
workspace and asserts it is gone. Requires ``CLOUDBOT_LIVE_ENGINE=1`` and a
reachable engine; never runs in CI by default. Secrets are read from files and
never printed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cloudbot.adapters.cloud_service.engine import EngineAdapter
from cloudbot.core.errors import EngineRefused
from cloudbot.domain.value_objects import WorkspaceId

pytestmark = pytest.mark.live

LIVE = os.environ.get("CLOUDBOT_LIVE_ENGINE") == "1"
ENGINE_URL = os.environ.get("CLOUDBOT_LIVE_ENGINE_URL", "http://127.0.0.1:8098")
KEY_FILE = os.environ.get("CLOUDBOT_LIVE_KEY_FILE", "/home/zerops/cloud-service-public/api_key")


def _read_key() -> str:
    return Path(KEY_FILE).read_text().strip()


@pytest.mark.skipif(not LIVE, reason="set CLOUDBOT_LIVE_ENGINE=1 to run against a real engine")
async def test_live_workspace_file_exec_cycle():
    adapter = EngineAdapter.create(ENGINE_URL, _read_key(), timeout_s=30.0, max_retries=0)
    created = None
    try:
        meta = await adapter.meta(refresh=True)
        assert meta.api_version == "v1"

        created = await adapter.create_workspace(name="cloudbot-live-test", profile=None, limits=None)
        assert str(created.id).startswith("ws_")

        # Write then read a file.
        await adapter.write_file(created.id, "/hello.txt", "hello live\n")
        content = await adapter.read_file(created.id, "/hello.txt", meta=True)
        assert "hello live" in content.content

        # Run a quick synchronous command.
        result = await adapter.exec(created.id, ["/bin/sh", "-c", "echo live-ok"])
        assert result.exit_code == 0
        assert "live-ok" in result.stdout
    finally:
        if created is not None:
            await adapter.delete_workspace(created.id)
            with pytest.raises(EngineRefused):
                await adapter.get_workspace(WorkspaceId(str(created.id)))
        await adapter.aclose()
