"""Python-package use-cases (Phase 2a): list, install, remove.

Policy lives here: the ownership gate first, then package-name validation (a name
reaches ``pip`` inside the sandbox, so shell-hostile input is refused before the
engine is called), then the call. An install is **honest about failure**: the
engine returns ``200`` with a non-zero ``exit_code`` for a failed install, and
:class:`InstallOutcome` carries ``allow_net`` so the presentation layer can say
*why* (no egress ⇒ pip cannot reach an index) instead of only echoing pip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cloudbot.core.errors import InvalidInput
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.value_objects import InstallResult, PackageList, WorkspaceId
from cloudbot.services.ownership import OwnershipService

#: A pip requirement safe to pass through: a name (optionally a version spec).
#: Deliberately rejects whitespace and every shell metacharacter — the value
#: reaches a ``pip install`` invocation, so ``six; rm -rf /`` must never pass.
_SAFE_PACKAGE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._+-]*"  # package name
    r"(?:[<>=!~]=?[A-Za-z0-9._+*,-]+)?$"  # optional version specifier
)


@dataclass(frozen=True)
class InstallOutcome:
    """The engine's install result plus the workspace's egress setting."""

    result: InstallResult
    allow_net: bool

    @property
    def ok(self) -> bool:
        return self.result.ok


class PackagesService:
    def __init__(
        self,
        engine: EnginePort,
        ownership: OwnershipService,
        *,
        subscription: "object | None" = None,
    ) -> None:
        self._engine = engine
        self._ownership = ownership
        self._subscription = subscription

    async def _assert_usable(self, tg_id: int, ws_id: WorkspaceId) -> None:
        """Refuse install/remove on a lapsed plan; listing stays allowed."""
        if self._subscription is not None:
            await self._subscription.assert_usable(tg_id, ws_id)

    async def meta(self) -> EngineMeta:
        return await self._engine.meta()

    async def list(self, tg_id: int, ws_id: WorkspaceId) -> PackageList:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.list_packages(ws_id)

    async def install(
        self, tg_id: int, ws_id: WorkspaceId, packages: list[str]
    ) -> InstallOutcome:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_usable(tg_id, ws_id)
        cleaned = [validate_package_name(name) for name in packages]
        # Fetch the workspace so a failed install can be explained honestly
        # ("this workspace has no internet") without a second round trip at
        # render time.
        workspace = await self._engine.get_workspace(ws_id)
        result = await self._engine.install_packages(ws_id, cleaned)
        return InstallOutcome(result=result, allow_net=workspace.limits.allow_net)

    async def remove(self, tg_id: int, ws_id: WorkspaceId, name: str) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_usable(tg_id, ws_id)
        cleaned = validate_package_name(name)
        await self._engine.remove_package(ws_id, cleaned)

    async def remove_all(self, tg_id: int, ws_id: WorkspaceId) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_usable(tg_id, ws_id)
        await self._engine.remove_all_packages(ws_id)


def validate_package_name(name: str) -> str:
    """Return the cleaned name or raise ``InvalidInput`` (never reaches ``pip``)."""
    cleaned = (name or "").strip()
    if not cleaned:
        raise InvalidInput("package", "empty")
    if not _SAFE_PACKAGE.match(cleaned):
        raise InvalidInput("package", "invalid")
    return cleaned


def parse_package_input(text: str) -> list[str]:
    """Split a typed line into one or more package names (whitespace/commas)."""
    parts = re.split(r"[\s,]+", (text or "").strip())
    return [p for p in parts if p]
