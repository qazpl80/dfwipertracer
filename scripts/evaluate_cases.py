"""Run WiperTrace over a folder of labelled cases and report how often it is right.

Each case directory needs a ground_truth.json with either `tool_id` (harness
runs, see harness_to_case.py) or `scenario` (synthetic / Generate-WiperDataset
cases). Controls (tool ids starting with "control", or no tool) are expected to
produce `no_wiper_indicated`.

Usage:
    python scripts/evaluate_cases.py CASES_ROOT [--drop logfile] [--csv results.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from wipertrace.case import Case
from wipertrace.pipeline import analyze


def expected_tool(gt: dict) -> str | None:
    tool = gt.get("tool_id") or gt.get("expected_tool") or gt.get("scenario")
    if not tool or str(tool).startswith("control") or tool in ("explorer_delete", "rename_churn"):
        return None
    return tool


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--drop", action="append", default=[], help="artifact to ignore (repeatable)")
    ap.add_argument("--signatures", type=Path)
    ap.add_argument("--csv", type=Path, help="write one row per case")
    a = ap.parse_args(argv)

    rows = []
    for gt_path in sorted(a.root.rglob("ground_truth.json")):
        case_dir = gt_path.parent
        gt = json.loads(gt_path.read_text(encoding="utf-8-sig"))
        want = expected_tool(gt)
        r = analyze(Case.discover(case_dir, drop=a.drop), signatures_path=a.signatures)
        status = r.conclusion["status"]
        got = r.conclusion["tools"][0] if status == "attributed" else None
        top = r.verdicts[0] if r.verdicts else None
        second = r.verdicts[1] if len(r.verdicts) > 1 else None
        ok = (got == want) if want else status == "no_wiper_indicated"
        rows.append(dict(case=case_dir.name, expected=want or "-", status=status, attributed=got or "-",
                         top=top.tool if top else "-", top_support=top.support if top else 0,
                         second=second.tool if second else "-",
                         second_support=second.support if second else 0,
                         also=",".join(r.conclusion.get("tools", [])[1:]), correct=ok,
                         matched=",".join(top.matched_rules) if top else ""))
        mark = "ok " if ok else "XX "
        print(f"{mark}{case_dir.name:40s} want={want or '-':12s} {status:20s} "
              f"top={rows[-1]['top']}({rows[-1]['top_support']:.2f}) "
              f"2nd={rows[-1]['second']}({rows[-1]['second_support']:.2f})"
              + (f" also={rows[-1]['also']}" if rows[-1]["also"] else ""))

    n_ok = sum(r["correct"] for r in rows)
    print(f"\n{n_ok}/{len(rows)} cases correct" + (f" (dropped: {', '.join(a.drop)})" if a.drop else ""))
    by_tool = Counter(r["expected"] for r in rows)
    ok_tool = Counter(r["expected"] for r in rows if r["correct"])
    for t in sorted(by_tool):
        print(f"  {t:12s} {ok_tool[t]}/{by_tool[t]}")
    clean = sum(1 for r in rows if r["correct"] and not r["also"])
    print(f"  attributed with no other tool listed: {clean}/{len(rows)}")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return 0 if n_ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
