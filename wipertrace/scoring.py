"""Aggregate rule results into ranked, confidence-scored tool verdicts.

support    = sum(weight * strength of matched rules) / sum(weight of evaluable rules)
coverage   = sum(weight of evaluable rules) / sum(weight of all rules of the tool)
confidence = support * sqrt(coverage)

The confidence formula is deliberately simple and transparent: it rewards
agreement between rules and discounts verdicts that rest on few available
artifacts. Thresholds and weights can be tuned against labelled data.
"""

from __future__ import annotations

import math
from typing import Any

from wipertrace.config import SCORING
from wipertrace.engine.signatures import SignatureDB
from wipertrace.models import ArtifactStatus, RuleResult, ToolVerdict


def _label(conf: float) -> str:
    if conf >= 0.75:
        return "high"
    if conf >= 0.5:
        return "medium"
    if conf > 0:
        return "low"
    return "none"


def score(db: SignatureDB, results: list[RuleResult]) -> list[ToolVerdict]:
    verdicts = []
    for tool in db.tools:
        rs = [r for r in results if r.tool == tool]
        if not rs:
            continue
        w_all = sum(r.weight for r in rs)
        evaluable = [r for r in rs if r.status != "not_evaluable"]
        w_eval = sum(r.weight for r in evaluable)
        w_hit = sum(r.weight * r.strength for r in evaluable if r.status == "matched")
        support = w_hit / w_eval if w_eval else 0.0
        coverage = w_eval / w_all if w_all else 0.0
        conf = support * math.sqrt(coverage)
        verdicts.append(
            ToolVerdict(
                tool=tool,
                display_name=db.display_name(tool),
                support=round(support, 4),
                coverage=round(coverage, 4),
                confidence=round(conf, 4),
                label=_label(conf),
                matched_rules=[r.rule_id for r in rs if r.status == "matched"],
                evaluable_rules=len(evaluable),
                total_rules=len(rs),
            )
        )
    verdicts.sort(key=lambda v: (v.support, v.confidence), reverse=True)
    return verdicts


def conclude(
    verdicts: list[ToolVerdict],
    artifacts: list[ArtifactStatus],
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    t = {**SCORING, **(thresholds or {})}
    if not any(a.state == "present" for a in artifacts):
        return {
            "status": "insufficient_artifacts",
            "summary": "No usable artifacts were supplied; no attribution is possible.",
            "tools": [],
        }
    detected = [v for v in verdicts if v.support >= t["detect_threshold"]]
    if not detected or detected[0].support < t["attribute_threshold"]:
        return {
            "status": "no_wiper_indicated",
            "summary": "No wiping tool signature reached the attribution threshold.",
            "tools": [v.tool for v in detected],
            "thresholds": t,
        }
    top = detected[0]
    runner = detected[1] if len(detected) > 1 else None
    if runner and top.support - runner.support < t["ambiguity_margin"]:
        tied = [v for v in detected if top.support - v.support < t["ambiguity_margin"]]
        return {
            "status": "ambiguous",
            "summary": "Evidence is consistent with more than one tool: "
            + ", ".join(f"{v.display_name} (support {v.support:.2f})" for v in tied)
            + ". Check which artifacts are missing; the discriminating rules may be not evaluable.",
            "tools": [v.tool for v in tied],
            "thresholds": t,
        }
    others = [v for v in detected[1:]]
    summary = (
        f"Attributed to {top.display_name}: support {top.support:.2f}, "
        f"coverage {top.coverage:.2f}, confidence {top.confidence:.2f} ({top.label})."
    )
    if others:
        summary += " Also consistent (possibly additional tools run): " + ", ".join(
            f"{v.display_name} ({v.support:.2f})" for v in others
        )
    return {"status": "attributed", "summary": summary, "tools": [top.tool] + [v.tool for v in others], "thresholds": t}
