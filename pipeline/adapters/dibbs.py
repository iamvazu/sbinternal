"""Bronze -> silver for DIBBS (DLA Internet Bid Board System) RFQs, open ones only.

Bronze input: data/bronze/dibbs/DIBBS_Data.json (from DLA's daily RFQ download files). Each solicitation groups its
lines (NSN/part, quantity, unit) and DLA's approved sources (CAGE + part number). DLA publishes the return-by DATE
only; the countdown uses the start of that day (Eastern) so it never overstates the time left - check the RFQ PDF.
"""
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pipeline import schema
from pipeline.adapters._federal import federal_extra
from pipeline.paths import BRONZE, PROJECT
from pipeline.sources import SOURCES
from pipeline.util import PACIFIC, attachment

SRC = "dibbs"
META = SOURCES[SRC]
DATA_JSON = BRONZE / SRC / "DIBBS_Data.json"
EASTERN = ZoneInfo("America/New_York")


def due(v):
    try:
        return datetime.strptime(v, "%m/%d/%Y").replace(tzinfo=EASTERN)
    except (TypeError, ValueError):
        return None


def nsn(v):
    return f"{v[:4]}-{v[4:6]}-{v[6:9]}-{v[9:]}" if v and len(v) == 13 and v.isdigit() else v


def records():
    recs = json.loads(DATA_JSON.read_text(encoding="utf-8")) if DATA_JSON.exists() else []
    today = date.today()
    for r in recs:
        d = due(r.get("Return By"))
        if not d or d.date() < today:
            continue
        sol, lines = r["Solicitation"], r.get("Lines") or []
        items = [f"{nsn(x['nsn_or_part'])} {x.get('nomenclature') or ''} - qty {x.get('qty')} {x.get('unit') or ''}" for x in lines]
        name = lines[0].get("nomenclature") if lines else None
        title = f"{name or 'DLA RFQ'} ({nsn(lines[0]['nsn_or_part'])})" if lines else sol
        if len(lines) > 1:
            title += f" + {len(lines) - 1} more line{'s' if len(lines) > 2 else ''}"
        fsc = lines[0]["nsn_or_part"][:4] if lines and lines[0]["nsn_or_part"][:4].isdigit() else None
        extra = federal_extra(r.get("Set-Aside"))
        extra.update({"Solicitation Type": r.get("Solicitation Type"), "HUBZone Price Preference": r.get("HUBZone Preference"),
                      "Return By (DLA, date only)": r.get("Return By"), "Issued": r.get("Issued"), "Lines": lines,
                      "Approved Sources": r.get("Approved Sources") or {}, "FSC": fsc,
                      "Documents Note": "Quotes are submitted on DIBBS (free DLA account + CAGE code). The RFQ PDF is attached.",
                      "Category": "Goods & Supplies"})
        yield schema.empty(
            bid_uid=f"{SRC}:{sol}", id=f"{META['id_prefix']}{sol}", source=SRC, source_label=META["label"],
            platform=META["platform"], level="Federal", state="US", source_id=sol,
            agency="Defense Logistics Agency", agency_code=sol[:6], department=f"DLA ({sol[:6]})",
            title=title, description="DLA request for quotation.\n" + "\n".join(items),
            procurement_type=f"RFQ{' - ' + r['Solicitation Type'] if r.get('Solicitation Type') else ''}",
            solicitation_number=sol, status="Open", published_at=f"{r['Issued']}T00:00" if r.get("Issued") else None,
            due_at=d.astimezone(PACIFIC).strftime("%Y-%m-%dT%H:%M"),
            codes=[{"system": "PSC", "code": fsc, "title": None}] if fsc else [],
            attachments=[attachment(a, PROJECT) for a in r.get("Attachments") or []],
            documents=[{"name": "DIBBS RFQ lines", "text": "\n".join(items)}] if items else [],
            url=r.get("Detail URL"), extra=extra, raw_ref=f"data/bronze/dibbs/DIBBS_Data.json#{sol}", scraped_at=r.get("Scraped At"),
        )
