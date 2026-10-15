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


def bid_row(ev, facts, analysis):
    f = facts or {}
    a = analysis or {}
    sig = f.get("signals", {})
    d = days_left(ev["end_date"])
    sa = a.get("set_aside_and_preferences") or {}
    return {
        "id": ev["id"], "name": ev["name"], "dept": ev["dept"], "end_date": ev["end_date"],
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
    live = [r for r in rows if r["urgency"] not in ("closed",)]
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
    sql = "SELECT event_id, source, body, bm25(chunks) AS rank FROM chunks WHERE chunks MATCH ?"
    args = [q]
    if event_id:
        sql += " AND event_id = ?"
        args.append(event_id)
    sql += " ORDER BY rank LIMIT ?"
    args.append(k)
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    except Exception:
        return []
