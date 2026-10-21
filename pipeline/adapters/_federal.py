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


PRE_RFP = {"Special Notice", "Presolicitation", "Sources Sought"}


def stage(notice_type, source=None):
    """What a bidder can do with it today: bid, quote a DLA part, or position for a coming RFP."""
    if source == "dibbs":
        return "DLA parts RFQ"
    if notice_type in PRE_RFP:
        return "Pre-RFP notice"
    if notice_type == "Funding Opportunity":
        return "Grant / funding"
    return "Solicitation"


# NAICS (longest prefix wins) and PSC (first letter / 2-digit FSC) -> the app's work types (analyze.WORK_TYPES)
NAICS_CAT = [("5413", "Engineering & Architecture"), ("5415", "IT & Software"), ("5112", "IT & Software"), ("518", "IT & Software"),
             ("5617", "Facilities & Maintenance"), ("5621", "Environmental & Waste"), ("5622", "Environmental & Waste"),
             ("5629", "Environmental & Waste"), ("5616", "Safety & Security"), ("5311", "Real Estate & Leasing"), ("5312", "Real Estate & Leasing"),
             ("23", "Construction"), ("48", "Transportation & Logistics"), ("49", "Transportation & Logistics"), ("62", "Health & Human Services"),
             ("54", "Professional Services"), ("56", "Professional Services"), ("81", "Facilities & Maintenance"),
             ("31", "Goods & Supplies"), ("32", "Goods & Supplies"), ("33", "Goods & Supplies"), ("42", "Goods & Supplies")]
PSC_CAT = {"Y": "Construction", "Z": "Facilities & Maintenance", "C": "Engineering & Architecture", "D": "IT & Software",
           "S": "Facilities & Maintenance", "J": "Facilities & Maintenance", "F": "Environmental & Waste",
           "Q": "Health & Human Services", "G": "Health & Human Services", "R": "Professional Services", "B": "Professional Services",
           "A": "Professional Services", "V": "Transportation & Logistics", "X": "Real Estate & Leasing", "H": "Facilities & Maintenance",
           "K": "Facilities & Maintenance", "W": "Goods & Supplies", "L": "Facilities & Maintenance", "N": "Facilities & Maintenance"}


def category(naics=None, psc=None):
    naics, psc = (naics or "").strip(), (psc or "").strip().upper()
    hit = next((c for p, c in sorted(NAICS_CAT, key=lambda x: -len(x[0])) if naics.startswith(p)), None)
    if hit:
        return hit
    if psc:
        if psc[0].isdigit():
            return "IT & Software" if psc.startswith("70") else "Goods & Supplies"  # FSC 70xx = IT equipment
        return PSC_CAT.get(psc[0])
    return None
