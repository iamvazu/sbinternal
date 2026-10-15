"""Deterministic enrichment: fast, free, explainable facts pulled with rules, each with the
snippet of text that proves it. The LLM layer builds on these instead of guessing them.

    python enrich.py
"""
import json
import re
from datetime import datetime

from db import connect

COUNTIES = """Alameda Alpine Amador Butte Calaveras Colusa Contra_Costa Del_Norte El_Dorado Fresno Glenn
Humboldt Imperial Inyo Kern Kings Lake Lassen Los_Angeles Madera Marin Mariposa Mendocino Merced Modoc
Mono Monterey Napa Nevada Orange Placer Plumas Riverside Sacramento San_Benito San_Bernardino San_Diego
San_Francisco San_Joaquin San_Luis_Obispo San_Mateo Santa_Barbara Santa_Clara Santa_Cruz Shasta Sierra
Siskiyou Solano Sonoma Stanislaus Sutter Tehama Trinity Tulare Tuolumne Ventura Yolo Yuba""".split()
COUNTIES = [c.replace("_", " ") for c in COUNTIES]
COUNTY_RE = re.compile(r"\b(" + "|".join(COUNTIES) + r")\s+Count(?:y|ies)\b")

# (signal key, label, regex). First match wins; the matched sentence is kept as evidence.
SIGNALS = [
    ("sb_set_aside", "Small Business set-aside",
     r"small business \(?SB\)?[ -]?set[- ]aside|set[- ]aside for (?:certified )?(?:small business|SB)|SB[- ]only|SB/DVBE set[- ]aside|(?:certified )?small business(?:es)? only|limited to (?:certified )?(?:small business|SB)"),
    ("sb_dvbe_option", "SB/DVBE Option (only certified SB/DVBE may bid)", r"SB\s*/\s*DVBE\s+Option|DVBE\s+Option"),
    ("sb_preference", "5% Small Business preference", r"small business preference|\bSB preference|Non-?Small Business Subcontractor Preference|5\s*%\s*(?:small business|SB)"),
    ("dvbe_participation", "DVBE participation requirement",
     r"DVBE[^.\n]{0,120}participation|participation[^.\n]{0,80}DVBE|Disabled Veteran Business Enterprise[^.\n]{0,80}(?:participation|requirement)"),
    ("dvbe_incentive", "DVBE incentive", r"DVBE incentive|DVBE Incentive"),
    ("dbe_goal", "Federal DBE contract goal",
     r"\bDBE\b[^.\n]{0,80}goal|Disadvantaged Business Enterprise[^.\n]{0,80}goal"),
    ("federal_aid", "Federally funded", r"federal[- ]aid|\bFHWA\b|Federal Highway Administration|federally funded"),
    ("microbusiness", "Microbusiness mentioned", r"micro\s?business"),
    ("prevailing_wage", "Prevailing wage / DIR registration", r"prevailing wage|Department of Industrial Relations|DIR registration"),
    ("bid_bond", "Bid bond", r"bid (?:bond|security)|bidder'?s bond"),
    ("performance_bond", "Performance / payment bond", r"performance bond|payment bond"),
    ("mandatory_prebid", "MANDATORY pre-bid / site visit",
     r"mandatory\s+(?:pre-?bid|pre-?proposal|bidders?'? conference|site (?:visit|walk)|job walk|conference|walk-?through)"),
    ("optional_prebid", "Optional pre-bid / site visit", r"(?:non-?mandatory|optional)\s+(?:pre-?bid|site (?:visit|walk)|conference|job walk)"),
    ("insurance", "Insurance requirements", r"general liability|certificate of insurance|workers'? compensation insurance"),
    ("statewide_contract", "Statewide / multi-agency contract", r"statewide contract|leveraged procurement|available for use by"),
    ("master_agreement", "Master agreement / on-call", r"master (?:service )?agreement|on-?call"),
]
SIGNALS = [(k, l, re.compile(p, re.I)) for k, l, p in SIGNALS]

