#!/usr/bin/env python3
"""Pennsylvania eMarketplace (https://www.emarketplace.state.pa.us): every current solicitation, every field,
every public attachment. ASP.NET WebForms; plain HTTP, no browser, no bot challenge.

Listing: Search.aspx with "records per page = ALL" in one postback (~190 rows, 12 columns).
Detail:  Solicitations.aspx?SID=<number>  (label/value page) + public FileDownload.aspx attachments.

Output (bronze): data/bronze/pa_emarketplace/PA_eMarketplace_Data.json, attachments/<SID>/, raw/<SID>.json

    python pa_emarketplace_scraper.py [--limit 5] [--refresh] [--no-attachments]
"""
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.statecommon import Bronze, call, download, fingerprint, html_text, now_iso, session  # noqa: E402

BASE = "https://www.emarketplace.state.pa.us/"
SEARCH = BASE + "Search.aspx"
B = Bronze("pa_emarketplace", "PA_eMarketplace_Data.json")
log = B.log


def form_fields(soup):
    data = {i["name"]: i.get("value", "") for i in soup.find_all("input") if i.get("name") and i.get("type") in ("hidden", "text", None)}
    for sel in soup.find_all("select"):
        opt = sel.find("option", selected=True) or sel.find("option")
        data[sel["name"]] = opt.get("value", "") if opt else ""
    return data


def listing(s):
    soup = BeautifulSoup(call(s, "GET", SEARCH, log).text, "html.parser")
    data = form_fields(soup)
    data.update({"__EVENTTARGET": "ctl00$MainBody$ddlRows", "__EVENTARGUMENT": "", "ctl00$MainBody$ddlRows": "32767"})
    soup = BeautifulSoup(call(s, "POST", SEARCH, log, data=data, timeout=240).text, "html.parser")
    grid = soup.find("table", id="ctl00_MainBody_grdResults")
    heads = [th.get_text(" ", strip=True) for th in grid.find("tr").find_all(["th", "td"])]
    rows = []
    for tr in grid.find_all("tr")[1:]:
        tds = tr.find_all("td")
        a = tr.find("a", href=re.compile(r"Solicitations\.aspx\?SID="))
        if len(tds) != len(heads) or not a:
            continue
        rows.append({"sid": a["href"].split("SID=", 1)[1], "row": {h: td.get_text(" ", strip=True) for h, td in zip(heads, tds)}})
    return rows


def detail(s, sid):
    url = BASE + "Solicitations.aspx"
    soup = BeautifulSoup(call(s, "GET", url, log, params={"SID": sid}).text, "html.parser")
    fields = {}
    for strong in soup.find_all("strong"):
        label = strong.get_text(" ", strip=True)
        if not label.endswith(":"):
            continue
        td = strong.find_next("td")
        if not td:
            continue
        checked = [lab.get_text(strip=True) for inp in td.find_all("input", checked=True)
                   for lab in [td.find("label", attrs={"for": inp.get("id")})] if lab]
        is_choice = td.find("input", type=re.compile(r"^(checkbox|radio)$"))  # e.g. Advertisement Type radios
        fields[label.rstrip(":").strip()] = ", ".join(checked) if is_choice else html_text(str(td))
    files = []
    for a in soup.find_all("a", href=re.compile(r"FileDownload\.aspx", re.I)):
        href = urljoin(BASE, a["href"])
        name = re.search(r"OriginalFileName=([^&]+)", a["href"])
        files.append({"name": (name.group(1) if name else a.get_text(strip=True)), "url": href})
    return fields, files, soup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-attachments", action="store_true")
    a = ap.parse_args()
    s = session()
    existing = B.load("SID")
    rows = listing(s)
    log.info("%d current PA solicitations", len(rows))
    live = set()
    for n, item in enumerate(rows[: a.limit] if a.limit else rows, 1):
        sid, row = item["sid"], item["row"]
        live.add(sid)
        fp = fingerprint(row)
        # unchanged and every file fetched -> skip; a transient download failure is retried on the next run
        failed = any(x.get("error") and "Connection" in x["error"] for x in (existing.get(sid) or {}).get("Attachments", []))
        if sid in existing and existing[sid].get("_fingerprint") == fp and not a.refresh and not failed:
            continue
        try:
            fields, files, soup = detail(s, sid)
            atts = [] if a.no_attachments else [download(s, B, sid, f["name"], f["url"], log=log) for f in files]
            if a.no_attachments:
                atts = [{"file_name": f["name"], "download_url": f["url"], "local_path": None, "size_bytes": None, "error": "not downloaded (--no-attachments)"} for f in files]
            existing[sid] = {"SID": sid, "List Row": row, "Detail": fields, "Attachments": atts,
                             "Detail URL": f"{BASE}Solicitations.aspx?SID={sid}",
                             "Raw Path": B.save_raw(sid, {"row": row, "detail": fields, "files": files}),
                             "Scraped At": now_iso(), "_fingerprint": fp}
            log.info("[%d/%d] %s %-55s files=%d", n, len(rows), sid[:24], (row.get("Solicitation Title") or "")[:55], len(atts))
        except Exception as e:
            log.error("%s failed: %s", sid, e)
        if n % 20 == 0:
            B.save(existing)
    for k, r in existing.items():
        r["Listed"] = k in live
    B.save(existing)
    log.info("saved %d records", len(existing))


if __name__ == "__main__":
    main()
