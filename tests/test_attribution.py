"""End-to-end attribution on synthetic cases, including artifact ablation."""

import pytest

TOOLS = ["sdelete", "cipher", "bleachbit6", "bleachbit4", "eraser"]


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("hard", [False, True])
def test_each_tool_is_attributed(run_case, tool, hard):
    r = run_case(tool, hard=hard)
    assert r.conclusion["status"] == "attributed", r.conclusion
    assert r.verdicts[0].tool == tool
    assert r.verdicts[0].support == 1.0


@pytest.mark.parametrize("control", ["explorer_delete", "rename_churn"])
def test_controls_produce_no_attribution(run_case, control):
    r = run_case(control)
    assert r.conclusion["status"] == "no_wiper_indicated", r.conclusion
    assert all(v.support < 0.35 for v in r.verdicts)


@pytest.mark.parametrize("tool", TOOLS)
def test_journal_lost_still_attributes_via_logfile_and_prefetch(run_case, tool):
    """$LogFile + Prefetch alone must still identify the tool."""
    r = run_case(tool, drop=["usnjrnl"])
    assert r.verdicts[0].tool == tool
    assert r.conclusion["status"] in ("attributed", "ambiguous")
    assert tool in r.conclusion["tools"]


def test_sdelete_without_usnjrnl_is_still_clear(run_case):
    r = run_case("sdelete", drop=["usnjrnl"])
    assert r.conclusion["status"] == "attributed"
    top = r.verdicts[0]
    assert top.coverage < 1.0          # degraded, and the report says so
    assert any("usnjrnl" in lim for lim in r.limitations)


def test_prefetch_only_cannot_split_bleachbit_versions(run_case):
    """Same executable name for 4.x and 6.x: the honest answer is 'ambiguous'."""
    r = run_case("bleachbit4", drop=["usnjrnl", "logfile", "mft"])
    assert r.conclusion["status"] == "ambiguous"
    assert set(r.conclusion["tools"]) == {"bleachbit4", "bleachbit6"}


def test_rules_marked_not_evaluable_when_artifact_missing(run_case):
    r = run_case("eraser", drop=["logfile"])
    era_log = next(x for x in r.rule_results if x.rule_id == "ERA-LOG-001")
    assert era_log.status == "not_evaluable"
    assert era_log.missing_artifacts == ["logfile"]


def test_no_artifacts_is_insufficient(run_case):
    r = run_case("sdelete", drop=["usnjrnl", "logfile", "mft", "prefetch", "amcache"])
    assert r.conclusion["status"] == "insufficient_artifacts"


def test_evidence_points_back_to_csv_rows(run_case):
    r = run_case("sdelete")
    usn = next(x for x in r.rule_results if x.rule_id == "SDEL-USN-001")
    assert usn.evidence and all(e["csv"] and e["row"] >= 1 for e in usn.evidence)
    cor = next(x for x in r.rule_results if x.rule_id == "SDEL-COR-001")
    assert cor.status == "matched" and cor.evidence[0]["gap_seconds"] <= 900
