"""Bid <-> certified-business matching engine.

Pipeline per bid:  build bid profile -> recall candidates (UNSPSC hierarchy | BM25 with learned query
expansion | semantic vectors) -> score explainable features -> calibrate 0-100 -> assign roles.
Everything is local: plain Python + numpy, optional vectors from a local Ollama embedding model.
"""
import json
import math
import pickle
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime

import config
import insights
from db import connect, loads

EMBED_MODEL = config.os.getenv("BI_EMBED_MODEL", "nomic-embed-text")
INDEX_CACHE = config.DB_PATH.parent / "firm_index.pkl"
INDEX_VERSION = 6

LEVEL_WEIGHT = {8: 1.0, 6: 0.8, 4: 0.5, 2: 0.12}
LEVEL_NAME = {8: "exact commodity", 6: "same class", 4: "same family", 2: "same segment"}

STOP = set("""a an and are as at be by for from has have in inc is it its llc of on or our the to with we
co corp company corporation services service general other misc misc. all any etc ltd dba &""".split())

CALTRANS_DISTRICTS = {
    1: ["Del Norte", "Humboldt", "Lake", "Mendocino"],
    2: ["Lassen", "Modoc", "Plumas", "Shasta", "Siskiyou", "Tehama", "Trinity"],
    3: ["Butte", "Colusa", "El Dorado", "Glenn", "Nevada", "Placer", "Sacramento", "Sierra", "Sutter", "Yolo", "Yuba"],
    4: ["Alameda", "Contra Costa", "Marin", "Napa", "San Francisco", "San Mateo", "Santa Clara", "Solano", "Sonoma"],
    5: ["Monterey", "San Benito", "San Luis Obispo", "Santa Barbara", "Santa Cruz"],
    6: ["Fresno", "Kern", "Kings", "Madera", "Tulare"],
    7: ["Los Angeles", "Ventura"],
    8: ["Riverside", "San Bernardino"],
    9: ["Inyo", "Mono"],
    10: ["Alpine", "Amador", "Calaveras", "Mariposa", "Merced", "San Joaquin", "Stanislaus", "Tuolumne"],
    11: ["Imperial", "San Diego"],
    12: ["Orange"],
}
COUNTIES = sorted({c for v in CALTRANS_DISTRICTS.values() for c in v}, key=len, reverse=True)
# Cities that host many state facilities (prisons, CHP offices, hospitals, parks) -> county.
CITY_COUNTY = {
    "Tracy": "San Joaquin", "Stockton": "San Joaquin", "Lodi": "San Joaquin", "Folsom": "Sacramento",
    "Elk Grove": "Sacramento", "Rancho Cordova": "Sacramento", "Ione": "Amador", "Jamestown": "Tuolumne",
    "Vacaville": "Solano", "Fairfield": "Solano", "Vallejo": "Solano", "San Quentin": "Marin", "Soledad": "Monterey",
    "Salinas": "Monterey", "Coalinga": "Fresno", "Avenal": "Kings", "Corcoran": "Kings", "Hanford": "Kings",
    "Delano": "Kern", "Bakersfield": "Kern", "Tehachapi": "Kern", "Wasco": "Kern", "Chowchilla": "Madera",
    "Norco": "Riverside", "Blythe": "Riverside", "Chino": "San Bernardino", "Rancho Cucamonga": "San Bernardino",
    "Barstow": "San Bernardino", "Victorville": "San Bernardino", "Lancaster": "Los Angeles", "Pomona": "Los Angeles",
    "Long Beach": "Los Angeles", "Pasadena": "Los Angeles", "Calipatria": "Imperial", "El Centro": "Imperial",
    "Imperial": "Imperial", "San Luis Obispo": "San Luis Obispo", "Atascadero": "San Luis Obispo", "Susanville": "Lassen",
    "Crescent City": "Del Norte", "Redding": "Shasta", "Eureka": "Humboldt", "Ukiah": "Mendocino", "Chico": "Butte",
    "Oroville": "Butte", "Marysville": "Yuba", "Yuba City": "Sutter", "Woodland": "Yolo", "Davis": "Yolo",
    "Auburn": "Placer", "Roseville": "Placer", "Rocklin": "Placer", "Placerville": "El Dorado", "Grass Valley": "Nevada",
    "Truckee": "Nevada", "Modesto": "Stanislaus", "Turlock": "Stanislaus", "Merced": "Merced", "Atwater": "Merced",
    "Visalia": "Tulare", "Porterville": "Tulare", "Napa": "Napa", "Santa Rosa": "Sonoma", "Petaluma": "Sonoma",
    "Oakland": "Alameda", "Berkeley": "Alameda", "Hayward": "Alameda", "Fremont": "Alameda", "San Jose": "Santa Clara",
    "Martinez": "Contra Costa", "Richmond": "Contra Costa", "Concord": "Contra Costa", "Santa Ana": "Orange",
    "Irvine": "Orange", "Costa Mesa": "Orange", "Anaheim": "Orange", "Oxnard": "Ventura", "Camarillo": "Ventura",
    "Bishop": "Inyo", "Bridgeport": "Mono", "Mammoth Lakes": "Mono", "San Andreas": "Calaveras", "Sonora": "Tuolumne",
    "Hollister": "San Benito", "Watsonville": "Santa Cruz", "Santa Maria": "Santa Barbara", "Lompoc": "Santa Barbara",
    "Yreka": "Siskiyou", "Weed": "Siskiyou", "Red Bluff": "Tehama", "Weaverville": "Trinity", "Quincy": "Plumas",
    "Alturas": "Modoc", "Willows": "Glenn", "Colusa": "Colusa", "Lakeport": "Lake", "Mariposa": "Mariposa",
    "Markleeville": "Alpine", "Downieville": "Sierra", "Jackson": "Amador",
}
COUNTY_RE = re.compile(r"\b(" + "|".join(re.escape(c) for c in COUNTIES) + r")\b(?:\s+Count(?:y|ies))?")
CITY_RE = re.compile(r"\b(" + "|".join(re.escape(c) for c in sorted(CITY_COUNTY, key=len, reverse=True)) + r")\b")
LICENSE_CLASS = re.compile(r"\b([AB])\b(?:\s*[-–(]|\s+(?:license|contractor|general))|\(\s*([AB])\s*\)|\b(C-?\s?\d{1,2}|D-\d{2})\b|^([AB])$")
LICENSE_WORDS = {"general engineering": "A", "general building": "B", "electrical": "C-10", "fire protection": "C-16",
                 "plumbing": "C-36", "roofing": "C-39", "landscap": "C-27", "painting": "C-33", "paving": "C-12",
                 "hvac": "C-20", "air-conditioning": "C-20", "low voltage": "C-7", "elevator": "C-11",
                 "asbestos": "C-22", "hazardous substance": "HAZ", "fencing": "C-13", "glazing": "C-17"}

