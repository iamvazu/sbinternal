#!/usr/bin/env python3
"""Texas (Electronic State Business Daily on Texas SmartBuy, https://www.txsmartbuy.gov/esbd): every open
solicitation (status Posted / Addendum Posted), its full detail and its public attachments.
Plain HTTP against the site's own public JSON services; sign-in is not required.

Listing: POST .../ESBD.Service.ss  {"lines":[],"page":n,"urlRoot":"esbd","status":"1"}   (24 per page)
Detail:  GET  .../ESBD.Details.Service.ss?identification=<solicitation id>&urlRoot=esbd
Files:   attachment fileURL (/core/media/media.nl?...): public

The ESBD carries state agencies and Texas SmartBuy members (school districts, cities, counties, universities);
the agency number tells them apart (members are prefixed "S", state agencies are numeric).

Output (bronze): data/bronze/tx_esbd/TX_ESBD_Data.json, attachments/<id>/, raw/<id>.json

    python tx_esbd_scraper.py [--limit 5] [--refresh] [--no-attachments]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.statecommon import Bronze, call, download, fingerprint, now_iso, safe_name, session  # noqa: E402

HOST = "https://www.txsmartbuy.gov"
SVC = HOST + "/app/extensions/CPA/CPAMain/1.0.0/services/"
B = Bronze("tx_esbd", "TX_ESBD_Data.json")
log = B.log


def listing(s):
    lines, page = {}, 1
    while True:
        r = call(s, "POST", SVC + "ESBD.Service.ss?c=852252&n=2", log,
                 data=json.dumps({"lines": [], "page": page, "urlRoot": "esbd", "status": "1"}),
                 headers={"Content-Type": "application/json"}).json()
        batch = r.get("lines") or []
        lines.update({l["solicitationId"]: l for l in batch})
        total = r.get("totalRecordsFound") or 0
        if not batch or len(lines) >= total or page * (r.get("recordsPerPage") or 24) >= total:
            break
        page += 1
    log.info("listing: %d of %s open solicitations", len(lines), total)
    return list(lines.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-attachments", action="store_true")
    a = ap.parse_args()
    s = session()
    existing = B.load("Solicitation ID")
    rows = listing(s)
    live = set()
    for n, row in enumerate(rows[: a.limit] if a.limit else rows, 1):
        sid = row["solicitationId"]
        live.add(sid)
        fp = fingerprint(row.get("lastModified"), row.get("statusName"), row.get("responseDue"))
        if sid in existing and existing[sid].get("_fingerprint") == fp and not a.refresh:
            continue
        try:
            det = call(s, "GET", SVC + "ESBD.Details.Service.ss", log,
                       params={"c": "852252", "email": "", "identification": sid, "n": "2", "optOutKey": "", "urlRoot": "esbd"}).json()
            atts = []
            for f in det.get("attachments") or []:
                url = HOST + f["fileURL"] if f.get("fileURL", "").startswith("/") else f.get("fileURL")
                if a.no_attachments or not url:
                    atts.append({"file_name": f.get("fileName"), "download_url": url, "local_path": None, "size_bytes": None, "error": "not downloaded"})
                    continue
                rec = download(s, B, sid, f.get("fileName") or f"file_{f.get('fileId')}", url, log=log)
                rec["description"] = f.get("fileDescription")
                atts.append(rec)
            det.pop("highwayDistrictList", None)  # static lookup tables, not record data
            det.pop("zonesList", None)
            existing[sid] = {"Solicitation ID": sid, "List": row, "Detail": det, "Attachments": atts,
                             "Detail URL": f"{HOST}/esbd/{sid}", "Raw Path": B.save_raw(safe_name(sid), {"list": row, "detail": det}),
                             "Scraped At": now_iso(), "_fingerprint": fp}
            log.info("[%d/%d] %s %-50s %s | value=%s files=%d", n, len(rows), sid[:20], (det.get("title") or "")[:50],
                     (det.get("agencyName") or "")[:28], det.get("value"), len(atts))
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
