#!/usr/bin/env python3
"""OpenGov Procurement scraper: all open California opportunities, every field, every attachment.

OpenGov server-renders its full application state into `window.__data`. Each project page carries the
complete project object (~150 fields: contacts, dates, NIGP categories, attachments with signed download
URLs, the full solicitation text, submission questionnaires, addenda). The scraper reads that object
directly instead of screen-scraping, so nothing is lost.

Output mirrors the Cal eProcure scraper so bidintel ingests both:
    opengov/OpenGov_Events_Data.json      one record per opportunity (Caleprocure-compatible + OpenGov extras)
    opengov/attachments/<project_id>/     every attachment + the solicitation PDF snapshot
    opengov/raw/<project_id>.json         the complete raw OpenGov project object

    python opengov_scraper.py                  # vendor "open bids, state = CA" list (needs login in .env)
    python opengov_scraper.py --portals        # public agency portals in agencies.txt (no login needed)
    python opengov_scraper.py --portals octa saccounty --limit 5
    python opengov_scraper.py --refresh        # re-capture projects already saved
    python opengov_scraper.py --inventory      # every datapoint captured and its fill rate

Cloudflare: procurement.opengov.com is behind a Cloudflare check. The scraper drives a real, visible Chrome
with a persistent profile; if "Just a moment..." appears, click the checkbox once in that window. The
clearance and login persist in opengov/.chrome_profile. Nothing here tries to bypass the check.
Credentials: opengov/.env  (OPENGOV_EMAIL, OPENGOV_PASSWORD, OPENGOV_VENDOR_ID)
"""
import argparse
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

HERE = Path(__file__).resolve().parent              # scrapers/opengov: code, agencies.txt, .env, browser profile
PROJECT = HERE.parent.parent
BRONZE = PROJECT / "data" / "bronze" / "opengov"     # pipeline bronze layer: everything this scraper produces
RAW_DIR = BRONZE / "raw"
ATT_DIR = BRONZE / "attachments"
BRONZE.mkdir(parents=True, exist_ok=True)
PROFILE = HERE / ".chrome_profile"
OUT_JSON = BRONZE / "OpenGov_Events_Data.json"
AGENCIES = HERE / "agencies.txt"
BASE = "https://procurement.opengov.com"
PT = ZoneInfo("America/Los_Angeles")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                    handlers=[logging.FileHandler(BRONZE / "opengov_scraper.log", encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger("opengov")


def load_env():
    env = {}
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items() if k.startswith("OPENGOV_")})
    return env


ENV = load_env()
BIDS_URL = f"{BASE}/vendors/{ENV.get('OPENGOV_VENDOR_ID', '505726')}/open-bids?states=CA"
PROJECT_RE = re.compile(r"/portal/([\w-]+)/projects/(\d+)")
CODE_SETS = {100: "NIGP", 200: "NAICS", 300: "UNSPSC"}  # OpenGov category setId -> code system


# ---------------------------------------------------------------- browser

def make_driver():
    o = webdriver.ChromeOptions()
    o.add_argument("--window-size=1400,950")
    o.add_argument(f"--user-data-dir={PROFILE}")
    o.add_argument("--disable-blink-features=AutomationControlled")
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=o)


def challenged(d):
    try:
        return "just a moment" in (d.title or "").lower()
    except WebDriverException:
        return False