CERT_PRIME_SB = {"SB", "MB", "DVBE"}
BOILERPLATE_WORDS = """bid bids bidder bidders ifb rfp rfq rfi state california contract contractor contractors
department provide providing shall rebid addendum dept statewide location locations pursuant agreement invite invitation
prospective proposal proposals solicitation solicit request event number award awarded submit submittal due date
qualified responsive responsible interested firm firms vendor vendors purpose seeking seek issue issued notice the this
cdcr chp caltrans dgs calfire cal fire""".split()
NOCODE_WEIGHTS = (0.55, 0.45)  # text, semantic when a bid lists no UNSPSC codes
PROGRAM_WORDS = """county counties certified certification sb mb dvbe dbe sb-pw only small business businesses micro
microbusiness set-aside aside option disabled veteran enterprise""".split()
ON_SITE = {"Construction", "Facilities & Maintenance", "Environmental & Waste", "Transportation & Logistics",
           "Safety & Security", "Real Estate & Leasing"}
# CSLB replaced D-49 Tree Service with C-49 Tree and Palm; asbestos work needs C-22 or the ASB certification.
LICENSE_EQUIV = [("C-49", "D-49"), ("D-49", "C-49"), ("C-22", "ASB"), ("C-61", "D-64")]


# ---------------------------------------------------------------- text

def stem(w):
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(text):
    return [stem(w) for w in re.findall(r"[a-z][a-z0-9\-]+", (text or "").lower()) if w not in STOP and len(w) > 1]


def firm_doc(f):
    return " ".join([f["name"], f.get("dba") or "", f.get("other_dbas") or "", " , ".join(f["keywords"]),
                     " ".join(f["licenses"])])


# ---------------------------------------------------------------- firm index

