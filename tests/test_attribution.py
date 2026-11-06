"""End-to-end attribution on synthetic cases, including artifact ablation."""

import pytest

TOOLS = ["sdelete", "cipher", "bleachbit6", "bleachbit4", "eraser", "ccleaner", "bcwipe"]


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


@pytest.mark.parametrize("tool", TOOLS)
def test_no_other_tool_is_listed(run_case, tool):
    """A clean attribution: no second tool reaches the detection threshold."""
    r = run_case(tool)
    assert r.conclusion["tools"] == [tool], r.conclusion


def test_ccleaner_is_not_taken_for_sdelete_or_bleachbit6(run_case):
    """CCleaner's ZZZZZZ.ZZZ is SDelete's last letter name, and it is one same-length
    rename like BleachBit 6; neither rule may fire on it."""
    r = run_case("ccleaner")
    by_id = {x.rule_id: x.status for x in r.rule_results}
    assert by_id["SDEL-USN-001"] == "not_matched"
    assert by_id["BB6-USN-001"] == "not_matched"


def test_bcwipe_is_not_taken_for_eraser(run_case):
    """Both tools leave 1601 timestamps; only Eraser's reach $FILE_NAME."""
    r = run_case("bcwipe")
    by_id = {x.rule_id: x.status for x in r.rule_results}
    assert by_id["ERA-MFT-001"] == "not_matched"
    assert by_id["BCW-MFT-002"] == "matched"


def test_ccleaner_prefetch_alone_is_low_confidence(run_case):
    """CCleaner also runs for ordinary cleaning: with only Prefetch the verdict rests on
    one rule, and the report must say so."""
    r = run_case("ccleaner", drop=["usnjrnl", "mft", "logfile"])
    top = r.verdicts[0]
    assert top.tool == "ccleaner" and top.coverage < 0.5 and top.label == "low"
    assert any("CCleaner" in lim for lim in r.limitations)


def test_ccleaner_ordinary_delete_is_not_a_wipe(tmp_path):
    """CCleaner ran but deleted normally (secure deletion off): journals present, so
    no wiper is indicated even though CCLEANER64.EXE is in Prefetch."""
    from wipertrace.case import Case
    from wipertrace.pipeline import analyze
    from wipertrace.testing.synth import CaseBuilder

    b = CaseBuilder(3)
    b.background()
    files = b.corpus(10)
    b.prefetch("CCLEANER64.EXE", [b.t])
    for e, name, size in files:
        b.usn(name, e, "FileDelete|Close")
        b.mft(e, name, size, in_use=False)
    b.write(tmp_path / "c", "ccleaner_ordinary", {"expected_tool": None})
    r = analyze(Case.discover(tmp_path / "c"))
    assert r.conclusion["status"] == "no_wiper_indicated", r.conclusion
