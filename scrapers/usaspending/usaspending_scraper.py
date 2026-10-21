#!/usr/bin/env python3
"""USAspending.gov: federal contract award history (FPDS prime transactions) by place of performance and fiscal year.

Official open API, no key: POST /api/v2/bulk_download/awards/ -> poll /api/v2/download/status -> download the zip
from files.usaspending.gov. One request per (state, fiscal year): contracts (A-D) and IDVs. Requests run one at a time
and the server builds each file (minutes for a big state), so this is gentle on the service.

Then build_reference() turns the zips into data/reference/usaspending/awards.db (SQLite):
  transactions  one row per contract action (skinny: ~60 columns that matter for capture)
  awards        one row per award (contract_award_unique_key): totals, dates, recipient, office, NAICS, PSC, set-aside,
                competition, offers, solicitation id. Indexed for "who won what, where, for how much".
The app links SAM.gov notices to their history through solicitation_identifier (= SAM "Sol#") and NAICS + office.

Output (bronze): data/bronze/usaspending/raw/<STATE>_FY<year>_<kind>.zip  (+ manifest.json)

    python usaspending_scraper.py                              # 7 states, FY2021 -> current FY
    python usaspending_scraper.py --states all --from-fy 2008  # everything (run on the Jetson drive)
    python usaspending_scraper.py --build-only                 # rebuild awards.db from the zips already downloaded
"""
import argparse
import csv
import io
import json
import sqlite3
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.statecommon import PROJECT, Bronze, now_iso, session  # noqa: E402

API = "https://api.usaspending.gov/api/v2"
TARGET_STATES = ["CA", "OR", "FL", "TX", "GA", "VA", "PA"]
ALL_STATES = ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND "
              "OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR GU VI AS MP").split()
