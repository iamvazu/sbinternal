"""Dashboard + JSON API. Standard library only so it runs as-is on the Jetson.

    python server.py            # http://127.0.0.1:8765
    BI_HOST=0.0.0.0 python server.py   # reachable from the office LAN
"""
import json
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import analyze
import auth
import config
import insights
import llm
import matchapi
import matcher
from db import connect, loads

conn = connect()
auth.seed_default_accounts(conn)
lock = threading.Lock()  # one sqlite connection shared by request threads
llm_busy = threading.Semaphore(1)  # the Jetson runs one generation at a time; queue the rest

GLOBAL_ASK_SYSTEM = """You are STRONGERbuilt's senior bid-intelligence assistant for public procurement: California state and local (Cal eProcure, OpenGov, Caltrans, CSU, Bonfire) plus
Oregon (OregonBuys), Florida (MFMP), Texas (ESBD), Georgia (GPR), Virginia (eVA) and Pennsylvania (eMarketplace).
Always say which state a bid is in; California SB/DVBE certifications do not apply to other states' bids.
Answer clearly from the catalog and excerpts provided. Cite event IDs in square brackets like [0000040342] or [OG-299986]. Be concise, practical, and direct."""

BID_CONSULTANT_SYSTEM = """You are the Senior Capture Director & Contracts Counsel at STRONGERbuilt, specializing in California state (Cal eProcure, Caltrans, CDCR, DGS, CSU) and local (municipalities, counties, transit agencies, OpenGov, Bonfire) public procurement, and in the state portals of Oregon, Florida, Texas, Georgia, Virginia and Pennsylvania.

Your role is to interrogate solicitation documents and attachments with forensic accuracy to protect bidders from hidden traps, identify win themes, and extract exact requirements.

Operating Rules:
1. STRICT DOCUMENT CITATION:
   - Base all statements on the provided solicitation text and attachment excerpts.
   - Always cite the specific document name (e.g. "[Attachment 2 - Flooring & Maintenance]", "[RFB Pages 1-7]", "[Addendum 3 Revision]") whenever asserting a fact, requirement, or frequency.
   - If an answer is not contained in the excerpts, state clearly: "Not specified in the retrieved document excerpts" and formulate the precise question the bidder should submit before the Q&A deadline.

2. CALIFORNIA STATUTORY & PROCUREMENT CONTEXT:
   - Public works & maintenance > $1,000 require DIR registration, certified payroll, and prevailing wage.
   - Verify CSLB license classes (A, B, C-xx) or required registrations (e.g. California Labor Commissioner property service employer registration).
   - DGS Small Business (SB/MB) 5% bid preference or SB set-aside rules (California Government Code § 14838).
   - Distinguish state 3% DVBE participation/incentives from federal-aid CUCP DBE goals.
   - Contract Hierarchy: Verify if Addenda supersede base RFB/IFB terms.
   - OUT-OF-STATE: if the dossier's State is not CA, the California rules above do NOT apply. Use that state's
     program instead (Oregon COBID, Florida OSD, Texas HUB subcontracting plan, Georgia GOSBO, Virginia SWaM,
     Pennsylvania Small Diverse Business) plus its contractor licensing, and tell the bidder which registrations
     they would need first.

3. STRUCTURE FOR IMMEDIATE ACTION:
   - **Direct Answer**: Concise 1-2 sentence bottom line.
   - **Detailed Breakdown / Evidence**: Specific requirements with document citations.
   - **Risks, Gotchas & Strict Thresholds**: Note penalties, liquidated damages, experience gates, or mandatory meetings.
   - **Capture Recommendation**: Immediate next steps or clarifying questions for the buyer."""


