"""The doctor's finding model: severity, location, report, JSON and text output."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Iterable

class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"error": 0, "warning": 1, "info": 2}[self.value]

@dataclass(frozen=True)
class Finding:
    check: str
    severity: Severity
    message: str
    location: str = ""
    detail: str = ""

@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    root: str = ""

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def extend(self, findings: Iterable[Finding]) -> None:
        self.findings.extend(findings)

    @property
    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for finding in self.findings:
            out[finding.severity.value] += 1
        return out

    @property
    def has_errors(self) -> bool:
        return any(f.severity is Severity.ERROR for f in self.findings)

    def sorted(self) -> list[Finding]:
        return sorted(
            self.findings,
            key=lambda f: (f.severity.rank, f.check, f.location, f.message),
        )

    # -- output --------------------------------------------------------------
    def as_dict(self) -> dict:
        return {
            "root": self.root,
            "counts": self.counts,
            "findings": [
                {
                    "check": f.check,
                    "severity": f.severity.value,
                    "message": f.message,
                    "location": f.location,
                    "detail": f.detail,
                }
                for f in self.sorted()
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2)

    def to_text(self) -> str:
        lines: list[str] = []
        counts = self.counts
        lines.append(
            f"doctor: {len(self.findings)} finding(s) — "
            f"{counts['error']} error, {counts['warning']} warning, {counts['info']} info"
        )
        for severity in (Severity.ERROR, Severity.WARNING, Severity.INFO):
            group = [f for f in self.sorted() if f.severity is severity]
            if not group:
                continue
            lines.append("")
            lines.append(f"[{severity.value.upper()}] {len(group)}")
            for f in group:
                where = f" {f.location}" if f.location else ""
                lines.append(f"  {f.check}{where}: {f.message}")
                if f.detail:
                    lines.append(f"      {f.detail}")
        return "\n".join(lines)

__all__ = ["Finding", "Report", "Severity"]
