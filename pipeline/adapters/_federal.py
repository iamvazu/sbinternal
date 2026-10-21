"""Shared helpers for the federal adapters (SAM.gov, PIEE, FedConnect, DIBBS)."""
import csv
import io
import re
from functools import lru_cache

from pipeline.paths import BRONZE

SAM_EXTRACT = BRONZE / "sam" / "raw" / "ContractOpportunitiesFullCSV.csv"

# set-aside text (any source) -> the SBA program a firm needs; CA SB/DVBE never satisfies these
PROGRAMS = [(r"SDVOSB|Service[- ]Disabled Veteran", "SDVOSB (SBA VetCert)"), (r"EDWOSB|Economically Disadvantaged W", "EDWOSB (SBA)"),
            (r"WOSB|Wom[ae]n[- ]Owned", "WOSB (SBA)"), (r"8\(a\)|8a\b", "8(a) (SBA)"), (r"HUBZone", "HUBZone (SBA)"),
            (r"Veteran[- ]Owned", "VOSB"), (r"Indian|Buy Indian|IEE|ISBEE", "Indian Economic Enterprise"),
            (r"Small Business|Total Small|Partial Small|\bSBA\b|SB Set", "Small Business (SBA size standard)")]


def norm_sol(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def program(set_aside):
    if not set_aside or re.search(r"^(N/?A|None|No Set aside used)", set_aside, re.I):
        return None
    return next((p for pat, p in PROGRAMS if re.search(pat, set_aside, re.I)), set_aside)


@lru_cache(maxsize=1)
def sam_solicitations():
    """Normalized SAM.gov solicitation numbers, to keep PIEE/FedConnect duplicates out of silver (SAM wins)."""
    if not SAM_EXTRACT.exists():
        return frozenset()
    raw = SAM_EXTRACT.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    return frozenset(norm_sol(r["Sol#"]) for r in csv.DictReader(io.StringIO(text)) if r.get("Sol#"))


def federal_extra(set_aside, pop_state=None, pop_city=None):
    return {"Set-Aside": set_aside or None, "SBA Program": program(set_aside), "Place of Performance State": pop_state or None,
            "Place of Performance City": pop_city or None,
            "State Program": "Federal bid: SBA programs (SB, 8(a), HUBZone, SDVOSB, WOSB) and an active SAM.gov registration "
                             "apply - California SB/DVBE certifications do not"}
