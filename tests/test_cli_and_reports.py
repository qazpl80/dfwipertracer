import json

from wipertrace.cli import main


def test_demo_then_analyze_writes_reports(tmp_path, capsys):
    case = tmp_path / "case"
    out = tmp_path / "out"
    assert main(["demo", "eraser", "--out", str(case)]) == 0
    assert main(["analyze", str(case), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "ATTRIBUTED" in printed and "Eraser" in printed

    data = json.loads((out / "wipertrace_report.json").read_text())
    assert data["conclusion"]["tools"][0] == "eraser"
    assert all(len(m["sha256"]) == 64 for m in data["case"]["inputs"])
    md = (out / "wipertrace_report.md").read_text()
    assert "## Limitations" in md and "ERA-USN-001" in md


def test_rules_command(capsys):
    assert main(["rules"]) == 0
    assert "SDEL-USN-001" in capsys.readouterr().out


def test_analyze_requires_input(capsys):
    assert main(["analyze"]) == 2


def test_column_override(tmp_path):
    """A $LogFile CSV with non-default headers is read after mapping its columns."""
    case = tmp_path / "case"
    main(["demo", "sdelete", "--out", str(case)])
    lf = case / "LogFile.csv"
    lf.write_text(lf.read_text().replace("lf_RedoOperation", "Redo").replace("lf_MFTReference", "Ref"
                  ).replace("lf_FileName", "FName"))
    cols = tmp_path / "cols.yaml"
    cols.write_text("logfile:\n  redo_op: Redo\n  file_ref: Ref\n  name: FName\n")
    out = tmp_path / "out"
    assert main(["analyze", str(case), "--columns", str(cols), "--out", str(out), "--quiet"]) == 0
    data = json.loads((out / "wipertrace_report.json").read_text())
    lf_status = next(a for a in data["artifacts"] if a["artifact"] == "logfile")
    assert lf_status["state"] == "present"
    assert "SDEL-LOG-001" in data["verdicts"][0]["matched_rules"]
