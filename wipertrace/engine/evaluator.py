"""Evaluate every rule against the event store, degrading gracefully."""

from __future__ import annotations

from wipertrace.config import EVIDENCE_CAP
from wipertrace.engine.context import EvalContext
from wipertrace.engine.signatures import SignatureDB
from wipertrace.models import Rule, RuleResult


def _missing(rule: Rule, available: set[str]) -> list[str]:
    """Requirements with no available alternative ('a|b' needs a or b)."""
    return [req for req in rule.requires if not any(a in available for a in req.split("|"))]


def evaluate(db: SignatureDB, ctx: EvalContext) -> list[RuleResult]:
    # correlate rules consume other rules' results, so run them last
    ordered = sorted(db.rules, key=lambda r: r.kind == "correlate")
    results: list[RuleResult] = []
    for rule in ordered:
        base = dict(
            rule_id=rule.id,
            tool=rule.tool,
            description=rule.description,
            weight=rule.weight,
            calibrated=rule.calibrated,
        )
        missing = _missing(rule, ctx.available)
        if missing:
            res = RuleResult(
                **base,
                status="not_evaluable",
                missing_artifacts=missing,
                detail=f"artifact(s) unavailable: {', '.join(missing)}",
            )
        else:
            out = db.matchers[rule.id].evaluate(ctx)
            evidence = [e.to_evidence() for e in out.events[:EVIDENCE_CAP]] + out.extra
            res = RuleResult(
                **base,
                status="matched" if out.matched else "not_matched",
                strength=out.strength if out.matched else 0.0,
                detail=out.detail,
                evidence=evidence if out.matched else evidence[:5],
                evidence_total=len(out.events) + len(out.extra),
                evidence_times=EvalContext.times(out.events) if out.matched else [],
            )
        ctx.results[rule.id] = res
        results.append(res)
    # restore signature-file order for reporting
    order = {r.id: i for i, r in enumerate(db.rules)}
    results.sort(key=lambda r: order[r.rule_id])
    return results
