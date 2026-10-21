#!/usr/bin/env python3
"""PIEE Solicitation Module (DoD): every open public notice, plus detail and attachments for notices not on SAM.gov.

Public, unauthenticated pages only (piee.eb.mil/sol/xhtml/unauth/...). The search is JSF/MyFaces with a client-side
DataTable, so a visible Chrome drives it; no stealth flags, it is plainly an automated browser.
PIEE publishes its notices to SAM.gov (2026-10-20: 330 of 373 open notices were on SAM), so by default only the
PIEE-only notices get their detail page and documents; --all does every notice.

Output (bronze): data/bronze/piee/PIEE_Data.json, attachments/<notice id>/, raw/<notice id>.json

    python piee_scraper.py [--all] [--limit 5]
"""
import argparse
import csv
import io
import re
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.statecommon import PROJECT, Bronze, fingerprint, now_iso  # noqa: E402

INDEX = "https://piee.eb.mil/sol/xhtml/unauth/index.xhtml"
COLS = ["Notice ID", "Response Date", "Notice Type", "Subject", "Description", "Restricted", "Status", "PIEE Posted Date",
        "PSC", "NAICS", "Contracting Office Name", "Set Aside", "Number of Updates"]
SAM_EXTRACT = PROJECT / "data" / "bronze" / "sam" / "raw" / "ContractOpportunitiesFullCSV.csv"
PAUSE = 3

B = Bronze("piee", "PIEE_Data.json")
log = B.log


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def sam_solicitations():
    if not SAM_EXTRACT.exists():
        return set()
    raw = SAM_EXTRACT.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    return {norm(r["Sol#"]) for r in csv.DictReader(io.StringIO(text)) if r.get("Sol#")}


def browser():
    o = webdriver.ChromeOptions()
    o.add_argument("--window-size=1400,950")
    o.add_experimental_option("prefs", {"download.default_directory": str(B.att / "_incoming"),
                                        "download.prompt_for_download": False, "plugins.always_open_pdf_externally": True})
    (B.att / "_incoming").mkdir(parents=True, exist_ok=True)
    return webdriver.Chrome(options=o)


def search_open(d):
    d.get(INDEX)
    time.sleep(PAUSE)
    d.execute_script("myfaces.oam.submitForm('form','searchSolicitation');")
    time.sleep(PAUSE + 2)
    Select(d.find_element(By.NAME, "status")).select_by_value("O")
    next(e for e in d.find_elements(By.ID, "search") if e.is_displayed()).click()
    for _ in range(60):
        time.sleep(1)
        if d.execute_script("return !!(window.jQuery && jQuery.fn.dataTable && jQuery.fn.dataTable.isDataTable('#DataTables_Table_0'))"):
            break
    rows = d.execute_script("return jQuery('#DataTables_Table_0').DataTable().rows().nodes().toArray()"
                            ".map(tr => [...tr.cells].map(c => c.innerText.trim()))")
    return [dict(zip(COLS, r)) for r in rows if len(r) == len(COLS)]


def detail_fields(d):
    """Label -> value from the read-only notice form (values sit in inputs/selects/spans next to the labels)."""
    js = r"""
    const out = {};
    const val = c => {
      const el = c.querySelector('select,textarea,input:not([type=hidden]):not([type=radio]):not([type=checkbox])');
      if (el && el.tagName === 'SELECT') return [...el.selectedOptions].map(o => o.text.trim()).join('; ');
      if (el) return (el.value || '').trim();
      const radio = c.querySelector('input[type=radio]:checked,input[type=checkbox]:checked');
      if (radio) { const l = c.querySelector('label[for="' + radio.id + '"]'); return l ? l.innerText.trim() : radio.value; }
      return c.innerText.trim();
    };
    // the notice form is Bootstrap rows: <div class=row><div class=col><label class=control-label>Label</label></div><div class=col>value</div></div>
    document.querySelectorAll('label.control-label').forEach(l => {
      const k = l.innerText.trim().replace(/\s+/g, ' ');
      const col = l.closest('div[class*=col-]');
      const sib = col && col.nextElementSibling;
      if (!k || !sib || k in out) return;
      const v = val(sib);
      if (v) out[k] = v;
    });
    const atts = [...document.querySelectorAll('table')].filter(t => /File\/URL/.test(t.innerText))
      .flatMap(t => [...t.querySelectorAll('tbody tr')].map(tr => [...tr.cells].map(c => c.innerText.trim())));
    return [out, atts, document.body.innerText];
    """
    return d.execute_script(js)


