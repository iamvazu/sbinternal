"""Bronze -> silver for Virginia eVA (state agencies, universities, localities).

Bronze input: data/bronze/eva/eVA_Data.json (the public Solr listing feed). Detail pages and documents sit behind
reCAPTCHA, so they are not scraped: the record links people to the opportunity page instead.
eVA's pubdate/closedate carry a 'Z' but are Virginia local (Eastern) time.
Department codes: "A..." state agency, "E..." higher education (state), "L..." locality.
"""
import json
import re

from pipeline import schema
from pipeline.paths import BRONZE, rel
from pipeline.sources import SOURCES
from pipeline.util import clean, local_to_pacific

SRC = "eva"
META = SOURCES[SRC]
DATA_JSON = BRONZE / SRC / "eVA_Data.json"
FMTS = ("%Y-%m-%dT%H:%M:%S",)
CATEGORY = [(r"^Professional Services - Construction", "Engineering & Architecture"), (r"Construction", "Construction"),
            (r"(?<!Non-)Technology", "IT & Software"), (r"^(Supplies|Equipment)", "Goods & Supplies"),
            (r"^Real Property", "Real Estate & Leasing"), (r"^Professional Services$", "Professional Services")]


def when(v):
    return local_to_pacific((v or "")[:19], FMTS, "VA")


def as_list(v):
    return v if isinstance(v, list) else [v] if v else []


def records():
    recs = json.loads(DATA_JSON.read_text(encoding="utf-8")) if DATA_JSON.exists() else []
    for r in recs:
        x, rid = r["Record"], r["ID"]
        dept = x.get("docdeptcd") or ""
        loc = x.get("workloc") or ""
        county = re.search(r"County:\s*(.+)$", loc)
        codes, seen = [], set()
        for code, desc in zip(as_list(x.get("commcode")), as_list(x.get("commdesc"))):
            if code not in seen:
                seen.add(code)
                codes.append({"system": "NIGP", "code": code, "title": desc})
        lines = as_list(x.get("commlinedesc"))
        cat = x.get("category") or ""
        yield schema.empty(
            bid_uid=f"{SRC}:{rid}", id=f"{META['id_prefix']}{rid}", source=SRC, source_label=META["label"],
            platform=META["platform"], level="Local" if dept.startswith("L") else "State", state="VA", source_id=rid,
            agency=x.get("agencyname"), agency_code=dept or None, department=clean(x.get("buyerdeptname")) or None,
            agency_city=county.group(1).strip() if county else None, title=clean(x.get("shortdesc")) or rid,
            description=clean(x.get("longdesc")), procurement_type=x.get("doccddesc"),
            solicitation_number=x.get("externalid"), version=x.get("version"), status=x.get("status"),
            published_at=when(x.get("pubdate")), due_at=when(x.get("closedate")),
            contacts=[{"role": "buyer", "name": x.get("buyername"), "title": None, "email": None, "phone": None}]
            if x.get("buyername") else [],
            codes=codes,
            documents=[{"name": "eVA line items", "text": "\n".join(f"- {clean(t)}" for t in lines)}] if lines else [],
            url=r.get("Detail URL"),
            extra={"Category": next((c for p, c in CATEGORY if re.search(p, cat)), None), "eVA Category": cat or None,
                   "Work Location": loc or None, "Set-Aside": x.get("setasideshortdesc"),
                   "Due (local, Eastern)": (x.get("closedate") or "")[:16].replace("T", " ") or None,
                   "Documents Note": r.get("Documents Note"),
                   "State Program": "Virginia SBSD SWaM certification - California certifications do not apply",
                   "Listed": r.get("Listed", True)},
            raw_ref=f"{rel(DATA_JSON)}#{rid}", scraped_at=r.get("Scraped At"),
        )
