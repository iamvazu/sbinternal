#!/usr/bin/env python3
"""California State University (all 23 campuses + Chancellor's Office): open formal solicitations.

Source: CSU's public bid site on Jaggaer (SciQuest), https://bids.sciquest.com/apps/Router/PublicEvent?CustomerOrg=CalState
Plain HTTP: the listing is a server-rendered page, and each event has a public "View as PDF" (signed S3 link) holding
the full event summary. No browser, no bot challenge.

Captured per event: status, title, open / close date-time, type (RFP, IFB ...), number (campus-prefixed), buyer
name + email, and the event PDF (downloaded). Specs, Q&A and attachments inside Jaggaer need a free CSU supplier
account. They are noted, not accessed.

Output (bronze): data/bronze/csu/CSU_Events_Data.json, attachments/<event_id>/<event_id>-event.pdf

    python csu_scraper.py
"""
import html as H
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
BRONZE = PROJECT / "data" / "bronze" / "csu"
ATT = BRONZE / "attachments"
OUT_JSON = BRONZE / "CSU_Events_Data.json"
BASE = "https://bids.sciquest.com/apps/Router/"
LIST_URL = BASE + "PublicEvent?CustomerOrg=CalState"
BRONZE.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                    handlers=[logging.FileHandler(BRONZE / "csu_scraper.log", encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger("csu")
S = requests.Session()
S.headers["User-Agent"] = "STRONGERbuilt-BidIntelligence/1.0 (public bid data)"


def listing_pages():
    """Yield parsed listing pages, 200 events per page, until all open events are seen."""
    first = BeautifulSoup(S.get(LIST_URL, timeout=90).text, "html.parser")
    form = next(f for f in first.find_all("form") if "PageSize" in str(f))
    action = BASE + H.unescape(form["action"])
    data = {i.get("name"): i.get("value") or "" for i in form.find_all(["input", "select"]) if i.get("name")}
    page = 1
    while True:
        data.update({"PageSize": "200", "PageNum": str(page), "Open": "true", "ParamAction": "optionSearch"})
        soup = BeautifulSoup(S.post(action, data=data, timeout=120).text, "html.parser")
        yield soup
        total = re.search(r"of\s+(\d+)", soup.get_text(" "))
        if not total or page * 200 >= int(total.group(1)):
            return
        page += 1
        time.sleep(2)


def parse_rows(soup):
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td", class_="ResultsColumnValue", recursive=False)
        if len(cells) < 2:
            continue
        info = cells[1]
        title_a = info.find("a", href=re.compile("ViewSourcingEvent"))
        if not title_a:
            continue
        pairs = {}
        for rowdiv in info.find_all("div", class_="table-row-layout"):
            name = rowdiv.find(class_="data-row-name")
            content = rowdiv.find(class_="data-row-content")
            if name and content:
                pairs[name.get_text(strip=True)] = content
        pdf_a = info.find("a", href=re.compile(r"event\.pdf"))
        pdf_url = H.unescape(pdf_a["href"]) if pdf_a else None
        event_id = re.search(r"/(\d+)-event\.pdf", pdf_url).group(1) if pdf_url else None
        contact = pairs.get("Contact")
        email = contact.find("a", href=re.compile("mailto:")) if contact else None
        txt = lambda k: re.sub(r"\s+", " ", pairs[k].get_text(" ", strip=True)) if k in pairs else None
        yield {
            "Event ID": event_id,
            "Title": title_a.get_text(strip=True),
            "Status": cells[0].get_text(strip=True),
            "Open": txt("Open"), "Close": txt("Close"), "Type": txt("Type"), "Number": txt("Number"),
            "Contact Name": (contact.get_text(" ", strip=True).replace(email.get_text(strip=True), "").strip() if contact and email else txt("Contact")),
            "Contact Email": email.get_text(strip=True) if email else None,
            "Event PDF URL": pdf_url,
            "Detail URL": LIST_URL,
            "Documents Note": "Specifications, Q&A and attachments are inside CSUBUY (Jaggaer) and need a free CSU supplier account.",
        }


def download_pdf(ev):
    if not ev["Event PDF URL"] or not ev["Event ID"]:
        return None
    dest = ATT / ev["Event ID"] / f"{ev['Event ID']}-event.pdf"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = S.get(ev["Event PDF URL"], timeout=180)
        if not (r.ok and r.content[:5] == b"%PDF-"):
            return {"file_name": dest.name, "download_url": ev["Event PDF URL"], "local_path": None, "size_bytes": None,
                    "error": f"download failed ({r.status_code})"}
        dest.write_bytes(r.content)
    return {"file_name": dest.name, "download_url": ev["Event PDF URL"], "local_path": dest.relative_to(PROJECT).as_posix(),
            "size_bytes": dest.stat().st_size, "error": None}


def main():
    events = {}
    for soup in listing_pages():
        for ev in parse_rows(soup):
            if ev["Event ID"]:
                events[ev["Event ID"]] = ev
    log.info("%d open CSU events", len(events))
    for n, ev in enumerate(events.values(), 1):
        att = download_pdf(ev)
        ev["Attachments"] = [att] if att else []
        ev["Scraped At"] = datetime.now().isoformat(timespec="seconds")
        log.info("[%d/%d] %s %-50s %s | closes %s", n, len(events), ev["Event ID"], ev["Title"][:50], ev["Number"], ev["Close"])
        time.sleep(1)
    OUT_JSON.write_text(json.dumps(list(events.values()), indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("saved %d events -> %s", len(events), OUT_JSON.relative_to(PROJECT))


if __name__ == "__main__":
    main()