PCT_DVBE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%[^.\n]{0,60}DVBE|DVBE[^.\n]{0,100}?(\d{1,2}(?:\.\d+)?)\s*%", re.I)
PCT_DBE = re.compile(r"\bDBE\b[^.\n]{0,80}?goal[^.\n]{0,40}?(\d{1,2}(?:\.\d+)?)\s*%|(\d{1,2}(?:\.\d+)?)\s*%[^.\n]{0,30}\bDBE\b", re.I)
# A class only counts when it sits near the word "license" (fire-alarm "Class A" wiring is not a CSLB class).
LICENSE_CTX = re.compile(r"licen[cs]e", re.I)
LICENSE = re.compile(r"\b[Cc]lass\s*[\"“']?\s*([AB]|C\s?-?\s?\d{1,2})\b(?![\w-])|\b(C-\s?\d{1,2})\b(?![\w-])")
LICENSE_NAMED = {"General Engineering": "A", "General Building": "B"}
MONEY = re.compile(r"(?:estimated|estimate|not to exceed|\bNTE\b|budget|engineer'?s estimate|contract (?:amount|value)|maximum)"
                   r"[^$\n]{0,80}\$\s?([\d,]{4,}(?:\.\d{2})?)\s*(million|M\b)?", re.I)
METHOD = [("RFI", r"\bRFI\b|request for information"), ("RFP", r"\bRFP\b|request for proposal"),
          ("RFQ", r"\bRFQ\b|request for qualifications|request for quot"), ("IFB", r"\bIFB\b|invitation for bid|invitation to bid"),
          ("SLP", r"\bSLP\b|leveraged procurement|\bLPA\b"), ("Lease", r"\blease\b")]
DISTRICT = re.compile(r"\b(?:Caltrans\s+)?District\s+(\d{1,2})\b")

CATEGORY_RULES = [  # (UNSPSC prefix, category); longest prefix wins
    ("7210", "Facilities & Maintenance"), ("7611", "Facilities & Maintenance"), ("70", "Facilities & Maintenance"),
    ("72", "Construction"), ("30", "Construction"),
    ("8110", "Engineering & Architecture"), ("8111", "IT & Software"), ("8116", "IT & Software"), ("43", "IT & Software"),
    ("8013", "Real Estate & Leasing"), ("95", "Real Estate & Leasing"),
    ("76", "Environmental & Waste"), ("77", "Environmental & Waste"),
    ("80", "Professional Services"), ("84", "Professional Services"), ("86", "Professional Services"),
    ("82", "Professional Services"), ("93", "Professional Services"), ("94", "Professional Services"),
    ("81", "Engineering & Architecture"), ("78", "Transportation & Logistics"), ("85", "Health & Human Services"),
    ("92", "Safety & Security"), ("46", "Safety & Security"), ("25", "Vehicles & Equipment"),
    ("22", "Vehicles & Equipment"), ("21", "Vehicles & Equipment"),
]
KEYWORD_CATEGORY = [
    (r"construct|paving|bridge|roof|asphalt|striping|grading|culvert|highway", "Construction"),
    (r"software|\bIT\b|network|cloud|cyber|data|computer|technology", "IT & Software"),
    (r"engineer|architect|design services|survey", "Engineering & Architecture"),
    (r"janitor|custodial|landscap|maintenance|repair|hvac|elevator|pest", "Facilities & Maintenance"),
    (r"waste|refuse|hazardous|recycl|environmental|abatement", "Environmental & Waste"),
    (r"lease|real estate|office space", "Real Estate & Leasing"),
    (r"consult|training|audit|study|evaluation|outreach", "Professional Services"),
]


def categorize(codes, text):
    best = None
    for code in codes:
        for prefix, cat in CATEGORY_RULES:
            if code.startswith(prefix) and (best is None or len(prefix) > best[0]):
                best = (len(prefix), cat)
    if best:
        return best[1]
    if codes and int(codes[0][:2]) < 60:
        return "Goods & Supplies"
    for pat, cat in KEYWORD_CATEGORY:
        if re.search(pat, text, re.I):
            return cat
    return "Other"