class FirmIndex:
    def __init__(self, firms, vectors=None, vec_ids=None):
        self.firms = firms
        self.N = len(firms)
        self.pos = {f["id"]: i for i, f in enumerate(firms)}
        self.today = datetime.now().strftime("%Y-%m-%d")
        # UNSPSC hierarchy -> firms
        self.code_ix = {lvl: defaultdict(set) for lvl in (8, 6, 4, 2)}
        self.prefixes = []
        for i, f in enumerate(firms):
            p = {lvl: {c[:lvl] for c in f["unspsc"]} for lvl in (8, 6, 4, 2)}
            self.prefixes.append(p)
            for lvl, s in p.items():
                for c in s:
                    self.code_ix[lvl][c].add(i)
        # BM25
        self.doc_tf, self.doc_len = [], []
        self.postings = defaultdict(list)
        for i, f in enumerate(firms):
            tf = Counter(tokens(firm_doc(f)))
            self.doc_tf.append(tf)
            self.doc_len.append(sum(tf.values()))
            for t, n in tf.items():
                self.postings[t].append((i, n))
        self.avgdl = sum(self.doc_len) / max(1, self.N)
        self.idf = {t: math.log(1 + (self.N - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}
        self.expansion = self._learn_expansion()
        self.zip_county, self.zip3_county, self.city_county = self._learn_geography()
        self.vectors, self.vec_pos = None, {}
        if vectors is not None:
            self.vectors = vectors
            self.vec_pos = {fid: k for k, fid in enumerate(vec_ids)}

    def _learn_expansion(self):
        """For each UNSPSC class (6) and family (4): the keyword stems most over-represented among firms
        holding it. Learned from the 21k certified firms themselves - no external taxonomy needed."""
        kw_sets = [set(tokens(" ".join(f["keywords"]))) for f in self.firms]
        glob = Counter(t for s in kw_sets for t in s)
        out = {}
        for lvl in (6, 4):
            for code, members in self.code_ix[lvl].items():
                if len(members) < 8:
                    continue
                c = Counter(t for i in members for t in kw_sets[i])
                n = len(members)
                scored = []
                for t, k in c.items():
                    if k < 4 or len(t) < 3:
                        continue
                    lift = (k / n) / (glob[t] / self.N)
                    if lift >= 2.5:
                        scored.append((k / n * math.log(lift), t))
                out[code] = [t for _, t in sorted(scored, reverse=True)[:10]]
        return out

    def _learn_geography(self):
        """The export has no county column, but firms that serve only 1-3 counties almost always serve
        their home county. Majority vote over those firms gives zip -> county and city -> county maps."""
        votes = {"zip": defaultdict(Counter), "zip3": defaultdict(Counter), "city": defaultdict(Counter)}
        for f in self.firms:
            if 1 <= len(f["areas"]) <= 3 and f["state"] == "CA":
                for a in f["areas"]:
                    votes["zip"][f["zip"]][a] += 1
                    votes["zip3"][f["zip"][:3]][a] += 1
                    votes["city"][f["city"].lower()][a] += 1

        def winners(v, min_n):
            out = {}
            for k, c in v.items():
                (top, n), total = c.most_common(1)[0], sum(c.values())
                if n >= min_n and n / total >= 0.5:
                    out[k] = top
            return out
        city = winners(votes["city"], 2)
        city.update({k.lower(): v for k, v in CITY_COUNTY.items()})
        return winners(votes["zip"], 2), winners(votes["zip3"], 3), city

    def locate(self, zipcode=None, city=None):
        if zipcode:
            c = self.zip_county.get(zipcode) or self.zip3_county.get(zipcode[:3])
            if c:
                return c
        return self.city_county.get((city or "").strip().lower())

    def bm25(self, qweights):
        k1, b = 1.2, 0.75
        scores = defaultdict(float)
        for t, w in qweights.items():
            idf = self.idf.get(t)
            if not idf:
                continue
            for i, tf in self.postings[t]:
                dl = self.doc_len[i]
                scores[i] += w * idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / self.avgdl))
        return scores

    def bm25_one(self, qweights, i):
        k1, b = 1.2, 0.75
        tf_map, dl, s = self.doc_tf[i], self.doc_len[i], 0.0
        for t, w in qweights.items():
            tf = tf_map.get(t)
            if tf:
                s += w * self.idf[t] * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / self.avgdl))
        return s

    def specificity(self, lvl, code):
        df = len(self.code_ix[lvl].get(code, ()))
        if not df:
            return 0.0
        return 0.55 + 0.45 * min(1.0, math.log(self.N / df) / math.log(self.N / 10))


_index = None
_index_lock = threading.Lock()


def load_index(force=False):
    global _index
    with _index_lock:
        if _index is not None and not force:
            return _index
        conn = connect()
        stamp = conn.execute("SELECT MAX(updated), COUNT(*) FROM firms").fetchone()
        nvec = conn.execute("SELECT COUNT(*) FROM firm_emb WHERE model=?", (EMBED_MODEL,)).fetchone()[0]
        key = (INDEX_VERSION, stamp[0], stamp[1], nvec)
        if INDEX_CACHE.exists() and not force:
            try:
                with open(INDEX_CACHE, "rb") as fh:
                    cached_key, idx = pickle.load(fh)
                if cached_key == key:
                    _index = idx
                    return _index
            except Exception:
                pass
        t = time.time()
        firms = [json.loads(r[0]) for r in conn.execute("SELECT json FROM firms ORDER BY id")]
        vectors = vec_ids = None
        if nvec:
            import numpy as np
            rows = conn.execute("SELECT id, vec FROM firm_emb WHERE model=?", (EMBED_MODEL,)).fetchall()
            vec_ids = [r[0] for r in rows]
            vectors = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
        _index = FirmIndex(firms, vectors, vec_ids)
        with open(INDEX_CACHE, "wb") as fh:
            pickle.dump((key, _index), fh)
        print(f"firm index built: {len(firms)} firms, {nvec} vectors, {time.time() - t:.1f}s")
        return _index


# ---------------------------------------------------------------- bid profile

def parse_license_classes(items):
    out = set()
    for s in items or []:
        s = str(s)
        for m in LICENSE_CLASS.finditer(s):
            c = next(g for g in m.groups() if g).upper().replace(" ", "")
            if c.startswith("C") and not c.startswith("C-"):
                c = "C-" + c[1:]
            out.add(c)
        low = s.lower()
        for w, c in LICENSE_WORDS.items():
            if w in low:
                out.add(c)
    return out


ADDRESS_RE = re.compile(r"([A-Z][A-Za-z\.]+(?:\s[A-Z][A-Za-z\.]+){0,2}),\s*(?:CA|California)\.?\s*(9[0-6]\d{3})?")
HQ_COUNTIES = {"Sacramento", "Yolo"}  # buyer headquarters addresses appear in nearly every bid package


