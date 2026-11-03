"""Command-line interface.

  wipertrace analyze CASE_DIR [--usnjrnl F ...] [--drop logfile] [--out DIR]
  wipertrace collect IMAGE --out CASE_DIR [--mftecmd P --pecmd P --amcacheparser P]
  wipertrace rules [--signatures F]            list / validate the signature DB
  wipertrace demo TOOL --out DIR               generate a synthetic case
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from wipertrace import ARTIFACTS, __version__
from wipertrace.util import parse_ts

EXIT_OK, EXIT_ERROR = 0, 2


def _add_analyze(sub) -> None:
    p = sub.add_parser("analyze", help="attribute wiping activity from parser CSVs")
    p.add_argument("case_dir", nargs="?", type=Path, help="directory containing parser CSV outputs")
    for a in ARTIFACTS:
        p.add_argument(f"--{a}", type=Path, action="append", metavar="CSV",
                       help=f"explicit {a} CSV (overrides discovery; repeatable)")
    p.add_argument("--drop", choices=ARTIFACTS, action="append", default=[],
                   help="ignore an artifact class (ablation / robustness testing)")
    p.add_argument("--signatures", type=Path, help="signature DB YAML (default: bundled)")
    p.add_argument("--columns", type=Path, help="YAML of CSV column-name overrides")
    p.add_argument("--since", help="only events at/after this UTC time (YYYY-MM-DD HH:MM:SS)")
    p.add_argument("--until", help="only events at/before this UTC time")
    p.add_argument("--out", type=Path, default=Path("wipertrace_out"), help="report directory")
    p.add_argument("--format", default="json,md", help="comma list of: json, md")
    p.add_argument("--quiet", action="store_true", help="do not print the summary")


def _add_collect(sub) -> None:
    p = sub.add_parser("collect", help="extract NTFS artifacts from a raw/E01 image (read-only)")
    p.add_argument("image", type=Path)
    p.add_argument("--out", type=Path, required=True, help="case directory to create")
    p.add_argument("--volume", type=int, default=0, help="index of the NTFS volume (default 0)")
    p.add_argument("--mftecmd", type=Path, help="path to MFTECmd(.exe|.dll)")
    p.add_argument("--pecmd", type=Path, help="path to PECmd(.exe|.dll)")
    p.add_argument("--amcacheparser", type=Path, help="path to AmcacheParser(.exe|.dll)")


def _add_rules(sub) -> None:
    p = sub.add_parser("rules", help="validate and list the signature database")
    p.add_argument("--signatures", type=Path)


def _add_demo(sub) -> None:
    from wipertrace.testing.synth import SCENARIOS

    p = sub.add_parser("demo", help="write a synthetic case for a tool (testing and demonstration)")
    p.add_argument("scenario", choices=sorted(SCENARIOS))
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--files", type=int, default=10, help="number of wiped files")
    p.add_argument("--hard-shutdown", action="store_true", help="simulate power cut before final records")
    p.add_argument("--seed", type=int, default=1)


def cmd_analyze(args) -> int:
    from wipertrace.case import Case
    from wipertrace.pipeline import analyze
    from wipertrace.report import WRITERS, write_reports

    explicit = {a: getattr(args, a) for a in ARTIFACTS if getattr(args, a)}
    if args.case_dir is None and not explicit:
        print("error: give a case directory or at least one --<artifact> CSV", file=sys.stderr)
        return EXIT_ERROR
    formats = {f.strip() for f in args.format.split(",") if f.strip()}
    if formats - WRITERS:
        print(f"error: unknown format(s) {formats - WRITERS}", file=sys.stderr)
        return EXIT_ERROR
    since, until = parse_ts(args.since), parse_ts(args.until)
    if (args.since and not since) or (args.until and not until):
        print("error: --since/--until must look like 2026-10-14 09:00:00", file=sys.stderr)
        return EXIT_ERROR

    case = Case.discover(args.case_dir, explicit, drop=args.drop)
    result = analyze(case, args.signatures, args.columns, since, until)
    written = write_reports(result, args.out, formats)

    if not args.quiet:
        print(f"\n{result.conclusion['status'].upper()}: {result.conclusion['summary']}\n")
        print(f"{'tool':<26}{'support':>9}{'coverage':>10}{'conf':>7}  matched")
        for v in result.verdicts:
            print(f"{v.display_name:<26}{v.support:>9.2f}{v.coverage:>10.2f}{v.confidence:>7.2f}  "
                  f"{','.join(v.matched_rules) or '-'}")
        print("\nartifacts: " + ", ".join(f"{a.artifact}={a.state}" for a in result.artifacts))
        for p in written:
            print(f"wrote {p}")
    return EXIT_OK


def cmd_collect(args) -> int:
    from wipertrace.acquisition.eztools import run_eztools
    from wipertrace.acquisition.image import extract_artifacts

    manifest = extract_artifacts(args.image, args.out, args.volume)
    print(f"extracted {len(manifest['files'])} file(s) to {args.out / 'raw'}")
    for note in run_eztools(args.out / "raw", args.out, args.mftecmd, args.pecmd, args.amcacheparser):
        print(f"note: {note}")
    return EXIT_OK


def cmd_rules(args) -> int:
    from wipertrace.engine.signatures import load_signatures

    db = load_signatures(args.signatures)
    print(f"signature DB {db.meta['db_version']}  ({db.meta['rules']} rules, "
          f"{db.meta['uncalibrated_rules']} provisional)  {db.meta['path']}\n")
    for tool, info in db.tools.items():
        print(f"{info.get('display_name', tool)}  [{tool}]  versions: {', '.join(info.get('versions_observed', []))}")
        for r in db.rules_for(tool):
            flag = "" if r.calibrated else "  (provisional)"
            print(f"   {r.id:<14} w={r.weight:<4g} {r.kind:<15} needs {'+'.join(r.requires):<26} {r.description}{flag}")
        print()
    return EXIT_OK


def cmd_demo(args) -> int:
    from wipertrace.testing.synth import write_case

    paths = write_case(args.scenario, args.out, n_files=args.files, hard_shutdown=args.hard_shutdown, seed=args.seed)
    print(f"wrote synthetic '{args.scenario}' case to {args.out}:")
    for p in paths:
        print(f"  {p.name}")
    print(f"\nnow run:  wipertrace analyze {args.out}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wipertrace", description="Attribute NTFS file-wiping activity to a tool.")
    ap.add_argument("--version", action="version", version=f"wipertrace {__version__}")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="command", required=True)
    _add_analyze(sub)
    _add_collect(sub)
    _add_rules(sub)
    _add_demo(sub)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    handlers = {"analyze": cmd_analyze, "collect": cmd_collect, "rules": cmd_rules, "demo": cmd_demo}
    try:
        return handlers[args.command](args)
    except Exception as exc:  # surface clean errors on the CLI, full trace with -v
        if args.verbose:
            raise
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
