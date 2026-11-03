"""Human-readable report with the full evidence trail."""

from __future__ import annotations

from pathlib import Path

from wipertrace.models import AnalysisResult

STATUS_ICON = {"matched": "MATCH", "not_matched": "-", "not_evaluable": "n/a"}
MD_EVIDENCE_ROWS = 8


def _esc(v) -> str:
    return str(v if v is not None else "").replace("|", "\\|")


def render_markdown(r: AnalysisResult) -> str:
    c = r.conclusion
    out: list[str] = []
    out.append(f"# WiperTrace report — {r.case['name']}\n")
    out.append(f"*Analysed {r.case['analysed_at']} · WiperTrace {r.case['wipertrace_version']} · "
               f"signature DB {r.signature_db.get('db_version')} (sha256 {r.signature_db['sha256'][:12]}…)*\n")

    out.append("## Conclusion\n")
    out.append(f"**{c['status'].replace('_', ' ').upper()}** — {c['summary']}\n")

    out.append("## Ranked candidates\n")
    out.append("| # | Tool | Support | Coverage | Confidence | Matched rules |")
    out.append("|---|---|---|---|---|---|")
    for i, v in enumerate(r.verdicts, 1):
        out.append(f"| {i} | {v.display_name} | {v.support:.2f} | {v.coverage:.2f} | "
                   f"{v.confidence:.2f} ({v.label}) | {len(v.matched_rules)}/{v.evaluable_rules} evaluable "
                   f"({v.total_rules} total) |")
    out.append("")

    out.append("## Artifacts\n")
    out.append("| Artifact | State | Events | Files |")
    out.append("|---|---|---|---|")
    for a in r.artifacts:
        files = ", ".join(Path(f).name for f in a.files) or "—"
        out.append(f"| {a.artifact} | {a.state} | {a.events} | {_esc(files)} |")
    out.append("")

    out.append("## Rule results\n")
    by_tool: dict[str, list] = {}
    for rr in r.rule_results:
        by_tool.setdefault(rr.tool, []).append(rr)
    names = {v.tool: v.display_name for v in r.verdicts}
    for tool, rules in by_tool.items():
        out.append(f"### {names.get(tool, tool)}\n")
        out.append("| Rule | Status | Weight | Detail |")
        out.append("|---|---|---|---|")
        for rr in rules:
            flag = "" if rr.calibrated else " *(provisional)*"
            out.append(f"| `{rr.rule_id}` {_esc(rr.description)}{flag} | {STATUS_ICON[rr.status]} | "
                       f"{rr.weight:g} | {_esc(rr.detail)} |")
        out.append("")
        for rr in rules:
            if rr.status != "matched" or not rr.evidence:
                continue
            out.append(f"<details><summary>Evidence for {rr.rule_id} "
                       f"({rr.evidence_total} item(s), first {min(MD_EVIDENCE_ROWS, len(rr.evidence))} shown)"
                       f"</summary>\n")
            if "pair" in rr.evidence[0]:
                for e in rr.evidence[:MD_EVIDENCE_ROWS]:
                    out.append(f"- {e['pair'][0]} ↔ {e['pair'][1]}: closest gap {e['gap_seconds']} s "
                               f"({e['times'][0]} / {e['times'][1]})")
            else:
                out.append("| Source | Time (UTC) | Entry | Name | Ops | CSV row |")
                out.append("|---|---|---|---|---|---|")
                for e in rr.evidence[:MD_EVIDENCE_ROWS]:
                    out.append(f"| {e['source']} | {e['timestamp'] or ''} | {e['file_ref'] if e['file_ref'] is not None else ''} | "
                               f"{_esc(e['name'])} | {', '.join(e['ops'])} | "
                               f"{_esc(Path(e['csv']).name if e['csv'] else '')}:{e['row']} |")
            out.append("\n</details>\n")

    out.append("## Limitations\n")
    for lim in r.limitations:
        out.append(f"- {lim}")
    out.append("")

    out.append("## Input manifest\n")
    out.append("| Artifact | File | Size | SHA-256 |")
    out.append("|---|---|---|---|")
    for m in r.case["inputs"]:
        out.append(f"| {m['artifact']} | {_esc(Path(m['path']).name)} | {m['size']} | `{m['sha256']}` |")
    out.append("")
    return "\n".join(out)
