"""Core data models: a remote DICOM destination (Node)."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, asdict

AE_TITLE_MAX = 16


def validate_aetitle(ae: str, *, allow_empty: bool = False) -> str:
    """Check a DICOM AE Title (PS3.5 ``AE`` VR). '' when valid, else a reason."""
    if ae is None or ae == "":
        return "" if allow_empty else "cannot be empty"
    if ae.strip() == "":
        return "cannot be all spaces"
    if len(ae) > AE_TITLE_MAX:
        return f"too long ({len(ae)} chars; max {AE_TITLE_MAX})"
    if "\\" in ae:
        return "backslash (\\) is not allowed"
    if any(ord(c) < 0x20 or ord(c) > 0x7E for c in ae):
        return "only printable ASCII is allowed (no control/non-ASCII chars)"
    return ""


@dataclass
class Node:
    """A remote DICOM peer (research PACS / VNA / workstation) to export to."""

    name: str
    aetitle: str
    host: str
    port: int
    note: str = ""
    timeout: int = 30
    tls: bool = False
    calling_aetitle: str = ""     # local AE override for this node (else My AE)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            name=str(d.get("name", "")).strip(),
            aetitle=str(d.get("aetitle", "")).strip(),
            host=str(d.get("host", "")).strip(),
            port=int(d.get("port", 104) or 104),
            note=str(d.get("note", "")).strip(),
            timeout=int(d.get("timeout", 30) or 30),
            tls=bool(d.get("tls", False)),
            calling_aetitle=str(d.get("calling_aetitle", "")).strip(),
        )

    def calling(self, default: str) -> str:
        return self.calling_aetitle or default

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.name:
            problems.append("Name is empty.")
        ae_problem = validate_aetitle(self.aetitle)
        if ae_problem:
            problems.append(f"AE Title '{self.aetitle}': {ae_problem}.")
        calling_problem = validate_aetitle(self.calling_aetitle, allow_empty=True)
        if calling_problem:
            problems.append(f"Calling AE '{self.calling_aetitle}': "
                            f"{calling_problem}.")
        if not self.host:
            problems.append("Host is empty.")
        else:
            try:
                ipaddress.ip_address(self.host)
            except ValueError:
                if ".." in self.host or self.host.endswith("."):
                    problems.append(f"Host '{self.host}' looks malformed.")
        if not (1 <= int(self.port) <= 65535):
            problems.append(f"Port {self.port} is out of range (1-65535).")
        return problems

    @property
    def label(self) -> str:
        return f"{self.name}  ({self.aetitle}@{self.host}:{self.port})"
