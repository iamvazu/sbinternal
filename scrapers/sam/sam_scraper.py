#!/usr/bin/env python3
"""SAM.gov Contract Opportunities: every active federal notice + the public attachments of open ones.

Source of record, official public routes only:
  Notices:     the daily public extract (falextracts S3, "Contract Opportunities / datagov / Full CSV", ~200 MB,
               every ACTIVE notice, 47 columns: ids, office, type, set-aside, NAICS, PSC, place of performance,
               award fields, contacts, full description). Re-downloaded only when its Last-Modified changes.
  Attachments: the notice's public resource list, the same JSON sam.gov's own public notice page reads
               (/api/prod/opps/v3/opportunities/<noticeId>/resources). Each file states its access level; only
               accessLevel=public and not export-controlled files are downloaded. Controlled ones are listed, never
               requested.
If SAM_API_KEY is set (free key from sam.gov > Account Details) the official api.sam.gov route can be added later;
the extract already carries everything the search API returns.

Output (bronze): data/bronze/sam/SAM_Data.json   (all active notices; git-ignored, rebuilt daily from the extract)
                 data/bronze/sam/raw/ContractOpportunitiesFullCSV.csv, attachments/<noticeId>/

    python sam_scraper.py                         # refresh notices; attachments for open notices in the 7 states
    python sam_scraper.py --states all            # attachments for every open notice (long: run on the Jetson)
    python sam_scraper.py --no-attachments | --limit 20
"""
import argparse
import csv
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.statecommon import Bronze, call, download, fingerprint, now_iso, session  # noqa: E402

EXTRACT = "https://falextracts.s3.amazonaws.com/Contract%20Opportunities/datagov/ContractOpportunitiesFullCSV.csv"
RESOURCES = "https://sam.gov/api/prod/opps/v3/opportunities/{}/resources"
FILE = "https://sam.gov/api/prod/opps/v3/opportunities/resources/files/{}/download"
UI = "https://sam.gov/opp/{}/view"
BIDDABLE = {"Combined Synopsis/Solicitation", "Solicitation", "Presolicitation", "Sources Sought", "Special Notice"}
TARGET_STATES = {"CA", "OR", "FL", "TX", "GA", "VA", "PA"}
EASTERN = ZoneInfo("America/New_York")
PAUSE = 1.0

B = Bronze("sam", "SAM_Data.json")
log = B.log


def deadline(v):
    """ResponseDeadLine is ISO with offset, or a bare date (treated as end of day Eastern, SAM's default zone)."""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(hour=23, minute=59, tzinfo=EASTERN) if len(v) == 10 else d.replace(tzinfo=EASTERN)
    return d


def is_open(row, now):
    """Biddable and still due; notices with no deadline count as open for 90 days after posting."""
    d = deadline(row["ResponseDeadLine"])
    return row["Type"] in BIDDABLE and (d >= now if d else row["PostedDate"][:10] >= str(now.date() - timedelta(days=90)))


def fetch_extract(s):
    """Download the extract only if SAM published a newer one; returns the parsed rows."""
    dest = B.raw / "ContractOpportunitiesFullCSV.csv"
    stamp = B.raw / "extract_last_modified.txt"
    head = call(s, "HEAD", EXTRACT, log=log)
    lm = head.headers.get("Last-Modified", "")
    if not (dest.exists() and stamp.exists() and stamp.read_text() == lm):
        log.info("downloading SAM extract (Last-Modified %s, %s bytes)", lm, head.headers.get("Content-Length"))
        r = call(s, "GET", EXTRACT, log=log, timeout=900)
        r.raise_for_status()
        dest.write_bytes(r.content)
        stamp.write_text(lm)
    raw = dest.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")  # the extract is Windows-1252
    rows = list(csv.DictReader(io.StringIO(text)))
    log.info("extract %s: %d active notices", lm, len(rows))
    return rows, lm


