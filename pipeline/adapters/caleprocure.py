"""Bronze -> silver for Cal eProcure (California state agencies).

Bronze inputs (data/bronze/caleprocure/):
  Caleprocure_Events_Data.json   detailed events from the Selenium scrapers
  attachments/<event_id>/        downloaded files
and scrapers/caleprocure/exports/caleprocure_*.xls (the portal's "Excel" export, really an HTML table) which
fills gaps (buyer email, status) and adds events that were listed but not yet scraped in detail.
"""
import glob
import json
from pathlib import Path

from pipeline import schema
from pipeline.paths import BRONZE, PROJECT, SCRAPERS, rel
from pipeline.sources import SOURCES
from pipeline.util import clean, contact, parse_pacific

SRC = "caleprocure"
META = SOURCES[SRC]
BRONZE_DIR = BRONZE / SRC
EVENTS_JSON = BRONZE_DIR / "Caleprocure_Events_Data.json"
EXPORTS = SCRAPERS / SRC / "exports"


def read_exports():
    from bs4 import BeautifulSoup
    rows = {}
    for f in sorted(glob.glob(str(EXPORTS / "caleprocure_*.xls"))):  # newest file wins
        table = BeautifulSoup(Path(f).read_text(encoding="utf-8", errors="ignore"), "html.parser").find("table")
        if not table:
            continue
        trs = table.find_all("tr")
        headers = [th.get_text(strip=True) for th in trs[0].find_all(["th", "td"])]
        for tr in trs[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) == len(headers):
                rec = dict(zip(headers, cells))
                rows[rec.get("Event ID")] = rec
    return rows


def resolve_attachment(local_path):
    """Scrapers wrote paths relative to wherever they ran ("attachments\\<id>\\f.pdf") or absolute.
    Map either onto data/bronze/caleprocure/attachments/... and return it project-relative."""
    if not local_path:
        return None
    parts = Path(str(local_path).replace("\\", "/")).parts
    if "attachments" in parts:
        p = BRONZE_DIR.joinpath(*parts[parts.index("attachments"):])
        if p.is_file():
            return rel(p)
    p = Path(local_path)
    p = p if p.is_absolute() else PROJECT / p
    return rel(p) if p.is_file() else None


def records():
    events = json.loads(EVENTS_JSON.read_text(encoding="utf-8")) if EVENTS_JSON.exists() else []
    exports = read_exports()
    seen = set()
    for ev in events:
        d = ev.get("Details") or {}
        eid = d.get("Event ID")
        if not eid:
            continue
        seen.add(eid)
        c = ev.get("Contact Information") or {}
        x = exports.get(eid, {})
        atts = []
        for a in ev.get("Attachments") or []:
            lp = resolve_attachment(a.get("local_path"))
            atts.append({"file_name": a.get("file_name"), "url": a.get("download_url"), "local_path": lp,
                         "size_bytes": a.get("size_bytes"), "error": None if lp else "file missing on disk"})
        yield schema.empty(
            bid_uid=f"{SRC}:{eid}", id=f"{META['id_prefix']}{eid}", source=SRC, source_label=META["label"],
            platform=META["platform"], level=META["level"], source_id=eid,
            agency=d.get("Dept"), agency_code=d.get("Department Code") or x.get("Department"),
            title=clean(ev.get("Event Name")), description=clean(ev.get("Description")),
            procurement_type=d.get("Format/Type"), version=d.get("Event Version"),
            status=d.get("Status") or x.get("Status"),
            published_at=parse_pacific(d.get("Published Date")),
            due_at=parse_pacific(d.get("Event End Date")) or parse_pacific(x.get("End Date")),
            contacts=[c_ for c_ in [contact("buyer", c.get("Contact Name") or x.get("Buyer Name"),
                                            email=c.get("Email") or x.get("Buyer Email"), phone=c.get("Phone"))] if c_],
            codes=[{"system": "UNSPSC", "code": u.get("UNSPSC Classification"),
                    "title": u.get("UNSPSC Classification Description")} for u in ev.get("UNSPSC Codes") or []],
            attachments=atts, url=ev.get("Detail URL"),
            raw_ref=f"{rel(EVENTS_JSON)}#{eid}", scraped_at=None,
        )
    for eid, x in exports.items():  # listed on the portal but not scraped in detail yet
        if eid and eid not in seen:
            yield schema.empty(
                bid_uid=f"{SRC}:{eid}", id=f"{META['id_prefix']}{eid}", source=SRC, source_label=META["label"],
                platform=META["platform"], level=META["level"], source_id=eid,
                agency=x.get("Department Name"), agency_code=x.get("Department"), title=clean(x.get("Event Name")),
                procurement_type=f"{x.get('Format', '')} / {x.get('Type', '')}", status=x.get("Status"),
                due_at=parse_pacific(x.get("End Date")),
                contacts=[c_ for c_ in [contact("buyer", x.get("Buyer Name"), email=x.get("Buyer Email"))] if c_],
                url="https://caleprocure.ca.gov/pages/Events-BS3/event-search.aspx",
                extra={"Detail scraped": False}, raw_ref="scrapers/caleprocure/exports",
            )
