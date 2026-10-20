"""Bronze -> silver for the Pennsylvania eMarketplace (Commonwealth agencies).

Bronze input: data/bronze/pa_emarketplace/PA_eMarketplace_Data.json. Times are Eastern.
"""
import json

from pipeline import schema
from pipeline.paths import BRONZE, PROJECT
from pipeline.sources import SOURCES
from pipeline.util import attachment, clean, local_to_pacific

SRC = "pa_emarketplace"
META = SOURCES[SRC]
DATA_JSON = BRONZE / SRC / "PA_eMarketplace_Data.json"
FMTS = ("%m/%d/%y %I:%M %p", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%y", "%m/%d/%Y")
CATEGORY = {"PW Construction": "Construction", "Agency Construction": "Construction", "Materials": "Goods & Supplies",
            "Real Estate": "Real Estate & Leasing"}


def records():
    recs = json.loads(DATA_JSON.read_text(encoding="utf-8")) if DATA_JSON.exists() else []
    for r in recs:
        sid, row, d = r["SID"], r.get("List Row") or {}, r.get("Detail") or {}
        due_local = (f"{d.get('Solicitation Due Date') or ''} {d.get('Solicitation Due Time') or ''}".strip()
                     or row.get("Solicitation Due Date"))
        name = " ".join(x for x in (d.get("First Name"), d.get("Last Name")) if x) or row.get("Contact Person")
        adv = d.get("Advertisement Type")
        opening = f"{d.get('Solicitation Opening Date') or ''} {d.get('Solicitation Opening Time') or ''}".strip()
        yield schema.empty(
            bid_uid=f"{SRC}:{sid}", id=f"{META['id_prefix']}{sid}", source=SRC, source_label=META["label"],
            platform=META["platform"], level="State", state="PA", source_id=sid,
            agency=d.get("Department/Agency") or row.get("Agency"), department=d.get("Department for this solicitation"),
            title=clean(d.get("Solicitation/Project Title") or row.get("Solicitation Title")) or sid,
            description=clean(d.get("Description") or row.get("Description")),
            procurement_type=d.get("Types") or row.get("Types"), solicitation_number=sid,
            version=d.get("No. of Addendums"), status=row.get("Status"),
            published_at=local_to_pacific(d.get("Solicitation Start Date") or row.get("Solicitation Start Date"), FMTS, "PA"),
            due_at=local_to_pacific(due_local, FMTS, "PA"),
            contacts=[{"role": "buyer", "name": name, "title": None, "email": d.get("Email"), "phone": None}]
            if name or d.get("Email") else [],
            attachments=[attachment(a, PROJECT) for a in r.get("Attachments") or []], url=r.get("Detail URL"),
            extra={"Category": CATEGORY.get(adv), "Advertisement Type": adv, "Sub Type": d.get("Sub Type"),
                   "Counties": [c.strip() for c in (d.get("County") or row.get("County") or "").split(",") if c.strip()],
                   "Duration": d.get("Duration"), "Delivery Location": d.get("Delivery Location") or None,
                   "Opening": opening or None, "Opening Location": d.get("Opening Location"),
                   "Due (local, Eastern)": due_local or None, "Amended Date": d.get("Amended Date") or row.get("Amended Date"),
                   "State Program": "Pennsylvania DGS Small Diverse / Small Business - California certifications do not apply",
                   "Listed": r.get("Listed", True)},
            raw_ref=r.get("Raw Path"), scraped_at=r.get("Scraped At"),
        )