def bid_counties(ev, facts, analysis, conn=None, ix=None):
    found = set(facts.get("counties") or [])
    text = " ".join([ev["name"] or "", analysis.get("place_of_performance") or ""])
    found |= {m.group(1) for m in COUNTY_RE.finditer(text)}
    found |= {CITY_COUNTY[m.group(1)] for m in CITY_RE.finditer(text)}
    if conn is not None and ix is not None:
        # Addresses in the description and first pages of each document ("Redway, CA 95560").
        rows = conn.execute("SELECT source, body FROM chunks WHERE event_id=? ORDER BY rowid", (ev["id"],)).fetchall()
        seen, docs = Counter(), [ev["description"] or ""]
        for r in rows:
            if seen[r["source"]] < 150 and not re.search(r"std|form|payee|darfur|cert", r["source"], re.I):
                docs.append(r["body"])
                seen[r["source"]] += 1
        hits = Counter()
        for m in ADDRESS_RE.finditer(" ".join(docs)):
            city = m.group(1).split()[-2:] if len(m.group(1).split()) > 2 else m.group(1).split()
            c = ix.locate(m.group(2), " ".join(city)) or ix.locate(None, m.group(1).split()[-1])
            if c:
                hits[c] += 1
        if len(hits) > 12:  # a long list of sites = multi-location / statewide service
            hits = Counter(dict(hits.most_common(12)))
        found |= set(hits)
        named = " ".join([text, ev["description"] or ""])
        if found - HQ_COUNTIES:
            found = {c for c in found if c not in HQ_COUNTIES or c in named}
    if ev["dept"] == "Department of Transportation":
        m = re.match(r"^(\d{2})[A-Z]", ev["id"] or "")
        for d in [int(m.group(1))] if m else []:
            if not found:  # district is coarse; only use it when nothing more precise was found
                found |= set(CALTRANS_DISTRICTS.get(d, []))
        for d in facts.get("caltrans_districts") or []:
            if not found:
                found |= set(CALTRANS_DISTRICTS.get(d, []))
    statewide = bool(re.search(r"statewide|various locations|all regions|regions?.{0,40}counties", text + " " + (ev["description"] or "")[:900], re.I))
    if statewide and found <= HQ_COUNTIES:
        found = set()  # only the buyer's headquarters address was found
    return sorted(found), statewide


def bid_profile(conn, eid, ix=None):
    ev = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    if not ev:
        return None
    facts = loads((conn.execute("SELECT json FROM facts WHERE event_id=?", (eid,)).fetchone() or [None])[0], {})
    a = loads((conn.execute("SELECT json FROM analysis WHERE event_id=?", (eid,)).fetchone() or [None])[0], {})
    codes = [dict(r) for r in conn.execute("SELECT code, description FROM unspsc WHERE event_id=?", (eid,))]
    codes = [c for c in codes if re.fullmatch(r"\d{8}", c["code"] or "")]
    extra = loads(ev["extra"], {}) if "extra" in ev.keys() else {}
    sig = set((facts.get("signals") or {}).keys())
    sa = a.get("set_aside_and_preferences") or {}
    category = facts.get("category") or "Other"

    lic_ai = parse_license_classes(a.get("required_licenses"))
    lic_rules = set(facts.get("licenses") or [])
    # Rule-found license classes come from long documents full of multi-trade boilerplate, so they only
    # count as a requirement on construction bids; AI-extracted licenses (read in context) count everywhere.
    construction = category == "Construction"
    licenses = lic_ai or (lic_rules if construction else set())
    licenses = {l for l in licenses if l not in ("HIC",)}

    goods = bool(codes) and all(int(c["code"][:2]) < 70 for c in codes)
    labor = re.search(r"servic|install|maint|repair|inspect|testing|monitor|replace|upgrade|removal|construct",
                      f"{ev['name']} {a.get('what_is_being_bought') or ''}", re.I)
    if category == "Construction":
        industry = {"Construction"}
    elif goods or category in ("Goods & Supplies", "Vehicles & Equipment"):
        industry = {"Manufacturer", "Non-Manufacturer"} | ({"Service", "Construction"} if labor else set())
    elif category == "Facilities & Maintenance":
        industry = {"Service", "Construction"}
    elif category == "IT & Software":
        industry = {"Service", "Non-Manufacturer"}
    else:
        industry = {"Service"}

    state = insights.state_of(ev)
    # federal bids performed in California keep California geography (place of performance, not the buying office)
    geo_state = extra.get("Place of Performance State") if state == "US" else state
    counties, statewide = bid_counties(ev, facts, a, conn, ix) if geo_state == "CA" else ([], False)
    if state == "US" and geo_state == "CA" and not counties and ix:
        c = ix.locate(None, extra.get("Place of Performance City"))
        counties = [c] if c else []
    if state == "CA" and insights.level_of(ev) == "Local" and not counties:
        # a city/county/district buys for its own area: agency name ("County of Orange") or its city
        found = {m.group(1) for m in COUNTY_RE.finditer(ev["dept"] or "")}
        c = ix.locate(None, extra.get("Agency City")) if ix else None
        counties = sorted(found or ({c} if c else set()))
    money = [m["value"] for m in facts.get("money") or []]
    title = ev["name"] or ""
    body = " ".join([ev["description"] or "", a.get("what_is_being_bought") or "", a.get("plain_summary") or ""])
    code_text = " ".join(c["description"].split(" - ")[-1] for c in codes if c.get("description"))
    nigp = [c.get("title") or "" for c in extra.get("Commodity Codes") or []]
    nigp += [c.get("title") or "" for c in extra.get("Codes") or [] if c.get("system") == "NIGP" and c.get("title") not in nigp]
    if nigp:  # OpenGov / OR / TX / GA / VA classify with NIGP; its titles are strong, buyer-chosen search terms
        code_text = (code_text + " " + " ".join(nigp)).strip()

    q = Counter()
    for t in tokens(title):
        q[t] += 2.0
    for t in tokens(code_text):
        q[t] += 1.5
    for t, n in Counter(tokens(body)).items():
        q[t] += min(2.0, 0.6 * n)
    # geography and certification programs are scored by their own features, so keep them out of the keyword query
    drop = ({stem(w) for w in BOILERPLATE_WORDS} | set(tokens(ev["dept"] or "")) | set(tokens(" ".join(COUNTIES)))
            | {stem(w) for w in PROGRAM_WORDS})
    for t in list(q):
        if t in drop or any(ch.isdigit() for ch in t):
            q.pop(t)
    if state != "CA":
        # California programs (SB/DVBE set-asides, 5% SB preference, SB-PW) and CSLB license classes do not apply
        # to another state's bids; that state's own certification and contractor licensing do.
        sig = sig - {"sb_set_aside", "sb_dvbe_option", "sb_preference", "dvbe_participation"}
        sa = {k: v for k, v in sa.items() if k not in ("sb_set_aside", "sb_preference_available", "dvbe_requirement_pct")}
        facts = {**facts, "dvbe_pct": None}
        licenses, lic_ai, lic_rules = set(), [], set()
    return {
        "id": eid, "name": title, "dept": ev["dept"], "end_date": ev["end_date"], "category": category,
        "source": insights.source_of(ev), "level": insights.level_of(ev), "state": state, "geo_state": geo_state,
        "state_program": extra.get("State Program"), "sba_program": extra.get("SBA Program"), "nigp": nigp,
        "codes": codes, "query": dict(q), "title_terms": [w for w in dict.fromkeys(tokens(title)) if w in q], "text": f"{title}. {code_text}. {body}"[:1800],
        "licenses": sorted(licenses), "license_hard": bool(lic_ai) or (construction and bool(lic_rules)),
        "counties": counties, "statewide": statewide, "industry": sorted(industry),
        "set_aside": bool({"sb_set_aside", "sb_dvbe_option"} & sig) or bool(sa.get("sb_set_aside")),
        "sb_pref": "sb_preference" in sig or bool(sa.get("sb_preference_available")),
        "dvbe": "dvbe_participation" in sig or bool(facts.get("dvbe_pct")) or sa.get("dvbe_requirement_pct") not in (None, 0),
        "dvbe_pct": (facts.get("dvbe_pct") or {}).get("value") or sa.get("dvbe_requirement_pct"),
        "dbe": "dbe_goal" in sig or bool(sa.get("dbe_goal_pct")),
        "public_works": "prevailing_wage" in sig and construction,
        "complexity": a.get("complexity"), "value": max(money) if money else None,
        "summary": a.get("plain_summary") or (ev["description"] or "")[:300],
    }


