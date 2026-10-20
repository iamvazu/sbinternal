"""Bronze -> silver for the Texas Electronic State Business Daily (state agencies + SmartBuy member locals).

Bronze input: data/bronze/tx_esbd/TX_ESBD_Data.json. Response due date/time are Central time.
Agency numbers: digits = state agency; a letter prefix (M, S, J, G, E, R, K ...) = SmartBuy member (local).
"""
import json
import re

from pipeline import schema
from pipeline.paths import BRONZE, PROJECT
from pipeline.sources import SOURCES
from pipeline.util import attachment, clean, html_to_text, local_to_pacific

SRC = "tx_esbd"
META = SOURCES[SRC]
DATA_JSON = BRONZE / SRC / "TX_ESBD_Data.json"
FMTS = ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y")
GOODS_OR_SERVICE = {"1": "Goods", "2": "Services"}


def nigp(text):
    out, seen = [], set()
    for part in re.split(r";\s*", text or ""):
        m = re.match(r"\s*(\d+)\s*-\s*(.+)", part)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append({"system": "NIGP", "code": m.group(1), "title": clean(m.group(2))})
    return out


def money(v):
    try:
        f = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return f"${f:,.0f}" if f > 0 else None


def records():
    recs = json.loads(DATA_JSON.read_text(encoding="utf-8")) if DATA_JSON.exists() else []
    for r in recs:
        d = r.get("Detail") or r.get("List") or {}
        sid = r["Solicitation ID"]
        agency_no = d.get("agencyNumber") or ""
        due_local = f"{d.get('responseDue') or ''} {d.get('responseTime') or ''}".strip()
        agency = re.sub(rf"\s*-\s*{re.escape(agency_no)}$", "", d.get("agencyName") or "") if agency_no else d.get("agencyName")
        kind = sid.split("-")[0]
        yield schema.empty(
            bid_uid=f"{SRC}:{sid}", id=f"{META['id_prefix']}{sid}", source=SRC, source_label=META["label"],
            platform=META["platform"], level="State" if agency_no.isdigit() else "Local", state="TX", source_id=sid,
            agency=agency or None, agency_code=agency_no or None, title=clean(d.get("title")) or sid,
            description=html_to_text(d.get("description")),
            procurement_type=kind if "-" in sid and kind.isalpha() else None,
            solicitation_number=sid, version=d.get("addendum") or None, status=d.get("statusName"),
            published_at=local_to_pacific(d.get("postingDate"), FMTS, "TX"),
            due_at=local_to_pacific(due_local, FMTS, "TX"),
            contacts=[{"role": "buyer", "name": d.get("contactName"), "title": None, "email": d.get("contactEmail"),
                       "phone": d.get("contactNumber")}] if d.get("contactName") or d.get("contactEmail") else [],
            codes=nigp(d.get("nigpCodes")), attachments=[attachment(a, PROJECT) for a in r.get("Attachments") or []],
            url=r.get("Detail URL"),
            extra={"Due (local, Central)": due_local or None, "Estimated Value": money(d.get("value")),
                   "Goods or Service": GOODS_OR_SERVICE.get(str(d.get("goodsOrService"))),
                   "Posting Requirement": d.get("postingRequirementText"),
                   "Agency Certification": d.get("stateAgencyCertText"),
                   "Statutory Exemption": d.get("statutoryExemption") or None,
                   "TxDOT Highway Districts": d.get("highwayDistricts") or [], "Zones": d.get("zones") or [],
                   "Bid Response Email": d.get("bidResponseEmail"),
                   "Bid Response URL": d.get("bidResponseURL") or d.get("url") or None,
                   "State Program": "Texas HUB certification - California certifications do not apply",
                   "Listed": r.get("Listed", True)},
            raw_ref=r.get("Raw Path"), scraped_at=r.get("Scraped At"),
        )
