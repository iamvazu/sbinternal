#!/usr/bin/env python3
"""Georgia (Georgia Procurement Registry, https://ssl.doas.state.ga.us/gpr/): every OPEN bidding event from state
agencies and local governments (cities, counties, K-12, authorities), its detail page and public documents.
Plain HTTP. GPR redirects non-browser user agents to an "unsupported browser" page, so this sends an honest,
browser-compatible User-Agent that still names STRONGERbuilt; there is no bot challenge.

Listing: POST /gpr/eventSearch (DataTables)  eventStatus=OPEN, length=1000
Detail:  GET  /gpr/eventDetails?eSourceNumber=<id>&sourceSystemType=<sys>   (event table, buyer, description,
              NIGP table, offerors' conference, documents)
Files:   GET  /gpr/downloadAttachment?attachmentId=<n>&sourceSystemType=<sys>   (public)

Output (bronze): data/bronze/ga_gpr/GA_GPR_Data.json, attachments/<id>/, raw/<id>.json

    python ga_gpr_scraper.py [--limit 5] [--refresh] [--no-attachments]
"""
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.statecommon import Bronze, call, download, fingerprint, html_text, now_iso, session  # noqa: E402

BASE = "https://ssl.doas.state.ga.us/gpr/"
B = Bronze("ga_gpr", "GA_GPR_Data.json")
log = B.log
COLUMNS = [("function", "false"), ("function", "true"), ("title", "true"), ("agencyName", "true"),
           ("function", "true"), ("function", "true"), ("function", "false"), ("status", "false")]


def listing(s):
    data = {"draw": "1", "order[0][column]": "5", "order[0][dir]": "asc", "start": "0", "length": "1000",
            "search[value]": "", "search[regex]": "false", "responseType": "ALL", "eventStatus": "OPEN", "eventIdTitle": "",
            "govType": "ALL", "govEntity": "", "catType": "ALL", "eventProcessType": "ALL", "dateRangeType": "",
            "rangeStartDate": "", "rangeEndDate": "", "isReset": "false", "persisted": "", "refreshSearchData": "false"}
    for i, (col, orderable) in enumerate(COLUMNS):
        data.update({f"columns[{i}][data]": col, f"columns[{i}][name]": "", f"columns[{i}][searchable]": "true",
                     f"columns[{i}][orderable]": orderable, f"columns[{i}][search][value]": "", f"columns[{i}][search][regex]": "false"})
    call(s, "GET", BASE, log)  # establishes the session the search expects
    r = call(s, "POST", BASE + "eventSearch", log, data=data,
             headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE}).json()
    rows = r.get("data") or []
    log.info("listing: %d of %s open events", len(rows), r.get("recordsTotal"))
    return rows


def section_text(soup, pane_id):
    el = soup.find(id=pane_id)
    return html_text(str(el)) if el else ""


def detail(s, row):
    params = {"eSourceNumber": row["esourceNumber"], "sourceSystemType": row.get("sourceId") or row.get("eSourceId") or "gpr20"}
    soup = BeautifulSoup(call(s, "GET", BASE + "eventDetails", log, params=params).text, "html.parser")
    pane = soup.find(id="collapse-eventDetails") or soup
    # The event header is a div-based table: each value cell carries its label in data-header="Label:".
    info = {}
    for cell in pane.find_all("div", attrs={"data-header": True}):
        label = cell["data-header"].rstrip(":").strip()
        link = cell.find("a", href=True)
        info.setdefault(label, link["href"] if label == "Agency Site" and link else cell.get_text(" ", strip=True))
    text = html_text(str(pane))
    buyer = re.search(r"Buyer Contact:\s*(.+?)(?:\n\s*Description|\Z)", text, re.S)
    desc = re.search(r"Description\s*\n(.+?)(?:\nNIGP Codes|\Z)", text, re.S)
    dates = {k: v for k, v in re.findall(r"(Start Date|End Date):\s*([^\n]+)", soup.get_text("\n"))}
    nigp = []
    t = soup.find("table", class_="nigp_table")
    if t:
        for tr in t.find_all("tr")[1:]:
            tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(tds) >= 2:
                nigp.append({"code": tds[0], "title": tds[1]})
    files = [{"name": a.get_text(strip=True), "url": urljoin(BASE, a["href"])}
             for a in soup.find_all("a", href=re.compile(r"downloadAttachment\?attachmentId="))]
    squash = lambda t: " · ".join(x.strip() for x in (t or "").split("\n") if x.strip())
    return {"event": info, "dates": dates, "buyer": squash(buyer.group(1)) if buyer else None,
            "description": (desc.group(1).strip() if desc else None), "nigp": nigp,
            "conference": squash(section_text(soup, "collapse-Offerors")), "files": files}, params


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-attachments", action="store_true")
    a = ap.parse_args()
    s = session(browser_ua=True)
    existing = B.load("Event ID")
    rows = listing(s)
    live = set()
    for n, row in enumerate(rows[: a.limit] if a.limit else rows, 1):
        eid = row["esourceNumber"]
        live.add(eid)
        fp = fingerprint(row.get("closingDate"), row.get("status"), row.get("title"))
        if eid in existing and existing[eid].get("_fingerprint") == fp and not a.refresh:
            continue
        try:
            det, params = detail(s, row)
            atts = []
            for f in det["files"]:
                if a.no_attachments:
                    atts.append({"file_name": f["name"], "download_url": f["url"], "local_path": None, "size_bytes": None, "error": "not downloaded (--no-attachments)"})
                else:
                    atts.append(download(s, B, eid, f["name"], f["url"], log=log))
            existing[eid] = {"Event ID": eid, "List": row, "Detail": det, "Attachments": atts,
                             "Detail URL": f"{BASE}eventDetails?eSourceNumber={params['eSourceNumber']}&sourceSystemType={params['sourceSystemType']}",
                             "Raw Path": B.save_raw(eid, {"list": row, "detail": det}), "Scraped At": now_iso(), "_fingerprint": fp}
            log.info("[%d/%d] %s %-50s %s | nigp=%d files=%d", n, len(rows), eid, (row.get("title") or "")[:50],
                     (row.get("agencyName") or "")[:28], len(det["nigp"]), len(atts))
        except Exception as e:
            log.error("%s failed: %s", eid, e)
        if n % 20 == 0:
            B.save(existing)
    for k, r in existing.items():
        r["Listed"] = k in live
    B.save(existing)
    log.info("saved %d records", len(existing))


if __name__ == "__main__":
    main()
