import re
import json
import time
import os
import sys
import base64
import argparse
from typing import List, Dict, Optional, Tuple
import bs4

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementNotInteractableException,
    StaleElementReferenceException,
)

BANNER_SNIPPET = (
    "All businesses are encouraged to provide voluntary diversity data information in their CaleProcure profiles."
)

def build_driver(headless: bool = False, attachments_dir: str = "attachments") -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    import tempfile
    profile_dir = tempfile.mkdtemp(prefix="caleprocure_chrome_")
    opts.add_argument(f"--user-data-dir={profile_dir}")
    opts.add_argument("--disable-renderer-backgrounding")
    opts.add_argument("--disable-backgrounding-occluded-windows")
    abs_dl = os.path.abspath(attachments_dir)
    os.makedirs(abs_dl, exist_ok=True)
    prefs = {
        "download.default_directory": abs_dl,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "plugins.always_open_pdf_externally": True,
    }
    opts.add_experimental_option("prefs", prefs)

    service = None
    possible_paths = [
        "/home/iamvazu/.wdm/drivers/chromedriver/linux-arm64/154.0.8037.92/chromedriver-linux-arm64/chromedriver",
        "/usr/bin/chromedriver",
        "/usr/local/bin/chromedriver",
    ]
    for p in possible_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            from selenium.webdriver.chrome.service import Service
            service = Service(executable_path=p)
            break
    if not service:
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from selenium.webdriver.chrome.service import Service
            service = Service(executable_path=ChromeDriverManager().install())
        except Exception:
            service = None

    if service:
        driver = webdriver.Chrome(service=service, options=opts)
    else:
        driver = webdriver.Chrome(options=opts)

    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except Exception:
        pass
    driver.set_page_load_timeout(120)
    return driver

def ensure_driver(driver, headless: bool = False, attachments_dir: str = "attachments") -> webdriver.Chrome:
    try:
        driver.current_url
        return driver
    except Exception:
        print("  [INFO] Browser disconnected or closed, restarting fresh Chrome session...")
        try:
            driver.quit()
        except Exception:
            pass
        d = build_driver(headless=headless, attachments_dir=attachments_dir)
        try:
            d.get("https://caleprocure.ca.gov/pages/Events-BS3/event-search.aspx")
            time.sleep(3)
        except Exception:
            pass
        return d

def clean_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value
    if BANNER_SNIPPET in v:
        v = v.replace(BANNER_SNIPPET, "")
    v = re.sub(r"\s+", " ", v).strip()
    return v or None

def clean_lines(text: str) -> List[str]:
    if not text:
        return []
    if BANNER_SNIPPET in text:
        text = text.replace(BANNER_SNIPPET, "")
    return [l.strip() for l in text.splitlines() if l.strip()]

def get_text(el) -> str:
    try:
        return el.get_attribute("innerText") or el.text or ""
    except Exception:
        return ""

def parse_excel_export(excel_path: str) -> List[Dict[str, str]]:
    with open(excel_path, "r", encoding="utf-8", errors="ignore") as f:
        soup = bs4.BeautifulSoup(f.read(), "html.parser")

    headers = [th.get_text(strip=True) for th in soup.find_all("th")]
    events = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if tds and len(tds) >= 4:
            row_dict = dict(zip(headers, tds))
            if row_dict.get("Event ID"):
                events.append(row_dict)
    return events

def download_attachment_file(driver, url: str, out_path: str) -> bool:
    fetch_js = """
    var url = arguments[0];
    var callback = arguments[1];
    fetch(url)
        .then(res => {
            if (!res.ok) throw new Error("HTTP " + res.status);
            return res.blob();
        })
        .then(blob => {
            var reader = new FileReader();
            reader.onloadend = function() {
                callback(reader.result);
            };
            reader.readAsDataURL(blob);
        })
        .catch(err => callback("ERROR:" + err));
    """
    try:
        driver.set_script_timeout(120)
        data_url = driver.execute_async_script(fetch_js, url)
        if data_url and data_url.startswith("data:"):
            _, b64 = data_url.split(",", 1)
            raw = base64.b64decode(b64)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(raw)
            return True
        else:
            print(f"      [WARN] fetch returned non-data: {data_url[:60] if data_url else None}")
            return False
    except Exception as e:
        print(f"      [WARN] in-browser fetch error: {e}")
        return False

