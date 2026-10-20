"""Bronze -> silver for OregonBuys (Periscope BuySpeed): Oregon state agencies + local governments.

Bronze input: data/bronze/oregonbuys/OregonBuys_Data.json (+ attachments/<bid>/, raw/<bid>.json)
Oregon is on Pacific time, so portal times are already on the app's clock.
"""
import json
import re

from pipeline import schema
from pipeline.paths import BRONZE, PROJECT
from pipeline.sources import SOURCES
from pipeline.util import attachment, clean, local_to_pacific

SRC = "oregonbuys"
META = SOURCES[SRC]
DATA_JSON = BRONZE / SRC / "OregonBuys_Data.json"
FMTS = ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y")
# Listing columns, used when the detail page could not be read.
LIST = {"org": 2, "purchaser": 5, "description": 6, "opening": 7, "status": 9, "alt_id": 10}
LOCAL = re.compile(r"School District|\bSD\b|County|City of|\bCity\b|Transit|\bESD\b|District|Port of|Community College|"
                   r"Housing Authority|Parks|Metro\b|TriMet|Library|Fire|Sanitary|Water", re.I)
CATEGORY = [(r"A and E|Architect|Engineer", "Engineering & Architecture"), (r"Construction|Public Improvement", "Construction"),
            (r"Information Technology|\bIT\b|Software", "IT & Software"), (r"Goods|Supplies|Equipment", "Goods & Supplies")]


def records():
    recs = json.loads(DATA_JSON.read_text(encoding="utf-8")) if DATA_JSON.exists() else []
    for r in recs:
        bid, h, cells = r["Bid Number"], r.get("Header") or {}, r.get("List Cells") or []

        def cell(k):
            return cells[LIST[k]] if len(cells) > LIST[k] and cells[LIST[k]] else None

        org = h.get("Organization") or cell("org")
        purchaser = h.get("Purchaser") or cell("purchaser")
        items = r.get("Items") or []
        disc = h.get("Discipline Type") or ""
        codes, seen = [], set()
        for it in items:
            m = re.match(r"\s*([\d-]+)\s+(.*)", it.get("nigp") or "")
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                codes.append({"system": "NIGP", "code": m.group(1), "title": clean(m.group(2)).title()})
        title = clean(h.get("Description") or cell("description")) or bid
        desc = "\n\n".join(x for x in [title, clean(h.get("Bulletin Desc"))] + [clean(it.get("description")) for it in items] if x)
        yield schema.empty(
            bid_uid=f"{SRC}:{bid}", id=f"{META['id_prefix']}{bid}", source=SRC, source_label=META["label"],
            platform=META["platform"], level="Local" if org and LOCAL.search(org) else "State", state="OR",
            source_id=bid, agency=org, agency_code=bid.split("-")[1] if bid.count("-") >= 2 else None,
            department=h.get("Department"), title=title, description=desc or None,
            procurement_type=" / ".join(x for x in (h.get("Procurement Method"), h.get("Purchase Method")) if x) or None,
            solicitation_number=h.get("Alternate Id") or cell("alt_id"), status=cell("status") or h.get("Bid Type"),
            published_at=local_to_pacific(h.get("Available Date"), FMTS, "OR"),
            due_at=local_to_pacific(h.get("Bid Opening Date") or cell("opening"), FMTS, "OR"),
            prebid_meeting=clean(h.get("Pre Bid Conference")),
            contacts=[{"role": "buyer", "name": purchaser, "title": None, "email": None, "phone": None}] if purchaser else [],
            codes=codes, attachments=[attachment(a, PROJECT) for a in r.get("Attachments") or []],
            documents=[{"name": "OregonBuys items", "text": "\n".join(
                f"- {it.get('nigp') or ''}: {clean(it.get('description')) or ''}" for it in items)}] if items else [],
            url=r.get("Detail URL"),
            extra={"Category": next((c for p, c in CATEGORY if re.search(p, disc)), None),
                   "Discipline Type": disc or None, "Bid Opening (local)": h.get("Bid Opening Date") or cell("opening"),
                   "Fiscal Year": h.get("Fiscal Year"), "Electronic Quote": h.get("Allow Electronic Quote"),
                   "Informal Bid": h.get("Informal Bid Flag"), "Ship-to": h.get("Ship-to Address") or None,
                   "Notice Type": h.get("Notice Type"), "Amendments": h.get("Amendments"),
                   "State Program": "Oregon COBID (MBE/WBE/SDV/ESB) - California certifications do not apply",
                   "Detail scraped": bool(h.get("Bid Number")), "Listed": r.get("Listed", True)},
            raw_ref=r.get("Raw Path"), scraped_at=r.get("Scraped At"),
        )
