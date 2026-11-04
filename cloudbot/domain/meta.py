"""``EngineMeta`` — the engine's self-description (``/v1/meta``).

Only the parts the bot derives behaviour from are modelled; unknown keys are
ignored so a newer engine never breaks the bot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cloudbot.domain.value_objects import Limits


@dataclass(frozen=True)
class ProfileInfo:
    name: str
    description: str = ""
    commands: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class Profiles:
    default: str
    ready: bool = True
    profiles: tuple[ProfileInfo, ...] = ()


@dataclass(frozen=True)
class Network:
    enabled: bool = False
    available: bool = False
    ready: bool = False


@dataclass(frozen=True)
class Ports:
    enabled: bool = False


@dataclass(frozen=True)
class Metrics:
    enabled: bool = False
    interval_seconds: float = 3.0
    min_interval_sec: float = 1.0
    max_interval_sec: float = 300.0


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int = 20000
    max_total_mb: int = 2048
    max_file_mb: int = 512
    max_ratio: int = 200
    upload_mb: int = 50
    overwrite: bool = False
    skip_junk: bool = True


@dataclass(frozen=True)
class Archives:
    enabled: bool = False
    formats: tuple[str, ...] = ()
    read_only: tuple[str, ...] = ()
    limits: ArchiveLimits = field(default_factory=ArchiveLimits)


@dataclass(frozen=True)
class Subscriptions:
    enabled: bool = False
    default_duration: str = ""
    max_duration: str = ""


@dataclass(frozen=True)
class Tunnels:
    enabled: bool = False
    ready: bool = False
    max_per_workspace: int = 2
    url_timeout_sec: int = 60


@dataclass(frozen=True)
class WorkspaceLimits:
    enabled: bool = False
    mechanism: str = "counter"


@dataclass(frozen=True)
class SessionConfig:
    """The engine's session limits (``sessions:`` config).

    The engine's ``/v1/meta`` does not currently advertise these, so the values
    are the documented defaults (API.md §"Session limits and config"). A real
    ``sessions`` block, if the engine ever emits one, overrides them.
    """

    max_per_workspace: int = 4
    idle_timeout_sec: int = 900
    lifetime_sec: int = 3600
    output_kb: int = 1024
    max_input_kb: int = 64
    shell: str = "/bin/sh"
    cols_default: int = 80
    rows_default: int = 24


@dataclass(frozen=True)
class WriteRate:
    enabled: bool = False
    server_max_mb_s: int = 0
    default_mb_s: int = 0


@dataclass(frozen=True)
class Capability:
    enabled: bool = False
    ready: bool = False
    reason: str = ""


@dataclass(frozen=True)
class EngineMeta:
    version: str
    api_version: str
    runtimes: tuple[str, ...] = ()
    interpreters: tuple[str, ...] = ()
    runnable_extensions: tuple[str, ...] = ()
    profiles: Profiles = field(default_factory=lambda: Profiles(default="python"))
    limits_defaults: Limits = field(default_factory=Limits)
    limits_maxima: Limits = field(default_factory=Limits)
    network: Network = field(default_factory=Network)
    ports: Ports = field(default_factory=Ports)
    metrics: Metrics = field(default_factory=Metrics)
    archives: Archives = field(default_factory=Archives)
    subscriptions: Subscriptions = field(default_factory=Subscriptions)
    tunnels: Tunnels = field(default_factory=Tunnels)
    sessions: SessionConfig = field(default_factory=SessionConfig)
    workspace_limits: WorkspaceLimits = field(default_factory=WorkspaceLimits)
    write_rate: WriteRate = field(default_factory=WriteRate)
    egress_decoy: Capability = field(default_factory=Capability)
    identity_masking: Capability = field(default_factory=Capability)
    provider_tools_hidden: Capability = field(default_factory=Capability)

    @property
    def default_profile(self) -> str:
        return self.profiles.default

    @property
    def has_multiple_profiles(self) -> bool:
        return len(self.profiles.profiles) > 1

    @property
    def upload_mb(self) -> int:
        return self.archives.limits.upload_mb
