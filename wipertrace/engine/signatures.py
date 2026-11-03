"""Load and validate the signature database (YAML)."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from wipertrace import ARTIFACTS
from wipertrace.engine.matchers import KINDS, Matcher, RuleParamError
from wipertrace.models import Rule
from wipertrace.util import sha256_file

SUPPORTED_SCHEMA = 1


class SignatureError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("Signature database is invalid:\n  - " + "\n  - ".join(errors))
        self.errors = errors


@dataclass
class SignatureDB:
    meta: dict[str, Any]
    tools: dict[str, dict[str, Any]]
    rules: list[Rule]
    matchers: dict[str, Matcher] = field(default_factory=dict)

    def rules_for(self, tool: str) -> list[Rule]:
        return [r for r in self.rules if r.tool == tool]

    def display_name(self, tool: str) -> str:
        return self.tools.get(tool, {}).get("display_name", tool)


def default_path() -> Path:
    return Path(str(resources.files("wipertrace") / "signatures" / "default.yaml"))


def load_signatures(path: Path | None = None) -> SignatureDB:
    path = path or default_path()
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}

    errors: list[str] = []
    if doc.get("schema_version") != SUPPORTED_SCHEMA:
        errors.append(f"schema_version must be {SUPPORTED_SCHEMA}")
    tools: dict[str, dict] = doc.get("tools") or {}
    if not tools:
        errors.append("no tools defined")

    rules: list[Rule] = []
    matchers: dict[str, Matcher] = {}
    seen: set[str] = set()
    raw_rules = doc.get("rules") or []
    for i, raw in enumerate(raw_rules):
        rid = raw.get("id") or f"<rule #{i}>"
        if rid in seen:
            errors.append(f"{rid}: duplicate id")
        seen.add(rid)
        if raw.get("tool") not in tools:
            errors.append(f"{rid}: unknown tool {raw.get('tool')!r}")
        kind = raw.get("kind")
        if kind not in KINDS:
            errors.append(f"{rid}: unknown kind {kind!r} (expected one of {sorted(KINDS)})")
            continue
        requires = raw.get("requires") or []
        for req in requires:
            for alt in str(req).split("|"):
                if alt not in ARTIFACTS:
                    errors.append(f"{rid}: requires unknown artifact {alt!r}")
        if kind != "correlate" and not requires:
            errors.append(f"{rid}: 'requires' must list the artifact(s) the rule needs")
        weight = raw.get("weight", 1.0)
        if not isinstance(weight, (int, float)) or weight <= 0:
            errors.append(f"{rid}: weight must be a positive number")
        try:
            matchers[rid] = KINDS[kind](raw.get("params") or {})
        except (RuleParamError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{rid}: {exc}")
            continue
        rules.append(
            Rule(
                id=rid,
                tool=raw.get("tool"),
                description=raw.get("description", ""),
                kind=kind,
                requires=[str(r) for r in requires],
                weight=float(weight),
                params=raw.get("params") or {},
                calibrated=bool(raw.get("calibrated", True)),
                references=list(raw.get("references") or []),
            )
        )

    # correlate rules: references must exist, be non-correlate, same tool;
    # their `requires` is the union of the referenced rules' requirements.
    by_id = {r.id: r for r in rules}
    for r in rules:
        if r.kind != "correlate":
            continue
        reqs: list[str] = []
        for ref in r.params.get("rules", []):
            target = by_id.get(ref)
            if target is None:
                errors.append(f"{r.id}: references unknown rule {ref}")
            elif target.kind == "correlate":
                errors.append(f"{r.id}: cannot reference another correlate rule ({ref})")
            else:
                if target.tool != r.tool:
                    errors.append(f"{r.id}: references rule {ref} of a different tool")
                reqs.extend(q for q in target.requires if q not in reqs)
        r.requires = reqs

    if errors:
        raise SignatureError(errors)

    meta = {
        "path": str(path),
        "sha256": sha256_file(path),
        "schema_version": doc.get("schema_version"),
        "db_version": doc.get("db_version"),
        "derived_from": doc.get("derived_from"),
        "rules": len(rules),
        "uncalibrated_rules": sum(1 for r in rules if not r.calibrated),
    }
    return SignatureDB(meta=meta, tools=tools, rules=rules, matchers=matchers)
