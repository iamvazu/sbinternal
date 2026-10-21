"""Bronze -> silver for FedConnect public opportunities (DOE, DOI, EPA, NRC, USAID ... contracts and funding).

Bronze input: data/bronze/fedconnect/FedConnect_Data.json. Opportunities whose reference number is also a SAM.gov
solicitation are skipped here (SAM is the record of truth); FedConnect-only ones (often grants/cooperative
agreements) are kept. Times are stated in US/Eastern.
"""
import json
import re

from pipeline import schema
from pipeline.adapters._federal import category, federal_extra, norm_sol, sam_solicitations, stage
from pipeline.paths import BRONZE, PROJECT
from pipeline.sources import SOURCES
from pipeline.util import attachment, clean, local_to_pacific

SRC = "fedconnect"
META = SOURCES[SRC]
DATA_JSON = BRONZE / SRC / "FedConnect_Data.json"
FMTS = ("%m/%d/%Y %I:%M %p", "%m/%d/%Y")


def when(v):
    return local_to_pacific(re.sub(r"\s*US/\w+$", "", v or ""), FMTS, "VA")  # VA = an Eastern-time zone key


def code(v, system):
    m = re.match(r"\s*([\w.]+)\s*-\s*(.*)", v or "")
    return [{"system": system, "code": m.group(1), "title": clean(m.group(2))}] if m else []


def fix(text):
    return clean(text.replace("�", "-")) if text else text  # FedConnect mixes Windows-1252 dashes into UTF-8 pages


def records():
    recs = json.loads(DATA_JSON.read_text(encoding="utf-8")) if DATA_JSON.exists() else []
    on_sam = sam_solicitations()
    for r in recs:
        ref, row, f = r["Reference Number"], r.get("List Row") or {}, r.get("Summary") or {}
        if norm_sol(ref) in on_sam or not f:
            continue
        extra = federal_extra(f.get("Set Aside"))
        extra.update({"Contracting Office": f.get("Contracting office"), "Place of Performance": f.get("Place of Performance") or None,
                      "Vendors Conference": f.get("Vendors conference") or None, "Notice Type": row.get("Type"),
                      "Notice Stage": stage(row.get("Type")),
                      "Category": category((f.get("NAICS") or "").split("-")[0], (f.get("PSC / FSC") or "").split("-")[0]),
                      "Response Due (as posted)": f.get("Response due") or row.get("Response Due Date"),
                      "Documents": r.get("Documents") or [],
                      "Documents Note": "Submitting a response needs a free FedConnect account; documents are public.",
                      "Listed": r.get("Listed", True)})
        m = re.search(r",\s*([A-Z]{2})\s+\d{5}", f.get("Contracting office") or "")
        yield schema.empty(
            bid_uid=f"{SRC}:{ref}", id=f"{META['id_prefix']}{ref}", source=SRC, source_label=META["label"],
            platform=META["platform"], level="Federal", state="US", source_id=ref,
            agency=f.get("Agency") or row.get("Agency"), department=row.get("Issuing Office"),
            title=fix(row.get("Title")) or ref, description=fix(f.get("Description")),
            procurement_type=row.get("Type"), solicitation_number=ref,
            published_at=when(f.get("Issue date") or row.get("Issue Date")), due_at=when(f.get("Response due") or row.get("Response Due Date")),
            prebid_meeting=f.get("Vendors conference") or None,
            contacts=[{"role": "contact", "name": f.get("Contact") or None, "title": None, "email": f.get("Email") or None,
                       "phone": f.get("Phone") or None}] if f.get("Contact") or f.get("Email") else [],
            codes=code(f.get("NAICS"), "NAICS") + code(f.get("PSC / FSC"), "PSC"),
            attachments=[attachment(a, PROJECT) for a in r.get("Attachments") or []], url=r.get("Detail URL"),
            extra={**extra, "Office State": m.group(1) if m else None},
            raw_ref=r.get("Raw Path"), scraped_at=r.get("Scraped At"),
        )
