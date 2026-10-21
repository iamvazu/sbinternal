"""Read-side logic shared by the server and the analyzer: bid rows, portfolio stats,
client matching ("who should bid this?") and retrieval for the Ask box."""
import json
import re
from collections import Counter
from datetime import datetime, timedelta

from config import CLIENTS_JSON
from db import loads

CERTS = {
    "SB": "Small Business (DGS OSDS)",
    "MB": "Microbusiness (DGS OSDS)",
    "SB-PW": "Small Business for Public Works (DGS OSDS)",
    "DVBE": "Disabled Veteran Business Enterprise (DGS OSDS)",
    "DBE": "Disadvantaged Business Enterprise (CUCP, federal-aid work)",
    "WBE": "Women Business Enterprise (CPUC Supplier Clearinghouse / WBENC)",
    "WOSB": "Women-Owned Small Business (SBA, federal)",
}


def days_left(end_iso, now=None):
    if not end_iso:
        return None
    now = now or datetime.now()
    return round((datetime.fromisoformat(end_iso) - now).total_seconds() / 86400, 2)


def urgency(d):
    if d is None:
        return "unknown"
    if d < 0:
        return "closed"
    if d <= 3:
        return "critical"
    if d <= 7:
        return "soon"
    return "open"


# Gold rows carry source/level from the silver layer. If one is ever missing, infer it from the id prefix;
# never default an unknown bid to a particular source (that is how OpenGov bids got shown as Cal eProcure).
ID_PREFIX_SOURCE = {"OG-": ("OpenGov", "Local"), "PB-": ("PlanetBids", "Local"), "CT-": ("Caltrans DES", "State"),
                    "CSU-": ("CSU", "State"), "BF-": ("Bonfire", "Local"), "BN-": ("BidNet Direct", "Local"),
                    "BG-": ("Biddingo", "Local"), "QC-": ("QuestCDN", "Local"), "CP-": ("CivicPlus Bids", "Local"),
                    "AG-": ("Agency portals", "Local"), "OR-": ("OregonBuys", None), "FL-": ("Florida MFMP", "State"),
                    "TX-": ("Texas ESBD", None), "GA-": ("Georgia GPR", None), "VA-": ("Virginia eVA", None),
                    "PA-": ("PA eMarketplace", "State"), "SAM-": ("SAM.gov", "Federal"), "PIEE-": ("PIEE", "Federal"),
                    "FC-": ("FedConnect", "Federal"), "DIBBS-": ("DIBBS", "Federal")}
STATE_NAMES = {"CA": "California", "OR": "Oregon", "FL": "Florida", "TX": "Texas", "GA": "Georgia", "VA": "Virginia",
               "PA": "Pennsylvania", "AZ": "Arizona", "WA": "Washington", "US": "Federal"}


def _infer(ev):
    eid = ev["id"] or ""
    for prefix, sl in ID_PREFIX_SOURCE.items():
        if eid.startswith(prefix):
            return sl
    return ("Cal eProcure", "State") if eid[:1].isdigit() or eid[:2].isdigit() else ("Unknown", None)


def source_of(ev):
    return (ev["source"] if "source" in ev.keys() and ev["source"] else None) or _infer(ev)[0]


def state_of(ev):
    if "state" in ev.keys() and ev["state"]:
        return ev["state"]
    eid = ev["id"] or ""
    if eid.startswith(("SAM-", "PIEE-", "FC-", "DIBBS-")):
        return "US"
    return eid[:2] if eid[:3] in ("OR-", "FL-", "TX-", "GA-", "VA-", "PA-") else "CA"


def level_of(ev):
    return (ev["level"] if "level" in ev.keys() and ev["level"] else None) or _infer(ev)[1]


def bid_row(ev, facts, analysis):
    f = facts or {}
    a = analysis or {}
    st = state_of(ev)
    x = loads(ev["extra"], {}) if st == "US" and "extra" in ev.keys() else {}  # federal: notice stage + SBA program
    sig = f.get("signals", {})
    d = days_left(ev["end_date"])
    sa = a.get("set_aside_and_preferences") or {}
    return {
        "id": ev["id"], "name": ev["name"], "dept": ev["dept"], "end_date": ev["end_date"],
        "source": source_of(ev), "level": level_of(ev), "state": st,
        "stage": x.get("Notice Stage") or ("Solicitation" if st == "US" else None), "sba": x.get("SBA Program"),
        "published": ev["published"], "days_left": d, "urgency": urgency(d),
        "category": f.get("category") or "Other", "method": f.get("method"),
        "signals": sorted(sig.keys()),
        "dvbe_pct": (f.get("dvbe_pct") or {}).get("value") or sa.get("dvbe_requirement_pct"),
        "dbe_pct": (f.get("dbe_pct") or {}).get("value") or sa.get("dbe_goal_pct"),
        "licenses": a.get("required_licenses") or f.get("licenses") or [],
        "counties": f.get("counties") or [],
        "attachments": f.get("attachment_count", 0),
        "analyzed": bool(a),
        "summary": a.get("plain_summary") or (ev["description"] or "")[:280],
        "fit": (a.get("small_business_fit") or {}).get("score"),
        "complexity": a.get("complexity"),
        "teaming": (a.get("teaming_opportunity") or {}).get("exists"),
        "value": a.get("estimated_value") or (fmt_money(f["money"][0]["value"]) if f.get("money") else None),
    }


