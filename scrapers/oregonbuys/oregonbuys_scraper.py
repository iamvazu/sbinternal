#!/usr/bin/env python3
"""Oregon (OregonBuys, https://oregonbuys.gov, Periscope BuySpeed): every open bid solicitation, every header
field, item lines with NIGP codes, and every public file attachment. Plain HTTP; the site's reCAPTCHA protects only
its login form and is not touched.

Listing: advancedSearchBid.xhtml?openBids=true, a PrimeFaces data table paged through its own AJAX call (25 rows).
Detail:  /bso/external/bidDetail.sda?docId=<bid #>&external=true  (label/value tables + item lines)
Files:   the page's own form POST (mode=download, downloadFileNbr=<n>); public, no login.

OregonBuys also carries local agencies (school districts, transit, cities): level = Local for those, State otherwise.

Output (bronze): data/bronze/oregonbuys/OregonBuys_Data.json, attachments/<bid>/, raw/<bid>.json

    python oregonbuys_scraper.py [--limit 5] [--refresh] [--no-attachments]
"""
import argparse
import re
import sys
import time
import warnings
from pathlib import Path

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.statecommon import Bronze, call, download, fingerprint, html_text, now_iso, session  # noqa: E402

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
HOST = "https://oregonbuys.gov"
LIST_URL = HOST + "/bso/view/search/external/advancedSearchBid.xhtml?openBids=true"
DETAIL_URL = HOST + "/bso/external/bidDetail.sda"
TABLE = "bidSearchResultsForm:bidResultId"
B = Bronze("oregonbuys", "OregonBuys_Data.json")
PAUSE = 2.5  # seconds between requests; OregonBuys throttles faster clients
log = B.log