KINDS = {"contracts": ["A", "B", "C", "D"], "idvs": ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"]}
REFERENCE = PROJECT / "data" / "reference" / "usaspending"
DB = REFERENCE / "awards.db"

REQUEST_GAP = 60      # seconds between bulk-download requests: the service throttles bursts (learned 2026-10-20)
BACKOFF = (120, 240, 480)
MAX_CONSECUTIVE_FAILURES = 3

B = Bronze("usaspending", "manifest.json")
log = B.log

COLS = [  # the capture-relevant subset of the 297 FPDS columns
    "contract_award_unique_key", "award_id_piid", "modification_number", "parent_award_id_piid", "parent_award_agency_name",
    "federal_action_obligation", "current_total_value_of_award", "potential_total_value_of_award", "action_date",
    "action_date_fiscal_year", "period_of_performance_start_date", "period_of_performance_current_end_date",
    "period_of_performance_potential_end_date", "solicitation_date", "solicitation_identifier",
    "awarding_agency_name", "awarding_sub_agency_name", "awarding_office_code", "awarding_office_name",
    "funding_agency_name", "funding_office_name", "recipient_uei", "recipient_name", "recipient_parent_name",
    "recipient_city_name", "recipient_state_code", "primary_place_of_performance_city_name",
    "primary_place_of_performance_county_name", "primary_place_of_performance_state_code",
    "primary_place_of_performance_zip_4", "award_type", "idv_type", "type_of_contract_pricing",
    "transaction_description", "prime_award_base_transaction_description", "product_or_service_code",
    "product_or_service_code_description", "naics_code", "naics_description", "extent_competed",
    "solicitation_procedures", "type_of_set_aside", "type_of_set_aside_code", "number_of_offers_received",
    "contracting_officers_determination_of_business_size", "service_disabled_veteran_owned_business",
    "veteran_owned_business", "women_owned_small_business", "economically_disadvantaged_women_owned_small_business",
    "c8a_program_participant", "historically_underutilized_business_zone_hubzone_firm", "small_disadvantaged_business",
    "usaspending_permalink", "last_modified_date",
]


def current_fy():
    t = date.today()
    return t.year + 1 if t.month >= 10 else t.year


def polite(s, method, url, **kw):
    """One request with long back-off on dropped connections / 429 / 5xx (USAspending throttles by closing sockets)."""
    kw.setdefault("timeout", 120)
    for wait in (*BACKOFF, None):
        retry_after = None
        try:
            r = s.request(method, url, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                retry_after = r.headers.get("Retry-After")
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except Exception as e:
            if wait is None:
                raise RuntimeError(f"USAspending unavailable after back-off: {e}")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else wait
            log.warning("%s %s: %s -> waiting %ds", method, url.split("?")[0].rsplit("/", 2)[-2], e, wait)
            time.sleep(wait)


# a state-year too big for the server to build (VA FY2026, 2026-10-20) is fetched as two half-years
HALVES = {"": ("{p}-10-01", "{y}-09-30"), "_H1": ("{p}-10-01", "{y}-03-31"), "_H2": ("{y}-04-01", "{y}-09-30")}


def request_file(s, state, fy, kind, half=""):
    start, end = (x.format(p=fy - 1, y=fy) for x in HALVES[half])
    body = {"filters": {"prime_award_types": KINDS[kind], "date_type": "action_date",
                        "date_range": {"start_date": start, "end_date": end},
                        "place_of_performance_locations": [{"country": "USA", "state": state}]},
            "file_format": "csv"}
    r = polite(s, "POST", f"{API}/bulk_download/awards/", json=body)
    fn = r.json()["file_name"]
    for i in range(240):  # up to ~40 min for the largest state-years
        st = polite(s, "GET", f"{API}/download/status", params={"file_name": fn}).json()
        if st.get("status") == "finished":
            return st
        if st.get("status") == "failed":
            raise RuntimeError(f"USAspending failed to build {fn}: {st.get('message')}")
        time.sleep(10 if i < 30 else 30)
    raise RuntimeError(f"timed out waiting for {fn}")


def fetch(s, states, fys, kinds, manifest):
    failures = 0
    for fy in fys:
        for state in states:
            for kind in kinds:
                for half in ("", "_H1", "_H2"):
                    base = f"{state}_FY{fy}_{kind}"
                    if half and not (manifest.get(base, {}).get("too_big")):
                        continue  # halves only for a year the server could not build whole
                    key = base + half
                    dest = B.raw / f"{key}.zip"
                    done = manifest.get(key)
                    # closed fiscal years are final; the current one is refreshed when older than 7 days
                    fresh = done and not done.get("too_big") and dest.exists() and (
                        fy < current_fy() or (time.time() - dest.stat().st_mtime) < 7 * 86400)
                    if fresh or (done and done.get("too_big")):
                        continue
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        log.error("stopping: %d requests in a row failed; USAspending is throttling or down. Re-run later "
                                  "(finished files are kept and skipped).", failures)
                        return
                    try:
                        st = request_file(s, state, fy, kind, half)
                        r = polite(s, "GET", st["file_url"], timeout=1800)
                        part = dest.with_suffix(".part")  # atomic: --build-only may run while this downloads
                        part.write_bytes(r.content)
                        part.replace(dest)
                        manifest[key] = {"state": state, "fy": fy, "kind": kind, "half": half or None, "rows": st.get("total_rows"),
                                         "file": dest.name, "bytes": dest.stat().st_size, "downloaded": now_iso()}
                        log.info("%s: %s rows, %.1f MB", key, st.get("total_rows"), dest.stat().st_size / 2**20)
                        failures = 0
                    except Exception as e:
                        if "failed to build" in str(e) and not half:
                            manifest[key] = {"state": state, "fy": fy, "kind": kind, "too_big": True, "error": str(e)[:200]}
                            log.warning("%s: too big for one file, fetching it as two half-years", key)
                        else:
                            failures += 1
                            log.error("%s failed: %s", key, e)
                    B.records_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
                    time.sleep(REQUEST_GAP)


def num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def build_reference():
    """zips -> awards.db. Rebuilt from scratch each time (it is derived data)."""
    REFERENCE.mkdir(parents=True, exist_ok=True)
    tmp = DB.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    c = sqlite3.connect(tmp)
    c.execute(f"CREATE TABLE transactions ({', '.join(f'{k} TEXT' for k in COLS)}, kind TEXT)")
    n = 0
    for z in sorted(B.raw.glob("*.zip")):
        kind = "idvs" if "_idvs" in z.stem else "contracts"  # stems: CA_FY2026_contracts, VA_FY2026_contracts_H1
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                if not name.endswith(".csv"):
                    continue
                rd = csv.DictReader(io.TextIOWrapper(zf.open(name), encoding="utf-8", errors="replace"))
                batch = []
                for row in rd:
                    batch.append([row.get(k) or None for k in COLS] + [kind])
                    if len(batch) >= 20000:
                        c.executemany(f"INSERT INTO transactions VALUES ({','.join('?' * (len(COLS) + 1))})", batch)
                        n += len(batch)
                        batch = []
                if batch:
                    c.executemany(f"INSERT INTO transactions VALUES ({','.join('?' * (len(COLS) + 1))})", batch)
                    n += len(batch)
        log.info("loaded %s (%d transactions so far)", z.name, n)
    # one row per award; descriptive fields from the latest action, money summed over every action
    c.executescript("""
    CREATE TABLE awards AS
    SELECT t.contract_award_unique_key AS award_key, t.award_id_piid AS piid, t.parent_award_id_piid AS parent_piid,
           t.kind, t.award_type, t.idv_type, upper(replace(replace(t.solicitation_identifier,'-',''),' ','')) AS sol_key,
           t.solicitation_identifier AS solicitation_id, t.awarding_agency_name AS agency,
           t.awarding_sub_agency_name AS sub_agency, t.awarding_office_code AS office_code, t.awarding_office_name AS office,
           t.recipient_uei AS uei, t.recipient_name AS recipient, t.recipient_parent_name AS recipient_parent,
           t.recipient_state_code AS recipient_state, t.primary_place_of_performance_city_name AS pop_city,
           t.primary_place_of_performance_county_name AS pop_county, t.primary_place_of_performance_state_code AS pop_state,
           t.naics_code AS naics, t.naics_description, t.product_or_service_code AS psc, t.product_or_service_code_description AS psc_description,
           t.type_of_set_aside AS set_aside, t.extent_competed, t.number_of_offers_received AS offers,
           t.type_of_contract_pricing AS pricing, t.contracting_officers_determination_of_business_size AS business_size,
           t.service_disabled_veteran_owned_business AS sdvosb, t.women_owned_small_business AS wosb,
           t.c8a_program_participant AS c8a, t.historically_underutilized_business_zone_hubzone_firm AS hubzone,
           t.prime_award_base_transaction_description AS description, t.period_of_performance_start_date AS start_date,
           t.period_of_performance_current_end_date AS current_end, t.period_of_performance_potential_end_date AS potential_end,
           t.current_total_value_of_award AS current_value, t.potential_total_value_of_award AS potential_value,
           s.obligated, s.actions, s.first_action, s.last_action, t.usaspending_permalink AS permalink
    FROM transactions t
    JOIN (SELECT contract_award_unique_key k, SUM(CAST(federal_action_obligation AS REAL)) obligated, COUNT(*) actions,
                 MIN(action_date) first_action, MAX(action_date) last_action,
                 MAX(action_date || '|' || COALESCE(modification_number,'')) latest
          FROM transactions GROUP BY contract_award_unique_key) s
      ON s.k = t.contract_award_unique_key AND t.action_date || '|' || COALESCE(t.modification_number,'') = s.latest
    GROUP BY t.contract_award_unique_key;
    CREATE INDEX ix_awards_sol ON awards(sol_key);
    CREATE INDEX ix_awards_naics ON awards(naics, pop_state);
    CREATE INDEX ix_awards_office ON awards(office_code, naics);
    CREATE INDEX ix_awards_psc ON awards(psc);
    CREATE INDEX ix_awards_uei ON awards(uei);
    CREATE INDEX ix_awards_piid ON awards(piid);
    CREATE INDEX ix_tx_key ON transactions(contract_award_unique_key);
    """)
    c.commit()
    awards = c.execute("SELECT COUNT(*) FROM awards").fetchone()[0]
    c.close()
    tmp.replace(DB)
    log.info("awards.db: %d transactions, %d awards -> %s", n, awards, DB)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", default="target", help="'target' (CA OR FL TX GA VA PA), 'all', or a list like CA,NV")
    ap.add_argument("--from-fy", type=int, default=2021)
    ap.add_argument("--to-fy", type=int, default=current_fy())
    # contracts (A-D) include every task/delivery order placed against an IDV (parent_award_id_piid). IDV records
    # themselves carry no place of performance, so a state-filtered IDV request is always empty (verified 2026-10-20):
    # ask for idvs only with an agency filter, not a state one.
    ap.add_argument("--kinds", default="contracts")
    ap.add_argument("--build-only", action="store_true")
    a = ap.parse_args()
    states = TARGET_STATES if a.states == "target" else ALL_STATES if a.states == "all" else a.states.upper().split(",")
    if not a.build_only:
        manifest = json.loads(B.records_path.read_text(encoding="utf-8")) if B.records_path.exists() else {}
        # newest years first: the most useful history lands first
        fetch(session(), states, list(range(a.to_fy, a.from_fy - 1, -1)), a.kinds.split(","), manifest)
    build_reference()


if __name__ == "__main__":
    main()
