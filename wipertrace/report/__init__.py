"""Report writers — stage 4 of the pipeline."""

from pathlib import Path

from wipertrace.models import AnalysisResult
from wipertrace.report.json_report import write_json
from wipertrace.report.markdown_report import render_markdown

WRITERS = {"json", "md"}


def write_reports(result: AnalysisResult, out_dir: Path, formats: set[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    if "json" in formats:
        written.append(write_json(result, out_dir / "wipertrace_report.json"))
    if "md" in formats:
        p = out_dir / "wipertrace_report.md"
        p.write_text(render_markdown(result), encoding="utf-8")
        written.append(p)
    return written


__all__ = ["WRITERS", "render_markdown", "write_json", "write_reports"]
