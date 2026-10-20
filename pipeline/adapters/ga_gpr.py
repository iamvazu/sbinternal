"""Bronze -> silver for the Georgia Procurement Registry (state agencies, counties, cities, K-12).

Bronze input: data/bronze/ga_gpr/GA_GPR_Data.json. Times are Eastern ("Oct 20, 2026 @ 01:30 PM ET").
"""
import json
import re

from pipeline import schema
from pipeline.paths import BRONZE, PROJECT
from pipeline.sources import SOURCES
from pipeline.util import attachment, clean, local_to_pacific

SRC = "ga_gpr"
META = SOURCES[SRC]
DATA_JSON = BRONZE / SRC / "GA_GPR_Data.json"
FMTS = ("%b %d, %Y @ %I:%M %p",)
CATEGORY = {"Construction / Public Works": "Construction", "Goods": "Goods & Supplies",
            "Information Technology": "IT & Software", "Design Professional, General Consultant": "Engineering & Architecture"}


def when(v):
    return local_to_pacific(re.sub(r"\s*(ET|EST|EDT)$", "", v or ""), FMTS, "GA")


def buyer(text):
    parts = [p.strip() for p in re.split(r"\s*[·�]\s*", text or "") if p.strip()]
    email = next((p for p in parts if "@" in p), None)
    phone = next((p for p in parts if re.search(r"\d{3}\D?\d{3}\D?\d{4}", p)), None)
    name = next((p for p in parts if p not in (email, phone)), None)
    return [{"role": "buyer", "name": name, "title": None, "email": email, "phone": phone}] if parts else []


def records():
    recs = json.loads(DATA_JSON.read_text(encoding="utf-8")) if DATA_JSON.exists() else []
    for r in recs:
        lst, d = r.get("List") or {}, r.get("Detail") or {}
        ev, dates = d.get("event") or {}, d.get("dates") or {}
        eid = r["Event ID"]
        gov = ev.get("Government Type") or lst.get("governmentType") or ""
        yield schema.empty(
            bid_uid=f"{SRC}:{eid}", id=f"{META['id_prefix']}{eid}", source=SRC, source_label=META["label"],
            platform=META["platform"], level="State" if gov.lower() == "state" else "Local", state="GA", source_id=eid,
            agency=lst.get("agencyName"), agency_code=lst.get("agencyCode"), title=clean(lst.get("title")) or eid,
            description=clean(d.get("description")), procurement_type=ev.get("Purchase Type") or lst.get("bidProcessType"),
            solicitation_number=eid, status=ev.get("Event Status") or lst.get("status"),
            published_at=when(dates.get("Start Date") or lst.get("postingDateStr")),
            due_at=when(dates.get("End Date") or lst.get("closingDateStr")),
            prebid_meeting=clean(d.get("conference")), contacts=buyer(d.get("buyer")),
            codes=[{"system": "NIGP", "code": n.get("code"), "title": n.get("title")} for n in d.get("nigp") or []],
            attachments=[attachment(a, PROJECT) for a in r.get("Attachments") or []], url=r.get("Detail URL"),
            extra={"Category": CATEGORY.get(ev.get("Category Type")), "Category Type": ev.get("Category Type"),
                   "Government Type": gov or None, "Fiscal Year": ev.get("Fiscal Year"),
                   "Agency Site": ev.get("Agency Site"), "Electronic Bid": lst.get("electronicBid"),
                   "Due (local, Eastern)": dates.get("End Date"),
                   "State Program": "Georgia GOSBO small/minority business - California certifications do not apply",
                   "Listed": r.get("Listed", True)},
            raw_ref=r.get("Raw Path"), scraped_at=r.get("Scraped At"),
        )