def sentence(text, m, width=220):
    start = max(0, text.rfind(".", 0, m.start()) + 1, m.start() - width // 2)
    end = text.find(".", m.end())
    end = min(len(text), end + 1 if end != -1 else len(text), m.end() + width)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def find_signals(sources):
    """sources: list of (label, text) ordered by authority (description first)."""
    found = {}
    for key, label, rx in SIGNALS:
        for src, text in sources:
            m = rx.search(text)
            if m:
                found[key] = {"label": label, "source": src, "evidence": sentence(text, m)}
                break
    if "optional_prebid" in found and "mandatory_prebid" in found:
        # "non-mandatory pre-bid" also matches the mandatory regex via the word "mandatory".
        if "non-mandatory" in found["mandatory_prebid"]["evidence"].lower():
            del found["mandatory_prebid"]
    return found


def first_pct(rx, sources):
    for src, text in sources:
        for m in rx.finditer(text):
            val = next(g for g in m.groups() if g)
            if 0 < float(val) <= 50:
                return {"value": float(val), "source": src, "evidence": sentence(text, m)}
    return None


def enrich_event(conn, ev):
    eid = ev["id"]
    codes = [r["code"] for r in conn.execute("SELECT code FROM unspsc WHERE event_id=?", (eid,))]
    atts = conn.execute("SELECT file_name, text FROM attachments WHERE event_id=? AND text IS NOT NULL", (eid,)).fetchall()
    # Main solicitation documents first; standard forms (STD 204, DVBE forms) last so their
    # boilerplate does not masquerade as an event-specific requirement.
    def rank(a):
        n = a["file_name"].lower()
        if re.search(r"ifb|rfp|rfq|solicitation|bid_?package|invitation|specification|addendum", n):
            return 0
        if re.search(r"std|form|dvbe|certification|payee|darfur|iran|cuf", n):
            return 2
        return 1
    atts = sorted(atts, key=rank)
    head = f"{ev['name'] or ''}\n{ev['description'] or ''}"
    sources = [("Description", head)] + [(a["file_name"], a["text"]) for a in atts]
    alltext = "\n".join(t for _, t in sources)

    licenses = set()
    for ctx in LICENSE_CTX.finditer(alltext):
        for m in LICENSE.finditer(alltext[max(0, ctx.start() - 80):ctx.end() + 120]):
            raw = re.sub(r"[\s-]", "", (m.group(1) or m.group(2)).upper())
            licenses.add("C-" + raw[1:] if raw.startswith("C") else raw)
    licenses = sorted(licenses)
    for name, cls in LICENSE_NAMED.items():
        if re.search(name, alltext, re.I) and cls not in licenses:
            licenses.append(cls)

    money = []
    for src, text in sources:
        for m in MONEY.finditer(text):
            v = float(m.group(1).replace(",", ""))
            if m.group(2):
                v *= 1_000_000
            if 10_000 <= v < 5e9:
                money.append({"value": v, "source": src, "evidence": sentence(text, m)})
    money.sort(key=lambda x: -x["value"])

    head_and_files = head + " " + " ".join(a["file_name"] for a in atts)
    method = next((m for m, p in METHOD if re.search(p, head_and_files, re.I)), None)
    if not method and re.search(r"RFI|Request For Information", ev["format"] or ""):
        method = "RFI"

    counties = sorted({m.group(1) for _, t in sources[:3] for m in COUNTY_RE.finditer(t)})
    districts = sorted({int(m.group(1)) for m in DISTRICT.finditer(head)} if ev["dept"] == "Department of Transportation" else set())

    facts = {
        "category": categorize(codes, head),
        "method": method,
        "signals": find_signals(sources),
        "dvbe_pct": first_pct(PCT_DVBE, sources),
        "dbe_pct": first_pct(PCT_DBE, sources),
        "licenses": licenses[:8],
        "money": money[:3],
        "counties": counties[:12],
        "caltrans_districts": districts,
        "attachment_count": len(atts),
        "doc_chars": sum(len(t) for _, t in sources),
    }
    conn.execute("INSERT OR REPLACE INTO facts VALUES (?,?,?)",
                 (eid, json.dumps(facts), datetime.now().isoformat(timespec="seconds")))
    return facts


def main():
    conn = connect()
    events = conn.execute("SELECT * FROM events").fetchall()
    for ev in events:
        enrich_event(conn, ev)
    conn.commit()
    counts = {}
    for row in conn.execute("SELECT json FROM facts"):
        for k in json.loads(row["json"])["signals"]:
            counts[k] = counts.get(k, 0) + 1
    print(f"enriched {len(events)} events")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k:22} {v}")


if __name__ == "__main__":
    main()
