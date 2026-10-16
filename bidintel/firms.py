"""Load the Cal eProcure certified-firm export (SB / MB / SB-PW / DVBE) into SQLite.

    python firms.py                       # ingest Caleprocure_businesses_final.xls
    python firms.py --embed               # optional: semantic vectors via local Ollama (nomic-embed-text)

Protected characteristics in the export (ethnicity, race, gender identity, LGBQTIA, disabled) are
deliberately never read into the database and never used for matching.
"""
import argparse
import html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import config
from db import connect

FIRMS_FILE = Path(config.os.getenv("BI_FIRMS_FILE", config.PROJECT / "Caleprocure_businesses_final.xls"))
EMBED_MODEL = config.os.getenv("BI_EMBED_MODEL", "nomic-embed-text")

PROTECTED = {"Ethnicity", "Race", "Gender Identity", "LGBQTIA", "Disabled"}

# CSLB classifications. The export truncates names to 30 characters, so match by prefix.
CSLB = [
    ("General Engineering", "A"), ("General Building", "B"), ("Residential Remodel", "B-2"),
    ("Insulation and Acoustical", "C-2"), ("Boiler, Hot Water Heating", "C-4"), ("Framing and Rough Carpentry", "C-5"),
    ("Cabinet, Millwork", "C-6"), ("Low Voltage Systems", "C-7"), ("Concrete Contractor", "C-8"), ("Drywall", "C-9"),
    ("Electrical Contractor", "C-10"), ("Elevator", "C-11"), ("Earthwork and Paving", "C-12"), ("Fencing Contractor", "C-13"),
    ("Flooring and Floor Covering", "C-15"), ("Fire Protection", "C-16"), ("Glazing", "C-17"),
    ("Warm-Air Heating", "C-20"), ("Building Moving/Demolition", "C-21"), ("Asbestos Abatement", "C-22"),
    ("Ornamental Metal", "C-23"), ("Landscaping Contractor", "C-27"), ("Lock and Security Equipment", "C-28"),
    ("Masonry", "C-29"), ("Construction Zone Traffic", "C-31"), ("Parking and Highway", "C-32"),
    ("Painting and Decorating", "C-33"), ("Pipeline", "C-34"), ("Lathing and Plastering", "C-35"),
    ("Plumbing", "C-36"), ("Refrigeration", "C-38"), ("Roofing", "C-39"), ("Sanitation System", "C-42"),
    ("Sheet Metal", "C-43"), ("Sign Contractor", "C-45"), ("Solar", "C-46"), ("General Manufactured Housing", "C-47"),
    ("Tree and Palm", "C-49"), ("Reinforcing Steel", "C-50"), ("Structural Steel", "C-51"), ("Swimming Pool", "C-53"),
    ("Ceramic and Mosaic Tile", "C-54"), ("Water Conditioning", "C-55"), ("Water Well Drilling", "C-57"),
    ("Welding", "C-60"), ("Limited Specialty", "C-61"),
    ("Hazardous Substance Removal", "HAZ"), ("Asbestos Certification", "ASB"), ("Home Improvement", "HIC"),
    # C-61 / D-subcategories
    ("Tree Service", "D-49"), ("Doors, Gates and Activating", "D-28"), ("Prefabricated Equipment", "D-34"),
    ("Concrete Related Services", "D-06"), ("Synthetic Products", "D-12"), ("Non-Electrical Sign", "D-42"),
    ("Construction Clean-up", "D-63"), ("Machinery and Pumps", "D-21"), ("Window Coverings", "D-52"),
    ("Sand and Water Blasting", "D-38"), ("Metal Products", "D-24"), ("Non-specialized", "D-64"),
    ("Drilling, Blasting and Oil", "D-09"), ("Hardware, Locks and Safes", "D-16"), ("Scaffolding", "D-35"),
    ("Hydroseed Spraying", "D-59"), ("Air and Water Balancing", "D-62"), ("Service Station Equipment", "D-40"),
    ("Trenching Only", "D-56"), ("Suspended Ceilings", "D-50"), ("Pile Driving", "D-30"),
    ("Pole Installation", "D-31"), ("Awnings", "D-03"), ("Waterproofing", "D-65"), ("Weatherization", "D-65"),
    ("Elevated Floors", "D-10"), ("Pool and Spa Maintenance", "D-35"), ("Paperhanging", "D-29"),
    ("Siding and Decking", "D-39"), ("Theater and School Equipment", "D-53"), ("Doors and Door Services", "D-28"),
    ("Marble", "D-23"), ("Central Vacuum", "D-04"), ("Wood Tanks", "D-55"), ("Floor Covering", "D-15"),
    ("Soil Grouting", "D-41"), ("Mirrors and Fixed Glass", "D-24"), ("Striping", "D-32"), ("Land Clearing", "D-21"),
    ("Safes and Vaults", "D-44"), ("Conveyors", "D-06"), ("Movable Partitions", "D-25"), ("Fencing", "C-13"),
    ("Rockscaping", "D-37"),
]

