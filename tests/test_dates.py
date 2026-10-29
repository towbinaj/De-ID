"""Date-handling modes: baseline (configurable), jitter, round."""

from __future__ import annotations

from datetime import date

from deid.engine.processing import ReceiverConfig, Processor
from deid.project import Project
from conftest import make_ct


def _d(s: str) -> date:
    s = str(s)
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def test_baseline_is_configurable():
    cfg = ReceiverConfig(basic_profile=True, date_mode="baseline",
                         date_baseline="20000101")
    out = Processor(cfg).process(
        make_ct(PatientBirthDate="19800101", StudyDate="20200101"))
    assert str(out.PatientBirthDate) == "20000101"      # DOB -> chosen anchor
    assert str(out.StudyDate).startswith("2040")        # anchor + ~40y age


def test_jitter_is_consistent_and_preserves_age():
    cfg = ReceiverConfig(basic_profile=True, date_mode="jitter",
                         date_jitter_days=10, date_jitter_salt="proj")
    kw = dict(PatientID="MRN1", PatientBirthDate="19800101",
              StudyDate="20200110")
    o1 = Processor(cfg).process(make_ct(**kw))
    o2 = Processor(cfg).process(make_ct(**kw))
    # Same patient -> same shift across runs/objects.
    assert str(o1.StudyDate) == str(o2.StudyDate)
    # Real date hidden but within the ±N window...
    assert abs((_d(o1.StudyDate) - _d("20200110")).days) <= 10
    # ...and age preserved exactly (DOB shifted by the same offset).
    before = (_d("20200110") - _d("19800101")).days
    after = (_d(o1.StudyDate) - _d(o1.PatientBirthDate)).days
    assert before == after


def test_jitter_zero_window_is_noop_shift():
    cfg = ReceiverConfig(basic_profile=True, date_mode="jitter",
                         date_jitter_days=0)
    out = Processor(cfg).process(make_ct(StudyDate="20200110"))
    assert str(out.StudyDate) == "20200110"


def test_round_to_year_and_month():
    cfg = ReceiverConfig(basic_profile=True, date_mode="round",
                         date_round="year")
    out = Processor(cfg).process(
        make_ct(StudyDate="20200715", PatientBirthDate="19800715"))
    assert str(out.StudyDate) == "20200101"
    assert str(out.PatientBirthDate) == "19800101"

    cfg2 = ReceiverConfig(basic_profile=True, date_mode="round",
                          date_round="month")
    out2 = Processor(cfg2).process(make_ct(StudyDate="20200715"))
    assert str(out2.StudyDate) == "20200701"


def test_project_maps_date_mode_and_legacy_flags():
    p = Project(name="Cohort", date_mode="jitter", date_jitter_days=5)
    cfg = p.to_receiver_config()
    assert cfg.date_mode == "jitter"
    assert cfg.date_jitter_days == 5
    assert cfg.date_jitter_salt == "Cohort"      # stable per-project
    # Legacy project (only the old flag set) still resolves to baseline.
    legacy = Project(name="Old", calculated_dates=True)
    assert legacy.effective_date_mode() == "baseline"
    assert legacy.to_receiver_config().date_mode == "baseline"