def parse_rows(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tr in soup.find_all("tr"):
        a = tr.find("a", href=re.compile(r"bidDetail\.sda\?docId="))
        if not a:
            continue
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        out.append({"bid": a.get_text(strip=True), "cells": cells})
    return out


def listing(s):
    html = call(s, "GET", LIST_URL, log).text
    rows = parse_rows(html)
    total = int(re.search(r"rowCount:(\d+)", html).group(1))
    form = BeautifulSoup(html, "html.parser").find("form", id="bidSearchResultsForm")
    base = {i["name"]: i.get("value", "") for i in form.find_all("input") if i.get("name")}
    first = 25
    while first < total:  # PrimeFaces paging, exactly as the page does it (only 25-row pages are accepted)
        data = {**base, "javax.faces.partial.ajax": "true", "javax.faces.source": TABLE, "javax.faces.partial.execute": TABLE,
                "javax.faces.partial.render": TABLE, "javax.faces.behavior.event": "page", "javax.faces.partial.event": "page",
                TABLE + "_pagination": "true", TABLE + "_first": str(first), TABLE + "_rows": "25", TABLE + "_encodeFeature": "true"}
        r = call(s, "POST", LIST_URL, log, data=data, headers={"Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest"})
        rows += parse_rows("".join(re.findall(r"<!\[CDATA\[(.*?)\]\]>", r.text, re.S)))
        first += 25
    uniq = {r["bid"]: r for r in rows}
    log.info("listing: %d of %d open bids", len(uniq), total)
    return list(uniq.values())


def detail(s, bid):
    r = call(s, "GET", DETAIL_URL, log, params={"docId": bid, "external": "true", "parentUrl": "close"}, pause=PAUSE)
    soup = BeautifulSoup(r.text, "html.parser")
    header = {}
    for td in soup.find_all("td", class_="t-head-01"):
        label = td.get_text(" ", strip=True).rstrip(":").strip()
        val = td.find_next_sibling("td")
        if label and val is not None and label not in header:
            header[label] = html_text(str(val))
    # Item lines: the header table labels each one "Item # n: (nigp)" with the item text as its value; NIGP
    # code rows appear in the same order.
    item_texts = [v for k, v in header.items() if k.startswith("Item #")]
    header = {k: v for k, v in header.items() if not k.startswith("Item #")}
    codes = [c.find_next_sibling("td").get_text(" ", strip=True) for c in soup.find_all("td", string=re.compile(r"^\s*NIGP Code:"))
             if c.find_next_sibling("td")]
    items = [{"nigp": codes[i] if i < len(codes) else None, "description": t[:4000]} for i, t in enumerate(item_texts)]
    items += [{"nigp": c, "description": ""} for c in codes[len(item_texts):]]
    if not header.get("Bid Number"):  # never save an empty shell: the caller retries with a fresh session
        raise RuntimeError("detail page came back without its header")
    files = [{"name": a.get_text(strip=True), "nbr": re.search(r"downloadFile\('(\d+)'\)", a["href"]).group(1)}
             for a in soup.find_all("a", href=re.compile(r"downloadFile\('\d+'\)"))]
    form = soup.find("form", id="bidDetail")
    formdata = {i.get("name"): i.get("value", "") for i in form.find_all("input") if i.get("name")} if form else {}
    return header, items, files, formdata


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-attachments", action="store_true")
    a = ap.parse_args()
    s = session()
    existing = B.load("Bid Number")
    rows = listing(s)
    live = set()
    for n, row in enumerate(rows[: a.limit] if a.limit else rows, 1):
        bid = row["bid"]
        live.add(bid)
        fp = fingerprint(row["cells"])
        # unchanged and every file fetched -> skip; files the portal throttled (HTTP 202) are retried next run
        failed = any((x.get("error") or "").startswith("HTTP") for x in (existing.get(bid) or {}).get("Attachments", []))
        if bid in existing and existing[bid].get("_fingerprint") == fp and not a.refresh and not failed:
            continue
        try:
            # A download is a form POST that changes OregonBuys' server-side session; reusing that session made
            # later detail pages come back empty. So each bid gets its own fresh session (detail + its downloads).
            # OregonBuys rate-limits: after ~20 quick requests it serves empty pages for a few minutes. So we
            # go slowly (PAUSE between every request) and, if an empty page still appears, back off and retry.
            for attempt in range(4):
                s = session()
                try:
                    header, items, files, formdata = detail(s, bid)
                    break
                except RuntimeError:
                    if attempt == 3:
                        raise
                    log.warning("%s: empty page (rate limit), backing off %ds", bid, 60 * (attempt + 1))
                    time.sleep(60 * (attempt + 1))
            atts = []
            for f in files:
                name = f["name"] if "." in f["name"][-6:] else f"{f['name']}_{f['nbr']}.pdf"
                if a.no_attachments:
                    atts.append({"file_name": name, "download_url": f"{DETAIL_URL}?docId={bid}", "local_path": None, "size_bytes": None, "error": "not downloaded (--no-attachments)"})
                else:
                    for attempt in range(4):
                        rec = download(s, B, bid, name, DETAIL_URL, method="POST", data={**formdata, "downloadFileNbr": f["nbr"], "mode": "download"},
                                       log=log, server_name=True, pause=PAUSE)
                        # 202 with an empty body = OregonBuys throttling downloads; wait it out, don't hammer
                        if not (rec.get("error") or "").startswith("HTTP 202") or attempt == 3:
                            break
                        log.warning("%s: download throttled (202), waiting %ds", bid, 30 * (attempt + 1))
                        time.sleep(30 * (attempt + 1))
                    rec["download_url"] = f"{DETAIL_URL}?docId={bid}&external=true"
                    atts.append(rec)
            existing[bid] = {"Bid Number": bid, "List Cells": row["cells"], "Header": header, "Items": items,
                             "Attachments": atts, "Detail URL": f"{DETAIL_URL}?docId={bid}&external=true&parentUrl=close",
                             "Raw Path": B.save_raw(bid, {"cells": row["cells"], "header": header, "items": items, "files": files}),
                             "Scraped At": now_iso(), "_fingerprint": fp}
            log.info("[%d/%d] %s %-50s %s | files=%d", n, len(rows), bid, (header.get("Description") or "")[:50],
                     header.get("Organization", "")[:30], len(atts))
        except Exception as e:
            log.error("%s failed: %s", bid, e)
        if n % 20 == 0:
            B.save(existing)
    for k, r in existing.items():
        r["Listed"] = k in live
    B.save(existing)
    log.info("saved %d records", len(existing))


if __name__ == "__main__":
    main()