DIVCERTS = [("8(a)", "SBA 8(a)"), ("Disadvantaged Business", "DBE"), ("Minority Business", "MBE"),
            ("Woman Business", "WBE"), ("LGBT", "LGBTBE"), ("Other Non-State", "Other")]
JUNK = {"", "n/a", "na", "none", "null", "https://", "http://", "000/000-0000", "-", "0"}


def license_class(name):
    for prefix, cls in CSLB:
        if name.lower().startswith(prefix.lower()):
            return cls
    return None


def quoted_list(value):
    """"'A,'B,'C," -> ['A','B','C'] (the export prefixes items with an apostrophe)."""
    if not value:
        return []
    if "'" in value:
        return [p.strip(" ,") for p in value.split("'") if p.strip(" ,")]
    return [p.strip() for p in value.split(",") if p.strip()]


def clean(v):
    v = (v or "").strip()
    return None if v.lower() in JUNK else v


def date(v):
    try:
        return datetime.strptime(v, "%m/%d/%Y").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def certs_of(cert_type):
    out = set()
    for c in cert_type.split(","):
        c = c.strip()
        if c == "SB(Micro)":
            out |= {"SB", "MB"}
        elif c:
            out.add(c)
    return out


def read_rows(path):
    raw = path.read_text(encoding="utf-8", errors="ignore")
    heads = [html.unescape(h) for h in re.findall(r"<th>(.*?)</th>", raw[:5000])]
    for m in re.finditer(r"<tr>(.*?)</tr>", raw, re.S):
        cells = [html.unescape(c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)]
        if len(cells) == len(heads):
            yield {h: c for h, c in zip(heads, cells) if h not in PROTECTED}


