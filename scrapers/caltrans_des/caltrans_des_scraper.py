#!/usr/bin/env python3
"""Caltrans DES (Division of Engineering Services) advertised construction contracts.

Source: https://ppmoe.dot.ca.gov/cc?id=cc_advertisement, a ServiceNow Service Portal. Every portal page's data is
public JSON at /api/now/sp/page?id=<page>, so this uses plain HTTP: no browser, no bot challenge, nothing to bypass.

Captured per contract (all public, no login): contract record (EA, work description, county-route-postmile,
advertise / bid-open dates, engineer's estimate, license callout, working days, DBE / DVBE / SBE goals, status),
every bid line item, bidder inquiries and responses, subcontractor opt-ins (firms wanting sub work: teaming leads),
primes advertising for help, plan holders, contract award, and the bid-document list (file names, tags, sizes).

The bid documents themselves (plans, specs, forms) need a free Caltrans contractor login ("Bid Book" -> csm_login).
They are listed with their size and marked login-required; nothing tries to get around that.

Output (bronze layer):
    data/bronze/caltrans_des/Caltrans_DES_Events_Data.json    one record per advertised contract
    data/bronze/caltrans_des/raw/<EA>.json                     full portal JSON for the contract (nothing lost)

    python caltrans_des_scraper.py               # all advertised contracts (resumable: skips unchanged)
    python caltrans_des_scraper.py --refresh     # re-fetch everything
    python caltrans_des_scraper.py --limit 3
"""
import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
BRONZE = PROJECT / "data" / "bronze" / "caltrans_des"
RAW = BRONZE / "raw"
OUT_JSON = BRONZE / "Caltrans_DES_Events_Data.json"
BASE = "https://ppmoe.dot.ca.gov"
API = f"{BASE}/api/now/sp/page"
BRONZE.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                    handlers=[logging.FileHandler(BRONZE / "caltrans_des_scraper.log", encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger("caltrans_des")

S = requests.Session()
S.headers.update({"User-Agent": "STRONGERbuilt-BidIntelligence/1.0 (public bid data)", "Accept": "application/json"})


def page(page_id, **params):
    """Return {widget name: widget data} for one portal page. Polite: retries with backoff, never hammers."""
    for attempt in range(4):
        try:
            r = S.get(API, params={"id": page_id, **params}, timeout=120)
            r.raise_for_status()
            res = r.json()["result"]
            return {w["widget"].get("name"): w["widget"].get("data") or {}
                    for c in res.get("containers", []) for row in c.get("rows", [])
                    for col in row.get("columns", []) for w in col.get("widgets", [])}, res
        except Exception as e:
            log.warning("%s %s attempt %d: %s", page_id, params, attempt + 1, e)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"could not load {page_id} {params}")


def fields(rec):
    """ServiceNow list record -> {label: display value} (display_field + secondary_fields)."""
    out = {}
    df = rec.get("display_field") or {}
    if df.get("label"):
        out[df["label"]] = df.get("display_value")
    for f in rec.get("secondary_fields") or []:
        if f.get("label"):
            out[f["label"]] = f.get("display_value")
    for k, v in rec.items():  # some widgets (plan holders) already use flat keys
        if isinstance(v, (str, int, float)) and k not in ("sys_id", "className", "editUrl") and k not in out:
            out[k] = v
    return out


def list_contracts():
    w, _ = page("cc_advertisement")
    ads = w["CC Advertisements"]
    contracts = {x["display_field"]["value"]: x for x in ads.get("list") or []}
    if ads.get("count", 0) >= ads.get("maxCount", 100):  # list capped: union over the 12 districts
        log.info("list at its cap (%s); querying per district", ads.get("maxCount"))
        for n in range(1, 13):
            wd, _ = page("cc_advertisement", district=str(n))
            contracts.update({x["display_field"]["value"]: x for x in wd["CC Advertisements"].get("list") or []})
    log.info("%d advertised contracts", len(contracts))
    return contracts


def documents(bid_docs, kind):
    out = []
    for group in bid_docs.get("allTags") or []:
        for tag in group:
            for rec in tag.get("records") or []:
                f = fields(rec)
                out.append({"file_name": f.get("File name"), "tag": tag.get("tag"), "kind": kind,
                            "size_bytes": int(f["Size bytes"]) if str(f.get("Size bytes") or "").isdigit() else None,
                            "sys_id": rec.get("sys_id"),
                            "download_url": f"{BASE}/sys_attachment.do?sys_id={rec.get('sys_id')}",
                            "local_path": None, "error": "Caltrans contractor login required to download"})
    return out


def scrape_contract(ea, list_rec):
    w, raw = page("cc_advertisement_details", ad_id=ea)
    (RAW / f"{ea}.json").write_text(json.dumps(raw, indent=1), encoding="utf-8")
    det = (w.get("CC Details", {}).get("list") or [{}])[0]
    get = lambda name: w.get(name) or {}
    return {
        "EA": ea,
        "Contract": {**fields(list_rec), **fields(det)},
        "Counts": {k: list_rec.get(k) for k in ("addendaCount", "inquiryCount", "optInCount", "primeCount")},
        "Licenses": list_rec.get("licenses"),
        "Bid Items": [fields(x) for x in get("CC Advertisements Detail - Bid Items").get("list") or []],
        "Bidder Inquiries": [fields(x) for x in get("CC Advertisements Detail - Bid Inquiry").get("list") or []],
        "Subcontractor Opt-Ins": [fields(x) for x in get("CC Advertisements Detail - Subcontractor").get("list") or []],
        "Primes Seeking Help": [fields(x) for x in get("CC Advertisements Detail - Primes").get("list") or []],
        "Plan Holders": [fields(x) for x in get("CC Advertisements Detail - Plan Holders").get("list") or []],
        "Award": {k: v for k, v in get("CC Advertisements Detail - Contract Awar").items() if not isinstance(v, (dict, list))},
        "Documents": documents(get("CC Advertisements Detail - Bid Docs"), "bid")
                     + documents(get("CC Advertisements Detail - Post Bid Docs"), "post_bid"),
        "Detail URL": f"{BASE}/cc?id=cc_advertisement_details&ad_id={ea}",
        "Raw Path": f"data/bronze/caltrans_des/raw/{ea}.json",
        "Scraped At": datetime.now().isoformat(timespec="seconds"),
    }


def fingerprint(list_rec):
    """Re-fetch a contract only when something visible on the list changed (addenda, inquiries, opt-ins ...)."""
    f = fields(list_rec)
    return json.dumps([f.get("Status"), f.get("Bid Open Date"), list_rec.get("addendaCount"), list_rec.get("inquiryCount"),
                       list_rec.get("optInCount"), list_rec.get("primeCount")])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    existing = {r["EA"]: r for r in json.loads(OUT_JSON.read_text(encoding="utf-8"))} if OUT_JSON.exists() else {}
    contracts = list_contracts()
    todo = list(contracts.items())[: a.limit] if a.limit else list(contracts.items())
    for n, (ea, rec) in enumerate(todo, 1):
        fp = fingerprint(rec)
        if ea in existing and existing[ea].get("_fingerprint") == fp and not a.refresh:
            continue
        try:
            c = scrape_contract(ea, rec)
            c["_fingerprint"] = fp
            existing[ea] = c
            log.info("[%d/%d] %s %-55s items=%d opt-ins=%d primes=%d docs=%d", n, len(todo), ea,
                     (c["Contract"].get("Work Description") or "")[:55], len(c["Bid Items"]),
                     len(c["Subcontractor Opt-Ins"]), len(c["Primes Seeking Help"]), len(c["Documents"]))
        except Exception as e:
            log.error("[%d/%d] %s failed: %s", n, len(todo), ea, e)
        time.sleep(1)  # be gentle with a state server
    for ea, r in existing.items():
        r["Listed"] = ea in contracts
    OUT_JSON.write_text(json.dumps(list(existing.values()), indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("saved %d contracts -> %s", len(existing), OUT_JSON.relative_to(PROJECT))


if __name__ == "__main__":
    main()
