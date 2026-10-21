"""Bronze -> silver for SAM.gov Contract Opportunities (all federal agencies).

Bronze input: data/bronze/sam/SAM_Data.json (every active notice from SAM's public daily extract + attachments).
Only open, biddable notices go to silver (bronze keeps all ~71k, including award notices, as history).
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from pipeline import schema
from pipeline.adapters._federal import category, federal_extra, stage
from pipeline.paths import BRONZE, PROJECT
from pipeline.sources import SOURCES
from pipeline.util import PACIFIC, attachment, clean, html_to_text

SRC = "sam"
META = SOURCES[SRC]
DATA_JSON = BRONZE / SRC / "SAM_Data.json"
EASTERN = ZoneInfo("America/New_York")


def pacific(v, date_only_eod=False):
    """SAM times: ISO with offset, a bare date (end of day ET), or 'YYYY-MM-DD HH:MM:SS' (ET)."""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(hour=23, minute=59, tzinfo=EASTERN) if (date_only_eod and len(v) == 10) else d.replace(tzinfo=EASTERN)
    return d.astimezone(PACIFIC).strftime("%Y-%m-%dT%H:%M")


def contacts(r):
    out = []
    for role, p in (("primary", "Primary"), ("secondary", "Secondary")):
        if r.get(f"{p}ContactFullname") or r.get(f"{p}ContactEmail"):
            out.append({"role": role, "name": r.get(f"{p}ContactFullname") or None, "title": r.get(f"{p}ContactTitle") or None,
                        "email": r.get(f"{p}ContactEmail") or None, "phone": r.get(f"{p}ContactPhone") or None})
    return out


def records():
    recs = json.loads(DATA_JSON.read_text(encoding="utf-8")) if DATA_JSON.exists() else []
    for rec in recs:
        if not rec.get("Open"):
            continue
        r, nid = rec["Row"], rec["Notice ID"]
        desc = r.get("Description") or ""
        desc = html_to_text(desc) if "<" in desc else clean(desc)
        codes = ([{"system": "NAICS", "code": r["NaicsCode"], "title": None}] if r.get("NaicsCode") else []) + \
                ([{"system": "PSC", "code": r["ClassificationCode"], "title": None}] if r.get("ClassificationCode") else [])
        atts = [attachment(a, PROJECT) for a in rec.get("Attachments") or []]
        extra = federal_extra(r.get("SetASide"), r.get("PopState"), r.get("PopCity"))
        extra.update({
            "Notice Type": r.get("Type"), "Notice Stage": stage(r.get("Type")), "Base Type": r.get("BaseType"),
            "Category": category(r.get("NaicsCode"), r.get("ClassificationCode")), "Sub-Tier": r.get("Sub-Tier"),
            "Office": r.get("Office"), "AAC Code": r.get("AAC Code"), "CGAC": r.get("CGAC"), "FPDS Code": r.get("FPDS Code"),
            "Place of Performance": ", ".join(x for x in (r.get("PopStreetAddress"), r.get("PopCity"), r.get("PopState"),
                                                         r.get("PopZip"), r.get("PopCountry")) if x) or None,
            "Office Location": ", ".join(x for x in (r.get("City"), r.get("State"), r.get("ZipCode")) if x) or None,
            "Response Deadline (as posted)": r.get("ResponseDeadLine") or None, "Archive Date": r.get("ArchiveDate") or None,
            "Additional Info": r.get("AdditionalInfoLink") or None,
            "Attachments Note": None if rec.get("Attachments") is not None else
            "Attachments for this notice have not been downloaded yet; they are on its SAM.gov page",
            "Listed": rec.get("Listed", True),
        })
        yield schema.empty(
            bid_uid=f"{SRC}:{nid}", id=f"{META['id_prefix']}{nid}", source=SRC, source_label=META["label"],
            platform=META["platform"], level="Federal", state="US", source_id=nid,
            agency=r.get("Department/Ind.Agency") or None, agency_code=r.get("AAC Code") or r.get("FPDS Code") or None,
            department=r.get("Office") or r.get("Sub-Tier") or None, agency_city=(r.get("City") or "").title() or None,
            title=clean(r.get("Title")) or nid, description=desc or None, procurement_type=r.get("Type"),
            solicitation_number=r.get("Sol#") or None, status="Active" if r.get("Active") == "Yes" else r.get("Active"),
            published_at=pacific(r.get("PostedDate")), due_at=pacific(r.get("ResponseDeadLine"), date_only_eod=True),
            contacts=contacts(r), codes=codes, attachments=atts, url=f"https://sam.gov/opp/{nid}/view",  # public notice page
            extra=extra, raw_ref=f"data/bronze/sam/SAM_Data.json#{nid}", scraped_at=rec.get("Scraped At"),
        )