def attachments(s, nid, no_download):
    r = call(s, "GET", RESOURCES.format(nid), log=log, pause=PAUSE)
    if r.status_code == 404:
        return [], None
    r.raise_for_status()
    lists = (r.json().get("_embedded") or {}).get("opportunityAttachmentList") or []
    out = []
    for a in (lists[0].get("attachments") if lists else []) or []:
        if a.get("deletedFlag") == "1":
            continue
        meta = {"name": a.get("name"), "type": a.get("type"), "access": a.get("accessLevel"),
                "export_controlled": a.get("exportControlled") == "1", "posted": a.get("postedDate"),
                "size": a.get("size"), "resource_id": a.get("resourceId")}
        if a.get("type") == "link":
            out.append({"file_name": a.get("name") or a.get("uri"), "download_url": a.get("uri"), "local_path": None,
                        "size_bytes": None, "error": "external link (not a SAM file)", **{"meta": meta}})
            continue
        url = FILE.format(a.get("resourceId"))
        if a.get("accessLevel") != "public" or meta["export_controlled"]:
            out.append({"file_name": a.get("name"), "download_url": UI.format(nid), "local_path": None, "size_bytes": a.get("size"),
                        "error": f"controlled ({a.get('accessLevel')}{', export-controlled' if meta['export_controlled'] else ''}): "
                                 "request access on SAM.gov", "meta": meta})
            continue
        if no_download:
            out.append({"file_name": a.get("name"), "download_url": url, "local_path": None, "size_bytes": a.get("size"),
                        "error": "not downloaded (--no-attachments)", "meta": meta})
            continue
        rec = download(s, B, nid, a.get("name") or a.get("resourceId"), url, log=log, pause=PAUSE)
        if rec.get("error") == "HTTP 400":  # SAM answers 400 "The resource has been deleted." for superseded files
            rec["error"] = "deleted on SAM.gov (replaced by an amendment); the current files are listed alongside"
        rec["meta"] = meta
        out.append(rec)
    return out, r.json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", default="target", help="'target' (CA OR FL TX GA VA PA) or 'all'")
    ap.add_argument("--limit", type=int, help="attachment pass: at most N notices")
    ap.add_argument("--no-attachments", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-list attachments even if the notice is unchanged")
    a = ap.parse_args()
    s = session()
    rows, lm = fetch_extract(s)
    existing = B.load("Notice ID")
    now = datetime.now(timezone.utc)
    live = set()
    for row in rows:
        nid = row["NoticeId"]
        live.add(nid)
        fp = fingerprint(row)
        old = existing.get(nid) or {}
        existing[nid] = {"Notice ID": nid, "Row": row, "Detail URL": UI.format(nid), "Open": is_open(row, now),
                         "Attachments": old.get("Attachments") if old.get("_fingerprint") == fp else None,
                         "Attachments Listed At": old.get("Attachments Listed At") if old.get("_fingerprint") == fp else None,
                         "Extract": lm, "Scraped At": old.get("Scraped At") or now_iso(), "_fingerprint": fp, "Listed": True}
    for k, r in existing.items():
        if k not in live:
            r["Listed"], r["Open"] = False, False  # dropped from the extract = archived/cancelled on SAM
    B.save(existing)
    todo = [r for r in existing.values() if r["Open"] and (a.refresh or r["Attachments"] is None)
            and (a.states == "all" or r["Row"]["PopState"] in TARGET_STATES)]
    todo.sort(key=lambda r: deadline(r["Row"]["ResponseDeadLine"]) or now)  # soonest-due first
    if a.limit:
        todo = todo[: a.limit]
    log.info("%d open notices; attachment pass for %d (%s)", sum(r["Open"] for r in existing.values()), len(todo), a.states)
    for n, r in enumerate(todo, 1):
        nid = r["Notice ID"]
        try:
            atts, raw = attachments(s, nid, a.no_attachments)
            r["Attachments"], r["Attachments Listed At"] = atts, now_iso()
            if raw:
                B.save_raw(nid, raw)
            log.info("[%d/%d] %s %-55s %s | files=%d", n, len(todo), nid[:12], r["Row"]["Title"][:55], r["Row"]["PopState"],
                     sum(1 for x in atts if x.get("local_path")))
        except Exception as e:
            log.error("%s failed: %s", nid, e)
        if n % 200 == 0:  # the records file is ~300 MB; checkpoint occasionally
            B.save(existing)
    B.save(existing)
    log.info("saved %d notices (%d open)", len(existing), sum(r["Open"] for r in existing.values()))


if __name__ == "__main__":
    main()