def extract_details(driver, fallback_meta: Dict[str, str]) -> Dict[str, str]:
    details: Dict[str, str] = {}
    label_selectors = {
        "Event ID": ["//*[@data-if-label='eventId']", "//*[@id='RESP_AUC_H0B_WK_AUC_ID_BUS_UNIT']"],
        "Dept": ["//*[@data-if-label='dept']", "//*[@id='BUS_UNIT_TBL_FS_DESCR']"],
        "Format/Type": ["//*[@data-if-label='format1']"],
        "Event Version": ["//*[@data-if-label='eventVersion']", "//*[contains(@id,'AUC_VERSION')]"],
        "Published Date": ["//*[@data-if-label='eventStartDate']", "//*[contains(@id,'AUC_DTTM_START')]"],
        "Event End Date": ["//*[@data-if-label='eventEndDate']", "//*[contains(@id,'AUC_DTTM_FINISH')]"],
    }
    for key, xpaths in label_selectors.items():
        for xp in xpaths:
            try:
                el = driver.find_element(By.XPATH, xp)
                val = clean_text(get_text(el))
                if val:
                    if key == "Format/Type":
                        try:
                            f2 = driver.find_element(By.XPATH, "//*[@data-if-label='format2']")
                            v2 = clean_text(get_text(f2))
                            if v2:
                                val = f"{val} / {v2}"
                        except Exception:
                            pass
                    details[key] = val
                    break
            except NoSuchElementException:
                continue

    # Guaranteed non-null Event ID and department from Excel fallback
    if not details.get("Event ID"):
        details["Event ID"] = fallback_meta.get("Event ID", "")
    if not details.get("Dept") and fallback_meta.get("Department Name"):
        details["Dept"] = fallback_meta.get("Department Name", "")
    if not details.get("Event End Date") and fallback_meta.get("End Date"):
        details["Event End Date"] = fallback_meta.get("End Date", "")
    if not details.get("Format/Type") and (fallback_meta.get("Format") or fallback_meta.get("Type")):
        fmt = fallback_meta.get("Format", "")
        typ = fallback_meta.get("Type", "")
        details["Format/Type"] = f"{fmt} / {typ}".strip(" /")

    return details

def extract_contact(driver, fallback_meta: Dict[str, str]) -> Dict[str, str]:
    contact = {}
    try:
        c_name = driver.find_element(By.XPATH, "//*[@data-if-label='contactName']")
        val = clean_text(get_text(c_name))
        if val and not (val.startswith("[") and val.endswith("]")):
            contact["Contact Name"] = val
    except Exception:
        pass
    try:
        phone = driver.find_element(By.XPATH, "//*[@data-if-label='phoneText']")
        val = clean_text(get_text(phone))
        if val and not (val.startswith("[") and val.endswith("]")):
            contact["Phone"] = val
    except Exception:
        pass
    try:
        email = driver.find_element(By.XPATH, "//*[@data-if-label='emffffailText' or @data-if-label='emailText'] | //a[starts-with(@href,'mailto:')]")
        val = clean_text(get_text(email))
        if not val and email.tag_name == "a":
            href = email.get_attribute("href") or ""
            val = href.replace("mailto:", "").split("?", 1)[0].strip()
        if val and not (val.startswith("[") and val.endswith("]")):
            contact["Email"] = val
    except Exception:
        pass

    # Fallback to buyer in Excel
    if not contact.get("Contact Name") and fallback_meta.get("Buyer Name"):
        contact["Contact Name"] = fallback_meta.get("Buyer Name")
    if not contact.get("Email") and fallback_meta.get("Buyer Email"):
        contact["Email"] = fallback_meta.get("Buyer Email")

    return contact

def extract_prebid(driver) -> Dict[str, str]:
    pre = {}
    try:
        heading = driver.find_element(By.XPATH, "//*[contains(normalize-space(),'Pre Bid Conference')]")
        parent = heading.find_element(By.XPATH, "./ancestor::*[contains(@class,'col') or contains(@class,'card') or contains(@class,'panel')][1]")
        lines = clean_lines(get_text(parent))
        for line in lines:
            low = line.lower()
            if low.startswith("mandatory") and ":" in line:
                pre["Mandatory"] = line.split(":", 1)[1].strip()
            elif low.startswith("date") and ":" in line:
                pre["Date"] = line.split(":", 1)[1].strip()
            elif low.startswith("time") and ":" in line:
                pre["Time"] = line.split(":", 1)[1].strip()
            elif low.startswith("location") and ":" in line:
                pre["Location"] = line.split(":", 1)[1].strip()
            elif low.startswith("comments") and ":" in line:
                pre["Comments"] = line.split(":", 1)[1].strip()
    except Exception:
        pass
    return pre