def fmt_money(v):
    return f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.0f}K" if v >= 1e3 else f"${v:.0f}"


def load_rows(conn):
    facts = {r["event_id"]: loads(r["json"], {}) for r in conn.execute("SELECT event_id, json FROM facts")}
    ana = {r["event_id"]: loads(r["json"], {}) for r in conn.execute("SELECT event_id, json FROM analysis WHERE json IS NOT NULL")}
    rows = [bid_row(ev, facts.get(ev["id"]), ana.get(ev["id"]))
            for ev in conn.execute("SELECT * FROM events")]
    rows.sort(key=lambda r: (r["days_left"] is None or r["days_left"] < 0, r["days_left"] if r["days_left"] is not None else 1e9))
    return rows


def stats(rows):
    open_all = [r for r in rows if r["urgency"] not in ("closed",)]
    # sources sought / presolicitations / special notices can't be bid on yet: counted separately, not as open bids
    live = [r for r in open_all if r.get("stage") != "Pre-RFP notice"]
    fed = [r for r in live if r["state"] == "US"]
    now = datetime.now()
    week0 = (now - timedelta(days=now.weekday())).date()
    weeks = Counter()
    for r in live:
        if r["end_date"]:
            wk = (datetime.fromisoformat(r["end_date"]).date() - week0).days // 7
            weeks[min(wk, 8)] += 1
    sig = Counter(s for r in live for s in r["signals"])
    return {
        "total": len(rows), "open": len(live),
        "closing_7d": sum(1 for r in live if r["days_left"] is not None and r["days_left"] <= 7),
        "closing_3d": sum(1 for r in live if r["urgency"] == "critical"),
        "analyzed": sum(1 for r in rows if r["analyzed"]),
        "by_source": Counter(r["source"] for r in live).most_common(),
        "by_state": Counter(r["state"] for r in live).most_common(),
        "pre_rfp": sum(1 for r in open_all if r.get("stage") == "Pre-RFP notice"),
        "federal": {"solicitations": sum(1 for r in fed if r.get("stage") in ("Solicitation", "Grant / funding")),
                    "dla_rfqs": sum(1 for r in fed if r.get("stage") == "DLA parts RFQ"),
                    "set_asides": sum(1 for r in fed if r.get("sba")),
                    "by_program": Counter(r["sba"] for r in fed if r.get("sba")).most_common()},
        "state_names": STATE_NAMES,
        "sb_access": sum(1 for r in live if {"sb_set_aside", "sb_dvbe_option", "sb_preference"} & set(r["signals"])),
        "dvbe": sum(1 for r in live if r["dvbe_pct"] or "dvbe_participation" in r["signals"]),
        "dbe": sum(1 for r in live if r["dbe_pct"] or "dbe_goal" in r["signals"]),
        "mandatory_prebid": sig.get("mandatory_prebid", 0),
        "by_dept": Counter(r["dept"] for r in live).most_common(12),
        "by_category": Counter(r["category"] for r in live).most_common(),
        "by_week": [{"week": i, "start": str(week0 + timedelta(weeks=i)), "count": weeks.get(i, 0)} for i in range(9)],
        "signals": dict(sig),
        "generated": now.isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------------ clients & matching

def load_clients():
    if CLIENTS_JSON.exists():
        return json.loads(CLIENTS_JSON.read_text(encoding="utf-8"))
    return []


def save_clients(clients):
    CLIENTS_JSON.write_text(json.dumps(clients, indent=2), encoding="utf-8")


def match(row, unspsc_codes, client, text=""):
    """Score 0-100 plus human-readable reasons and blockers. Pure rules: instant and auditable."""
    score, reasons, blockers, unlock = 0, [], [], []
    certs = set(client.get("certifications", []))
    sig = set(row["signals"])

    best = 0
    for p in client.get("unspsc", []):
        for c in unspsc_codes:
            if c.startswith(p):
                best = max(best, len(p))
    if best:
        pts = 20 if best <= 2 else 30 if best <= 4 else 40
        score += pts
        reasons.append(f"Commodity code match ({'segment' if best <= 2 else 'family' if best <= 4 else 'class'})")

    hay = f"{row['name']} {row['summary']} {text}".lower()
    hits = [k for k in client.get("keywords", []) if k.lower() in hay]
    if hits:
        score += min(30, 12 * len(hits))
        reasons.append("Keywords: " + ", ".join(hits[:4]))
    # Relevance gate: a broad code-family hit alone is weak, and preferences/certs only matter
    # once the work itself fits.
    if not hits and best < 6:
        score //= 2
    if score < 15:
        return {"score": 0, "reasons": [], "blockers": [], "unlock": []}

    need = set(row["licenses"])
    have = set(client.get("licenses", []))
    if need:
        if need & have:
            score += 15
            reasons.append("Holds required license " + ", ".join(sorted(need & have)))
        elif have or row["category"] == "Construction":
            score -= 20
            blockers.append("License required: " + ", ".join(sorted(need)))

    if client.get("counties") and row["counties"]:
        if set(client["counties"]) & set(row["counties"]):
            score += 10
            reasons.append("Works in " + ", ".join(sorted(set(client["counties"]) & set(row["counties"]))[:3]))
        elif not client.get("statewide"):
            score -= 10
            blockers.append("Outside service area")

    if {"sb_set_aside", "sb_dvbe_option"} & sig:
        if certs & {"SB", "MB", "SB-PW", "DVBE"}:
            score += 15
            reasons.append("Restricted to certified SB/DVBE - smaller field")
        else:
            blockers.append("Only certified SB/DVBE firms may bid")
            unlock.append("SB")
    if "sb_preference" in sig:
        if certs & {"SB", "MB", "SB-PW"}:
            score += 10
            reasons.append("5% SB bid preference applies")
        else:
            unlock.append("SB")
    if row["dvbe_pct"] or "dvbe_participation" in sig:
        if "DVBE" in certs:
            score += 10
            reasons.append("Primes need DVBE partners - teaming lead")
        else:
            unlock.append("DVBE")
    if row["dbe_pct"] or "dbe_goal" in sig:
        if "DBE" in certs:
            score += 10
            reasons.append("Counts toward federal DBE goal")
        else:
            unlock.append("DBE")

    if row["urgency"] == "closed":
        blockers.append("Closed")
    elif row["urgency"] == "critical":
        blockers.append("Closes within 3 days")

    return {"score": max(0, min(100, score)), "reasons": reasons, "blockers": blockers,
            "unlock": sorted(set(unlock))}


def match_all(conn, rows, client):
    codes = {}
    for r in conn.execute("SELECT event_id, code FROM unspsc"):
        codes.setdefault(r["event_id"], []).append(r["code"])
    out = []
    for row in rows:
        if row["urgency"] == "closed":
            continue
        m = match(row, codes.get(row["id"], []), client)
        if m["score"] > 0:
            out.append({**row, "match": m})
    out.sort(key=lambda r: -r["match"]["score"])
    return out


# ------------------------------------------------------------------ retrieval for Ask

STOP = set("""a an and are as at be by can do does for from has have how i in is it its me my of on or our
should show tell that the their there these this to us was we what when where which who why will with
you your bid bids any all about""".split())


def fts_query(q):
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{1,}", q.lower()) if w not in STOP]
    return " OR ".join(f'"{w}"' for w in words[:12]) or '""'


def retrieve(conn, question, event_id=None, k=10):
    q = fts_query(question)
    hits = []
    seen = set()

    # 1. Direct document / attachment targeting if mentioned in question (e.g. "attachment 2", "exhibit b")
    if event_id:
        doc_matches = re.findall(r"(?:attachment|exhibit|addendum|schedule|appendix)\s*#?\s*([a-z0-9]+)", question.lower())
        for doc_key in doc_matches:
            try:
                for r in conn.execute(
                    "SELECT event_id, source, body, 0.0 AS rank FROM chunks WHERE event_id = ? AND (source LIKE ? OR body LIKE ?) LIMIT ?",
                    (event_id, f"%{doc_key}%", f"%{doc_key}%", 6)
                ):
                    h = dict(r)
                    h_key = (h["source"], h["body"][:80])
                    if h_key not in seen:
                        seen.add(h_key)
                        hits.append(h)
            except Exception:
                pass

    # 2. Full-Text Search (FTS5) using BM25
    if q and q != '""':
        sql = "SELECT event_id, source, body, bm25(chunks) AS rank FROM chunks WHERE chunks MATCH ?"
        args = [q]
        if event_id:
            sql += " AND event_id = ?"
            args.append(event_id)
        sql += " ORDER BY rank LIMIT ?"
        args.append(k)
        try:
            for r in conn.execute(sql, args):
                h = dict(r)
                h_key = (h["source"], h["body"][:80])
                if h_key not in seen:
                    seen.add(h_key)
                    hits.append(h)
        except Exception:
            pass

    # 3. Fallback / supplement for specific bid deep dives if few hits found
    if event_id and len(hits) < k:
        needed = k - len(hits)
        try:
            for r in conn.execute(
                "SELECT event_id, source, body, 99.0 AS rank FROM chunks WHERE event_id = ? AND source != 'Description' ORDER BY rowid LIMIT ?",
                (event_id, needed * 2)
            ):
                h = dict(r)
                h_key = (h["source"], h["body"][:80])
                if h_key not in seen:
                    seen.add(h_key)
                    hits.append(h)
                    if len(hits) >= k:
                        break
        except Exception:
            pass

    return hits[:k]


# ------------------------------------------------------------------ federal award history (USAspending)

def _awards_db():
    import sqlite3
    from config import DATA
    path = DATA / "reference" / "usaspending" / "awards.db"
    if not path.exists():
        return None
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


AWARD_COLS = ("piid, parent_piid, recipient, uei, agency, office, naics, naics_description, psc, set_aside, extent_competed, "
              "offers, pop_city, pop_state, start_date, current_end, potential_end, round(obligated) AS obligated, "
              "current_value, potential_value, last_action, description, permalink")


def award_history(ev, extra):
    """Who won this before (same solicitation id) and what the market looks like (same NAICS + office / state)."""
    c = _awards_db()
    if not c:
        return None
    sol = re.sub(r"[^A-Z0-9]", "", (extra.get("Solicitation Number") or "").upper())
    naics = next((x["code"] for x in extra.get("Codes") or [] if x.get("system") == "NAICS"), None)
    psc = next((x["code"] for x in extra.get("Codes") or [] if x.get("system") == "PSC"), None)  # DIBBS parts: FSC only
    office = extra.get("AAC Code") or extra.get("DoDAAC")
    pop = extra.get("Place of Performance State")
    try:
        prior = [dict(r) for r in c.execute(f"SELECT {AWARD_COLS} FROM awards WHERE sol_key = ? ORDER BY last_action DESC LIMIT 20",
                                            (sol,))] if len(sol) >= 6 else []
        market, scope, by = [], None, None
        # NAICS first; PSC/FSC when there is no NAICS (DIBBS parts) or NAICS finds nothing comparable
        for col, code_ in (("naics", naics), ("psc", psc)):
            if not code_ or market:
                continue
            for where, args, label in ((f"{col} = ? AND office_code = ?", (code_, office), "same contracting office"),
                                       (f"{col} = ? AND pop_state = ?", (code_, pop), f"performed in {pop}"),
                                       (f"{col} = ?", (code_,), "all loaded states")):
                if None in args:
                    continue
                rows = [dict(r) for r in c.execute(f"SELECT {AWARD_COLS} FROM awards WHERE {where} AND obligated > 0 "
                                                   "ORDER BY last_action DESC LIMIT 400", args)]
                if len(rows) >= 3:
                    market, scope, by = rows, f"same {col.upper()} {code_}, {label}", col
                    break
        vals = sorted(r["obligated"] for r in market if r["obligated"])
        offers = [int(r["offers"]) for r in market if (r["offers"] or "").isdigit()]
        summary = {"scope": scope, "awards": len(market), "total_obligated": sum(vals),
                   "median_award": vals[len(vals) // 2] if vals else None,
                   "set_aside_share": round(sum(1 for r in market if r["set_aside"] and "NO SET ASIDE" not in r["set_aside"].upper())
                                            / len(market), 2) if market else None,
                   "avg_offers": round(sum(offers) / len(offers), 1) if offers else None,
                   "top_winners": Counter(r["recipient"] for r in market).most_common(5)} if market else None
        loaded = c.execute("SELECT COUNT(*) FROM awards").fetchone()[0]
        return {"prior": prior, "summary": summary, "recent": market[:12], "naics": naics, "psc": psc, "office": office,
                "awards_loaded": loaded}
    finally:
        c.close()
