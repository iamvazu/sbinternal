#!/usr/bin/env python3
"""FedConnect (Compusearch/Unison PRISM): every public opportunity, its summary and its public documents.

Public pages only (no sign-in): Public_Opportunities.aspx is an ASP.NET grid (10 rows/page, Page$n postbacks).
A row's title postback (Hyper$i) renders the Opportunity Summary on the same URL: description, reference number,
dates, set-aside, NAICS, PSC/FSC, agency, contracting office (with address), place of performance, contact, vendor
conference and the documentation tree. Each document is a tree postback that returns the file.
State lives in the page (__VIEWSTATE), so every opportunity gets a fresh session: list -> page -> summary -> files.

Output (bronze): data/bronze/fedconnect/FedConnect_Data.json, attachments/<reference>/, raw/<reference>.json

    python fedconnect_scraper.py [--limit 5] [--refresh] [--no-attachments]
"""
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.statecommon import Bronze, call, download, fingerprint, html_text, now_iso, session  # noqa: E402

URL = "https://www.fedconnect.net/FedConnect/PublicPages/PublicSearch/Public_Opportunities.aspx"
DESC = "https://www.fedconnect.net/FedConnect/PublicPages/PublicSearch/Public_OpportunityDescription.aspx"
PAUSE = 1.5
COLS = ["Title", "Type", "Agency", "Issuing Office", "Issue Date", "Response Due Date", "PSC / FSC", "NAICS", "Reference Number"]
LABELS = ["Reference number", "Issue date", "Response due", "Set Aside", "NAICS", "PSC / FSC", "Agency",
          "Contracting office", "Place of Performance", "Contact", "Phone", "Fax", "Email", "Vendors conference",
          "Location", "Details"]

B = Bronze("fedconnect", "FedConnect_Data.json")
log = B.log


def hidden(soup):
    return {i.get("name"): i.get("value", "") for i in soup.find_all("input", type="hidden") if i.get("name")}


def action(soup):
    """Each page posts back to its own form action (the summary page posts to Public_OpportunitySummary.aspx)."""
    f = soup.find("form")
    return urljoin(URL, f.get("action")) if f and f.get("action") else URL


def postback(s, soup, target, arg=""):
    d = hidden(soup)
    d.update({"__EVENTTARGET": target, "__EVENTARGUMENT": arg})
    return call(s, "POST", action(soup), log=log, data=d, pause=PAUSE)


def parse_grid(soup):
    g = soup.find("table", id="gridOpportunity")
    rows, pages = [], set()
    for tr in (g.find_all("tr") if g else [])[1:]:
        tds = tr.find_all("td", recursive=False)
        links = [a.get("href", "") for a in tr.find_all("a")]
        pages |= {int(m) for h in links for m in re.findall(r"Page\$(\d+)", h)}
        if len(tds) == len(COLS):
            m = re.search(r"Hyper\$(\d+)", tds[0].a.get("href", "")) if tds[0].a else None
            rows.append({"cells": dict(zip(COLS, (td.get_text(" ", strip=True) for td in tds))), "hyper": m.group(1) if m else None})
    return rows, pages


def listing(s):
    """Every row on every page. The pager shows a window of page numbers, so keep following the highest one."""
    soup = BeautifulSoup(call(s, "GET", URL, log=log, pause=PAUSE).text, "html.parser")
    out, page, seen = [], 1, {1}
    while True:
        rows, pages = parse_grid(soup)
        for r in rows:
            r["page"] = page
        out += rows
        nxt = min((p for p in pages if p > page), default=None)
        if not nxt:
            break
        soup = BeautifulSoup(postback(s, soup, "gridOpportunity", f"Page${nxt}").text, "html.parser")
        page = nxt
        seen.add(page)
    log.info("listing: %d opportunities on %d pages", len(out), len(seen))
    return out


