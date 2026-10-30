"""Matching OCR'd pixel text against a study's own DICOM metadata."""

from __future__ import annotations

from deid.engine import phimatch
from deid.engine import ocr
from conftest import make_ct


def _phi():
    ds = make_ct(PatientName="DOE^JANE", PatientID="MRN12345",
                 AccessionNumber="ACC9876", StudyDate="20200131",
                 PatientBirthDate="19800101")
    return phimatch.build_phi(ds)


def test_build_phi_harvests_values():
    phi = _phi()
    assert "DOE" in phi.name_tokens and "JANE" in phi.name_tokens
    assert "MRN12345" in phi.ids and "ACC9876" in phi.ids
    assert "20200131" in phi.date_tokens


def test_match_names_ids_dates():
    phi = _phi()
    assert phimatch.match_token("DOE", phi) == "name"
    assert phimatch.match_token("jane", phi) == "name"          # case-insensitive
    assert phimatch.match_token("MRN12345", phi) == "id"
    assert phimatch.match_token("ACC9876", phi) == "id"
    assert phimatch.match_token("2020-01-31", phi) == "date"    # separators
    assert phimatch.match_token("01/31/2020", phi) == "date"


def test_scan_parameters_do_not_match():
    phi = _phi()
    for token in ("120", "5mm", "kVp", "W/L", "3.0", "SE"):
        assert phimatch.match_token(token, phi) is None


def test_id_substring_match_requires_length():
    phi = _phi()
    # The full MRN embedded in a longer OCR token still matches.
    assert phimatch.match_token("ID:MRN12345", phi) == "id"
    # A short fragment must not.
    short = phimatch.build_phi(make_ct(PatientID="7", AccessionNumber="",
                                       PatientName="", StudyDate="",
                                       PatientBirthDate=""))
    assert phimatch.match_token("7", short) is None             # below MIN_ID_LEN


def test_match_phrases_spelled_date():
    phi = _phi()                                                # 2020-01-31
    toks = ["TAKEN", "JAN", "31", "2020", "AT", "NOON"]
    spans = phimatch.match_phrases(toks, phi)
    assert (1, 4) in spans                                      # JAN 31 2020


def test_classify_words_tags_phi_and_suspects():
    phi = _phi()
    words = [
        {"text": "DOE", "conf": 90, "box": (0, 10, 0, 30), "line": (1, 1, 1)},
        {"text": "120", "conf": 90, "box": (0, 10, 40, 60), "line": (1, 1, 1)},
        {"text": "555123456", "conf": 88, "box": (0, 10, 0, 40),
         "line": (1, 1, 2)},
        {"text": "Smithfield", "conf": 80, "box": (0, 10, 0, 40),
         "line": (1, 1, 3)},
        {"text": "5mm", "conf": 80, "box": (0, 10, 0, 20), "line": (1, 1, 4)},
    ]
    tagged = {w["text"]: w["category"] for w in ocr.classify_words(words, phi)}
    assert tagged.get("DOE") == "name"
    assert tagged.get("555123456") == "suspect"     # long number, not in meta
    assert tagged.get("Smithfield") == "suspect"    # name-like, not in meta
    assert "120" not in tagged and "5mm" not in tagged   # scan params ignored
