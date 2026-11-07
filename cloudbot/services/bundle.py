"""A bundle of the domain services, injected into handler data."""

from __future__ import annotations

from dataclasses import dataclass

from cloudbot.services.account import AccountService
from cloudbot.services.admin import AdminService
from cloudbot.services.archives import ArchivesService
from cloudbot.services.backup import BackupService
from cloudbot.services.billing import BillingService
from cloudbot.services.broadcast import BroadcastService
from cloudbot.services.events import EventsService
from cloudbot.services.execution import ExecutionService
from cloudbot.services.files import FilesService
from cloudbot.services.forcejoin import ForceJoinService
from cloudbot.services.metrics import MetricsService
from cloudbot.services.moderation import ModerationService
from cloudbot.services.notifier import NotifierService
from cloudbot.services.ownership import OwnershipService
from cloudbot.services.packages import PackagesService
from cloudbot.services.ports import PortsService
from cloudbot.services.processes import ProcessesService
from cloudbot.services.session import SessionService
from cloudbot.services.sessions import SessionsService
from cloudbot.services.tunnels import TunnelsService
from cloudbot.services.workspaces import WorkspacesService


@dataclass(frozen=True)
class Services:
    account: AccountService
    billing: BillingService
    backup: BackupService
    workspaces: WorkspacesService
    session: SessionService
    files: FilesService
    execution: ExecutionService
    sessions: SessionsService
    ownership: OwnershipService
    packages: PackagesService
    tunnels: TunnelsService
    ports: PortsService
    metrics: MetricsService
    processes: ProcessesService
    archives: ArchivesService
    events: EventsService
    admin: AdminService
    notifier: NotifierService
    broadcast: BroadcastService
    forcejoin: ForceJoinService
    moderation: ModerationService
