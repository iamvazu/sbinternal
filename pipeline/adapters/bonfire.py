"""Bronze -> silver for Bonfire (Euna) public portals (MTC, ...)."""
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pipeline import schema
from pipeline.paths import BRONZE
from pipeline.sources import SOURCES
from pipeline.util import clean

SRC = "bonfire"
META = SOURCES[SRC]
EVENTS_JSON = BRONZE / SRC / "Bonfire_Events_Data.json"
PT = ZoneInfo("America/Los_Angeles")


def when(v):
    """'Oct 28th 2026, 4:00 PM PDT' -> '2026-10-28T16:00'."""
    if not v:
        return None
    v = re.sub(r"(\d)(st|nd|rd|th)", r"\1", v.strip())
    v = re.sub(r"\s+[A-Z]{2,4}$", "", v)
    try:
        return datetime.strptime(v, "%b %d %Y, %I:%M %p").strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def utc_to_pt(v):
    try:
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(PT).strftime("%Y-%m-%dT%H:%M")
    except (TypeError, ValueError):
        return None


def contact(text):
    if not text:
        return []
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    name = text.replace(m.group(0), "").strip(" ,") if m else text
    return [{"role": "buyer", "name": name or None, "title": None, "email": m.group(0) if m else None, "phone": None}]


def records():
    recs = json.loads(EVENTS_JSON.read_text(encoding="utf-8")) if EVENTS_JSON.exists() else []
    for r in recs:
        d = r.get("Details") or {}
        key = r["Key"]
        reqs = r.get("Requested Information") or []
        docs_text = []
        if reqs:
            docs_text.append({"name": "Bonfire requested information", "text": "\n".join(
                f"- {q.get('Name')} ({q.get('Type')}, {q.get('Requirement')})" for q in reqs)})
        yield schema.empty(
            bid_uid=f"{SRC}:{key}", id=f"{META['id_prefix']}{key}", source=SRC, source_label=META["label"],
            platform=META["platform"], level=META["level"], state="CA", source_id=r["Project ID"],
            agency=r.get("Agency"), agency_code=r.get("Portal"), department=r.get("Department"),
            agency_city="San Francisco" if r.get("Portal") == "mtc" else None,
            title=clean(d.get("Project") or r.get("Name")), description=clean(r.get("Description")),
            procurement_type=d.get("Type"), solicitation_number=d.get("Ref. #") or r.get("Reference ID"),
            status=d.get("Status") if r.get("Listed", True) else "not listed",
            published_at=when(d.get("Open Date")), due_at=when(d.get("Close Date")) or utc_to_pt(r.get("Close (portal time)")),
            question_deadline=d.get("Questions Due Date"), contacts=contact(d.get("Contact Information")),
            attachments=[{"file_name": x.get("File"), "url": r.get("Detail URL"), "local_path": None, "size_bytes": None,
                          "error": "Euna Supplier Network login required to download"} for x in r.get("Documents") or []],
            documents=docs_text, url=r.get("Detail URL"),
            extra={"Important Events": r.get("Events") or [], "Requested Information": reqs,
                   "Document Takers": r.get("Document Takers") or [], "Prime Contractors": r.get("Prime Contractors") or [],
                   "Subcontractors": r.get("Subcontractors") or [], "Document List": r.get("Documents") or [],
                   "Documents Note": r.get("Documents Note"), "Listed": r.get("Listed", True)},
            raw_ref=r.get("Raw Path"), scraped_at=r.get("Scraped At"),
        )
