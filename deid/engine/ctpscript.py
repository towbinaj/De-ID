"""RSNA/CTP DicomAnonymizer script import/export.

CTP anonymizer scripts are XML: ``<e>`` elements carry a per-tag command
(``@remove()``, ``@empty()``, ``@keep()``, a literal replacement value, or a
function like ``@hashuid()``), and ``<r>`` elements carry remove-directives
(e.g. ``privategroups``). We map the parts that correspond to our per-tag rules
and flags, and REPORT the rest rather than silently dropping it - CTP functions
such as ``@hashdate``/``@hashptid`` have no direct per-tag equivalent here
(dates and UIDs are handled by the project's date policy and the Basic Profile).

Export is best-effort and symmetric: it writes the project's per-tag rules and
the remove-private flag, and lists in XML comments what a CTP script can't
capture (the full PS3.15 Basic Profile, date shifting, object-drop rules,
pseudonyms, OCR).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from pydicom.datadict import tag_for_keyword

from .anon_profiles import AnonRule, keyword_for

_CMD = {"REMOVE": "@remove()", "BLANK": "@empty()", "KEEP": "@keep()"}


def _tag8(t: str) -> str | None:
    """Normalize a CTP ``t`` attribute to 8 upper-hex, or None (group/keyword)."""
    s = (t or "").strip().replace(",", "").replace(" ", "")
    if len(s) == 8 and all(c in "0123456789abcdefABCDEF" for c in s):
        return s.upper()
    kw = tag_for_keyword(t.strip()) if t else None
    if kw is not None:
        return f"{kw:08X}"
    return None


def _map_command(cmd: str):
    """(action, value) for a CTP element command, or (None, None) if unmappable."""
    c = (cmd or "").strip()
    low = c.lower()
    if low.startswith("@remove"):
        return "REMOVE", ""
    if low.startswith("@empty") or c == "":
        return "BLANK", ""
    if low.startswith("@keep"):
        return "KEEP", ""
    if c.startswith("@"):
        return None, None          # @hashuid/@hashdate/@hashptid/... - report
    return "REPLACE", c            # a literal replacement value


def parse_script(text: str) -> dict:
    """Parse a CTP script into {rules, remove_private, unmapped}."""
    out = {"rules": [], "remove_private": False, "unmapped": []}
    root = ET.fromstring(text)
    for el in root:
        if not isinstance(el.tag, str):     # XML comments / PIs
            continue
        tag = el.tag.lower()
        if tag == "e":
            if (el.get("en", "T") or "T").upper() != "T":
                continue
            tag8 = _tag8(el.get("t", ""))
            if not tag8:
                out["unmapped"].append(f"element t={el.get('t', '')!r}")
                continue
            action, value = _map_command(el.text or "")
            if action is None:
                out["unmapped"].append(
                    f"{el.get('t', '')} → {(el.text or '').strip()}")
                continue
            out["rules"].append(AnonRule(tag8, action, value))
        elif tag == "k":
            tag8 = _tag8(el.get("t", ""))
            if tag8:
                out["rules"].append(AnonRule(tag8, "KEEP", ""))
            else:
                out["unmapped"].append(f"keep t={el.get('t', '')!r}")
        elif tag == "r":
            t = (el.get("t", "") or "").lower()
            if "privategroup" in t:
                out["remove_private"] = True
            else:
                out["unmapped"].append(f"remove-directive '{t}'")
        elif tag == "p":
            out["unmapped"].append(f"param '{el.get('t', '')}'")
        else:
            out["unmapped"].append(f"<{tag}>")
    return out


def _unrepresented(project) -> list:
    """What a CTP script can't capture from this project."""
    notes = []
    if project.basic_profile:
        notes.append("PS3.15 Basic Confidentiality Profile (full attribute list)")
    if project.calculated_dates:
        notes.append("date shifting (age-preserving)")
    elif project.retain_dates:
        notes.append("retain study dates")
    if project.drop_rules:
        notes.append(f"{len(project.drop_rules)} object/series drop rule(s)")
    if project.pseudonymize:
        notes.append("consistent pseudonyms")
    if project.ocr_enabled:
        notes.append("OCR burned-in text redaction")
    if project.morph_enabled:
        notes.append("crosswalk morphing")
    if project.remove_image_top or project.remove_image_left:
        notes.append("pixel blanking")
    return notes


def to_script(project) -> str:
    """Export the project's per-tag rules + remove-private as a CTP script."""
    root = ET.Element("script")
    root.append(ET.Comment(f' Exported from De-ID project "{project.name}". '))
    notes = _unrepresented(project)
    if notes:
        root.append(ET.Comment(
            " NOT represented in this CTP script - configure these in CTP "
            "separately: " + "; ".join(notes) + ". "))
    for r in project.rules:
        rule = r if isinstance(r, AnonRule) else AnonRule(**r)
        rn = rule.normalized()
        if len(rn.tag) != 8:
            continue
        e = ET.SubElement(root, "e")
        e.set("en", "T")
        e.set("t", rn.tag.lower())
        e.set("n", keyword_for(rn.tag) or "")
        e.text = rn.value if rn.action == "REPLACE" else _CMD.get(rn.action,
                                                                  "@remove()")
    if project.remove_private:
        r = ET.SubElement(root, "r")
        r.set("en", "T")
        r.set("t", "privategroups")
        r.text = "Remove private groups"
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + \
        ET.tostring(root, encoding="unicode") + "\n"
