#!/usr/bin/env python3
"""Virginia (eVA, https://mvendor.cgieva.com/Vendor/public/AllOpportunities.jsp): every OPEN opportunity from
state agencies, localities, schools and universities, from eVA's own public search index.

How: eVA's firewall only admits a real browser (it returns 403 to scripts and headless browsers), so this drives a
real, visible Chrome with a persistent profile. It opens the public listing page and, from inside that page, calls the
same public search endpoint the page itself uses (solrconnect.jsp), filtered to status "Open".

Not captured, on purpose: eVA's detail pages and document downloads are gated by Google reCAPTCHA. Automating
through a captcha is off-limits, so records keep a direct link for a person to open, and the document list is
left to that page. Everything in the public index is captured: title, full description, issuing entity, buyer,
work location, opportunity type, category, commodity lines, set-aside (Virginia SWaM small/micro), dates.

Output (bronze): data/bronze/eva/eVA_Data.json, raw/_solr_open.json

    python eva_scraper.py
"""
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

from selenium import webdriver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.statecommon import Bronze, now_iso  # noqa: E402

HERE = Path(__file__).resolve().parent
LIST_URL = "https://mvendor.cgieva.com/Vendor/public/AllOpportunities.jsp"
DETAIL = "https://mvendor.cgieva.com/Vendor/public/IVDetails.jsp?PageTitle=SO%20Details&rfp_id_lot={lot}&rfp_id_round={rnd}"
B = Bronze("eva", "eVA_Data.json")
log = B.log

# eVA's endpoint rejects '+'-encoded spaces (URLSearchParams style); spaces must be %20, as the page sends them.
QUERY = "q=*:*&fq=status%3A(%22Open%22)&sort=pubdate%20desc,id%20desc&rows=500&wt=json&cursorMark={cursor}"
FETCH_JS = """
const done = arguments[arguments.length - 1];
fetch('solrconnect.jsp?' + arguments[0], {credentials: 'same-origin'})
  .then(r => r.text()).then(t => done(t)).catch(e => done('ERROR ' + e));
"""


def main():
    o = webdriver.ChromeOptions()
    o.add_argument("--window-size=1300,900")
    o.add_argument(f"--user-data-dir={HERE / '.chrome_profile'}")
    # No automation-hiding flags: eVA's firewall must see this for what it is (a visible browser driven for a
    # person). If it ever refuses that, eVA needs a sanctioned data route, not a disguise.
    d = webdriver.Chrome(options=o)
    d.set_script_timeout(180)
    docs, cursor = [], "*"
    try:
        d.get(LIST_URL)
        t0 = time.time()
        while "Opportunities" not in (d.execute_script("return document.body ? document.body.innerText : ''") or "")[:3000]:
            if time.time() - t0 > 600:
                raise RuntimeError("eVA listing page did not load (complete any check shown in the Chrome window)")
            if time.time() - t0 > 20 and int(time.time() - t0) % 30 == 0:
                log.warning(">>> eVA has not loaded yet. If a verification check shows in the Chrome window, please complete it.")
            time.sleep(2)
        while True:
            body = d.execute_async_script(FETCH_JS, QUERY.format(cursor=quote(cursor, safe="")))
            if body.startswith("ERROR"):
                raise RuntimeError(body)
            try:
                res = json.loads(body)
            except ValueError:
                raise RuntimeError(f"search endpoint returned non-JSON: {body.strip()[:300]!r}")
            batch = res["response"]["docs"]
            docs += batch
            nxt = res.get("nextCursorMark")
            log.info("search page: +%d (total %d of %d)", len(batch), len(docs), res["response"]["numFound"])
            if not batch or not nxt or nxt == cursor:
                break
            cursor = nxt
            time.sleep(1.5)
    finally:
        d.quit()
    B.save_raw("_solr_open", docs)
    existing = B.load("ID")
    live = set()
    for doc in docs:
        rid = doc["id"]
        live.add(rid)
        existing[rid] = {"ID": rid, "Record": doc,
                         "Detail URL": DETAIL.format(lot=doc.get("internalid") or doc.get("externalid"), rnd=doc.get("version") or 0),
                         "Documents Note": "eVA shows documents on the opportunity page after a reCAPTCHA check; open the link to view them.",
                         "Scraped At": now_iso()}
    for k, r in existing.items():
        r["Listed"] = k in live
    B.save(existing)
    log.info("saved %d open eVA opportunities (%d records kept in total)", len(live), len(existing))


if __name__ == "__main__":
    main()