def extract_description(driver, fallback_meta: Dict[str, str]) -> Optional[str]:
    try:
        el = driver.find_element(By.XPATH, "//*[@data-if-label='descriptiondetails'] | //*[@id='AUC_HDR_DESCRLONG']")
        txt = clean_text(get_text(el))
        if txt and not (txt.startswith("[") and txt.endswith("]")):
            return txt
    except Exception:
        pass
    return None

def extract_unspsc(driver) -> List[Dict[str, str]]:
    rows = []
    try:
        try:
            toggle = driver.find_element(By.ID, "unspscToggle")
            if toggle.get_attribute("aria-expanded") == "false":
                driver.execute_script("arguments[0].click();", toggle)
                time.sleep(0.5)
        except Exception:
            pass
        table = driver.find_element(By.ID, "unspscTable")
        headers = [clean_text(get_text(th)) or "" for th in table.find_elements(By.TAG_NAME, "th")]
        for tr in table.find_elements(By.XPATH, ".//tbody/tr"):
            tds = tr.find_elements(By.TAG_NAME, "td")
            if not tds: continue
            row_dict = {}
            for idx, td in enumerate(tds):
                key = headers[idx] if idx < len(headers) else f"col_{idx+1}"
                row_dict[key] = clean_text(get_text(td)) or ""
            if any(row_dict.values()):
                rows.append(row_dict)
    except Exception:
        pass
    return rows

