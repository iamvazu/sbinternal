#!/usr/bin/env python3
"""Florida (MyFloridaMarketPlace vendor portal, https://vendor.myfloridamarketplace.com): every OPEN advertisement,
its full detail (description, UNSPSC commodity codes, contacts, program indicators) and its public documents.
Plain HTTP against the portal's own public JSON; no browser, no bot challenge.

Listing: POST /mfmp/pub/search/bids  {"status": ["OPEN"], "pageSize": 100, "page": n}
Detail:  GET  /mfmp/pub/search/bids/detail?id=<advertisementId>
Files:   GET  /mfmp/bids/detail/attachment/download?attachmentId=<id>

Output (bronze): data/bronze/mfmp/MFMP_Data.json, attachments/<AD-id>/, raw/<AD-id>.json

    python mfmp_scraper.py [--limit 5] [--refresh] [--no-attachments]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.statecommon import Bronze, call, download, fingerprint, now_iso, session  # noqa: E402

API = "https://vendor.myfloridamarketplace.com/mfmp"
UI = "https://vendor.myfloridamarketplace.com/search/bids/detail/"
B = Bronze("mfmp", "MFMP_Data.json")
log = B.log
SEARCH = {"pageSize": 100, "type": [], "status": ["OPEN"], "agency": [], "adNumber": "", "agencyAdvertisementNumber": "",
          "title": "", "publishedDate": "", "openDate": "", "endDate": "", "commodityCodes": [], "intendsToParticipate": "", "assignee": ""}


def listing(s):
    total = int(call(s, "POST", f"{API}/pub/search/bids/count", log, json=SEARCH).text or 0)
    ads, page = {}, 1
    while len(ads) < total:
        batch = call(s, "POST", f"{API}/pub/search/bids", log, json={**SEARCH, "page": page}).json()
        if not batch:
            break
        ads.update({str(a["advertisementId"]): a for a in batch})
        page += 1
    log.info("listing: %d of %d open advertisements", len(ads), total)
    return list(ads.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-attachments", action="store_true")
    a = ap.parse_args()
    s = session()
    s.headers["Accept"] = "application/json"
    existing = B.load("Ad ID")
    ads = listing(s)
    live = set()
    for n, ad in enumerate(ads[: a.limit] if a.limit else ads, 1):
        key = ad.get("uniqueName") or f"AD-{ad['advertisementId']}"
        live.add(key)
        fp = fingerprint(ad.get("version"), ad.get("closeDate"), ad.get("status"))
        if key in existing and existing[key].get("_fingerprint") == fp and not a.refresh:
            continue
        try:
            det = call(s, "GET", f"{API}/pub/search/bids/detail", log, params={"id": ad["advertisementId"]}).json()
            atts = []
            for doc in det.get("docs") or []:
                url = f"{API}/bids/detail/attachment/download?attachmentId={doc['attachmentId']}"
                if a.no_attachments:
                    atts.append({"file_name": doc.get("fileName"), "download_url": url, "local_path": None, "size_bytes": None, "error": "not downloaded (--no-attachments)"})
                else:
                    rec = download(s, B, key, doc.get("fileName") or f"attachment_{doc['attachmentId']}", url, log=log)
                    rec["description"] = doc.get("description")
                    atts.append(rec)
            existing[key] = {"Ad ID": key, "List": ad, "Detail": det, "Attachments": atts,
                             "Detail URL": f"{UI}{ad['advertisementId']}",
                             "Raw Path": B.save_raw(key, {"list": ad, "detail": det}),
                             "Scraped At": now_iso(), "_fingerprint": fp}
            log.info("[%d/%d] %s %-55s %s | codes=%d files=%d", n, len(ads), key, (det.get("title") or "")[:55],
                     (det.get("agency") or "")[:30], len(det.get("commodityCodes") or []), len(atts))
        except Exception as e:
            log.error("%s failed: %s", key, e)
        if n % 20 == 0:
            B.save(existing)
    for k, r in existing.items():
        r["Listed"] = k in live
    B.save(existing)
    log.info("saved %d records", len(existing))


if __name__ == "__main__":
    main()
