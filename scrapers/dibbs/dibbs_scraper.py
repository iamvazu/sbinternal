#!/usr/bin/env python3
"""DIBBS (DLA Internet Bid Board System): every RFQ from DLA's own daily download files, plus the solicitation PDFs.

DIBBS shows the standard DoD Notice and Consent Banner; it is accepted once per host (www and dibbs2), the same
click every vendor makes. Then, instead of paging the huge ASP.NET grid, we use DLA's published daily files
(Acquisition Downloads > RFQ Download files, https://www.dibbs.bsm.dla.mil/RFQ/RFQDates.aspx?category=recent):
  in<yymmdd>.txt  index, fixed width (140 chars): solicitation, NSN/part, PR, return-by, PDF, qty, unit, nomenclature
  bq<yymmdd>.zip  bq*.txt  pre-formatted batch-quote CSV, 121 fields (layout: DIBBS "Batch File Format" page,
                           refs/help/Quoting/Batch/BatchFormat.aspx): set-aside code, solicitation type, HUBZone
                           preference, line number, PR, NSN, unit, quantity ...
                  as*.txt  approved sources: NSN, CAGE, part number
  ca<yymmdd>.zip  every solicitation PDF issued that day (~400 MB/day)
in/bq are kept in raw/. PDFs are extracted for solicitations still open (return-by >= today) into
attachments/<solicitation>/; the ca zip itself is then deleted (its content is what we keep).

Output (bronze): data/bronze/dibbs/DIBBS_Data.json  (one record per solicitation, lines grouped)

    python dibbs_scraper.py [--days 30] [--no-documents]
"""
import argparse
import csv
import io
import re
import sys
import time
import zipfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.statecommon import PROJECT, Bronze, call, now_iso, session  # noqa: E402

WWW = "https://www.dibbs.bsm.dla.mil"
FILES = "https://dibbs2.bsm.dla.mil/Downloads/RFQ/Archive/{}"
DATES = f"{WWW}/RFQ/RFQDates.aspx?category=recent"
DETAIL = f"{WWW}/RFQ/RfqRec.aspx?sn={{}}"
SET_ASIDE = {"Y": "Small Business Set-Aside", "H": "HUBZone Set-Aside", "R": "SDVOSB Set-Aside", "L": "WOSB Set-Aside",
             "A": "8(a) Set-Aside", "E": "EDWOSB Set-Aside", "N": None, "": None}
SOL_TYPE = {"F": "Fast Auto Evaluation", "P": "Auto Evaluation", "I": "Automated IDC (estimated quantities)", "": None}
PAUSE = 2.0

B = Bronze("dibbs", "DIBBS_Data.json")
log = B.log


def consent(s, url):
    """Accept the DoD Notice and Consent Banner on the host of `url` (it redirects there once per session)."""
    r = call(s, "GET", url, log=log, pause=PAUSE)
    if "Warning and Consent" not in r.text[:5000]:
        return r
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form")
    data = {i.get("name"): i.get("value", "") for i in soup.find_all("input") if i.get("name")}
    return call(s, "POST", urljoin(r.url, form.get("action") or r.url), log=log, data=data, pause=PAUSE)


def available_days(s):
    soup = BeautifulSoup(consent(s, DATES).text, "html.parser")
    return sorted({m.group(1) for a in soup.find_all("a") for m in [re.search(r"/in(\d{6})\.txt$", a.get("href") or "")] if m},
                  reverse=True)


def fetch(s, name):
    r = call(s, "GET", FILES.format(name), log=log, timeout=1800, pause=PAUSE)
    if r.status_code != 200 or r.content[:200].lstrip().lower().startswith((b"<!doctype", b"<html", b"\r<!doctype")):
        raise RuntimeError(f"{name}: no file (HTTP {r.status_code})")
    return r.content