def get_attachments(driver, event_id: str, attachments_dir: str = "attachments") -> List[Dict]:
    attachments: List[Dict] = []
    try:
        vp_btn = None
        selectors = [
            "//button[@id='RESP_INQ_DL0_WK_AUC_DOWNLOAD_PB']",
            "//a[@id='RESP_INQ_DL0_WK_AUC_DOWNLOAD_PB']",
            "//button[contains(normalize-space(),'View Event Package')]",
            "//a[contains(normalize-space(),'View Event Package')]",
            "//button[contains(normalize-space(),'Comments and Attachments')]",
            "//a[contains(normalize-space(),'Comments and Attachments')]",
            "//button[contains(normalize-space(),'View Attachments')]",
            "//a[contains(normalize-space(),'View Attachments')]",
        ]
        xpath_query = " | ".join(selectors)
        try:
            vp_btn = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((By.XPATH, xpath_query))
            )
        except Exception:
            cands = driver.find_elements(By.XPATH, xpath_query)
            if cands:
                vp_btn = cands[0]

        if not vp_btn:
            return attachments

        driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", vp_btn)
        time.sleep(2.5)
    except Exception as e:
        return attachments

    table_xpath = "//table[.//th[contains(.,'Attached File') or contains(.,'File Name') or contains(.,'Attachment')]]"
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, table_xpath + "//tbody/tr[.//td] | " + table_xpath + "//tr[.//td]"))
        )
    except TimeoutException:
        return attachments

    i = 0
    while True:
        rows = driver.find_elements(By.XPATH, table_xpath + "//tbody/tr[.//td] | " + table_xpath + "//tr[.//td]")
        if i >= len(rows):
            break
        tr = rows[i]
        i += 1

        raw_t = get_text(tr)
        lines = [l.strip() for l in raw_t.splitlines() if l.strip()]
        if not lines:
            continue
        first_line = lines[0]
        if first_line.lower() in ["delete", "upload", "attached file", ""] or "attached file" in first_line.lower():
            continue

        file_name = clean_text(first_line) or f"attachment_{i}"
        file_name = re.sub(r'[\\/*?:"<>|]', "_", file_name)

        dl_btns = tr.find_elements(
            By.XPATH,
            ".//button[@data-if-label='ViewAttachmentsView' or contains(@data-if-source,'SCM_DOWNLOAD')]"
            " | .//button[.//i[contains(@class,'fa-download')]]"
        )
        if not dl_btns:
            cand = tr.find_elements(By.XPATH, ".//td[last()]//button[not(contains(@class,'if-hide')) and not(contains(@class,'btn-danger'))]")
            if cand:
                dl_btns = cand
            else:
                continue

        # Reset downloadButton's href and InFlight attachment state
        try:
            driver.execute_script("""
                var b = document.getElementById('downloadButton');
                if (b) b.setAttribute('href', '#');
                if (typeof clearAttachmentWrapper === 'function') clearAttachmentWrapper();
            """)
        except Exception:
            pass

        dl_btn = dl_btns[0]
        try:
            driver.execute_script("arguments[0].click();", dl_btn)
            time.sleep(0.5)

            # Wait for downloadButton to have the viewredirect URL
            try:
                WebDriverWait(driver, 12).until(
                    lambda d: "viewredirect" in (d.find_element(By.ID, "downloadButton").get_attribute("href") or "")
                )
                modal_btn = driver.find_element(By.ID, "downloadButton")
                href = modal_btn.get_attribute("href")
            except Exception as wait_err:
                print(f"      [WARN] viewredirect URL did not appear for {file_name}: {wait_err}")
                href = None

            if href:
                url_fname = href.rsplit('/', 1)[-1].split('?')[0]
                if url_fname and not url_fname.startswith('{') and len(url_fname) > 3:
                    file_name = url_fname
                    file_name = re.sub(r'[\\/*?:"<>|]', "_", file_name)

                dest_dir = os.path.join(attachments_dir, event_id or "misc")
                out_file = os.path.join(dest_dir, file_name)

                # If file already exists and is not empty, reuse it
                if os.path.exists(out_file) and os.path.getsize(out_file) > 0:
                    size = os.path.getsize(out_file)
                    print(f"      [EXISTS] {file_name} ({size} bytes)")
                    attachments.append({
                        "file_name": file_name,
                        "download_url": href,
                        "local_path": out_file,
                        "size_bytes": size,
                    })
                else:
                    print(f"      [DOWNLOADING] {file_name}...")
                    if download_attachment_file(driver, href, out_file):
                        size = os.path.getsize(out_file)
                        print(f"      [SAVED] {out_file} ({size} bytes)")
                        attachments.append({
                            "file_name": file_name,
                            "download_url": href,
                            "local_path": out_file,
                            "size_bytes": size,
                        })
                    else:
                        print(f"      [WARN] Could not save {file_name}")

            # Close download modal and reset wrapper
            close_btns = driver.find_elements(By.XPATH, "//button[contains(normalize-space(),'Close')] | //*[@data-dismiss='modal']")
            if close_btns:
                driver.execute_script("arguments[0].click();", close_btns[0])
                time.sleep(0.5)
            try:
                driver.execute_script("if (typeof clearAttachmentWrapper === 'function') clearAttachmentWrapper();")
            except Exception:
                pass

        except Exception as err:
            print(f"      [WARN] Error downloading attachment {file_name}: {err}")
            try:
                close_btns = driver.find_elements(By.XPATH, "//button[contains(normalize-space(),'Close')] | //*[@data-dismiss='modal']")
                if close_btns:
                    driver.execute_script("arguments[0].click();", close_btns[0])
                    time.sleep(0.5)
            except Exception:
                pass
            continue

    return attachments

def get_latest_excel() -> str:
    import glob
    files = [f for f in glob.glob("caleprocure_*.xls") if "business" not in f.lower()]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[0] if files else "caleprocure_10_15.xls"

