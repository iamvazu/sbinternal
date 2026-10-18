"""Bronze -> silver for OpenGov Procurement (California local agencies).

Bronze inputs (data/bronze/opengov/): OpenGov_Events_Data.json, attachments/<project_id>/, raw/<project_id>.json
"""
import json

from pipeline import schema
from pipeline.paths import BRONZE, PROJECT, rel
from pipeline.sources import SOURCES
from pipeline.util import clean, contact, parse_pacific

SRC = "opengov"
META = SOURCES[SRC]
BRONZE_DIR = BRONZE / SRC
EVENTS_JSON = BRONZE_DIR / "OpenGov_Events_Data.json"
EXTRA_KEYS = ("Solicitation Number", "Department", "Agency City", "Question Deadline", "Q&A Response Deadline",
              "Pre-Bid Meeting", "Sealed Bid", "Addenda", "Vendors Notified", "Listed")


def local(path):
    if not path:
        return None
    p = PROJECT / str(path).replace("\\", "/")
    return rel(p) if p.is_file() else None


def records():
    recs = json.loads(EVENTS_JSON.read_text(encoding="utf-8")) if EVENTS_JSON.exists() else []
    for ev in recs:
        d = ev["Details"]
        pid = d["Event ID"].removeprefix("OG-")
        c, pc = ev.get("Contact Information") or {}, ev.get("Procurement Contact") or {}
        extra = {k: d.get(k) for k in EXTRA_KEYS}
        extra.update({"Procurement Contact": pc, "Commodity Codes": ev.get("Commodity Codes") or [],
                      "Timeline": ev.get("Timeline") or [], "Submission Requirements": ev.get("Submission Requirements") or [],
                      "Evaluation Criteria": ev.get("Evaluation Criteria") or [], "Price Items": ev.get("Price Items") or [],
                      "Raw Path": ev.get("Raw Path")})
        atts = []
        for a in ev.get("Attachments") or []:
            lp = local(a.get("local_path"))
            atts.append({"file_name": a.get("file_name"), "url": a.get("download_url"), "local_path": lp,
                         "size_bytes": a.get("size_bytes"), "error": a.get("error") or (None if lp else "file missing on disk")})
        sol = clean(ev.get("Solicitation Text") or "")
        yield schema.empty(
            bid_uid=f"{SRC}:{pid}", id=f"{META['id_prefix']}{pid}", source=SRC, source_label=META["label"],
            platform=META["platform"], level=META["level"], source_id=pid,
            agency=d.get("Dept"), agency_code=d.get("Department Code"), department=d.get("Department"),
            agency_city=d.get("Agency City"), title=clean(ev.get("Event Name")), description=clean(ev.get("Description")),
            procurement_type=d.get("Format/Type"), solicitation_number=d.get("Solicitation Number"),
            version=str(d.get("Addenda") or 0),
            status=d.get("Status") if d.get("Listed", True) else "not listed",
            published_at=parse_pacific(d.get("Published Date")), due_at=parse_pacific(d.get("Event End Date")),
            question_deadline=d.get("Question Deadline"), prebid_meeting=d.get("Pre-Bid Meeting"),
            contacts=[x for x in (contact("project", c.get("Contact Name"), c.get("Title"), c.get("Email"), c.get("Phone")),
                                  contact("procurement", pc.get("Name"), pc.get("Title"), pc.get("Email"), pc.get("Phone"))) if x],
            codes=[{"system": k.get("system") or "Other", "code": k.get("code"), "title": k.get("title")}
                   for k in ev.get("Commodity Codes") or []],
            attachments=atts,
            documents=[{"name": "OpenGov solicitation text", "text": sol}] if sol else [],
            url=ev.get("Detail URL"), extra=extra,
            raw_ref=(ev.get("Raw Path") or "").replace("\\", "/") or None, scraped_at=ev.get("Scraped At"),
        )
