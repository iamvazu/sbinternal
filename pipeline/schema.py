"""Silver schema: the one canonical shape every source adapter must produce.

Gold (bidintel) only ever reads this shape, so adding a source = a scraper (bronze) + an adapter (bronze -> silver).

Field                 Type / example
--------------------  --------------------------------------------------------------------------
bid_uid               "caleprocure:0000040342" | "opengov:301748"   globally unique, stable
id                    display / gold id: "0000040342" | "OG-301748"  (prefix per non-Cal-eProcure source)
source                registry key: "caleprocure" | "opengov" | "planetbids" ...
source_label          human label shown in the app: "Cal eProcure" | "OpenGov" | "PlanetBids" ...
platform              underlying software: "Cal eProcure (PeopleSoft)" | "OpenGov Procurement" ...
level                 "State" | "Local" | "Federal"
state                 2-letter US state of the buying agency: "CA", "OR", "FL", "TX", "GA", "VA", "PA";
                      "US" for federal bids (place-of-performance state in extra["Place of Performance State"])
source_id             the id on the source system
agency                buying organisation ("Dept of Corrections & Rehab", "County of Orange")
agency_code           short code on the source (dept code, OpenGov portal code, PlanetBids portal id)
department            sub-unit, if any
agency_city           city of the buying agency, if known
title, description    text
procurement_type      "Sell Event / RFx", "IFB / Invitation For Bid" ...
solicitation_number   agency's own number, if any
version               event version / addendum count as text
status                as published ("open", "Posted" ...)
published_at, due_at  "YYYY-MM-DDTHH:MM" in Pacific time, the app's clock (None if unknown).
                      The agency's own local time is kept in extra["Due (local)"] for non-Pacific states.
question_deadline     free text or ISO
prebid_meeting        free text
contacts              [{role, name, title, email, phone}]
codes                 [{system: "UNSPSC"|"NIGP"|"NAICS"|"PSC", code, title}]
attachments           [{file_name, url, local_path (project-relative, forward slashes), size_bytes, error}]
documents             [{name, text}]   solicitation text the source provides as data, not as a file
url                   detail page on the source
extra                 {}  source-specific fields worth keeping (shown in the bid drawer)
raw_ref               project-relative path to the bronze record / raw object
scraped_at            ISO timestamp
"""

REQUIRED = ("bid_uid", "id", "source", "source_label", "platform", "level", "state", "source_id", "title", "url")
STATES = {"CA", "OR", "FL", "TX", "GA", "VA", "PA", "AZ", "WA", "US"}
LEVELS = {"State", "Local", "Federal"}
CODE_SYSTEMS = {"UNSPSC", "NIGP", "NAICS", "PSC", "Other"}


def empty(**kw):
    rec = {
        "bid_uid": None, "id": None, "source": None, "source_label": None, "platform": None, "level": None, "state": None,
        "source_id": None, "agency": None, "agency_code": None, "department": None, "agency_city": None,
        "title": None, "description": None, "procurement_type": None, "solicitation_number": None,
        "version": None, "status": None, "published_at": None, "due_at": None, "question_deadline": None,
        "prebid_meeting": None, "contacts": [], "codes": [], "attachments": [], "documents": [], "url": None,
        "extra": {}, "raw_ref": None, "scraped_at": None,
    }
    rec.update(kw)
    return rec


def validate(rec):
    """Return a list of problems (empty = valid). Problems are reported in the manifest, never silently fixed."""
    errs = [f"missing {k}" for k in REQUIRED if not rec.get(k)]
    if rec.get("state") not in STATES:
        errs.append(f"bad state {rec.get('state')!r}")
    if rec.get("level") not in LEVELS:
        errs.append(f"bad level {rec.get('level')!r}")
    for c in rec.get("codes") or []:
        if c.get("system") not in CODE_SYSTEMS:
            errs.append(f"bad code system {c.get('system')!r}")
    for k in ("published_at", "due_at"):
        v = rec.get(k)
        if v and not (len(v) == 16 and v[4] == "-" and v[10] == "T"):
            errs.append(f"{k} not ISO-local: {v!r}")
    return errs
