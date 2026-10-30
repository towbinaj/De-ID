"""Match OCR'd pixel text against a study's OWN DICOM metadata.

The point of this, versus blanking every text region: burned-in overlays usually
mix PHI (patient name, MRN, accession, dates) with legitimate text you want to
KEEP (scan parameters like ``120 kV``, ``5.0 mm``, ``W/L``). By comparing OCR
tokens to the actual identifier values in this file's header, we flag/redact the
text that matches real PHI and leave expected acquisition text alone.

It is built from the ORIGINAL dataset (before de-identification blanks the
header), so the caller captures a :class:`PhiSet` up front.

Recall gap (handled by the caller's "suspect" pass, not here): text that is PHI
but is NOT in this file's metadata - a tech's handwritten note, a second
patient's name on a scanned form, a date in no DICOM tag - cannot match a value
we don't have. Metadata-matching gives precision; pattern-based suspects give the
safety net.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# PHI-bearing tags we harvest values from. Person-name and id tags plus dates.
PERSON_NAME_TAGS = ("PatientName", "OtherPatientNames", "ReferringPhysicianName",
                    "PerformingPhysicianName", "OperatorsName",
                    "RequestingPhysician", "ResponsiblePerson")
ID_TAGS = ("PatientID", "OtherPatientIDs", "AccessionNumber", "StudyID")
DATE_TAGS = ("PatientBirthDate", "StudyDate", "SeriesDate", "AcquisitionDate",
             "ContentDate")

_MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
           "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
_MON = [m[:3] for m in _MONTHS]

# Minimum lengths, to avoid matching trivially short tokens (e.g. a 1-char id).
MIN_NAME_TOKEN = 2
MIN_ID_LEN = 3


def _alnum(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _words(s: str) -> list[str]:
    """Uppercase alphanumeric 'words' of a string (for phrase matching)."""
    return re.findall(r"[A-Z0-9]+", str(s or "").upper())


def _date_forms(yyyymmdd: str) -> tuple[set[str], list[tuple[str, ...]]]:
    """Single-token (alnum) and multi-word (tuple) renderings of a DA value.

    Returns ``(tokens, phrases)`` where ``tokens`` are alnum strings like
    ``01012020`` / ``20200101`` and ``phrases`` are word tuples like
    ``("JAN", "1", "2020")`` for separator/month spellings.
    """
    s = re.sub(r"[^0-9]", "", str(yyyymmdd or ""))
    if len(s) != 8:
        return set(), []
    y, m, d = s[:4], s[4:6], s[6:8]
    yy, mi, di = y[2:], str(int(m)), str(int(d))     # 20, 1, 1
    tokens = {s, f"{m}{d}{y}", f"{d}{m}{y}", f"{m}{d}{yy}", f"{d}{m}{yy}"}
    phrases: list[tuple[str, ...]] = []
    for a, b, c in ((m, d, y), (d, m, y), (m, d, yy), (d, m, yy),
                    (mi, di, y), (di, mi, y), (y, m, d)):
        phrases.append((a, b, c))
    mon_name = _MONTHS[int(m) - 1]
    mon_abbr = _MON[int(m) - 1]
    for mo in (mon_name, mon_abbr):
        phrases.append((mo, di, y))        # JAN 1 2020
        phrases.append((di, mo, y))        # 1 JAN 2020
        phrases.append((mo, f"{di},", y))  # JAN 1, 2020 -> words drops comma
    return tokens, phrases


@dataclass
class PhiSet:
    name_tokens: set = field(default_factory=set)   # {"DOE", "JANE"}
    ids: set = field(default_factory=set)           # {"MRN123", "ACC9"}
    date_tokens: set = field(default_factory=set)   # {"20200101", "01012020"}
    date_phrases: list = field(default_factory=list)  # [("JAN","1","2020"), ...]

    def is_empty(self) -> bool:
        return not (self.name_tokens or self.ids or self.date_tokens
                    or self.date_phrases)


def build_phi(ds, *, name_tags=PERSON_NAME_TAGS, id_tags=ID_TAGS,
              date_tags=DATE_TAGS) -> PhiSet:
    """Harvest identifier values from ``ds``'s metadata into a :class:`PhiSet`."""
    phi = PhiSet()
    for tag in name_tags:
        val = getattr(ds, tag, None)
        if val is None:
            continue
        for part in _words(str(val)):
            if len(part) >= MIN_NAME_TOKEN and not part.isdigit():
                phi.name_tokens.add(part)
    for tag in id_tags:
        val = getattr(ds, tag, None)
        for one in (val if isinstance(val, (list, tuple)) else [val]):
            a = _alnum(one)
            if len(a) >= MIN_ID_LEN:
                phi.ids.add(a)
    for tag in date_tags:
        toks, phrases = _date_forms(getattr(ds, tag, "") or "")
        phi.date_tokens |= toks
        phi.date_phrases.extend(phrases)
    return phi


def match_token(token: str, phi: PhiSet) -> str | None:
    """Classify one OCR word against ``phi``: 'name' | 'id' | 'date' | None."""
    a = _alnum(token)
    if not a:
        return None
    if len(a) >= MIN_NAME_TOKEN and a in phi.name_tokens:
        return "name"
    if a in phi.date_tokens:
        return "date"
    for pid in phi.ids:
        if a == pid or (len(pid) >= 4 and pid in a):
            return "id"
    return None


def match_phrases(tokens: list[str], phi: PhiSet) -> list[tuple[int, int]]:
    """Index spans (start, end-exclusive) of multi-word date matches in a line.

    ``tokens`` is the list of OCR words on one line (original text); comparison
    is on their alnum-uppercased form.
    """
    norm = [_alnum(t) for t in tokens]
    spans: list[tuple[int, int]] = []
    for phrase in phi.date_phrases:
        p = [_alnum(x) for x in phrase]
        n = len(p)
        if n == 0:
            continue
        for i in range(len(norm) - n + 1):
            if norm[i:i + n] == p:
                spans.append((i, i + n))
    return spans