def bid_detail(eid):
    ev = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    if not ev:
        return None
    facts = loads((conn.execute("SELECT json FROM facts WHERE event_id=?", (eid,)).fetchone() or {"json": None})["json"], {})
    arow = conn.execute("SELECT * FROM analysis WHERE event_id=?", (eid,)).fetchone()
    a = loads(arow["json"], {}) if arow else {}
    out = {k: ev[k] for k in ev.keys() if k not in ("raw", "extra")}
    out["extra"] = loads(ev["extra"], {})
    out["row"] = insights.bid_row(ev, facts, a)
    out["facts"] = facts
    out["analysis"] = a
    out["analysis_meta"] = {k: arow[k] for k in ("model", "seconds", "created", "error")} if arow else None
    out["unspsc"] = [dict(r) for r in conn.execute("SELECT code, description FROM unspsc WHERE event_id=?", (eid,))]
    out["attachments"] = [dict(r) for r in conn.execute(
        "SELECT file_name, ext, size_bytes, pages, chars, error, download_url FROM attachments WHERE event_id=?", (eid,))]
    return out


def lens(rows):
    """Certification lens: for each certification, the live bids where holding it changes the outcome."""
    live = [r for r in rows if r["urgency"] != "closed"]
    groups = {
        "SB": [r for r in live if {"sb_set_aside", "sb_dvbe_option", "sb_preference"} & set(r["signals"])],
        "DVBE": [r for r in live if r["dvbe_pct"] or "dvbe_participation" in r["signals"] or "dvbe_incentive" in r["signals"]],
        "DBE": [r for r in live if r["dbe_pct"] or "dbe_goal" in r["signals"] or "federal_aid" in r["signals"]],
        "SB-PW": [r for r in live if "prevailing_wage" in r["signals"] and r["category"] in ("Construction", "Facilities & Maintenance")],
    }
    return {k: {"label": insights.CERTS[k], "count": len(v), "bids": v[:60]} for k, v in groups.items()}