def ingest():
    conn = connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS firms (id TEXT PRIMARY KEY, name TEXT, json TEXT, cert_end TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS firm_emb (id TEXT PRIMARY KEY, model TEXT, vec BLOB);
        CREATE TABLE IF NOT EXISTS bid_emb (event_id TEXT PRIMARY KEY, model TEXT, hash TEXT, vec BLOB);
    """)
    firms = {}
    n = 0
    for r in read_rows(FIRMS_FILE):
        n += 1
        fid = r["Certification ID"]
        f = firms.setdefault(fid, {
            "id": fid, "name": r["Legal Business Name"], "dba": clean(r["Doing Business As 1"]),
            "other_dbas": clean(r["Other DBAs"]), "certs": {}, "email": clean(r["Email ID"]),
            "contact": " ".join(x for x in (r["First Name"], r["Last Name"]) if x).strip() or None,
            "url": clean(r["URLID"]), "phone": clean(r["Telephone"]),
            "address": ", ".join(x for x in (r["Address Line 1"], r["Address Line 2"]) if clean(x)),
            "city": r["City"].strip().title(), "state": r["State"], "zip": r["Postal Code"][:5],
            "unspsc": set(), "keywords": set(), "naics": set(), "areas": set(), "licenses": {},
            "industry": set(), "divcerts": set()})
        start, end = date(r["Start Date"]), date(r["End Date"])
        for c in certs_of(r["Certification Type"]):
            prev = f["certs"].get(c)
            if not prev or (end or "") > prev["end"]:
                f["certs"][c] = {"start": start, "end": end or ""}
        f["unspsc"] |= {c for c in quoted_list(r["UNSPSC"]) if re.fullmatch(r"\d{8}", c)}
        f["naics"] |= {c for c in quoted_list(r["NAICS"]) if c.isdigit()}
        f["areas"] |= set(quoted_list(r["Service Areas"]))
        f["industry"] |= set(quoted_list(r["Industry Type"]))
        for kw in re.split(r"[,;]", r["Keywords"] or ""):
            kw = kw.strip(" .'\"").lower()
            if 2 <= len(kw) <= 60:
                f["keywords"].add(kw)
        for lic in quoted_list(r["License"]):
            f["licenses"][lic] = license_class(lic)
        for needle, code in DIVCERTS:
            if needle.lower() in (r["Supplier Diversity Certs"] or "").lower():
                f["divcerts"].add(code)

    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM firms")
    for f in firms.values():
        for k in ("unspsc", "keywords", "naics", "areas", "industry", "divcerts"):
            f[k] = sorted(f[k])
        f["license_classes"] = sorted({c for c in f["licenses"].values() if c})
        f["licenses"] = sorted(f["licenses"])
        f["statewide"] = len(f["areas"]) >= 50
        cert_end = max((c["end"] for c in f["certs"].values()), default="")
        conn.execute("INSERT INTO firms VALUES (?,?,?,?,?)", (f["id"], f["name"], json.dumps(f), cert_end, now))
    conn.commit()
    today = datetime.now().strftime("%Y-%m-%d")
    active = conn.execute("SELECT COUNT(*) FROM firms WHERE cert_end >= ?", (today,)).fetchone()[0]
    unmapped = {l for f in firms.values() for l in f["licenses"] if not license_class(l)}
    print(f"{n} rows -> {len(firms)} firms ({active} with an active certification today)")
    if unmapped:
        print("license names without a CSLB class:", sorted(unmapped))


def firm_text(f):
    return " ".join([f["name"], f.get("dba") or "", f.get("other_dbas") or "", ", ".join(f["keywords"]),
                     ", ".join(f["licenses"]), ", ".join(f["industry"])])


def embed(batch=64):
    """Semantic vectors for every firm, via local Ollama. Resumable; ~10-20 min on CPU, a few on the Jetson."""
    import numpy as np
    import urllib.request
    conn = connect()
    done = {r[0] for r in conn.execute("SELECT id FROM firm_emb WHERE model=?", (EMBED_MODEL,))}
    todo = [json.loads(r[0]) for r in conn.execute("SELECT json FROM firms")]
    todo = [f for f in todo if f["id"] not in done]
    print(f"embedding {len(todo)} firms with {EMBED_MODEL}")
    t = time.time()
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        payload = {"model": EMBED_MODEL, "input": ["search_document: " + firm_text(f)[:2000] for f in chunk]}
        req = urllib.request.Request(f"{config.LLM_URL.rstrip('/')}/api/embed", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            vecs = json.loads(r.read())["embeddings"]
        for f, v in zip(chunk, vecs):
            a = np.asarray(v, dtype=np.float32)
            a /= (np.linalg.norm(a) or 1)
            conn.execute("INSERT OR REPLACE INTO firm_emb VALUES (?,?,?)", (f["id"], EMBED_MODEL, a.tobytes()))
        conn.commit()
        done_n = i + len(chunk)
        rate = done_n / (time.time() - t)
        print(f"  {done_n}/{len(todo)}  {rate:.0f}/s  eta {(len(todo) - done_n) / rate / 60:.1f} min", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed", action="store_true")
    a = ap.parse_args()
    embed() if a.embed else ingest()
    sys.exit(0)
