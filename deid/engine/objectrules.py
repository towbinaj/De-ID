"""Rule-based object / series dropping and quarantine.

Some whole objects shouldn't be shared at all - Secondary Capture images,
encapsulated PDFs/CDAs, and scanned documents routinely carry burned-in or
free-text PHI that attribute de-identification can't clean. A drop rule matches
such objects by a tag value and either:

* ``drop``       - discard the object entirely (never written to the output), or
* ``quarantine`` - set the ORIGINAL aside in the local quarantine folder for a
  human to review (also never written to the output).

Rules act at two scopes:

* ``instance`` - decided per object, on its own values.
* ``series``   - if ANY object in a series matches, the WHOLE series is removed
  (so a multi-object scanned-document series goes together). Series-scope rules
  need a pre-scan of the batch, done by ``fileproc``.

Nothing here deletes the user's source files; ``drop`` simply omits them from
the output, and ``quarantine`` copies the original aside.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from pydicom.tag import Tag
from pydicom.datadict import tag_for_keyword

OPS = ["equals", "not_equals", "contains", "regex", "in", "exists", "absent"]
ACTIONS = ["drop", "quarantine"]
SCOPES = ["instance", "series"]

# Common SOP Class UIDs worth dropping by default.
_SC_IMAGE = "1.2.840.10008.5.1.4.1.1.7"
_SC_MULTIFRAME = "|".join(f"{_SC_IMAGE}.{n}" for n in (1, 2, 3, 4))
_ENCAP_PDF = "1.2.840.10008.5.1.4.1.1.104.1"
_ENCAP_CDA = "1.2.840.10008.5.1.4.1.1.104.2"


def _resolve_tag(spec: str):
    spec = str(spec or "").strip()
    if not spec:
        return None
    s = spec.replace(",", "").replace("x", "")
    if len(s) == 8 and all(c in "0123456789abcdefABCDEF" for c in s):
        return Tag(int(s[:4], 16), int(s[4:8], 16))
    t = tag_for_keyword(spec)
    return Tag(t) if t is not None else None


@dataclass
class DropRule:
    tag: str = "Modality"        # keyword or 8-hex
    op: str = "equals"
    value: str = ""
    action: str = "drop"         # drop | quarantine
    scope: str = "instance"      # instance | series
    ignore_case: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DropRule":
        fields = cls.__dataclass_fields__
        return cls(**{k: d[k] for k in fields if k in d})


def parse_rules(items) -> list[DropRule]:
    out = []
    for it in items or []:
        try:
            out.append(it if isinstance(it, DropRule) else DropRule.from_dict(it))
        except (TypeError, ValueError):
            continue
    return out


def value_of(ds, tag_spec: str):
    """(present, str_value) for a tag on a dataset."""
    tag = _resolve_tag(tag_spec)
    if tag is None or tag not in ds:
        return False, ""
    return True, str(ds[tag].value)


def rule_matches(ds, rule: DropRule) -> bool:
    present, val = value_of(ds, rule.tag)
    op = rule.op
    if op == "exists":
        return present
    if op == "absent":
        return not present
    if not present:
        return False
    target = rule.value or ""
    if op == "regex":
        flags = re.IGNORECASE if rule.ignore_case else 0
        try:
            return re.search(target, val, flags) is not None
        except re.error:
            return False
    v = val.lower() if rule.ignore_case else val
    t = target.lower() if rule.ignore_case else target
    if op == "equals":
        return v == t
    if op == "not_equals":
        return v != t
    if op == "contains":
        return t in v
    if op == "in":
        options = {s.strip() for s in re.split(r"[|,]", t) if s.strip()}
        return v in options
    return False


def evaluate(ds, rules: list[DropRule]) -> DropRule | None:
    """The first rule that matches ``ds``, or None."""
    for rule in rules:
        if rule_matches(ds, rule):
            return rule
    return None


def split_by_scope(rules: list[DropRule]):
    """(instance_rules, series_rules)."""
    inst = [r for r in rules if r.scope != "series"]
    series = [r for r in rules if r.scope == "series"]
    return inst, series


# Quick-add presets for the UI. Each is a ready-to-edit DropRule.
PRESETS: list[tuple[str, DropRule]] = [
    ("Modality = SC (Secondary Capture)",
     DropRule("Modality", "equals", "SC", "drop", "instance")),
    ("Secondary Capture images (SOP class)",
     DropRule("SOPClassUID", "in", f"{_SC_IMAGE}|{_SC_MULTIFRAME}",
              "drop", "instance")),
    ("Encapsulated PDF / CDA",
     DropRule("SOPClassUID", "in", f"{_ENCAP_PDF}|{_ENCAP_CDA}",
              "drop", "instance")),
    ("Scanned documents (SeriesDescription)",
     DropRule("SeriesDescription", "regex",
              r"scan|document|paper|consent|requisition", "quarantine",
              "series")),
    ("Dose report (SeriesDescription)",
     DropRule("SeriesDescription", "regex", r"dose\s*(report|sr)",
              "quarantine", "series")),
]
