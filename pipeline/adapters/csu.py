"""Bronze -> silver for California State University (Jaggaer public bid site)."""
import json
import re
from datetime import datetime

from pipeline import schema
from pipeline.paths import BRONZE, PROJECT, rel
from pipeline.sources import SOURCES
from pipeline.util import clean

SRC = "csu"
META = SOURCES[SRC]
EVENTS_JSON = BRONZE / SRC / "CSU_Events_Data.json"

# Campus prefix on the solicitation number -> campus.
CAMPUS = {
    "CSUCO": "Chancellor's Office", "CSUB": "CSU Bakersfield", "CSUCI": "CSU Channel Islands", "CSUC": "Chico State",
    "CSUDH": "CSU Dominguez Hills", "CSUEB": "Cal State East Bay", "CSUF": "Cal State Fullerton",
    "FRESNO": "Fresno State", "CPH": "Cal Poly Humboldt", "CSULB": "Cal State Long Beach", "CSULA": "Cal State LA",
    "CMA": "Cal Maritime", "CSUMB": "CSU Monterey Bay", "CSUN": "CSUN (Northridge)", "CPP": "Cal Poly Pomona",
    "CSUS": "Sacramento State", "CSUSB": "Cal State San Bernardino", "SDSU": "San Diego State",
    "SFSU": "San Francisco State", "SJSU": "San José State", "CPSLO": "Cal Poly San Luis Obispo",
    "CSUSM": "Cal State San Marcos", "SSU": "Sonoma State", "CSUSTAN": "Stanislaus State",
}
CAMPUS_CITY = {"Cal State LA": "Los Angeles", "Cal State Long Beach": "Long Beach", "Cal State San Marcos": "San Marcos",
               "CSU Monterey Bay": "Seaside", "San José State": "San Jose", "CSU Dominguez Hills": "Carson",
               "Chancellor's Office": "Long Beach", "San Diego State": "San Diego", "Sacramento State": "Sacramento",
               "San Francisco State": "San Francisco", "Fresno State": "Fresno", "Cal State Fullerton": "Fullerton"}


def when(v):
    """'11/13/2026, 5:00 PM PST' -> '2026-11-13T17:00'."""
    if not v:
        return None
    v = re.sub(r"\s+(PST|PDT|PT)$", "", v.strip())
    try:
        return datetime.strptime(v, "%m/%d/%Y, %I:%M %p").strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def records():
    recs = json.loads(EVENTS_JSON.read_text(encoding="utf-8")) if EVENTS_JSON.exists() else []
    for r in recs:
        eid = r["Event ID"]
        prefix = (r.get("Number") or "").split("-")[0].upper()
        campus = CAMPUS.get(prefix, prefix or None)
        atts = []
        for a in r.get("Attachments") or []:
            p = PROJECT / (a.get("local_path") or "_none_")
            atts.append({"file_name": a["file_name"], "url": a.get("download_url"),
                         "local_path": rel(p) if a.get("local_path") and p.is_file() else None,
                         "size_bytes": a.get("size_bytes"), "error": a.get("error")})
        yield schema.empty(
            bid_uid=f"{SRC}:{eid}", id=f"{META['id_prefix']}{eid}", source=SRC, source_label=META["label"],
            platform=META["platform"], level=META["level"], state="CA", source_id=eid,
            agency="California State University", agency_code=prefix or None, department=campus,
            agency_city=CAMPUS_CITY.get(campus), title=clean(r["Title"]),
            description=f"{clean(r['Title'])}. {r.get('Type') or ''} issued by {campus or 'the CSU'} "
                        f"(solicitation {r.get('Number')}). Full event summary in the attached event PDF.",
            procurement_type=r.get("Type"), solicitation_number=r.get("Number"), status=r.get("Status"),
            published_at=when(r.get("Open")), due_at=when(r.get("Close")),
            contacts=[{"role": "buyer", "name": r.get("Contact Name"), "title": None, "email": r.get("Contact Email"), "phone": None}]
            if r.get("Contact Name") or r.get("Contact Email") else [],
            attachments=atts, url=r.get("Detail URL"),
            extra={"Campus": campus, "Documents Note": r.get("Documents Note")},
            raw_ref=f"{rel(EVENTS_JSON)}#{eid}", scraped_at=r.get("Scraped At"),
        )