def wait_clear(d, timeout=900):
    if not challenged(d):
        return
    log.warning(">>> Cloudflare check is showing. Please click the checkbox in the Chrome window (waiting up to %d min).", timeout // 60)
    flag = HERE / "WAITING_FOR_CLOUDFLARE.txt"
    flag.write_text("Click the Cloudflare checkbox in the Chrome window opened by opengov_scraper.py\n")
    t = time.time()
    while time.time() - t < timeout:
        time.sleep(2)
        if not challenged(d):
            log.info("Cloudflare check passed.")
            flag.unlink(missing_ok=True)
            time.sleep(3)
            return
    raise RuntimeError("Cloudflare check was not completed in time")


def goto(d, url, settle=3):
    d.get(url)
    wait_clear(d)
    time.sleep(settle)


def state(d):
    return d.execute_script("return window.__data || null") or {}


def login_if_needed(d):
    goto(d, BIDS_URL)
    if not re.search(r"login|sign-in", d.current_url):
        return
    if not ENV.get("OPENGOV_EMAIL") or not ENV.get("OPENGOV_PASSWORD"):
        raise RuntimeError("Set OPENGOV_EMAIL / OPENGOV_PASSWORD in opengov/.env")
    log.info("Logging in as %s", ENV["OPENGOV_EMAIL"])
    try:  # 2026 sign-in screen: "Vendor Login" first, then email -> password
        WebDriverWait(d, 20).until(EC.element_to_be_clickable(
            (By.XPATH, "//button[normalize-space()='Vendor Login'] | //a[normalize-space()='Vendor Login']"))).click()
        time.sleep(3); wait_clear(d)
    except Exception:
        pass

    def fail(msg):
        d.save_screenshot(str(HERE / "login_failed.png"))
        raise RuntimeError(f"{msg} - see opengov/login_failed.png")
    try:
        email = WebDriverWait(d, 40).until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "input[type='email'], input[name='email'], input[id*='email' i], input[autocomplete='username']")))
    except Exception:
        fail("login form not found")
    email.clear(); email.send_keys(ENV["OPENGOV_EMAIL"])
    d.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit']").click()
    time.sleep(3); wait_clear(d)
    try:
        pw = WebDriverWait(d, 40).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']")))
    except Exception:
        fail("password field not found")
    pw.clear(); pw.send_keys(ENV["OPENGOV_PASSWORD"])
    d.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit']").click()
    for _ in range(60):
        time.sleep(1)
        wait_clear(d)
        if "incorrect email" in d.page_source.lower():  # stop at once: retrying can lock the account
            fail("OpenGov rejected the email/password in opengov/.env")
        if not re.search(r"login|sign-in|auth", d.current_url):
            break
    else:
        fail("login did not complete")
    log.info("Logged in.")
    goto(d, BIDS_URL)


# ---------------------------------------------------------------- listing

def walk(o, fn):
    fn(o)
    if isinstance(o, dict):
        for v in o.values():
            walk(v, fn)
    elif isinstance(o, list):
        for v in o:
            walk(v, fn)


def project_rows(data):
    """Project summaries anywhere in the app state (list pages)."""
    rows = {}

    def visit(o):
        if isinstance(o, dict) and "id" in o and "title" in o and "proposalDeadline" in o \
                and isinstance(o.get("government"), dict) and o["government"].get("code"):
            rows[str(o["id"])] = o
    walk(data, visit)
    return rows


def click_next(d):
    for xp in ["//button[normalize-space()='Next' and not(@disabled)]", "//*[@aria-label='Next page' and not(@disabled)]"]:
        try:
            b = d.find_element(By.XPATH, xp)
            if b.get_attribute("disabled") or "disabled" in (b.get_attribute("class") or ""):
                return False
            d.execute_script("arguments[0].click()", b)
            time.sleep(3)
            return True
        except Exception:
            continue
    return False


def paged_rows(d, base_url, label, page_size=50):
    """List pages are server-rendered from URL params (?page=N&limit=M), so load each page directly;
    fall back to clicking Next if a list ignores the params."""
    sep = "&" if "?" in base_url else "?"
    rows = {}
    for page in range(1, 200):
        goto(d, f"{base_url}{sep}status=open&page={page}&limit={page_size}")
        got = project_rows(state(d))
        new = {k: v for k, v in got.items() if k not in rows}
        rows.update(got)
        total = next((o.get("count") for o in _counts(state(d))), None)
        if not new or (total and len(rows) >= total):
            break
    if len(rows) <= page_size and click_next(d):  # params ignored? walk with the Next button instead
        for _ in range(200):
            before = len(rows)
            rows.update(project_rows(state(d)))
            if len(rows) == before or not click_next(d):
                break
    log.info("%-26s %d open projects", label, len(rows))
    return rows


def _counts(data):
    out = []

    def visit(o):
        if isinstance(o, dict) and isinstance(o.get("count"), int) and isinstance(o.get("rows"), list):
            out.append(o)
    walk(data, visit)
    return out


def as_listing(rows):
    return {pid: {"url": f"{BASE}/portal/{o['government']['code']}/projects/{pid}", "gov": o["government"]["code"],
                  "agency": ((o["government"].get("organization") or {}).get("name")), "row": o}
            for pid, o in rows.items()}


def collect_vendor_list(d):
    listing = as_listing(paged_rows(d, BIDS_URL, "vendor open-bids (CA)"))
    known = set(AGENCIES.read_text().split()) if AGENCIES.exists() else set()
    found = {v["gov"] for v in listing.values()}
    if found - known:  # remember every CA agency seen, so the no-login portal mode stays complete
        AGENCIES.write_text("\n".join(sorted(known | found)) + "\n")
        log.info("agencies.txt: +%d new agency portals", len(found - known))
    return listing


