"""CTP anonymizer-script import/export."""

from __future__ import annotations

from deid.engine import ctpscript
from deid.engine.anon_profiles import AnonRule
from deid.project import Project

SAMPLE = """<script>
  <e en="T" t="00100010" n="PatientName">@empty()</e>
  <e en="T" t="00100020" n="PatientID">@hashptid(SITE,ptid)</e>
  <e en="T" t="00080050" n="AccessionNumber">@remove()</e>
  <e en="T" t="00081030" n="StudyDescription">STUDY-X</e>
  <e en="F" t="00100040" n="PatientSex">@remove()</e>
  <k en="T" t="00080060">keep modality</k>
  <r en="T" t="privategroups">Remove private groups</r>
  <p t="UIDROOT">1.2.3</p>
</script>"""


def test_parse_maps_rules_and_reports_rest():
    out = ctpscript.parse_script(SAMPLE)
    got = {(r.tag, r.action, r.value) for r in out["rules"]}
    assert ("00100010", "BLANK", "") in got          # @empty
    assert ("00080050", "REMOVE", "") in got         # @remove
    assert ("00081030", "REPLACE", "STUDY-X") in got  # literal
    assert ("00080060", "KEEP", "") in got           # <k>
    # disabled element (en="F") is skipped
    assert not any(r.tag == "00100040" for r in out["rules"])
    assert out["remove_private"] is True
    # @hashptid and the UIDROOT param are reported, not silently dropped
    assert any("00100020" in u for u in out["unmapped"])
    assert any("UIDROOT" in u for u in out["unmapped"])


def test_round_trip_project_to_script_and_back():
    p = Project(name="Cohort", remove_private=True, rules=[
        AnonRule("00100010", "BLANK"),
        AnonRule("00080050", "REMOVE"),
        AnonRule("00081030", "REPLACE", "ANON"),
        AnonRule("00080020", "KEEP"),
    ])
    xml = ctpscript.to_script(p)
    out = ctpscript.parse_script(xml)
    got = {(r.tag, r.action, r.value) for r in out["rules"]}
    assert ("00100010", "BLANK", "") in got
    assert ("00080050", "REMOVE", "") in got
    assert ("00081030", "REPLACE", "ANON") in got
    assert ("00080020", "KEEP", "") in got
    assert out["remove_private"] is True


def test_export_notes_unrepresented_features():
    p = Project(name="Y", basic_profile=True, calculated_dates=True,
                pseudonymize=True, pseudonym_tags=["PatientID"])
    xml = ctpscript.to_script(p)
    assert "Basic Confidentiality Profile" in xml
    assert "date shifting" in xml
    assert "pseudonyms" in xml


def test_parse_tolerates_comments_and_unknown():
    text = ("<script><!-- a comment -->"
            "<e en='T' t='00100010'>@remove()</e>"
            "<weird t='x'>?</weird></script>")
    out = ctpscript.parse_script(text)
    assert any(r.tag == "00100010" for r in out["rules"])
    assert any("weird" in u for u in out["unmapped"])