# ---------------------------------------------------------------- embeddings for bids

def bid_vector(conn, prof):
    import hashlib
    import urllib.request
    import numpy as np
    h = hashlib.sha1(prof["text"].encode()).hexdigest()[:16]
    row = conn.execute("SELECT hash, vec FROM bid_emb WHERE event_id=? AND model=?", (prof["id"], EMBED_MODEL)).fetchone()
    if row and row[0] == h:
        return np.frombuffer(row[1], dtype=np.float32)
    payload = {"model": EMBED_MODEL, "input": ["search_query: " + prof["text"]]}
    req = urllib.request.Request(f"{config.LLM_URL.rstrip('/')}/api/embed", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        v = np.asarray(json.loads(r.read())["embeddings"][0], dtype=np.float32)
    v /= (np.linalg.norm(v) or 1)
    conn.execute("INSERT OR REPLACE INTO bid_emb VALUES (?,?,?,?)", (prof["id"], EMBED_MODEL, h, v.tobytes()))
    conn.commit()
    return v


# ---------------------------------------------------------------- scoring

def expansion_terms(ix, prof):
    terms = Counter()
    for c in prof["codes"]:
        for lvl, w in ((6, 0.9), (4, 0.5)):
            for t in ix.expansion.get(c["code"][:lvl], []):
                terms[t] = max(terms[t], w)
    return terms


def commodity(ix, prof, i):
    if not prof["codes"]:
        return None, []
    p = ix.prefixes[i]
    best, vals, ev = 0.0, [], []
    for c in prof["codes"]:
        code, got = c["code"], 0.0
        for lvl in (8, 6, 4, 2):
            if code[:lvl] in p[lvl]:
                got = LEVEL_WEIGHT[lvl] * ix.specificity(lvl, code[:lvl])
                if lvl >= 4:
                    firm_code = next(fc for fc in ix.firms[i]["unspsc"] if fc.startswith(code[:lvl]))
                    ev.append({"bid": code, "firm": firm_code, "level": LEVEL_NAME[lvl]})
                break
        vals.append(got)
        best = max(best, got)
    return 0.7 * best + 0.3 * (sum(vals) / len(vals)), ev


def geography(prof, f):
    if prof.get("state") == "US" and prof.get("geo_state") != "CA":
        where = prof.get("geo_state") or "outside California / not stated"
        return 0.5, f"Federal bid performed in {where}: CA service areas don't apply"
    if prof.get("state", "CA") not in ("CA", "US"):
        # the certified-firm directory holds California service areas only: neutral, and say so
        return 0.5, f"Out-of-state bid ({prof['state']}): CA service areas don't apply"
    if not prof["counties"]:
        return 0.6, None
    areas = set(f["areas"])
    if not areas:
        return 0.5, "Service area not listed"
    hit = areas & set(prof["counties"])
    if hit:
        if f["statewide"]:
            return 0.7, "Serves all CA counties"
        return 1.0, "Serves " + ", ".join(sorted(hit)[:3])
    return (0.35 if prof["statewide"] else 0.1), "Outside listed service area"


def run_match(prof, ix, conn=None, semantic=True, only=None, cand_limit=600):
    """Rank firms for one bid. `only`: restrict to these firm positions (reverse lookups)."""
    today = ix.today
    q = Counter(prof["query"])
    exp = expansion_terms(ix, prof)
    for t, w in exp.items():
        q[t] += w
    qtop = {t: w for t, w in q.most_common(45)}

    # --- recall
    if only is None:
        cands = set()
        for c in prof["codes"]:
            for lvl in (8, 6, 4):
                members = ix.code_ix[lvl].get(c["code"][:lvl], set())
                if len(members) <= 2500 or lvl == 8:
                    cands |= members
                    if len(cands) > 4000:
                        break
        bm = ix.bm25(qtop)
        cands |= {i for i, _ in sorted(bm.items(), key=lambda x: -x[1])[:cand_limit]}
        top_bm = max(bm.values(), default=0.0)
    else:
        cands = set(only)
        bm = {i: ix.bm25_one(qtop, i) for i in cands}
        top_bm = prof.get("_top_bm") or max(ix.bm25(qtop).values(), default=0.0)
    prof["_top_bm"] = top_bm

    sims, lo, hi = {}, 0.0, 1.0
    if semantic and ix.vectors is not None and conn is not None:
        try:
            import numpy as np
            if only is not None and "_vec" in prof:  # reverse lookups: reuse this bid's vector + calibration
                v, lo, hi = prof["_vec"], prof["_lo"], prof["_hi"]
                for i in cands:
                    k = ix.vec_pos.get(ix.firms[i]["id"])
                    if k is not None:
                        sims[i] = float(ix.vectors[k] @ v)
                raise _Done
            v = bid_vector(conn, prof)
            allsim = ix.vectors @ v
            # per-bid calibration: median firm -> 0, top 0.1% -> 1 (raw cosines sit in a narrow, bid-specific band)
            lo = float(np.percentile(allsim, 50))
            hi = max(float(np.percentile(allsim, 99.9)), lo + 0.08)
            prof["_vec"], prof["_lo"], prof["_hi"] = v, lo, hi
            if only is None:
                for k in np.argsort(-allsim)[:cand_limit]:
                    fid_pos = ix.pos.get(_vec_id(ix, k))
                    if fid_pos is not None:
                        cands.add(fid_pos)
            for i in cands:
                k = ix.vec_pos.get(ix.firms[i]["id"])
                if k is not None:
                    sims[i] = float(allsim[k])
        except _Done:
            pass
        except Exception as e:  # embedding service down -> lexical + codes only
            prof["_semantic_error"] = str(e)

    denom = max(top_bm, 10.0)
    has_sem = bool(sims)
    has_codes = bool(prof["codes"])
    if has_codes and has_sem:
        wc, wt, ws = 0.45, 0.30, 0.25
    elif has_codes:
        wc, wt, ws = 0.55, 0.45, 0.0
    elif has_sem:
        wc, (wt, ws) = 0.0, NOCODE_WEIGHTS
    else:
        wc, wt, ws = 0.0, 1.0, 0.0

    need = set(prof["licenses"])
    for a, b in LICENSE_EQUIV:
        if a in need:
            need.add(b)
    big = (prof["complexity"] or 0) >= 4 or (prof["value"] or 0) >= 5_000_000
    results = []
    for i in cands:
        f = ix.firms[i]
        active = {c: v for c, v in f["certs"].items() if v["end"] >= today}
        if not active:
            continue
        certs = set(active)
        flags, reasons = [], []

        com, com_ev = commodity(ix, prof, i)
        com = com or 0.0
        # keywords = BM25 (normalized to the bid's best firm) blended with idf-weighted coverage of the bid title
        tt = prof.get("title_terms") or []
        tw = sum(ix.idf.get(w, 0) for w in tt)
        cov = sum(ix.idf.get(w, 0) for w in tt if w in ix.doc_tf[i]) / tw if tw else 0.0
        txt = min(1.0, bm.get(i, 0.0) / denom) * (0.6 if tw else 1.0) + (0.4 * cov if tw else 0.0)
        if has_sem and i in sims:
            sem = max(0.0, min(1.0, (sims[i] - lo) / (hi - lo)))
            rel = wc * com + wt * txt + ws * sem
        else:  # no vector for this firm (yet): renormalize over the lexical + code signals
            sem = None
            rel = (wc * com + wt * txt) / max(1e-9, wc + wt)
        if rel < 0.16:
            continue

        # matched words (explainability)
        words = [t for t in qtop if t in ix.doc_tf[i]]
        words.sort(key=lambda t: -qtop[t] * ix.idf.get(t, 0))

        # eligibility: license
        lic_factor, prime_ok = 1.0, True
        have = set(f["license_classes"])
        if need:
            if have & need:
                reasons.append("Holds " + ", ".join(sorted(have & need)))
            elif prof["license_hard"]:
                if ("A" in have or "B" in have) and any(n.startswith(("C-", "D-")) for n in need):
                    lic_factor = 0.8
                    reasons.append("Holds " + "/".join(sorted(have & {"A", "B"})) + " (general) license")
                    flags.append("Specialty license " + ", ".join(sorted(need)) + " not listed")
                else:
                    lic_factor, prime_ok = 0.55, False
                    flags.append("Lacks required license " + ", ".join(sorted(need)))

        # set-aside gate
        sb_like = bool(certs & CERT_PRIME_SB)
        if prof["set_aside"] and not sb_like:
            continue

        geo, geo_note = geography(prof, f)
        if geo_note:
            (flags if geo < 0.5 or geo_note.startswith("Out-of-state") else reasons).append(geo_note)

        ind = 0.7 if not f["industry"] else (1.0 if set(f["industry"]) & set(prof["industry"]) else 0.4)
        if ind < 0.5:
            flags.append("Industry type " + "/".join(f["industry"]))

        prog = 0.5
        if prof["set_aside"] and sb_like:
            prog += 0.3
            reasons.append("Eligible for SB/DVBE set-aside")
        if prof["sb_pref"] and certs & {"SB", "MB"}:
            prog += 0.2
            reasons.append("5% SB preference" if prof["level"] == "State" else "Agency small-business preference (see bid)")
        if prof["dvbe"] and "DVBE" in certs:
            prog += 0.3
            reasons.append("DVBE: counts toward " + (f"{prof['dvbe_pct']:g}% " if prof["dvbe_pct"] else "") + "DVBE goal")
        if prof["public_works"] and "SB-PW" in certs:
            prog += 0.2
            reasons.append("SB-PW for public works")
        if prof["dbe"] and "DBE" in f["divcerts"]:
            prog += 0.3
            reasons.append("DBE: counts toward federal goal")
        if prof.get("state") == "US":
            sba = prof.get("sba_program")
            if sba and "Small Business (SBA" in sba:
                flags.append("Federal SB set-aside: size is self-certified in SAM.gov (CA SB status suggests small, doesn't prove it)")
            elif sba:
                flags.append(f"Federal set-aside needs {sba} certification; CA SB/DVBE doesn't count"
                             + (" (a CA DVBE firm can apply for SBA VetCert)" if "SDVOSB" in sba and "DVBE" in certs else ""))
            else:
                flags.append("Federal bid: needs an active SAM.gov registration (UEI); CA certifications don't apply")
        elif prof.get("state", "CA") != "CA":
            flags.append(f"CA certifications don't apply in {prof['state']}"
                         + (f" - needs {prof['state_program'].split(' - ')[0]}" if prof.get("state_program") else ""))
        prog = min(1.0, prog)
        # location matters most when the work is performed on site; far less for goods, IT or consulting
        fit = (0.60 * geo + 0.15 * ind + 0.25 * prog) if prof["category"] in ON_SITE else (0.25 * geo + 0.35 * ind + 0.40 * prog)

        cap = 1.0
        micro_only = "MB" in certs and big
        if micro_only:
            cap = 0.9
            flags.append("Microbusiness on a large/complex bid - better as sub")

        due = (prof["end_date"] or "")[:10]
        soonest = min(v["end"] for v in active.values())
        if due and soonest < due:
            flags.append(f"A certification expires {soonest} (before bid due)")
            cap *= 0.95

        score = 100 * (rel ** 0.85) * (0.65 + 0.35 * fit) * lic_factor * cap

        roles = []
        if prime_ok and rel >= 0.42 and not micro_only:
            roles.append("prime")
        if prof["dvbe"] and "DVBE" in certs:
            roles.append("dvbe")
        if prof["dbe"] and "DBE" in f["divcerts"]:
            roles.append("dbe")
        if certs & {"SB", "MB"} and "prime" not in roles:
            roles.append("sb_sub")
        if not roles:
            roles.append("sb_sub" if sb_like else "other")

        results.append({
            "firm_id": f["id"], "pos": i, "score": round(score, 1), "roles": roles,
            "components": {"commodity": round(com, 3) if has_codes else None, "text": round(txt, 3), "semantic": round(sem, 3) if sem is not None else None,
                           "relevance": round(rel, 3), "geography": round(geo, 2), "industry": round(ind, 2),
                           "program": round(prog, 2), "license": lic_factor, "capacity": round(cap, 2)},
            "codes": com_ev[:4], "words": words[:6], "reasons": reasons, "flags": flags,
        })
    results.sort(key=lambda r: -r["score"])
    return results, {"candidates": len(cands), "semantic": has_sem, "expansion": list(exp)[:15],
                     "query": list(qtop)[:20], "semantic_error": prof.get("_semantic_error")}


class _Done(Exception):
    pass


def _vec_id(ix, k):
    if not hasattr(ix, "_vec_ids"):
        ix._vec_ids = [None] * len(ix.vec_pos)
        for fid, kk in ix.vec_pos.items():
            ix._vec_ids[kk] = fid
    return ix._vec_ids[k]


def firm_card(f, today=None):
    today = today or datetime.now().strftime("%Y-%m-%d")
    return {
        "id": f["id"], "name": f["name"], "dba": f.get("dba"), "city": f["city"], "zip": f["zip"],
        "certs": sorted(c for c, v in f["certs"].items() if v["end"] >= today),
        "expired_certs": sorted(c for c, v in f["certs"].items() if v["end"] < today),
        "cert_end": max((v["end"] for v in f["certs"].values()), default=None),
        "divcerts": f["divcerts"], "industry": f["industry"], "licenses": f["license_classes"],
        "license_names": f["licenses"][:8], "keywords": f["keywords"][:14],
        "areas": "All CA counties" if f["statewide"] else (", ".join(f["areas"][:8]) + (" …" if len(f["areas"]) > 8 else "") if f["areas"] else "Not listed"),
        "email": f.get("email"), "phone": f.get("phone"), "url": f.get("url"), "contact": f.get("contact"),
        "address": f.get("address"), "n_codes": len(f["unspsc"]),
    }


# ---------------------------------------------------------------- public API

ROLE_LABEL = {"prime": "Prime candidate", "dvbe": "DVBE partner", "dbe": "DBE partner", "sb_sub": "SB subcontractor",
              "other": "Other"}
_profiles = {}


def match_bid(eid, limit=100, role=None):
    conn = connect()
    ix = load_index()
    prof = bid_profile(conn, eid, ix)
    if not prof:
        return None
    t = time.time()
    results, meta = run_match(prof, ix, conn)
    _profiles[eid] = prof
    counts = Counter(r for x in results for r in x["roles"])
    if role:
        results = [r for r in results if role in r["roles"]]
    out = []
    for r in results[:limit]:
        card = firm_card(ix.firms[r.pop("pos")], ix.today)
        out.append({**r, "firm": card})
    public = {k: v for k, v in prof.items() if not k.startswith("_") and k not in ("query", "text")}
    return {"bid": public, "results": out, "total": len(results), "role_counts": dict(counts),
            "meta": {**meta, "ms": int((time.time() - t) * 1000), "firms_indexed": ix.N,
                     "vectors": 0 if ix.vectors is None else len(ix.vec_pos)}}


def match_firm(fid, limit=40):
    """Reverse: best open bids for one firm."""
    conn = connect()
    ix = load_index()
    i = ix.pos.get(fid)
    if i is None:
        return None
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    out = []
    for (eid,) in conn.execute("SELECT id FROM events WHERE end_date >= ? ORDER BY end_date", (now,)).fetchall():
        prof = _profiles.get(eid) or bid_profile(conn, eid, ix)
        _profiles[eid] = prof
        res, _ = run_match(prof, ix, conn, only=[i])
        if res:
            r = res[0]
            r.pop("pos")
            out.append({**r, "bid": {k: prof[k] for k in ("id", "name", "dept", "end_date", "category", "summary")}})
    out.sort(key=lambda r: -r["score"])
    return {"firm": firm_card(ix.firms[i], ix.today), "results": out[:limit], "total": len(out)}


def warm():
    """Pre-build the index and every open bid's profile (+ vector) so reverse lookups are instant."""
    ix = load_index()
    conn = connect()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    t = time.time()
    for (eid,) in conn.execute("SELECT id FROM events WHERE end_date >= ?", (now,)).fetchall():
        if eid not in _profiles:
            prof = bid_profile(conn, eid, ix)
            run_match(prof, ix, conn)  # full run once: sets BM25 normalizer + semantic calibration
            _profiles[eid] = prof
    print(f"warmed {len(_profiles)} bid profiles in {time.time() - t:.0f}s", flush=True)


def search_firms(q, limit=25):
    ix = load_index()
    q = (q or "").strip().lower()
    if not q:
        return []
    if q.isdigit():
        hits = [i for i in [ix.pos.get(q)] if i is not None]
    else:
        name_hits = [i for i, f in enumerate(ix.firms) if q in f["name"].lower() or q in (f.get("dba") or "").lower()]
        bm = ix.bm25({t: 1.0 for t in tokens(q)})
        hits = name_hits[:limit] + [i for i, _ in sorted(bm.items(), key=lambda x: -x[1]) if i not in set(name_hits)]
    return [firm_card(ix.firms[i], ix.today) for i in hits[:limit]]


def firm_stats():
    ix = load_index()
    today = ix.today
    active = [f for f in ix.firms if any(v["end"] >= today for v in f["certs"].values())]
    certs = Counter(c for f in active for c, v in f["certs"].items() if v["end"] >= today)
    return {"firms": ix.N, "active": len(active), "certs": dict(certs),
            "vectors": 0 if ix.vectors is None else len(ix.vec_pos),
            "with_license": sum(1 for f in active if f["license_classes"]),
            "statewide": sum(1 for f in active if f["statewide"])}
