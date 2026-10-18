#!/usr/bin/env python3
"""Bonfire (Euna) public portals: one engine for any agency on <portal>.bonfirehub.com.
Portals come from pipeline/sources.py (SOURCES["bonfire"]["portals"]); first: MTC (Metropolitan Transportation Commission).

Listing: the portal's public JSON (/PublicPortal/getOpenPublicOpportunitiesSectionData), plain HTTP.
Detail:  opportunity pages sit behind a Cloudflare check, so a real, visible Chrome with a persistent profile reads
         them, the same approach as OpenGov. The check normally clears by itself; if a checkbox appears, click it once.
         Nothing tries to bypass it.

Captured per opportunity: project details (ref #, type, status, open / questions-due / close dates, contact),
full project description, important events, supporting documents (name, type, description, date), requested
submission items, document takers (vendors who downloaded = likely bidders), prime and subcontractor lists
(teaming), and the raw JSON the page loads. Document FILES need a free Euna Supplier Network login; they are
listed, not downloaded.

Output (bronze): data/bronze/bonfire/Bonfire_Events_Data.json, raw/<portal>-<id>.json

    python bonfire_scraper.py                 # every portal in the registry
    python bonfire_scraper.py --portals mtc --limit 3
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from selenium import webdriver
from selenium.common.exceptions import WebDriverException

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(PROJECT))
from pipeline.sources import SOURCES  # noqa: E402

BRONZE = PROJECT / "data" / "bronze" / "bonfire"
RAW = BRONZE / "raw"
OUT_JSON = BRONZE / "Bonfire_Events_Data.json"
PROFILE = HERE / ".chrome_profile"
BRONZE.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                    handlers=[logging.FileHandler(BRONZE / "bonfire_scraper.log", encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger("bonfire")

PAGE_JS = r"""
const out = {details: {}, description: '', tables: {}, notice: ''};
document.querySelectorAll('.projectDetailSection').forEach(d => {
  const b = d.querySelector('b'); if (!b) return;
  out.details[b.innerText.replace(/:\s*$/, '').trim()] = d.innerText.replace(b.innerText, '').trim();
});
document.querySelectorAll('.modalSection').forEach(d => {
  const b = d.querySelector('b');
  if (b && /Project Description/i.test(b.innerText)) out.description = d.innerText.replace(b.innerText, '').trim();
});
['events-table', 'publicProjectDocumentsTable', 'portalRequestedInformationTable-no-group', 'publicDocumentTakersTable',
 'primeContractorsTable', 'subContractorsTable'].forEach(id => {
  const t = document.getElementById(id); if (!t) return;
  const heads = [...t.querySelectorAll('thead th')].map(th => th.innerText.trim());
  out.tables[id] = [...t.querySelectorAll('tbody tr')].map(tr => {
    const cells = [...tr.querySelectorAll('td')].map(td => td.innerText.trim());
    if (cells.length === 1) return null;  // "No data available"
    const row = {}; heads.forEach((h, i) => { if (h && h !== 'Actions') row[h] = cells[i] || ''; }); return row;
  }).filter(Boolean);
});
const note = [...document.querySelectorAll('*')].find(e => /To download files/i.test(e.innerText || '') && e.children.length === 0);
out.notice = note ? note.innerText.trim() : '';
return out;
"""


def make_driver():
    o = webdriver.ChromeOptions()
    o.add_argument("--window-size=1400,950")
    o.add_argument(f"--user-data-dir={PROFILE}")
    o.add_argument("--disable-blink-features=AutomationControlled")
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return webdriver.Chrome(options=o)


def wait_clear(d, timeout=900):
    t0 = time.time()
    warned = False
    while "just a moment" in (d.title or "").lower():
        if not warned and time.time() - t0 > 30:
            log.warning(">>> Cloudflare check showing in the Chrome window. Click the checkbox if one appears.")
            warned = True
        if time.time() - t0 > timeout:
            raise RuntimeError("Cloudflare check not completed")
        time.sleep(2)


def page_json(d):
    """JSON bodies the opportunity page fetched (subcontractor lists, custom events, notices)."""
    out = {}
    for e in d.get_log("performance"):
        m = json.loads(e["message"])["message"]
        if m["method"] == "Network.responseReceived":
            r = m["params"]["response"]
            if "json" in (r.get("mimeType") or "") and "bonfirehub.com" in r["url"]:
                try:
                    body = d.execute_cdp_cmd("Network.getResponseBody", {"requestId": m["params"]["requestId"]})["body"]
                    out[r["url"].split("?")[0]] = json.loads(body)
                except Exception:
                    pass
    return out


def list_open(portal):
    r = requests.get(f"https://{portal}.bonfirehub.com/PublicPortal/getOpenPublicOpportunitiesSectionData",
                     headers={"User-Agent": "STRONGERbuilt-BidIntelligence/1.0 (public bid data)"}, timeout=90)
    r.raise_for_status()
    p = r.json()["payload"]
    depts = {str(k): v for k, v in (p.get("departments") or {}).items()} if isinstance(p.get("departments"), dict) else {}
    return list(p["projects"].values()), depts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--portals", nargs="*")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    portals = SOURCES["bonfire"]["portals"]
    codes = a.portals or list(portals)
    RAW.mkdir(parents=True, exist_ok=True)
    existing = {r["Key"]: r for r in json.loads(OUT_JSON.read_text(encoding="utf-8"))} if OUT_JSON.exists() else {}
    live = set()
    d = make_driver()
    try:
        for code in codes:
            projects, depts = list_open(code)
            log.info("portal %s (%s): %d open opportunities", code, portals.get(code, code), len(projects))
            for n, pr in enumerate(projects[: a.limit] if a.limit else projects, 1):
                key = f"{code}-{pr['ProjectID']}"
                live.add(key)
                url = f"https://{code}.bonfirehub.com/opportunities/{pr['ProjectID']}"
                try:
                    d.get_log("performance")  # clear
                    d.get(url)
                    wait_clear(d)
                    time.sleep(5)
                    page = d.execute_script(PAGE_JS)
                    api = page_json(d)
                    (RAW / f"{key}.json").write_text(json.dumps({"list": pr, "page": page, "api": api}, indent=1), encoding="utf-8")
                    t = page["tables"]
                    existing[key] = {
                        "Key": key, "Portal": code, "Agency": portals.get(code, code), "Project ID": pr["ProjectID"],
                        "Reference ID": pr.get("ReferenceID"), "Name": pr.get("ProjectName"),
                        "Close (portal time)": pr.get("DateClose"), "Department": depts.get(str(pr.get("DepartmentID"))),
                        "Details": page["details"], "Description": page["description"],
                        "Events": t.get("events-table", []), "Documents": t.get("publicProjectDocumentsTable", []),
                        "Requested Information": t.get("portalRequestedInformationTable-no-group", []),
                        "Document Takers": t.get("publicDocumentTakersTable", []),
                        "Prime Contractors": t.get("primeContractorsTable", []),
                        "Subcontractors": t.get("subContractorsTable", []),
                        "Documents Note": page["notice"] or "To download files, log in to the Euna Supplier Network.",
                        "Detail URL": url, "Raw Path": f"data/bronze/bonfire/raw/{key}.json",
                        "Scraped At": datetime.now().isoformat(timespec="seconds"),
                    }
                    log.info("[%s %d/%d] %s %-55s docs=%d takers=%d", code, n, len(projects), pr.get("ReferenceID"),
                             (pr.get("ProjectName") or "")[:55], len(existing[key]["Documents"]), len(existing[key]["Document Takers"]))
                except (WebDriverException, RuntimeError) as e:
                    log.error("%s failed: %s", key, e)
                time.sleep(2)
    finally:
        d.quit()
    for k, r in existing.items():
        r["Listed"] = k in live
    OUT_JSON.write_text(json.dumps(list(existing.values()), indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("saved %d opportunities -> %s", len(existing), OUT_JSON.relative_to(PROJECT))


if __name__ == "__main__":
    main()