def ask(question, event_id=None):
    with lock:
        k_val = 16 if event_id else 10
        hits = insights.retrieve(conn, question, event_id, k=k_val)
        focus = bid_detail(event_id) if event_id else None
        if not event_id:
            rows = insights.load_rows(conn)
            s = insights.stats(rows)
            ids = list(dict.fromkeys(h["event_id"] for h in hits))
            terms = [w for w in re.findall(r"\w{4,}", question.lower()) if w not in insights.STOP]
            ids += [r["id"] for r in rows if any(t in (r["name"] or "").lower() for t in terms)]
            by_id = {r["id"]: r for r in rows}
            catalog = [by_id[i] for i in dict.fromkeys(ids) if i in by_id][:40]

    if focus:
        att_list = [a["file_name"] for a in focus.get("attachments", [])]
        att_summary = ", ".join(att_list[:25])
        if len(att_list) > 25:
            att_summary += f" (and {len(att_list) - 25} more attachments)"
        ex = "\n\n".join(f"[DOCUMENT: {h['source']}]\n{h['body']}" for h in hits)
        analysis_body = json.dumps(focus.get("analysis", {}), indent=2) if focus.get("analysis") else "Not analyzed yet."
        facts_body = json.dumps(focus.get("facts", {}), indent=2) if focus.get("facts") else "No rule signals."
        extra_body = json.dumps(focus.get("extra", {}), indent=2) if focus.get("extra") else "{}"

        ctx = f"""SOLICITATION DOSSIER:
Event ID: {focus['id']}
Title: {focus['name']}
Agency / Department: {focus['dept']}
State: {focus.get('state') or 'CA'} | Source / Platform: {focus.get('source', '')} · {focus.get('level', '')}
Format: {focus.get('format', '')}
Published: {focus.get('published', '')} | Bids Due: {focus.get('end_date', '')}
Contact / Buyer: {focus.get('contact_name', '')} {focus.get('contact_email', '')} {focus.get('contact_phone', '')}
Available Attachments ({len(att_list)} total): {att_summary}

Pre-extracted Signals & Facts:
{facts_body}

Platform-Specific Details & Dates:
{extra_body}

Structured AI Analysis:
{analysis_body}

<document_excerpts>
{ex or 'No matching document text retrieved.'}
</document_excerpts>"""
        sys_prompt = BID_CONSULTANT_SYSTEM
    else:
        cat = "\n".join(f"[{r['id']}] {r['state']} | {r['name'][:90]} | {r['dept']} | {r['category']} | due {r['end_date']} "
                        f"({r['urgency']}) | signals: {','.join(r['signals'][:6])}" for r in catalog)
        ex = "\n\n".join(f"[{h['event_id']}] {h['source']}:\n{h['body'][:1200]}" for h in hits)
        ctx = (f"Today is {s['generated'][:10]}. {s['open']} open bids, {s['closing_7d']} close within 7 days.\n"
               f"By state: {s['by_state']}\nDepartments: {s['by_dept']}\nCategories: {s['by_category']}\n\n"
               f"Catalog of relevant bids:\n{cat or 'none'}\n\n<excerpts>\n{ex or 'none'}\n</excerpts>")
        sys_prompt = GLOBAL_ASK_SYSTEM

    with llm_busy:
        answer = llm.chat([{"role": "system", "content": sys_prompt},
                           {"role": "user", "content": f"{ctx}\n\nQuestion: {question}"}], temperature=0.2)
    return {
        "answer": answer,
        "event_id": event_id,
        "sources": [{"event_id": h["event_id"], "source": h["source"]} for h in hits],
        "attachments_consulted": sorted(list({h["source"] for h in hits}))
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json", headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def client_ip(self):
        return self.client_address[0] if self.client_address else ""

    def get_token(self):
        auth_header = self.headers.get("Authorization") or ""
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        cookie_header = self.headers.get("Cookie") or ""
        for part in cookie_header.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k == "bi_session":
                    return v.strip()
        return None

    def get_current_user(self):
        token = self.get_token()
        if not token:
            return None
        with lock:
            return auth.get_user_from_token(conn, token)

    def require_user(self, allowed_roles=None):
        user = self.get_current_user()
        if not user:
            self.send(401, {"error": "Authentication required"})
            return None
        if allowed_roles and user.get("role") not in allowed_roles:
            self.send(403, {"error": f"Permission denied for role: {user.get('role', '')}"})
            return None
        return user

    def do_GET(self):
        try:
            u = urlparse(self.path)
            self.qs = {k: v[0] for k, v in parse_qs(u.query).items()}
            self.route_get(unquote(u.path))
        except Exception as e:
            traceback.print_exc()
            self.send(500, {"error": str(e)})

    def do_POST(self):
        try:
            self.route_post(unquote(urlparse(self.path).path))
        except Exception as e:
            traceback.print_exc()
            self.send(500, {"error": str(e)})

    def do_DELETE(self):
        try:
            self.route_delete(unquote(urlparse(self.path).path))
        except Exception as e:
            traceback.print_exc()
            self.send(500, {"error": str(e)})

    def route_get(self, p):
        if p in ("/", "/index.html"):
            return self.send(200, (config.WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
        if p == "/api/status":
            return self.send(200, llm.status())

        # ---- Auth & Profile
        if p == "/api/auth/me":
            user = self.get_current_user()
            if not user:
                return self.send(401, {"error": "unauthorized"})
            return self.send(200, {"user": user})

        # ---- Admin & Superadmin
        if p == "/api/admin/users":
            user = self.require_user(("admin", "superadmin"))
            if not user:
                return
            with lock:
                return self.send(200, auth.list_users(conn))

        if p == "/api/superadmin/system":
            user = self.require_user(("superadmin",))
            if not user:
                return
            with lock:
                diag = auth.get_system_diagnostics(conn)
            return self.send(200, diag)

        # ---- bid <-> business matching (own sqlite connections; no shared lock needed)
        if p == "/api/firms/stats":
            return self.send(200, matcher.firm_stats())
        if p == "/api/firms/search":
            return self.send(200, matcher.search_firms(self.qs.get("q", ""), int(self.qs.get("limit", 25))))
        if m := re.fullmatch(r"/api/bidmatch/([^/]+)\.csv", p):
            body = matchapi.export_csv(m.group(1), self.qs.get("role") or None)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="bid-{m.group(1)}-matches.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if m := re.fullmatch(r"/api/bidmatch/([^/]+)", p):
            res = matcher.match_bid(m.group(1), int(self.qs.get("limit", 100)), self.qs.get("role") or None)
            if res:
                res["review"] = matchapi.saved_review(m.group(1))
            return self.send(200 if res else 404, res or {"error": "not found"})
        if m := re.fullmatch(r"/api/firmmatch/([\w-]+)", p):
            res = matcher.match_firm(m.group(1))
            return self.send(200 if res else 404, res or {"error": "not found"})
        with lock:
            if p == "/api/bids":
                return self.send(200, insights.load_rows(conn))
            if p == "/api/stats":
                return self.send(200, insights.stats(insights.load_rows(conn)))
            if p == "/api/lens":
                return self.send(200, lens(insights.load_rows(conn)))
            if p == "/api/brief":
                r = conn.execute("SELECT * FROM briefs WHERE kind='portfolio'").fetchone()
                return self.send(200, dict(r) if r else {})
            if p == "/api/clients":
                return self.send(200, insights.load_clients())
            if m := re.fullmatch(r"/api/bid/([^/]+)", p):
                d = bid_detail(m.group(1))
                return self.send(200 if d else 404, d or {"error": "not found"})
            if m := re.fullmatch(r"/api/match/([\w-]+)", p):
                client = next((c for c in insights.load_clients() if c["id"] == m.group(1)), None)
                if not client:
                    return self.send(404, {"error": "unknown client"})
                return self.send(200, insights.match_all(conn, insights.load_rows(conn), client))
            if m := re.fullmatch(r"/files/([^/]+)/(.+)", p):
                row = conn.execute("SELECT local_path FROM attachments WHERE event_id=? AND file_name=?",
                                   m.groups()).fetchone()
                if row:
                    clean_path = str(row["local_path"]).replace("\\", "/")
                    f = (config.PROJECT / clean_path).resolve()
                    roots = getattr(config, "ATTACHMENT_ROOTS", [config.PROJECT / "attachments", config.DATA / "bronze"])
                    if f.is_file() and any(r.resolve() in f.parents for r in roots):
                        ctype = "application/pdf" if f.suffix.lower() == ".pdf" else "application/octet-stream"
                        return self.send(200, f.read_bytes(), ctype)
                return self.send(404, {"error": "file not found"})
        self.send(404, {"error": "no route"})

    def route_delete(self, p):
        if m := re.fullmatch(r"/api/superadmin/users/([\w-]+)", p):
            user = self.require_user(("superadmin",))
            if not user:
                return
            with lock:
                ok, err = auth.delete_user(conn, m.group(1), actor_user=user, ip=self.client_ip())
            if not ok:
                return self.send(400, {"error": err})
            return self.send(200, {"ok": True})
        self.send(404, {"error": "no route"})

    def route_post(self, p):
        # ---- Auth endpoints
        if p == "/api/auth/login":
            b = self.body()
            ua = self.headers.get("User-Agent") or ""
            with lock:
                user, token, err = auth.authenticate(conn, b.get("email", ""), b.get("password", ""), ip=self.client_ip(), user_agent=ua)
            if err:
                return self.send(401, {"error": err})
            cookie = f"bi_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={auth.SESSION_DAYS*86400}"
            return self.send(200, {"user": user, "token": token}, headers={"Set-Cookie": cookie})

        if p == "/api/auth/signup":
            b = self.body()
            with lock:
                user, err = auth.create_user(
                    conn, b.get("email", ""), b.get("password", ""),
                    b.get("first_name", ""), b.get("last_name", ""),
                    b.get("business_name", ""), role="employee", ip=self.client_ip()
                )
            if err:
                return self.send(400, {"error": err})
            with lock:
                user, token, _ = auth.authenticate(conn, b.get("email", ""), b.get("password", ""), ip=self.client_ip())
            cookie = f"bi_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={auth.SESSION_DAYS*86400}"
            return self.send(200, {"user": user, "token": token}, headers={"Set-Cookie": cookie})

        if p == "/api/auth/logout":
            token = self.get_token()
            with lock:
                auth.logout(conn, token)
            cookie = "bi_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
            return self.send(200, {"ok": True}, headers={"Set-Cookie": cookie})

        if p == "/api/auth/change-password":
            user = self.require_user()
            if not user:
                return
            b = self.body()
            with lock:
                ok, err = auth.change_password(conn, user["id"], b.get("old_password", ""), b.get("new_password", ""), ip=self.client_ip())
            if not ok:
                return self.send(400, {"error": err})
            return self.send(200, {"ok": True})

        # ---- User management endpoints
        if p == "/api/admin/users":
            user = self.require_user(("admin", "superadmin"))
            if not user:
                return
            b = self.body()
            with lock:
                new_user, err = auth.create_user(
                    conn, b.get("email", ""), b.get("password", ""),
                    b.get("first_name", ""), b.get("last_name", ""),
                    b.get("business_name", ""), role=b.get("role", "employee"),
                    actor_user=user, ip=self.client_ip()
                )
            if err:
                return self.send(400, {"error": err})
            return self.send(200, new_user)

        if m := re.fullmatch(r"/api/superadmin/users/([\w-]+)/role", p):
            user = self.require_user(("superadmin",))
            if not user:
                return
            b = self.body()
            with lock:
                ok, err = auth.update_user_role(conn, m.group(1), b.get("role", ""), actor_user=user, ip=self.client_ip())
            if not ok:
                return self.send(400, {"error": err})
            return self.send(200, {"ok": True})

        if m := re.fullmatch(r"/api/superadmin/users/([\w-]+)/status", p):
            user = self.require_user(("superadmin", "admin"))
            if not user:
                return
            b = self.body()
            with lock:
                ok, err = auth.toggle_user_status(conn, m.group(1), b.get("is_active", True), actor_user=user, ip=self.client_ip())
            if not ok:
                return self.send(400, {"error": err})
            return self.send(200, {"ok": True})

        if m := re.fullmatch(r"/api/superadmin/users/([\w-]+)/delete", p):
            user = self.require_user(("superadmin",))
            if not user:
                return
            with lock:
                ok, err = auth.delete_user(conn, m.group(1), actor_user=user, ip=self.client_ip())
            if not ok:
                return self.send(400, {"error": err})
            return self.send(200, {"ok": True})

        if p == "/api/ask":
            b = self.body()
            return self.send(200, ask(b.get("question", ""), b.get("event_id")))
        if m := re.fullmatch(r"/api/bidmatch/([^/]+)/review", p):
            with llm_busy:
                res = matchapi.review(m.group(1))
            return self.send(200 if res else 404, res or {"error": "not found"})
        if m := re.fullmatch(r"/api/clients/from-firm/([\w-]+)", p):
            c = matchapi.client_from_firm(m.group(1))
            return self.send(200 if c else 404, c or {"error": "unknown firm"})
        if m := re.fullmatch(r"/api/analyze/([^/]+)", p):
            with lock:
                ev = conn.execute("SELECT * FROM events WHERE id=?", (m.group(1),)).fetchone()
            if not ev:
                return self.send(404, {"error": "not found"})
            with llm_busy:
                status, secs = analyze.analyze_event(connect(), ev, force=True)
            with lock:
                return self.send(200, {"status": status, "seconds": secs, "bid": bid_detail(ev["id"])})
        if p == "/api/brief":
            with llm_busy:
                body = analyze.portfolio_brief(connect())
            return self.send(200, {"body": body})
        if p == "/api/clients":
            clients = self.body()
            if not isinstance(clients, list):
                return self.send(400, {"error": "expected a list"})
            insights.save_clients(clients)
            return self.send(200, clients)
        self.send(404, {"error": "no route"})


if __name__ == "__main__":
    threading.Thread(target=matcher.warm, daemon=True).start()  # firm index + bid profiles
    srv = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    print(f"STRONGERbuilt Bid Intelligence on http://{config.HOST}:{config.PORT}  (model {config.LLM_MODEL} @ {config.LLM_URL})")
    srv.serve_forever()
