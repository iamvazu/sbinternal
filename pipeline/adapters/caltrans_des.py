"""Bronze -> silver for Caltrans DES advertised construction contracts (state, often federally funded).

Bronze input: data/bronze/caltrans_des/Caltrans_DES_Events_Data.json (+ raw/<EA>.json)
"""
import json
import re

from pipeline import schema
from pipeline.paths import BRONZE
from pipeline.sources import SOURCES
from pipeline.util import clean

SRC = "caltrans_des"
META = SOURCES[SRC]
EVENTS_JSON = BRONZE / SRC / "Caltrans_DES_Events_Data.json"

# Caltrans county abbreviations used in County-Route-Postmile ("Mer-59-14.1/14.8").
COUNTY_ABBR = {
    "ALA": "Alameda", "ALP": "Alpine", "AMA": "Amador", "BUT": "Butte", "CAL": "Calaveras", "COL": "Colusa",
    "CC": "Contra Costa", "DN": "Del Norte", "ED": "El Dorado", "FRE": "Fresno", "GLE": "Glenn", "HUM": "Humboldt",
    "IMP": "Imperial", "INY": "Inyo", "KER": "Kern", "KIN": "Kings", "LAK": "Lake", "LAS": "Lassen", "LA": "Los Angeles",
    "MAD": "Madera", "MRN": "Marin", "MPA": "Mariposa", "MEN": "Mendocino", "MER": "Merced", "MOD": "Modoc", "MNO": "Mono",
    "MON": "Monterey", "NAP": "Napa", "NEV": "Nevada", "ORA": "Orange", "PLA": "Placer", "PLU": "Plumas", "RIV": "Riverside",
    "SAC": "Sacramento", "SBT": "San Benito", "SBD": "San Bernardino", "SD": "San Diego", "SF": "San Francisco",
    "SJ": "San Joaquin", "SLO": "San Luis Obispo", "SM": "San Mateo", "SB": "Santa Barbara", "SCL": "Santa Clara",
    "SCR": "Santa Cruz", "SHA": "Shasta", "SIE": "Sierra", "SIS": "Siskiyou", "SOL": "Solano", "SON": "Sonoma",
    "STA": "Stanislaus", "SUT": "Sutter", "TEH": "Tehama", "TRI": "Trinity", "TUL": "Tulare", "TUO": "Tuolumne",
    "VEN": "Ventura", "YOL": "Yolo", "YUB": "Yuba", "VAR": None,  # "Var" = various counties
}


# Caltrans shorthand -> words contractors use in their profiles (keeps the original, appends the expansion).
ABBR = {
    "HMA": "hot mix asphalt paving", "RHMA": "rubberized hot mix asphalt paving", "PCC": "portland cement concrete",
    "JPCP": "jointed plain concrete pavement", "CRCP": "continuously reinforced concrete pavement",
    "MBGR": "metal beam guardrail", "MGS": "midwest guardrail system", "TMS": "traffic management system",
    "CCTV": "closed circuit television camera", "ADA": "accessibility curb ramps", "RSP": "rock slope protection",
    "SWPPP": "stormwater pollution prevention", "ITS": "intelligent transportation system electrical",
    "OGFC": "open graded friction course asphalt", "CAPM": "capital preventive maintenance pavement",
    "BMP": "stormwater best management practices", "TMP": "traffic management plan", "PM": None, "Rte": None,
}


def expand(text):
    if not text:
        return text
    extra = [v for k, v in ABBR.items() if v and re.search(rf"\b{re.escape(k)}\b", text)]
    return f"{text} ({'; '.join(extra)})" if extra else text


def counties(crpm):
    """'Mer-59-14.1/14.8' / '04-Ala,CC-80-...' -> ['Merced'] / ['Alameda', 'Contra Costa']."""
    found = []
    for tok in re.findall(r"[A-Za-z]{2,3}", crpm or ""):
        name = COUNTY_ABBR.get(tok.upper())
        if name and name not in found:
            found.append(name)
    return found


def pct(v):
    v = str(v or "").strip()
    return f"{v}%" if v and not v.endswith("%") else v


