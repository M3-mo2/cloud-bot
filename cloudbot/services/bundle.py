"""A bundle of the domain services, injected into handler data."""

from __future__ import annotations

from dataclasses import dataclass

from cloudbot.services.account import AccountService
from cloudbot.services.execution import ExecutionService
from cloudbot.services.files import FilesService
from cloudbot.services.ownership import OwnershipService
from cloudbot.services.sessions import SessionsService
from cloudbot.services.workspaces import WorkspacesService


@dataclass(frozen=True)
class Services:
    account: AccountService
    workspaces: WorkspacesService
    files: FilesService
    execution: ExecutionService
    sessions: SessionsService
    ownership: OwnershipService
