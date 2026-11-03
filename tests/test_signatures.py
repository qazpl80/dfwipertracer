from pathlib import Path

import pytest

from wipertrace.engine.signatures import SignatureError, load_signatures


def test_default_db_loads_and_validates():
    db = load_signatures()
    assert set(db.tools) == {"sdelete", "cipher", "bleachbit6", "bleachbit4", "eraser"}
    ids = [r.id for r in db.rules]
    assert len(ids) == len(set(ids))
    # correlate rules inherit requirements from the rules they reference
    cor = next(r for r in db.rules if r.id == "SDEL-COR-001")
    assert cor.requires == ["usnjrnl", "prefetch|amcache"]


def test_no_sdelete_rule_requires_delete_record():
    """SDelete's final FILE_DELETE is missing after a hard shutdown."""
    db = load_signatures()
    for r in db.rules_for("sdelete"):
        assert "FILE_DELETE" not in str(r.params).upper()


def test_invalid_db_reports_all_errors(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        """
schema_version: 1
tools: {x: {display_name: X}}
rules:
  - {id: A, tool: x, kind: nope, requires: [mft], weight: 1}
  - {id: B, tool: y, kind: match, requires: [disk], weight: -1, params: {sources: [mft]}}
  - {id: C, tool: x, kind: correlate, weight: 1, params: {rules: [A, Z], window_seconds: 5}}
""",
        encoding="utf-8",
    )
    with pytest.raises(SignatureError) as exc:
        load_signatures(bad)
    text = str(exc.value)
    for fragment in ("unknown kind", "unknown tool", "unknown artifact", "weight", "unknown rule Z"):
        assert fragment in text
