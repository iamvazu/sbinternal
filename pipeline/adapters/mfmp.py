"""Bronze -> silver for MyFloridaMarketPlace (Florida state agencies' Vendor Bid System).

Bronze input: data/bronze/mfmp/MFMP_Data.json. Portal times are true UTC.
"""
import json

from pipeline import schema
from pipeline.paths import BRONZE, PROJECT
from pipeline.sources import SOURCES
from pipeline.util import attachment, clean, html_to_text, utc_to_pacific

SRC = "mfmp"
META = SOURCES[SRC]
DATA_JSON = BRONZE / SRC / "MFMP_Data.json"
UI = "https://vendor.myfloridamarketplace.com/search/bids/detail/{}"
INDICATORS = {"agencyContactPeriod": "Agency contact period (cone of silence)", "disabilitiesAct": "ADA accommodation notice",
              "minorityEncouraged": "Certified minority businesses encouraged",
              "preSolicitationConference": "Pre-solicitation conference", "rightToReject": "Agency may reject all bids"}


def records():
    recs = json.loads(DATA_JSON.read_text(encoding="utf-8")) if DATA_JSON.exists() else []
    for r in recs:
        lst, d = r.get("List") or {}, r.get("Detail") or {}
        ad = r["Ad ID"]
        org = d.get("organization") or lst.get("organization") or {}
        rc = d.get("responseContact") or {}
        ind = d.get("indicators") or {}
        yield schema.empty(
            bid_uid=f"{SRC}:{ad}", id=f"{META['id_prefix']}{ad}", source=SRC, source_label=META["label"],
            platform=META["platform"], level="State", state="FL", source_id=ad,
            agency=d.get("agency") or lst.get("agency"), agency_code=org.get("shortName"),
            agency_city=rc.get("city"), title=clean(d.get("title") or lst.get("title")) or ad,
            description=html_to_text(d.get("description")), procurement_type=d.get("type") or lst.get("type"),
            solicitation_number=d.get("agencyAdNumber") or lst.get("agencyAdNumber"),
            version=str(d.get("version") or lst.get("version") or "") or None,
            status=(d.get("status") or lst.get("status") or "").title() or None,
            published_at=utc_to_pacific(d.get("publishedDate") or lst.get("publishDate")),
            due_at=utc_to_pacific(d.get("closeDate") or lst.get("closeDate")),
            contacts=[{"role": "response", "name": rc.get("responseContact"), "title": None, "email": rc.get("email"),
                       "phone": rc.get("ph")}] if rc.get("responseContact") or rc.get("email") else [],
            codes=[{"system": "UNSPSC", "code": c.get("id"), "title": c.get("value")} for c in d.get("commodityCodes") or []],
            attachments=[attachment(a, PROJECT) for a in r.get("Attachments") or []],
            url=UI.format(lst.get("advertisementId") or d.get("advertisementId")),
            extra={"Open Date (UTC)": d.get("openDate"), "Close Date (UTC)": d.get("closeDate"),
                   "Linked Ad": d.get("linkedAdNumber"),
                   "Response Address": ", ".join(x for x in (rc.get("address1"), rc.get("address2"), rc.get("city"),
                                                             rc.get("state"), rc.get("zip")) if x) or None,
                   "Indicators": [v for k, v in INDICATORS.items() if ind.get(k)],
                   "State Program": "Florida OSD certification (MBE/WBE/SDVBE) - California certifications do not apply",
                   "Listed": r.get("Listed", True)},
            raw_ref=r.get("Raw Path"), scraped_at=r.get("Scraped At"),
        )
