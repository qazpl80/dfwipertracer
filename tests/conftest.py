from pathlib import Path

import pytest

from wipertrace.case import Case
from wipertrace.pipeline import analyze
from wipertrace.testing.synth import write_case


@pytest.fixture
def run_case(tmp_path: Path):
    """Generate a synthetic case and analyse it. Returns the AnalysisResult."""

    def _run(scenario: str, drop=None, hard=False, n_files=10, seed=1):
        out = tmp_path / f"{scenario}_{'hard' if hard else 'clean'}_{seed}"
        write_case(scenario, out, n_files=n_files, hard_shutdown=hard, seed=seed)
        return analyze(Case.discover(out, drop=drop or []))

    return _run
