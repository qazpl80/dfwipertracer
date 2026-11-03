from __future__ import annotations

import json
from pathlib import Path

from wipertrace.models import AnalysisResult


def write_json(result: AnalysisResult, path: Path) -> Path:
    path.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")
    return path
