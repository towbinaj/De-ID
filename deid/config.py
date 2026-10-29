"""Local app settings (git-ignored JSON).

Minimal for now - the active project name, the receiver AE/port, and the
bug-report address. Real destinations and credentials are added in later phases
and, like everything here, live only in the local ``De-ID-data`` folder.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

from . import paths


@dataclass
class Settings:
    my_aetitle: str = "DEID"
    bug_report_email: str = ""
    receiver_aetitle: str = "DEID_SCP"
    receiver_port: int = 11112
    active_project: str = ""
    # Remembered picker selections, keyed by picker name (filled in later).
    last_destinations: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Settings":
        p = paths.config_path()
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                s = cls()
                for k in cls.__dataclass_fields__:
                    if k in d:
                        setattr(s, k, d[k])
                return s
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass
        return cls()

    def save(self) -> None:
        paths.config_path().write_text(
            json.dumps(asdict(self), indent=2), encoding="utf-8")