def download_attachments(d, nid):
    """Click each public attachment link; Chrome saves it to _incoming, then it is moved to attachments/<nid>/."""
    inc = B.att / "_incoming"
    dest = B.att / re.sub(r"[^\w.\-]+", "_", nid)
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    links = [a for a in d.find_elements(By.TAG_NAME, "a")
             if re.search(r"\.(pdf|docx?|xlsx?|zip|txt|pptx?|jpg|png)$", (a.text or "").strip(), re.I) and a.is_displayed()]
    for a in links:
        name = a.text.strip()
        got = None
        for attempt in range(2):  # PIEE shows a "Downloading, please wait" popup; a click while it is open is dropped
            d.execute_script("if (window.hideDownloadPopup) hideDownloadPopup();")
            before = set(inc.iterdir())
            try:
                d.execute_script("arguments[0].click();", a)  # a fixed page header overlaps the links; same handler as a click
            except Exception as e:
                log.warning("%s: %s", name, str(e)[:120])
                break
            for _ in range(60 if attempt == 0 else 120):
                time.sleep(1)
                new = [p for p in set(inc.iterdir()) - before if not p.name.endswith((".crdownload", ".tmp"))]
                if new:
                    got = new[0]
                    break
            if got:
                break
        if got:
            target = dest / got.name
            got.replace(target)
            out.append({"file_name": target.name, "download_url": INDEX, "local_path": target.relative_to(PROJECT).as_posix(),
                        "size_bytes": target.stat().st_size, "error": None})
        else:
            out.append({"file_name": name, "download_url": INDEX, "local_path": None, "size_bytes": None, "error": "download did not complete"})
        time.sleep(PAUSE)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="detail + documents for every notice, not only PIEE-only ones")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    on_sam = sam_solicitations()
    existing = B.load("Notice ID")
    d = browser()
    try:
        rows = search_open(d)
        log.info("PIEE open notices: %d", len(rows))
        live = set()
        for r in rows:
            nid = r["Notice ID"]
            live.add(nid)
            old = existing.get(nid, {})
            fp = fingerprint(r)
            existing[nid] = {**old, "Notice ID": nid, "List Row": r, "On SAM": norm(nid) in on_sam, "Listed": True,
                             "Scraped At": old.get("Scraped At") or now_iso(), "_fingerprint": fp,
                             "Detail": old.get("Detail") if old.get("_fingerprint") == fp else None}
        # new notices, plus any whose documents did not all download last time
        todo = [k for k in live if (a.all or not existing[k]["On SAM"])
                and (not existing[k].get("Detail") or any(x.get("error") for x in existing[k].get("Attachments") or []))]
        if a.limit:
            todo = todo[: a.limit]
        log.info("detail pass for %d notices (%s)", len(todo), "all" if a.all else "PIEE-only")
        for n, nid in enumerate(todo, 1):
            try:
                search_open(d)
                d.execute_script("jQuery('#DataTables_Table_0').DataTable().search(arguments[0]).draw()", nid)
                time.sleep(1)
                link = next(x for x in d.find_elements(By.CSS_SELECTOR, "#DataTables_Table_0 tbody tr td a") if x.text.strip() == nid)
                link.click()
                time.sleep(PAUSE + 3)
                fields, att_rows, text = detail_fields(d)
                atts = download_attachments(d, nid)
                existing[nid]["Detail"] = fields
                existing[nid]["Attachment Rows"] = att_rows
                existing[nid]["Attachments"] = atts
                existing[nid]["Detail URL"] = INDEX
                existing[nid]["Raw Path"] = B.save_raw(nid, {"fields": fields, "attachments": att_rows, "text": text})
                log.info("[%d/%d] %s %-50s files=%d/%d", n, len(todo), nid, existing[nid]["List Row"]["Subject"][:50],
                         sum(1 for x in atts if x.get("local_path")), len(att_rows))
            except Exception as e:
                log.error("%s failed: %s", nid, str(e)[:200])
            B.save(existing)
        for k, r in existing.items():
            r["Listed"] = k in live
        B.save(existing)
        log.info("saved %d notices (%d PIEE-only)", len(existing), sum(1 for r in existing.values() if not r["On SAM"]))
    finally:
        d.quit()


if __name__ == "__main__":
    main()