def collect_portals(d, codes):
    listing = {}
    for code in codes:
        try:
            listing.update(as_listing(paged_rows(d, f"{BASE}/portal/{code}", f"portal {code}")))
        except Exception as e:
            log.warning("portal %s: %s", code, e)
    return listing


# ---------------------------------------------------------------- detail

def html_text(h):
    if not h:
        return ""
    return re.sub(r"\n{3,}", "\n\n", BeautifulSoup(str(h), "html.parser").get_text("\n")).strip()


def pacific(iso):
    """'2026-11-02T19:00:00.000Z' -> ('11/02/2026 11:00AM PST', '2026-11-02T11:00') in Cal eProcure's format."""
    if not iso:
        return None, None
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PT)
    except ValueError:
        return iso, None
    return t.strftime("%m/%d/%Y %I:%M%p ") + t.tzname(), t.strftime("%Y-%m-%dT%H:%M")


def fetch_project(d, info):
    goto(d, info["url"], settle=1)
    pid = info["url"].rsplit("/", 1)[-1]
    for _ in range(30):
        p = (state(d).get("publicProject") or {}).get("project") or {}
        if str(p.get("id")) == pid:
            return p
        time.sleep(1)
    raise RuntimeError("project data did not load")


def file_list(p):
    """Every downloadable file on the project: attachments, addenda files, and the solicitation PDF."""
    files, seen = [], set()

    def visit(o):
        if isinstance(o, dict) and isinstance(o.get("url"), str) and o["url"].startswith("http") and (o.get("filename") or o.get("fileExtension")):
            key = o.get("id") or o["url"].split("?")[0]
            if key not in seen:
                seen.add(key)
                files.append(o)
    walk(p.get("attachments"), visit)
    walk(p.get("addendums"), visit)
    walk(p.get("proposalDocuments"), visit)
    if isinstance(p.get("documentAttachment"), dict) and p["documentAttachment"].get("url"):
        doc = dict(p["documentAttachment"], type="solicitation_document")
        if (doc.get("id") or doc["url"].split("?")[0]) not in seen:
            files.insert(0, doc)
    return files


def safe_name(s):
    return re.sub(r"[^\w.\-() ]+", "_", unquote(str(s))).strip(" ._")[:150] or "file"


def download(pid, files, session):
    out_dir = ATT_DIR / pid
    out_dir.mkdir(parents=True, exist_ok=True)
    results, used = [], set()
    for f in files:
        name = safe_name(f.get("filename") or f.get("name") or f"file_{f.get('id')}")
        if f.get("fileExtension") and not name.lower().endswith("." + f["fileExtension"].lower()):
            name += "." + f["fileExtension"].lower()
        while name.lower() in used:
            name = "_" + name
        used.add(name.lower())
        dest = out_dir / name
        err = None
        if not (dest.exists() and dest.stat().st_size > 0):
            try:  # signed S3 URLs: direct download, no Cloudflare, valid ~20 h
                with session.get(f["url"], stream=True, timeout=180) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as fh:
                        for chunk in r.iter_content(1 << 16):
                            fh.write(chunk)
            except Exception as e:
                err = str(e)[:200]
                dest.unlink(missing_ok=True)
        ok = dest.exists()
        results.append({
            "file_name": name, "title": f.get("title") or f.get("name"), "type": f.get("type"),
            "download_url": f["url"], "local_path": str(dest.relative_to(PROJECT)).replace("/", "\\") if ok else None,
            "size_bytes": dest.stat().st_size if ok else None, "error": err})
    return results


