"""The one place a handler asks "what is installed here?" for the offer.

Every surface that shows a failed command's output builds the missing-package
offer, and every one of them must answer it with the workspace's **real** package
list so a package that is already on disk is never offered again. Keeping the
fetch in one helper is what stops the four call sites from drifting apart: the
terminal view, the exec result, the file-run logs and the return-to-origin
rebuild all call :func:`package_names`.

The helper **degrades, never breaks the screen**: a failed fetch returns
``None``, which the screen treats as "unknown" and offers everything, exactly as
it did before the installed check existed.
"""

from __future__ import annotations

import structlog

from cloudbot.core.errors import DomainError
from cloudbot.domain.python_packages import normalize_distribution_name
from cloudbot.services.bundle import Services

log = structlog.get_logger("cloudbot.package_offer")


async def package_names(
    services: Services, tg_id: int, ws_id
) -> frozenset[str] | None:
    """The workspace's installed pip names (PEP 503-normalised), or ``None``.

    ``None`` means the list could not be fetched. It is deliberately **not** an
    empty set: empty means "nothing is installed, offer everything", while
    ``None`` means "we do not know" — and only ``None`` makes the offer fall back
    to the pre-check behaviour. Catching the domain failure here keeps a flaky
    packages endpoint from taking down the screen the user is reading.
    """
    try:
        pkg_list = await services.packages.list(tg_id, ws_id)
    except DomainError as exc:
        log.info("package_offer.list_unavailable", ws_id=str(ws_id), error=exc.code)
        return None
    return frozenset(
        normalize_distribution_name(package.name) for package in pkg_list.packages
    )
