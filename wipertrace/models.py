"""Core data contracts passed between pipeline stages.

Stage 2 (extractors) produce Events; stage 3 (engine) produces RuleResults;
stage 4 (scoring) produces ToolVerdicts and an AnalysisResult.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from wipertrace.util import iso


@dataclass(slots=True, frozen=True)
class Origin:
    """Audit pointer: which parser CSV and which row produced an event."""

    artifact: str
    path: str
    row: int  # 1-based data row (header is row 0)


@dataclass(slots=True)
class Event:
    """Normalised event — the common schema all extractors emit."""

    source: str                       # usnjrnl | mft | logfile | prefetch | amcache
    ops: frozenset[str]               # canonical UPPER_SNAKE operations
    timestamp: datetime | None
    name: str | None
    file_ref: int | None = None       # MFT entry number
    seq: int | None = None            # MFT sequence number
    parent_ref: int | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    origin: Origin | None = None
    order: int = 0                    # USN / LSN / row order within the source

    @property
    def file_key(self) -> tuple[int | None, int | None]:
        return (self.file_ref, self.seq)

    def get(self, fieldname: str) -> Any:
        """Field lookup used by rule predicates (top-level first, then attrs)."""
        if fieldname == "name_len":
            return len(self.name) if self.name else 0
        if fieldname in ("name", "file_ref", "seq", "parent_ref", "source", "timestamp", "order"):
            return getattr(self, fieldname)
        if fieldname == "ops":
            return self.ops
        return self.attrs.get(fieldname)

    def to_evidence(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "ops": sorted(self.ops),
            "timestamp": iso(self.timestamp),
            "name": self.name,
            "file_ref": self.file_ref,
            "seq": self.seq,
            "csv": self.origin.path if self.origin else None,
            "row": self.origin.row if self.origin else None,
        }


@dataclass(slots=True)
class ArtifactStatus:
    artifact: str
    state: str                        # present | empty | missing | error
    files: list[str] = field(default_factory=list)
    events: int = 0
    message: str = ""


@dataclass(slots=True)
class Rule:
    id: str
    tool: str
    description: str
    kind: str
    requires: list[str]               # each item "a" or "a|b" (any of)
    weight: float
    params: dict[str, Any]
    calibrated: bool = True
    references: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RuleResult:
    rule_id: str
    tool: str
    description: str
    status: str                       # matched | not_matched | not_evaluable
    weight: float
    strength: float = 0.0
    calibrated: bool = True
    detail: str = ""
    missing_artifacts: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    evidence_total: int = 0
    # Internal: timestamps of all evidence events, used by `correlate` rules.
    evidence_times: list[datetime] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("evidence_times")
        return d


@dataclass(slots=True)
class ToolVerdict:
    tool: str
    display_name: str
    support: float
    coverage: float
    confidence: float
    label: str                        # high | medium | low | none
    matched_rules: list[str]
    evaluable_rules: int
    total_rules: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AnalysisResult:
    case: dict[str, Any]
    signature_db: dict[str, Any]
    artifacts: list[ArtifactStatus]
    rule_results: list[RuleResult]
    verdicts: list[ToolVerdict]
    conclusion: dict[str, Any]
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "signature_db": self.signature_db,
            "conclusion": self.conclusion,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "artifacts": [asdict(a) for a in self.artifacts],
            "rule_results": [r.to_dict() for r in self.rule_results],
            "limitations": self.limitations,
        }