def normalize(p, info, atts):
    gov = p.get("government") or {}
    org = gov.get("organization") or {}
    tmpl = p.get("template") or {}
    pub, pub_iso = pacific(p.get("releaseProjectDate") or p.get("postedAt"))
    due, due_iso = pacific(p.get("proposalDeadline"))
    qa, _ = pacific(p.get("qaDeadline"))
    pre, _ = pacific(p.get("preProposalDate"))
    sections = {s["id"]: s.get("title") for s in p.get("projectSections") or []}
    sol_text = []
    for c in p.get("criteria") or []:
        body = html_text(c.get("description"))
        if body:
            head = c.get("title") or sections.get(c.get("project_section_id")) or ""
            sol_text.append(f"## {head}\n{body}" if head else body)
    evaluation = [{"phase": ph.get("title"), "criterion": c.get("title"), "weight": c.get("weight")}
                  for ph in p.get("evaluationPhases") or [] for c in ph.get("scoringCriteria") or []]
    price_items = [{"table": pt.get("title") or pt.get("description"), "description": html_text(it.get("description"))[:400],
                    "quantity": it.get("quantity"), "unit": it.get("unitToMeasure")}
                   for pt in p.get("priceTables") or [] for it in pt.get("priceItems") or [] if not it.get("isHeaderRow")][:300]
    if evaluation:
        sol_text.append("## Evaluation criteria\n" + "\n".join(f"- {e['criterion']}: {e['weight']}" for e in evaluation))
    if price_items:
        sol_text.append("## Bid line items\n" + "\n".join(f"- {i['description']} ({i['quantity'] or ''} {i['unit'] or ''})".strip() for i in price_items))
    contact = {
        "Contact Name": p.get("contactFullName"), "Title": p.get("contactTitle"), "Email": p.get("contactEmail"),
        "Phone": p.get("contactPhoneComplete") or p.get("contactPhone"),
        "Address": ", ".join(x for x in (p.get("contactAddress1"), p.get("contactCity"), p.get("contactState"), p.get("contactZipCode")) if x),
    } if not p.get("hideContact") else {}
    proc = {
        "Name": p.get("procurementFullName"), "Title": p.get("procurementTitle"), "Email": p.get("procurementEmail"),
        "Phone": p.get("procurementPhoneComplete") or p.get("procurementPhone"),
    } if not p.get("hideProcurementContact") else {}
    return {
        "Event Name": (p.get("title") or "").strip(),
        "Details": {
            "Event ID": f"OG-{p['id']}",
            "Dept": org.get("name"),
            "Department Code": gov.get("code"),
            "Department": p.get("departmentName"),
            "Agency City": org.get("city"),
            "Format/Type": " / ".join(x for x in (tmpl.get("processAcronym"), tmpl.get("procurementClassification") or tmpl.get("title")) if x),
            "Solicitation Number": p.get("financialId"),
            "Status": p.get("status"),
            "Published Date": pub,
            "Event End Date": due,
            "Question Deadline": qa,
            "Pre-Bid Meeting": " | ".join(x for x in (pre, p.get("preProposalLocation"), html_text(p.get("preProposalText"))[:300]) if x) or None,
            "Q&A Response Deadline": pacific(p.get("qaResponseDeadline"))[0],
            "Sealed Bid": p.get("hasSealedBid"),
            "Addenda": len(p.get("addendums") or []),
            "Vendors Notified": p.get("notifiedVendors"),
            "Last Updated": p.get("lastUpdatedAt"),
            "State": "CA",
        },
        "Contact Information": {k: v for k, v in contact.items() if v},
        "Procurement Contact": {k: v for k, v in proc.items() if v},
        "Description": html_text(p.get("summary")) + ("\n\n" + html_text(p.get("background")) if p.get("background") else ""),
        # Agencies tag projects with NIGP (set 100), NAICS (200) and/or UNSPSC (300). UNSPSC also goes into the
        # Cal eProcure-compatible field so the matching engine's commodity layer works on local bids too.
        "UNSPSC Codes": [{"UNSPSC Classification": str(c.get("code")), "UNSPSC Classification Description": c.get("title")}
                         for c in p.get("categories") or [] if c.get("setId") == 300],
        "Commodity Codes": [{"code": str(c.get("code")), "title": c.get("title"), "set_id": c.get("setId"),
                             "system": CODE_SETS.get(c.get("setId"), "Other")} for c in p.get("categories") or []],
        "Timeline": [{"label": t.get("title") or t.get("name"), "date": pacific(t.get("date") or t.get("startDate"))[0],
                      "location": t.get("location")} for t in p.get("timelines") or []],
        "Evaluation Criteria": evaluation,
        "Price Items": price_items,
        "Submission Requirements": [html_text(q.get("prompt") or q.get("title"))[:500] for q in p.get("questionnaires") or [] if not q.get("isTitle")],
        "Solicitation Text": "\n\n".join(sol_text),
        "Attachments": atts,
        "Detail URL": info["url"],
        "Source": "OpenGov",
        "Raw Path": f"data\\bronze\\opengov\\raw\\{p['id']}.json",
        "Scraped At": datetime.now().isoformat(timespec="seconds"),
        "_end_iso": due_iso, "_published_iso": pub_iso,
    }


# ---------------------------------------------------------------- inventory

