from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Protocol


class ActivityReporter(Protocol):
    def identity(self, series_id: int, title: Optional[str] = None) -> None: ...

    def waiting(self, seconds: float) -> None: ...

    def moving(self, directories: Iterable[Path]) -> None: ...


@dataclass(frozen=True)
class StageIssue:
    code: str
    message: str
    remedy: str


@dataclass
class StageResult:
    changed: int = 0
    unchanged: int = 0
    failed: int = 0
    outputs: list[str] = field(default_factory=list)
    issues: list[StageIssue] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        if self.failed:
            return "partial" if self.changed else "failed"
        return "completed" if self.changed else "unchanged"

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "failed": self.failed,
            "outputs": self.outputs[:10],
            "issues": [asdict(issue) for issue in self.issues[:10]],
        }
