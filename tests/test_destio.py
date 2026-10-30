"""CSV / JSON import + export of export destinations (Toolkit-compatible)."""

from __future__ import annotations

import json

from deid import config, destio
from deid.model import Node


def _nodes():
    return [
        Node(name="Research VNA", aetitle="RVNA", host="192.0.2.10", port=104),
        Node(name="Trial PACS", aetitle="TESTPACS", host="192.0.2.20",
             port=11112, calling_aetitle="DEID"),
    ]


def test_csv_round_trip(tmp_path):
    out = tmp_path / "destinations.csv"
    destio.export_csv(_nodes(), out)
    # Header + two rows.
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "name,aetitle,host,port,calling_aetitle,group"
    assert len(lines) == 3
    res = destio.import_csv_file(out)
    assert [n.name for n in res.nodes] == ["Research VNA", "Trial PACS"]
    trial = next(n for n in res.nodes if n.name == "Trial PACS")
    assert trial.aetitle == "TESTPACS" and trial.port == 11112
    assert trial.calling_aetitle == "DEID"
    assert res.skipped_nonstorage == 0 and res.warnings == []


def test_json_round_trip(tmp_path):
    out = tmp_path / "destinations.json"
    destio.export_json(_nodes(), out)
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert {d["aetitle"] for d in raw} == {"RVNA", "TESTPACS"}
    res = destio.import_any(out)
    assert len(res.nodes) == 2


def test_import_filters_nonstorage_and_dedupes_csv(tmp_path):
    p = tmp_path / "mixed.csv"
    p.write_text(
        "name,aetitle,host,port,calling_aetitle,group\n"
        "Research VNA,RVNA,192.0.2.10,104,,PACS / Storage\n"
        "Dup,RVNA,192.0.2.10,104,,PACS / Storage\n"      # same identity -> 1
        "RIS,WL,192.0.2.11,105,,Worklist / MPPS\n"       # non-storage -> skipped
        "Router,HL7R,192.0.2.12,2575,,HL7 / MLLP\n",     # non-storage -> skipped
        encoding="utf-8")
    res = destio.import_csv_file(p)
    assert len(res.nodes) == 1
    assert res.nodes[0].aetitle == "RVNA"
    assert res.skipped_nonstorage == 2


def test_import_headerless_csv(tmp_path):
    p = tmp_path / "bare.csv"
    p.write_text("Research VNA,RVNA,192.0.2.10,104\n", encoding="utf-8")
    res = destio.import_csv_file(p)
    assert len(res.nodes) == 1 and res.nodes[0].host == "192.0.2.10"


def test_import_json_filters_nonstorage(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps([
        {"name": "VNA", "aetitle": "RVNA", "host": "192.0.2.10", "port": 104,
         "group": "PACS / Storage"},
        {"name": "WL", "aetitle": "WL", "host": "192.0.2.11", "port": 105,
         "group": "Worklist / MPPS"},
    ]), encoding="utf-8")
    res = destio.import_json_file(p)
    assert [n.name for n in res.nodes] == ["VNA"]
    assert res.skipped_nonstorage == 1


def test_import_warns_on_invalid(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("name,aetitle,host,port\n"
                 "Bad,RVNA,10.0.0..5,104\n", encoding="utf-8")
    res = destio.import_csv_file(p)
    assert len(res.nodes) == 1
    assert any("Bad" in w for w in res.warnings)


def test_merge_into_saved_dedupes_and_disambiguates(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path / "deid"))
    config.save_destinations([
        Node(name="Research VNA", aetitle="RVNA", host="192.0.2.10", port=104),
    ])
    res = destio.ImportResult(nodes=[
        # Same identity as saved -> skipped.
        Node(name="Research VNA", aetitle="RVNA", host="192.0.2.10", port=104),
        # New endpoint, name collides -> added with host appended.
        Node(name="Research VNA", aetitle="RVNA2", host="192.0.2.30", port=104),
        # Genuinely new.
        Node(name="Trial PACS", aetitle="TESTPACS", host="192.0.2.20",
             port=11112),
    ], warnings=[], skipped_nonstorage=0, source="x")
    added, skipped = destio.merge_into_saved(res)
    assert added == 2 and skipped == 1
    saved = config.load_destinations()
    names = {n.name for n in saved}
    assert "Trial PACS" in names
    assert "Research VNA (192.0.2.30)" in names


def test_import_bad_json_raises(tmp_path):
    p = tmp_path / "obj.json"
    p.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    try:
        destio.import_json_file(p)
    except ValueError:
        return
    raise AssertionError("expected ValueError for non-list JSON")
