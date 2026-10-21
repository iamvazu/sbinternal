"""Bronze -> silver for the PIEE Solicitation Module (DoD), PIEE-only notices.

Bronze input: data/bronze/piee/PIEE_Data.json. Notices also on SAM.gov are skipped (SAM is the record of truth and
already carries them); the rest (Navy/NAVAIR, MDA, DeCA ... notices PIEE did not publish to SAM) come in here.
Response dates are stated with their own time zone name ("... Central Standard Time").
"""
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from pipeline import schema
from pipeline.adapters._federal import federal_extra
from pipeline.paths import BRONZE, PROJECT
from pipeline.sources import SOURCES
from pipeline.util import PACIFIC, attachment, clean

SRC = "piee"
META = SOURCES[SRC]
DATA_JSON = BRONZE / SRC / "PIEE_Data.json"
TZ = {"Eastern": "America/New_York", "Central": "America/Chicago", "Mountain": "America/Denver", "Pacific": "America/Los_Angeles",
      "Alaska": "America/Anchorage", "Hawaii": "Pacific/Honolulu", "UTC": "UTC", "Atlantic": "America/Puerto_Rico"}


def when(v):
    """'2026/11/16 15:00 Central Standard Time' / '2026/10/08 20:04 UTC' -> Pacific."""
    m = re.match(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2})\s*(.*)", v or "")
    if not m:
        return None
    zone = next((z for k, z in TZ.items() if k in m.group(2)), "America/New_York")
    return datetime.strptime(m.group(1), "%Y/%m/%d %H:%M").replace(tzinfo=ZoneInfo(zone)).astimezone(PACIFIC).strftime("%Y-%m-%dT%H:%M")


def code(v, system):
    m = re.match(r"\s*(\w+)\s*-\s*(.*)", v or "")
    return [{"system": system, "code": m.group(1), "title": clean(m.group(2))}] if m else []


def records():
    recs = json.loads(DATA_JSON.read_text(encoding="utf-8")) if DATA_JSON.exists() else []
    for r in recs:
        if r.get("On SAM") or not r.get("Listed", True):
            continue
        row, f, nid = r.get("List Row") or {}, r.get("Detail") or {}, r["Notice ID"]
        contacts = [{"role": role, "name": f.get(f"{p} Contact Name") or None, "title": None, "email": f.get(f"{p} Contact Email") or None,
                     "phone": f.get(f"{p} Contact Phone Number") or None}
                    for role, p in (("primary", "Primary"), ("alternate", "Alternate")) if f.get(f"{p} Contact Name") or f.get(f"{p} Contact Email")]
        m = re.search(r"\b([A-Z]{2})\s+\d{5}", f.get("Contracting Office Address") or "")
        extra = federal_extra(f.get("Set Aside Code") or row.get("Set Aside"), None, None)
        extra.update({"Contracting Office": f.get("Contracting Office Name") or row.get("Contracting Office Name"),
                      "DoDAAC": f.get("Contracting Office DoDAAC"), "Office Address": f.get("Contracting Office Address"),
                      "Office State": m.group(1) if m else None,
                      "Place of Performance": f.get("Place of Performance Address") or f.get("Place of Performance Zip Code"),
                      "Restricted": row.get("Restricted") == "Y", "Updates": row.get("Number of Updates"),
                      "Archive Date": f.get("Archive Date"), "Response Date (as posted)": row.get("Response Date"),
                      "Detail scraped": bool(f), "Listed": r.get("Listed", True)})
        yield schema.empty(
            bid_uid=f"{SRC}:{nid}", id=f"{META['id_prefix']}{nid}", source=SRC, source_label=META["label"],
            platform=META["platform"], level="Federal", state="US", source_id=nid, agency="Department of Defense",
            agency_code=f.get("Contracting Office DoDAAC"), department=row.get("Contracting Office Name"),
            title=clean(row.get("Subject")) or nid, description=clean(f.get("Description") or row.get("Description")),
            procurement_type=row.get("Notice Type"), solicitation_number=nid, status=row.get("Status"),
            published_at=when(row.get("PIEE Posted Date")), due_at=when(row.get("Response Date")), contacts=contacts,
            codes=code(row.get("NAICS"), "NAICS") + code(row.get("PSC") or f.get("Product or Service Code"), "PSC"),
            attachments=[attachment(a, PROJECT) for a in r.get("Attachments") or []],
            url="https://piee.eb.mil/sol/xhtml/unauth/index.xhtml", extra=extra,
            raw_ref=r.get("Raw Path"), scraped_at=r.get("Scraped At"),
        )