def records():
    recs = json.loads(EVENTS_JSON.read_text(encoding="utf-8")) if EVENTS_JSON.exists() else []
    for r in recs:
        c, ea = r["Contract"], r["EA"]
        district = ea.split("-")[0].lstrip("0") or ea.split("-")[0]
        cty = counties(c.get("County-Route-PM"))
        est = c.get("Call Out Number")
        est_text = f"${int(est):,}" if str(est or "").isdigit() else None
        goal_bits = [f"{k}: {pct(c.get(k))}" for k in ("DBE Goal", "DVBE Goal", "SBE Goal Percent") if c.get(k)]
        desc = "\n".join(x for x in [
            expand(clean(c.get("Work Description"))),
            f"Location: {', '.join(n + ' County' for n in cty)}. {clean(c.get('Location Description')) or ''}".strip() if cty else clean(c.get("Location Description")),
            f"County-Route-Postmile: {c.get('County-Route-PM')}" if c.get("County-Route-PM") else None,
            f"Engineer's estimate: {est_text}" if est_text else None,
            f"License: {clean(c.get('License Callout')) or ''} {r.get('Licenses') or c.get('licenses') or ''}".strip(),
            f"Total working days: {c.get('Total Working Days')}" if c.get("Total Working Days") else None,
            f"Goal type: {c.get('Type of Goal')}" if c.get("Type of Goal") else None,
            "; ".join(goal_bits) or None,
        ] if x)
        items = r.get("Bid Items") or []
        qa = r.get("Bidder Inquiries") or []
        primes = r.get("Primes Seeking Help") or []
        docs = []
        if items:
            docs.append({"name": "Caltrans bid items", "text": "\n".join(
                f"{i.get('Item Line Number')}. {i.get('Name') or ''} {i.get('Description') or ''} ({i.get('Quantity') or ''} {i.get('Unit') or ''})"
                for i in items)})
        if qa:
            docs.append({"name": "Caltrans bidder inquiries", "text": "\n\n".join(
                " | ".join(f"{k}: {v}" for k, v in q.items() if v and k not in ("canEdit",)) for q in qa)})
        if primes:
            docs.append({"name": "Caltrans primes seeking subcontractors", "text": "\n\n".join(
                f"{p.get('Prime Business')}: {p.get('Services Needed') or ''} {p.get('Other requirements') or ''}" for p in primes)})
        yield schema.empty(
            bid_uid=f"{SRC}:{ea}", id=f"{META['id_prefix']}{ea}", source=SRC, source_label=META["label"],
            platform=META["platform"], level=META["level"], state="CA", source_id=ea,
            agency="Caltrans - Division of Engineering Services", agency_code=f"D{district}",
            department=f"Caltrans District {district}", title=f"{ea} - {clean(c.get('Work Description')) or ''}".strip(" -"),
            description=desc, procurement_type="Construction contract (IFB, lowest responsible bidder)",
            solicitation_number=ea, version=str(r.get("Counts", {}).get("addendaCount") or 0), status=c.get("Status"),
            # Only the bid-open DATE is published; midnight is used so countdowns never overstate time left.
            published_at=f"{c['Advertisement Date']}T00:00" if c.get("Advertisement Date") else None,
            due_at=f"{c['Bid Open Date']}T00:00" if c.get("Bid Open Date") else None,
            attachments=[{"file_name": d["file_name"], "url": d["download_url"], "local_path": None,
                          "size_bytes": d.get("size_bytes"), "error": d.get("error")} for d in r.get("Documents") or []],
            documents=docs, url=r.get("Detail URL"),
            extra={
                "Category": "Construction",  # every DES advertisement is a construction contract
                "Counties": cty, "County-Route-PM": c.get("County-Route-PM"), "Engineer's Estimate": est_text,
                "Working Days": c.get("Total Working Days"), "Licenses": r.get("Licenses"),
                "Goal Type": c.get("Type of Goal"), "DBE Goal": pct(c.get("DBE Goal")) or None,
                "DVBE Goal": pct(c.get("DVBE Goal")) or None, "SBE Goal": pct(c.get("SBE Goal Percent")) or None,
                "Bid Opening": "Sacramento (date only published)",
                "Subcontractor Opt-Ins": r.get("Subcontractor Opt-Ins") or [],
                "Primes Seeking Help": primes, "Plan Holders": r.get("Plan Holders") or [],
                "Bid Items Count": len(items), "Inquiries Count": len(qa),
                "Documents Note": "Bid documents require a free Caltrans contractor login (Bid Book).",
                "Listed": r.get("Listed", True),
            },
            raw_ref=r.get("Raw Path"), scraped_at=r.get("Scraped At"),
        )