def summary(soup):
    text = re.sub(r"[ \t\xa0]+", " ", soup.get_text("\n"))
    lines = [x.strip() for x in text.split("\n") if x.strip()]
    f = {}
    for i, ln in enumerate(lines):
        for lab in LABELS:
            if ln.rstrip(":") == lab or ln.startswith(lab + ":"):
                val = ln.split(":", 1)[1].strip() if ":" in ln else ""
                if not val and i + 1 < len(lines) and lines[i + 1].rstrip(":") not in LABELS:
                    val = lines[i + 1]
                if val.startswith("What do I do now"):  # FedConnect help text follows an empty field
                    val = ""
                f.setdefault(lab, val)
    # the contracting office spans several lines (name + address) up to "Place of Performance"
    try:
        a, b = lines.index(next(x for x in lines if x.startswith("Contracting office"))), lines.index(next(x for x in lines if x.startswith("Place of Performance")))
        f["Contracting office"] = " ".join(x.split(":", 1)[-1].strip() if j == 0 else x for j, x in enumerate(lines[a:b])).strip()
    except StopIteration:
        pass
    m = re.search(r"Description\s*(.*?)\s*Overview\s*Reference number", "\n".join(lines), re.S)
    f["Description"] = m.group(1).strip() if m else None
    docs = []
    for a in soup.find_all("a"):
        h = a.get("href", "")
        name = a.get_text(" ", strip=True)
        m = re.search(r"__doPostBack\('([^']*Tree_Attachments[^']*)','([^']*)'\)", h)
        if m and name and m.group(2).startswith("s") and "," in m.group(2):  # leaf = "s<ref>\<folder>\<KIND>,<doc id>"
            # the argument is a JavaScript string literal: "\\\\" in the page source is one backslash
            docs.append({"name": name, "target": m.group(1), "arg": m.group(2).replace("\\\\", "\\")})
    return f, docs


def open_opportunity(s, row):
    """Fresh path to one opportunity's summary page (ASP.NET state is per page view)."""
    soup = BeautifulSoup(call(s, "GET", URL, log=log, pause=PAUSE).text, "html.parser")
    p = 1
    while p < row["page"]:
        _, pages = parse_grid(soup)
        nxt = min((x for x in pages if x > p and x <= row["page"]), default=max(x for x in pages if x > p))
        soup = BeautifulSoup(postback(s, soup, "gridOpportunity", f"Page${nxt}").text, "html.parser")
        p = nxt
    return BeautifulSoup(postback(s, soup, "gridOpportunity", f"Hyper${row['hyper']}").text, "html.parser")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-attachments", action="store_true")
    a = ap.parse_args()
    existing = B.load("Reference Number")
    rows = listing(session())
    live = set()
    for n, row in enumerate(rows[: a.limit] if a.limit else rows, 1):
        ref = row["cells"]["Reference Number"] or row["cells"]["Title"][:60]
        live.add(ref)
        fp = fingerprint(row["cells"])
        if ref in existing and existing[ref].get("_fingerprint") == fp and not a.refresh:
            continue
        try:
            s = session()
            soup = open_opportunity(s, row)
            fields, docs = summary(soup)
            if not fields.get("Reference number"):
                raise RuntimeError("summary page did not render")
            atts = []
            for d in docs:
                if a.no_attachments:
                    atts.append({"file_name": d["name"], "download_url": URL, "local_path": None, "size_bytes": None,
                                 "error": "not downloaded (--no-attachments)"})
                    continue
                data = {**hidden(soup), "__EVENTTARGET": d["target"], "__EVENTARGUMENT": d["arg"]}
                rec = download(s, B, ref, d["name"], action(soup), method="POST", data=data, log=log, server_name=True, pause=PAUSE)
                rec["download_url"] = URL
                atts.append(rec)
            existing[ref] = {"Reference Number": ref, "List Row": row["cells"], "Summary": fields, "Documents": [d["name"] for d in docs],
                             "Attachments": atts, "Detail URL": URL, "Raw Path": B.save_raw(ref, {"row": row, "summary": fields, "documents": docs}),
                             "Scraped At": now_iso(), "_fingerprint": fp}
            log.info("[%d/%d] %s %-50s %s | files=%d/%d", n, len(rows), ref, row["cells"]["Title"][:50], row["cells"]["Agency"][:20],
                     sum(1 for x in atts if x.get("local_path")), len(docs))
        except Exception as e:
            log.error("%s failed: %s", ref, e)
        if n % 10 == 0:
            B.save(existing)
    for k, r in existing.items():
        r["Listed"] = k in live
    B.save(existing)
    log.info("saved %d opportunities", len(existing))


if __name__ == "__main__":
    main()