def inventory():
    recs = json.loads(OUT_JSON.read_text(encoding="utf-8")) if OUT_JSON.exists() else []
    n = max(1, len(recs))
    print(f"{len(recs)} OpenGov records\n\nNORMALIZED FIELDS (share of records with a value)")
    c = Counter()
    for r in recs:
        for k, v in r.items():
            if isinstance(v, dict):
                for k2, v2 in v.items():
                    c[f"{k}.{k2}"] += v2 not in (None, "", [], 0, False)
            elif not k.startswith("_"):
                c[k] += v not in (None, "", [])
    for k, v in sorted(c.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {k:36} {v / n:5.0%}")
    raw = Counter()
    for f in RAW_DIR.glob("*.json"):
        p = json.loads(f.read_text(encoding="utf-8"))
        raw.update(k for k, v in p.items() if v not in (None, "", [], {}, False))
    print(f"\nRAW OPENGOV PROJECT FIELDS: {len(raw)} distinct keys with data across {len(list(RAW_DIR.glob('*.json')))} projects")
    print("  " + ", ".join(f"{k}({v})" for k, v in raw.most_common()))
    atts = [a for r in recs for a in r["Attachments"]]
    print(f"\nATTACHMENTS: {len(atts)} files, {sum(1 for a in atts if a['local_path'])} downloaded, "
          f"{sum(a['size_bytes'] or 0 for a in atts) / 1e6:.0f} MB, {sum(1 for a in atts if a['error'])} errors")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--portals", nargs="*", help="public agency portals (no login). No codes = all in agencies.txt")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--inventory", action="store_true")
    ap.add_argument("--renormalize", action="store_true", help="rebuild records from raw/ (no browser, no downloads)")
    a = ap.parse_args()
    if a.inventory:
        return inventory()
    if a.renormalize:
        recs = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        for i, r in enumerate(recs):
            pid = r["Details"]["Event ID"][3:]
            raw = RAW_DIR / f"{pid}.json"
            if raw.exists():
                listed = r["Details"].get("Listed")
                recs[i] = normalize(json.loads(raw.read_text(encoding="utf-8")), {"url": r["Detail URL"]}, r["Attachments"])
                recs[i]["Details"]["Listed"] = listed
        OUT_JSON.write_text(json.dumps(recs, indent=1, ensure_ascii=False), encoding="utf-8")
        return log.info("re-normalized %d records", len(recs))

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = {r["Details"]["Event ID"]: r for r in json.loads(OUT_JSON.read_text(encoding="utf-8"))} if OUT_JSON.exists() else {}
    d = make_driver()
    session = requests.Session()
    try:
        if a.portals is not None:
            goto(d, BASE)
            listing = collect_portals(d, a.portals or AGENCIES.read_text().split())
        else:
            login_if_needed(d)
            listing = collect_vendor_list(d)
        log.info("%d open CA projects found", len(listing))
        todo = list(listing.items())[: a.limit] if a.limit else list(listing.items())
        for n, (pid, info) in enumerate(todo, 1):
            prev = existing.get(f"OG-{pid}")
            changed = prev and prev["Details"].get("Last Updated") != info["row"].get("lastUpdatedAt", prev["Details"].get("Last Updated"))
            if prev and not a.refresh and not changed:
                continue
            try:
                p = fetch_project(d, info)
                (RAW_DIR / f"{pid}.json").write_text(json.dumps(p, indent=1, default=str), encoding="utf-8")
                rec = normalize(p, info, download(pid, file_list(p), session))
                existing[rec["Details"]["Event ID"]] = rec
                ok = sum(1 for x in rec["Attachments"] if x["local_path"])
                log.info("[%d/%d] %s %-60s %s | due %s | %d/%d files", n, len(todo), pid, rec["Event Name"][:60],
                         rec["Details"]["Dept"], rec["Details"]["Event End Date"], ok, len(rec["Attachments"]))
            except Exception as e:
                log.error("[%d/%d] %s failed: %s", n, len(todo), pid, e)
            if n % 10 == 0:
                OUT_JSON.write_text(json.dumps(list(existing.values()), indent=1, ensure_ascii=False), encoding="utf-8")
        live = {f"OG-{p}" for p in listing}
        for r in existing.values():
            r["Details"]["Listed"] = r["Details"]["Event ID"] in live
        OUT_JSON.write_text(json.dumps(list(existing.values()), indent=1, ensure_ascii=False), encoding="utf-8")
        log.info("saved %d records -> %s", len(existing), OUT_JSON.name)
    finally:
        d.quit()


if __name__ == "__main__":
    main()
