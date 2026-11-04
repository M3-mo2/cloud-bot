"""Value objects: ids, limits, statuses, and small records.

Pure Python — no aiogram, no httpx, no SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class _PrefixedId(str):
    """A string id carrying a required prefix; rejects a wrong prefix."""

    prefix = ""

    def __new__(cls, value: str) -> "_PrefixedId":
        if not isinstance(value, str) or not value.startswith(cls.prefix):
            raise ValueError(f"{cls.__name__} must start with {cls.prefix!r}")
        return super().__new__(cls, value)


class WorkspaceId(_PrefixedId):
    prefix = "ws_"


class RunId(_PrefixedId):
    prefix = "run_"


class SessionId(_PrefixedId):
    prefix = "ses_"


class WorkspaceStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    LOCKED = "locked"
    DELETED = "deleted"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: object) -> "WorkspaceStatus":
        try:
            return cls(str(value))
        except ValueError:
            return cls.UNKNOWN


class SummaryState(str, Enum):
    ACTIVE = "active"
    EXPIRING_SOON = "expiring_soon"
    LOCKED = "locked"
    UNLIMITED = "unlimited"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: object) -> "SummaryState":
        try:
            return cls(str(value))
        except ValueError:
            return cls.UNKNOWN


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    EXITED = "exited"
    KILLED = "killed"
    TIMEOUT = "timeout"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    OOM = "oom"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: object) -> "RunStatus":
        try:
            return cls(str(value))
        except ValueError:
            return cls.UNKNOWN

    @property
    def is_terminal(self) -> bool:
        return self.value in TERMINAL_RUN_STATUSES


#: Terminal run statuses (INTEGRATION-GUIDE §4.2). Module-level so it does not
#: become an accidental enum member.
TERMINAL_RUN_STATUSES: frozenset[str] = frozenset(
    {
        "exited",
        "killed",
        "timeout",
        "failed",
        "interrupted",
        "oom",
        "rate_limited",
    }
)


class SessionStatus(str, Enum):
    RUNNING = "running"
    EXITED = "exited"
    KILLED = "killed"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: object) -> "SessionStatus":
        try:
            return cls(str(value))
        except ValueError:
            return cls.UNKNOWN

    @property
    def is_terminal(self) -> bool:
        """A session in this status will never run again."""
        return self in _TERMINAL_SESSION_STATUSES


#: Terminal session statuses (API.md §"Session statuses"). ``UNKNOWN`` is not
#: terminal: an unrecognised value keeps the view polling rather than freezing.
_TERMINAL_SESSION_STATUSES: frozenset["SessionStatus"] = frozenset(
    {
        SessionStatus.EXITED,
        SessionStatus.KILLED,
        SessionStatus.TIMED_OUT,
        SessionStatus.FAILED,
    }
)

#: The signals the engine accepts (INT|TERM|QUIT|HUP). Anything else must be
#: refused by the domain before it reaches the engine (INTEGRATION-GUIDE §5.2).
SESSION_SIGNALS: frozenset[str] = frozenset({"INT", "TERM", "QUIT", "HUP"})


@dataclass(frozen=True)
class Limits:
    cpu_cores: float = 1.0
    memory_mb: int = 512
    disk_mb: int = 256
    max_wall_time_sec: int = 300
    max_runs: int = 8
    allow_net: bool = False
    allow_unlimited_runs: bool = False
    allow_tunnels: bool = False
    write_rate_mb_s: int = 0

    @classmethod
    def from_dict(cls, data: dict | None) -> "Limits":
        data = dict(data or {})
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)

    def to_request(self) -> dict:
        return {
            "cpu_cores": self.cpu_cores,
            "memory_mb": self.memory_mb,
            "disk_mb": self.disk_mb,
            "max_wall_time_sec": self.max_wall_time_sec,
            "max_runs": self.max_runs,
            "allow_net": self.allow_net,
            "allow_unlimited_runs": self.allow_unlimited_runs,
            "allow_tunnels": self.allow_tunnels,
            "write_rate_mb_s": self.write_rate_mb_s,
        }


@dataclass(frozen=True)
class WorkspaceSummary:
    state: SummaryState
    age_sec: int
    created_at: str
    runs_total: int
    runs_active: int
    disk_used_bytes: int
    disk_limit_bytes: int
    profile: str
    subscription_state: str | None = None
    subscription_remaining_sec: int | None = None


@dataclass(frozen=True)
class Money:
    amount: float
    currency: str = "USD"


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool


@dataclass(frozen=True)
class LifecycleResult:
    changed: bool
    stopped_run_count: int = 0
    stopped_session_count: int = 0
    stopped_process_count: int = 0
    stopped_run_ids: tuple[str, ...] = ()
    stopped_session_ids: tuple[str, ...] = ()
    stopped_process_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class LogChunk:
    data: str
    offset: int
    next: int
    eof: bool
    truncated: bool


@dataclass(frozen=True)
class FilePage:
    path: str
    entries: tuple["FileEntry", ...]
    count: int
    total: int
    truncated: bool
    offset: int = 0
    limit: int = 20


@dataclass(frozen=True)
class FileContent:
    path: str
    content: str
    encoding: str
    size: int
    total_size: int
    whole_file: bool
    sha256: str
    content_type: str
    truncated: bool
    binary: bool
    start_line: int | None = None
    end_line: int | None = None
    total_lines: int | None = None


@dataclass(frozen=True)
class RunPage:
    runs: tuple["Run", ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class SessionPage:
    sessions: tuple["TerminalSession", ...]
    total: int
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True)
class ResourceUsage:
    disk_bytes: int
    disk_limit_bytes: int
    disk_percent: float
    runs_total: int
    active_runs: int
    max_runs: int
    disk_complete: bool = True


# -- Phase 2a: Python packages (pip inside the workspace) --------------------
@dataclass(frozen=True)
class Package:
    name: str
    version: str = ""


@dataclass(frozen=True)
class PackageList:
    packages: tuple[Package, ...]
    count: int
    truncated: bool
    shared_enabled: bool
    shared_available: bool
    shared_path: str
    private_path: str


@dataclass(frozen=True)
class InstallResult:
    """The outcome of ``POST /packages/install``.

    ``exit_code`` is the honest bit: a non-zero code is a **failed** install (the
    reason is in ``output``). Never render this as success just because the HTTP
    call returned ``200``.
    """

    exit_code: int
    output: str
    stdout: str
    duration_ms: int
    timed_out: bool
    truncated: bool
    target: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


# -- Phase 2b: tunnels (ephemeral public URL) --------------------------------
@dataclass(frozen=True)
class Tunnel:
    id: str
    workspace_id: str
    port: int
    url: str
    status: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class TunnelList:
    tunnels: tuple[Tunnel, ...]

    @property
    def count(self) -> int:
        return len(self.tunnels)


# -- Phase 2c: port discovery ------------------------------------------------
@dataclass(frozen=True)
class PortEntry:
    port: int
    protocol: str
    address: str
    scope: str
    state: str
    attributed: bool
    pid: int | None = None
    kind: str = ""
    label: str = ""
    argv: tuple[str, ...] = ()
    owners: tuple[int, ...] = ()
    inode: int | None = None


@dataclass(frozen=True)
class UnixSocket:
    path: str
    type: str
    state: str
    attributed: bool
    pid: int | None = None
    kind: str = ""


@dataclass(frozen=True)
class PortReport:
    workspace_id: str
    ports: tuple[PortEntry, ...]
    unix: tuple[UnixSocket, ...]
    count: int
    unix_count: int
    complete: bool
    notes: tuple[str, ...]
    pids_scanned: int
    fds_scanned: int
    reachability: str
