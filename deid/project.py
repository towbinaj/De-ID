"""The Project model - a named, saved, re-runnable de-identification recipe.

A Project bundles *everything* a de-id run needs so that frequent work is one
click: the anonymization profile (flags + per-tag rules), date policy, pixel /
OCR redaction, object/series drop rules, pseudonym policy, and the default
output / export target. Projects are stored as JSON, one file per project, in
the git-ignored ``De-ID-data/projects/`` folder.

This model is forward-looking: it carries fields for stages added in later
phases (object-drop rules, pseudonyms, OCR, export). ``to_receiver_config``
maps a Project onto the engine's :class:`ReceiverConfig`, wiring up whichever
stages the engine currently implements.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

from . import paths
from .engine.anon_profiles import AnonRule
from .engine.processing import ReceiverConfig

_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def _slug(name: str) -> str:
    """Filesystem-safe stem for a project name (not shown to the user)."""
    s = _SAFE.sub("_", (name or "").strip()).strip(". ")
    return s or "project"


def _split_csvish(text: str) -> list[str]:
    """Split a 'a|b, c' style field into a clean list of tokens."""
    return [t.strip() for t in re.split(r"[|,]", text or "") if t.strip()]


@dataclass
class Project:
    name: str = "New project"

    # -- Anonymization (standards-based) --------------------------------
    basic_profile: bool = True
    retain_dates: bool = False
    remove_private: bool = True
    calculated_dates: bool = False
    # Per-tag rules: REMOVE / BLANK / REPLACE / KEEP (list[AnonRule]).
    rules: list = field(default_factory=list)
    remove_groups: list = field(default_factory=list)   # ["6000", ...]
    remove_tags: list = field(default_factory=list)      # ["00081040", ...]

    # -- Pixel redaction -------------------------------------------------
    remove_image_top: int = 0
    remove_image_top_modality: list = field(default_factory=list)
    remove_image_left: int = 0
    remove_image_left_modality: list = field(default_factory=list)
    ocr_enabled: bool = False          # wired up in the OCR phase

    # -- Object / series drop rules (wired up in the drop-rules phase) ---
    drop_rules: list = field(default_factory=list)       # list[dict]

    # -- Pseudonym policy (wired up in the pseudonym phase) --------------
    pseudonymize: bool = False
    pseudonym_tags: list = field(default_factory=list)   # keyword list
    pseudonym_mode: str = "sequential"                   # sequential | hash

    # -- Crosswalk morphing (external CSV; engine already supports it) ---
    morph_enabled: bool = False
    morph_key_tag: str = ""                              # 8-hex
    morph_target_tags: list = field(default_factory=list)  # 8-hex list
    morph_file: str = ""
    morph_has_header: bool = True

    # -- Workflow defaults ----------------------------------------------
    output_dir: str = ""
    export_enabled: bool = False
    export_dest: str = ""              # destination name (resolved in Settings)

    # -- (de)serialization ----------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["rules"] = [asdict(r) if isinstance(r, AnonRule) else dict(r)
                      for r in self.rules]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        fields = cls.__dataclass_fields__
        kw = {k: d[k] for k in fields if k in d}
        kw["rules"] = [AnonRule(**{k: r.get(k, "") for k in
                                   ("tag", "action", "value")})
                       for r in d.get("rules", [])]
        return cls(**kw)

    # -- engine mapping --------------------------------------------------
    def custom_rules(self) -> list:
        out = []
        for r in self.rules:
            rn = r.normalized() if isinstance(r, AnonRule) else AnonRule(
                **{k: r.get(k, "") for k in ("tag", "action", "value")}).normalized()
            if rn.tag:
                out.append((rn.tag, rn.action, rn.value))
        return out

    def to_receiver_config(self) -> ReceiverConfig:
        """Map this Project onto the engine config (stages it supports today)."""
        cfg = ReceiverConfig(
            basic_profile=self.basic_profile,
            retain_dates=self.retain_dates,
            remove_private_tags=self.remove_private,
            calculated_dates=self.calculated_dates,
            custom_rules=self.custom_rules(),
            remove_groups=list(self.remove_groups),
            remove_tags=list(self.remove_tags),
            remove_image_top=int(self.remove_image_top or 0),
            remove_image_top_modality=list(self.remove_image_top_modality),
            remove_image_left=int(self.remove_image_left or 0),
            remove_image_left_modality=list(self.remove_image_left_modality),
        )
        if self.morph_enabled and self.morph_key_tag and self.morph_target_tags:
            fmt = "|".join([self.morph_key_tag] + list(self.morph_target_tags))
            cfg.morph_tags = True
            cfg.morphing_file_format = fmt
            cfg.morphing_file = self.morph_file
            cfg.morph_has_header = self.morph_has_header
        if self.pseudonymize and self.pseudonym_tags:
            cfg.pseudonymize = True
            cfg.pseudonym_tags = list(self.pseudonym_tags)
            cfg.pseudonym_mode = self.pseudonym_mode or "sequential"
        return cfg

    def summary(self) -> str:
        """One-line plain-English description of what this project will do."""
        parts = []
        parts.append("PS3.15 Basic Profile" if self.basic_profile
                     else "NO Basic Profile")
        if self.calculated_dates:
            parts.append("shift dates (keep age)")
        elif self.retain_dates:
            parts.append("retain study dates")
        if self.rules:
            parts.append(f"{len(self.rules)} tag rule(s)")
        if self.drop_rules:
            parts.append(f"{len(self.drop_rules)} drop rule(s)")
        if self.remove_image_top or self.remove_image_left:
            parts.append("pixel blanking")
        if self.ocr_enabled:
            parts.append("OCR redaction")
        if self.pseudonymize:
            parts.append(f"pseudonyms ({self.pseudonym_mode})")
        if self.morph_enabled:
            parts.append("crosswalk morph")
        if self.export_enabled and self.export_dest:
            parts.append(f"export → {self.export_dest}")
        return " · ".join(parts)


class ProjectStore:
    """Load/save/list Projects as individual JSON files in ``projects/``."""

    def path_for(self, name: str) -> "object":
        return paths.projects_dir() / f"{_slug(name)}.json"

    def list(self) -> list[Project]:
        out: list[Project] = []
        for p in sorted(paths.projects_dir().glob("*.json")):
            try:
                out.append(Project.from_dict(json.loads(
                    p.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                continue
        return out

    def names(self) -> list[str]:
        return [p.name for p in self.list()]

    def load(self, name: str) -> Project | None:
        for p in self.list():
            if p.name == name:
                return p
        return None

    def save(self, project: Project) -> None:
        self.path_for(project.name).write_text(
            json.dumps(project.to_dict(), indent=2), encoding="utf-8")

    def delete(self, name: str) -> None:
        p = self.path_for(name)
        if p.exists():
            p.unlink()

    def ensure_default(self) -> Project:
        """Return the first project, creating a sensible default if none exist."""
        existing = self.list()
        if existing:
            return existing[0]
        proj = Project(name="Default (PS3.15 Basic)")
        self.save(proj)
        return proj