def fetch_to(s, name, dest):
    """Stream a (possibly multi-GB) daily bundle to disk; resumes nothing, retries the whole file with back-off."""
    for attempt in range(4):
        try:
            with s.get(FILES.format(name), stream=True, timeout=(30, 300)) as r:
                if r.status_code != 200 or "html" in (r.headers.get("content-type") or ""):
                    raise RuntimeError(f"{name}: no file (HTTP {r.status_code})")
                with open(dest, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
            return dest
        except Exception as e:
            dest.unlink(missing_ok=True)
            log.warning("%s attempt %d: %s", name, attempt + 1, e)
            time.sleep(60 * (attempt + 1))
    raise RuntimeError(f"giving up on {name}")


def parse_day(day, idx_text, bq_zip):
    """index + batch file -> {solicitation: record}."""
    idx = {}
    for ln in idx_text.splitlines():
        if len(ln) < 129:
            continue
        idx[ln[62:72].strip()] = {"nsn_or_part": ln[13:62].strip(), "return_by": ln[72:80], "pdf": ln[80:99].strip(),
                                  "qty": ln[99:106].lstrip("0") or "0", "unit": ln[106:108], "nomenclature": ln[108:129].strip(),
                                  "codes": ln[129:].strip()}
    zf = zipfile.ZipFile(io.BytesIO(bq_zip))
    bq_name = next(n for n in zf.namelist() if n.startswith("bq"))
    as_name = next((n for n in zf.namelist() if n.startswith("as")), None)
    sources = {}
    if as_name:
        for row in csv.reader(io.StringIO(zf.read(as_name).decode("latin-1"))):
            if len(row) >= 3:
                sources.setdefault(row[0], []).append({"cage": row[1], "part_number": row[2]})
    sols = {}
    for f in csv.reader(io.StringIO(zf.read(bq_name).decode("latin-1"))):
        if len(f) < 121:
            continue
        sol, pr = f[0], f[45]
        i = idx.get(pr, {})
        rec = sols.setdefault(sol, {"Solicitation": sol, "Issued": f"20{day[:2]}-{day[2:4]}-{day[4:]}",
                                    "Return By": f[4], "Set-Aside Code": f[2], "Set-Aside": SET_ASIDE.get(f[2], f[2]),
                                    "Solicitation Type": SOL_TYPE.get(f[1], f[1]), "HUBZone Preference": f[56] == "Y",
                                    "PDF": i.get("pdf"), "Lines": [], "Approved Sources": {}})
        nsn = f[46]
        rec["Lines"].append({"line": f[43], "pr": pr, "nsn_or_part": nsn, "nomenclature": i.get("nomenclature"),
                             "unit": f[47], "qty": f[48], "first_article_waiver": f[63] or None,
                             "guaranteed_min": f[51] or None, "contract_max": f[53] or None})
        if nsn in sources:
            rec["Approved Sources"][nsn] = sources[nsn]
    return sols


def due(rec):
    try:
        return datetime.strptime(rec["Return By"], "%m/%d/%Y").date()
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="how many recent issue days to read (DIBBS keeps ~30)")
    ap.add_argument("--no-documents", action="store_true", help="skip the daily ca*.zip PDF bundles")
    a = ap.parse_args()
    s = session()
    consent(s, f"{WWW}/")
    days = available_days(s)[: a.days]
    consent(s, FILES.format(f"in{days[0]}.txt"))  # second host, second banner
    existing = B.load("Solicitation")
    today = date.today()
    for day in days:
        try:
            idx = B.raw / f"in{day}.txt"
            bq = B.raw / f"bq{day}.zip"
            if not idx.exists():
                idx.write_bytes(fetch(s, f"in{day}.txt"))
            if not bq.exists():
                bq.write_bytes(fetch(s, f"bq{day}.zip"))
            sols = parse_day(day, idx.read_bytes().decode("latin-1"), bq.read_bytes())
        except Exception as e:
            log.error("day %s failed: %s", day, e)
            continue
        open_sols = {k for k, r in sols.items() if (due(r) or today) >= today}
        for k, r in sols.items():
            old = existing.get(k, {})
            r["Attachments"] = old.get("Attachments") or []
            r["Detail URL"] = DETAIL.format(k)
            r["Scraped At"] = old.get("Scraped At") or now_iso()
            r["Open"] = k in open_sols
            existing[k] = r
        need = [k for k in open_sols if not any(x.get("local_path") for x in existing[k]["Attachments"])]
        if need and not a.no_documents:
            try:
                tmp = fetch_to(s, f"ca{day}.zip", B.raw / f"ca{day}.zip")  # big days (10/12: 7,196 RFQs) are GBs
                with zipfile.ZipFile(tmp) as zf:
                    names = set(zf.namelist())
                    for k in need:
                        pdf = existing[k].get("PDF") or f"{k}.pdf"
                        if pdf not in names:
                            existing[k]["Attachments"] = [{"file_name": pdf, "download_url": DETAIL.format(k), "local_path": None,
                                                           "size_bytes": None, "error": "not in DLA's daily document bundle"}]
                            continue
                        dest = B.att / k / pdf
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(zf.read(pdf))
                        existing[k]["Attachments"] = [{"file_name": pdf, "download_url": FILES.format(f"ca{day}.zip"),
                                                       "local_path": dest.relative_to(PROJECT).as_posix(),
                                                       "size_bytes": dest.stat().st_size, "error": None}]
                tmp.unlink()
            except Exception as e:
                log.error("documents for %s failed: %s", day, e)
        log.info("day %s: %d solicitations (%d open), %d lines, PDFs for %d", day, len(sols), len(open_sols),
                 sum(len(r["Lines"]) for r in sols.values()), len(need) if not a.no_documents else 0)
        B.save(existing)
    for r in existing.values():
        r["Open"] = (due(r) or today) >= today
    B.save(existing)
    log.info("saved %d solicitations (%d open)", len(existing), sum(r["Open"] for r in existing.values()))


if __name__ == "__main__":
    main()