def main():
    parser = argparse.ArgumentParser(description="Caleprocure Targeted Scraper based on Excel Export")
    parser.add_argument("--excel", default=None, help="Path to caleprocure Excel export (defaults to latest)")
    parser.add_argument("--existing-json", default="Caleprocure_Events_Data.json", help="Path to existing JSON dataset")
    parser.add_argument("--out-json", default="Caleprocure_Events_Data.json", help="Path for output JSON dataset")
    parser.add_argument("--attachments-dir", default="attachments", help="Directory for attachments")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--ids", nargs="*", help="Specific Event IDs to re-scrape")
    parser.add_argument("--placeholders-only", action="store_true", help="Only re-scrape events with placeholder text")
    parser.add_argument("--missing-attachments-only", action="store_true", help="Re-scrape events that have 0 attachments")
    parser.add_argument("--force-all", action="store_true", help="Force re-scrape all events")
    args = parser.parse_args()

    out_json = args.out_json
    existing_json = args.existing_json
    attachments_dir = args.attachments_dir

    if args.excel:
        if not os.path.exists(args.excel):
            print(f"[ERROR] Excel file not found: {args.excel}")
            sys.exit(1)
        print(f"[1] Parsing Excel export: {args.excel}...")
        excel_records = parse_excel_export(args.excel)
    else:
        import glob
        excel_files = sorted([f for f in glob.glob("caleprocure_*.xls") if "business" not in f.lower()], reverse=True)
        print(f"[1] Parsing all Excel exports: {excel_files}...")
        all_rec_map = {}
        for f in excel_files:
            for r in parse_excel_export(f):
                eid = r.get("Event ID")
                if eid and eid not in all_rec_map:
                    all_rec_map[eid] = r
        excel_records = list(all_rec_map.values())
    print(f"[OK] Found {len(excel_records)} unique bid records across exports")

    # Build map of clean existing scraped records
    existing_events: Dict[str, Dict] = {}
    if os.path.exists(existing_json):
        try:
            with open(existing_json, "r", encoding="utf-8") as f:
                raw_scraped = json.load(f)
            for item in raw_scraped:
                eid = item.get("Details", {}).get("Event ID") or item.get("Event ID")
                if eid:
                    existing_events[eid] = item
            print(f"[2] Loaded {len(existing_events)} records from existing JSON {existing_json}")
        except Exception as e:
            print(f"[WARN] Could not parse existing JSON: {e}")
            existing_events = {}

    # Identify targets to scrape:
    targets = []
    for rec in excel_records:
        eid = rec.get("Event ID")
        if args.ids and eid not in args.ids:
            continue
        if args.ids and eid in args.ids:
            targets.append(rec)
            continue
        existing = existing_events.get(eid)
        if args.force_all or not existing:
            targets.append(rec)
            continue

        desc = existing.get("Description") or ""
        cname = (existing.get("Contact Information") or {}).get("Contact Name") or ""
        email = (existing.get("Contact Information") or {}).get("Email") or ""
        has_placeholder = ("[" in desc) or ("[" in cname) or ("[" in email)

        if args.missing_attachments_only:
            if not existing.get("Attachments"):
                targets.append(rec)
        elif args.placeholders_only:
            if has_placeholder:
                targets.append(rec)
        else:
            is_full = (not has_placeholder) and bool(desc or existing.get("Attachments") or existing.get("UNSPSC Codes"))
            if not is_full:
                targets.append(rec)

    print(f"[3] Identified {len(targets)} target events to scrape/update (out of {len(excel_records)} total)")
    if not targets:
        print("[INFO] All events in Excel are already cleanly scraped! Nothing to do.")
        return

    os.makedirs(attachments_dir, exist_ok=True)
    driver = build_driver(headless=args.headless, attachments_dir=attachments_dir)

    try:
        # Pre-authenticate session by hitting landing page once
        print("[4] Initializing session on CaleProcure...")
        driver.get("https://caleprocure.ca.gov/pages/Events-BS3/event-search.aspx")
        time.sleep(4)

        for idx, target in enumerate(targets):
            dept = target.get("Department", "")
            eid = target.get("Event ID", "")
            event_name = target.get("Event Name", "")
            
            canonical_url = f"https://caleprocure.ca.gov/event/{dept}/{eid}"
            print(f"\n[{idx+1}/{len(targets)}] Scraping ID={eid} (Dept: {dept}) | {event_name[:50]}")
            sys.stdout.flush()

            driver = ensure_driver(driver, headless=args.headless, attachments_dir=attachments_dir)

            try:
                driver.get(canonical_url)
                time.sleep(2.0)

                current_url = driver.current_url
                is_error_page = "error.aspx" in current_url

                # If redirected to error.aspx, try one reload
                if is_error_page:
                    time.sleep(1.5)
                    driver.get(canonical_url)
                    time.sleep(2.0)
                    current_url = driver.current_url
                    is_error_page = "error.aspx" in current_url

                event_data: Dict[str, object] = {}
                event_data["Event Name"] = event_name

                if is_error_page:
                    print(f"  [WARN] Canonical URL redirected to error.aspx (event expired or closed by agency)")
                    # Preserve full Excel metadata
                    details = {
                        "Event ID": eid,
                        "Dept": target.get("Department Name", ""),
                        "Department Code": dept,
                        "Format/Type": f"{target.get('Format', '')} / {target.get('Type', '')}".strip(" /"),
                        "Event Version": "1",
                        "Event End Date": target.get("End Date", ""),
                        "Status": target.get("Status", "Closed / Expired"),
                    }
                    event_data["Details"] = details
                    contact = {}
                    if target.get("Buyer Name"): contact["Contact Name"] = target.get("Buyer Name")
                    if target.get("Buyer Email"): contact["Email"] = target.get("Buyer Email")
                    if contact: event_data["Contact Information"] = contact
                    event_data["Description"] = f"Event listing closed or expired by agency as of 10/14/2026."
                    event_data["Attachments"] = []
                    event_data["Detail URL"] = canonical_url
                else:
                    # Clean page loaded: wait for SPA hydration
                    try:
                        def is_page_hydrated(d):
                            # check description
                            d_els = d.find_elements(By.XPATH, "//*[@data-if-label='descriptiondetails'] | //*[@id='AUC_HDR_DESCRLONG']")
                            if d_els:
                                t = d_els[0].text.strip()
                                if t and t != "[Detail Description]" and not (t.startswith("[") and t.endswith("]")):
                                    return True
                            # check contact
                            c_els = d.find_elements(By.XPATH, "//*[@data-if-label='contactName']")
                            if c_els:
                                t = c_els[0].text.strip()
                                if t and t != "[Contact Name]":
                                    return True
                            # check download button
                            if d.find_elements(By.ID, "RESP_INQ_DL0_WK_AUC_DOWNLOAD_PB"):
                                return True
                            return False

                        WebDriverWait(driver, 20).until(is_page_hydrated)
                        time.sleep(1.5)
                    except Exception:
                        pass

                    details = extract_details(driver, fallback_meta=target)
                    details["Event ID"] = eid
                    event_data["Details"] = details

                    contact = extract_contact(driver, fallback_meta=target)
                    if contact: event_data["Contact Information"] = contact

                    prebid = extract_prebid(driver)
                    if prebid: event_data["Pre Bid Conference"] = prebid

                    desc = extract_description(driver, fallback_meta=target)
                    if desc: event_data["Description"] = desc

                    unspsc = extract_unspsc(driver)
                    if unspsc: event_data["UNSPSC Codes"] = unspsc

                    attachments = get_attachments(driver, event_id=eid, attachments_dir=attachments_dir)
                    event_data["Attachments"] = attachments
                    event_data["Detail URL"] = canonical_url

                # Put into existing events dict
                existing_events[eid] = event_data
                att_count = len(event_data.get("Attachments", []))
                print(f"  [OK] Successfully saved {eid} ({att_count} attachments) [Total complete: {len(existing_events)}]")

                # Incremental save to JSON
                full_list = list(existing_events.values())
                with open(out_json, "w", encoding="utf-8") as f:
                    json.dump(full_list, f, ensure_ascii=False, indent=2)
                sys.stdout.flush()

            except Exception as ev_err:
                print(f"  [ERROR] Error processing {eid}: {ev_err}")
                # Save minimal record from Excel so no event is lost
                details = {
                    "Event ID": eid,
                    "Dept": target.get("Department Name", ""),
                    "Department Code": dept,
                    "Format/Type": f"{target.get('Format', '')} / {target.get('Type', '')}".strip(" /"),
                    "Event End Date": target.get("End Date", ""),
                    "Status": target.get("Status", ""),
                }
                event_data = {
                    "Event Name": event_name,
                    "Details": details,
                    "Detail URL": canonical_url,
                    "Attachments": [],
                }
                if target.get("Buyer Name") or target.get("Buyer Email"):
                    event_data["Contact Information"] = {
                        "Contact Name": target.get("Buyer Name", ""),
                        "Email": target.get("Buyer Email", ""),
                    }
                existing_events[eid] = event_data
                with open(out_json, "w", encoding="utf-8") as f:
                    json.dump(list(existing_events.values()), f, ensure_ascii=False, indent=2)
                sys.stdout.flush()

                if any(k in str(ev_err).lower() for k in ["invalid session id", "disconnected", "no such window", "unable to receive message"]):
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = build_driver(headless=args.headless, attachments_dir=attachments_dir)

    finally:
        driver.quit()
        final_list = list(existing_events.values())
        total_attachments = sum(len(ev.get("Attachments", [])) for ev in final_list)
        print(f"\n==========================================")
        print(f"[SUMMARY] Total events in full dataset: {len(final_list)}")
        print(f"[SUMMARY] Total attachments downloaded: {total_attachments}")
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(final_list, f, ensure_ascii=False, indent=2)
        print(f"[OK] Full dataset saved to {out_json}")
        print(f"==========================================")
        sys.stdout.flush()

if __name__ == "__main__":
    main()
