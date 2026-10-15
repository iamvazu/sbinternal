import re
import json
import time
import os
import sys
import base64
import argparse
from typing import List, Dict, Optional

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

START_URL = "https://caleprocure.ca.gov/pages/Events-BS3/event-search.aspx"

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
    opts.add_argument("--start-maximized")
    opts.add_argument("--window-size=1600,1200")
    
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

    driver = webdriver.Chrome(options=opts)
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except Exception:
        pass
    driver.set_page_load_timeout(120)
    return driver

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

def wait_for_searching_overlay(driver, timeout: int = 45):
    try:
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located((By.XPATH, "//*[contains(text(),'Searching...')]"))
        )
    except Exception:
        pass

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

def extract_details(driver) -> Dict[str, str]:
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
    return details

def extract_contact(driver) -> Dict[str, str]:
    contact = {}
    try:
        c_name = driver.find_element(By.XPATH, "//*[@data-if-label='contactName']")
        val = clean_text(get_text(c_name))
        if val: contact["Contact Name"] = val
    except Exception:
        pass
    try:
        phone = driver.find_element(By.XPATH, "//*[@data-if-label='phoneText']")
        val = clean_text(get_text(phone))
        if val: contact["Phone"] = val
    except Exception:
        pass
    try:
        email = driver.find_element(By.XPATH, "//*[@data-if-label='emffffailText' or @data-if-label='emailText'] | //a[starts-with(@href,'mailto:')]")
        val = clean_text(get_text(email))
        if not val and email.tag_name == "a":
            href = email.get_attribute("href") or ""
            val = href.replace("mailto:", "").split("?", 1)[0].strip()
        if val: contact["Email"] = val
    except Exception:
        pass
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

def extract_description(driver) -> Optional[str]:
    try:
        el = driver.find_element(By.XPATH, "//*[@data-if-label='descriptiondetails'] | //*[@id='AUC_HDR_DESCRLONG']")
        txt = clean_text(get_text(el))
        if txt: return txt
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
    detail_url = driver.current_url

    try:
        vp_list = driver.find_elements(
            By.XPATH,
            "//a[contains(normalize-space(),'View Event Package')]"
            " | //button[contains(normalize-space(),'View Event Package')]"
        )
        if not vp_list:
            return attachments
        driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", vp_list[0])
        time.sleep(2.5)
    except Exception:
        return attachments

    table_xpath = "//table[.//th[contains(.,'Attached File') or contains(.,'File Name') or contains(.,'Attachment')]]"
    try:
        WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.XPATH, table_xpath + "//tbody/tr[.//td]"))
        )
    except TimeoutException:
        return attachments

    i = 0
    while True:
        rows = driver.find_elements(By.XPATH, table_xpath + "//tbody/tr[.//td]")
        if i >= len(rows):
            break
        tr = rows[i]
        i += 1

        raw_t = get_text(tr)
        lines = [l.strip() for l in raw_t.splitlines() if l.strip()]
        if not lines:
            continue
        first_line = lines[0]
        if first_line.lower() in ["delete", "upload", ""]:
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
                # Use server filename from URL if valid
                url_fname = href.rsplit('/', 1)[-1].split('?')[0]
                if url_fname and not url_fname.startswith('{') and len(url_fname) > 3:
                    file_name = url_fname
                    file_name = re.sub(r'[\\/*?:"<>|]', "_", file_name)

                dest_dir = os.path.join(attachments_dir, event_id or "misc")
                out_file = os.path.join(dest_dir, file_name)
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

def get_next_page_button(driver):
    selectors = [
        "//li[contains(@class,'next') and not(contains(@class,'disabled'))]/a",
        "//li[contains(@id,'_next') and not(contains(@class,'disabled'))]/a",
        "//a[normalize-space()='Next' and not(contains(@class,'disabled')) and not(parent::li[contains(@class,'disabled')])]",
        "//button[normalize-space()='Next' and not(@disabled) and not(contains(@class,'disabled'))]",
    ]
    for xp in selectors:
        try:
            btn = driver.find_element(By.XPATH, xp)
            if btn.is_displayed() and btn.is_enabled():
                return btn
        except Exception:
            pass
    return None

def main():
    parser = argparse.ArgumentParser(description="Caleprocure Events Scraper")
    parser.add_argument("--out-json", default="Caleprocure_Events_Data.json", help="Output JSON path")
    parser.add_argument("--limit", type=int, default=0, help="Target number of events to scrape (0 for all)")
    parser.add_argument("--max-pages", type=int, default=0, help="Maximum number of pages to scrape (0 for all)")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--attachments-dir", default="attachments", help="Directory for attachments")
    parser.add_argument("--attachments-only", action="store_true", help="Only save events that contain attachments")
    args = parser.parse_args()

    target_count = args.limit if args.limit > 0 else float("inf")
    out_json = args.out_json
    attachments_dir = args.attachments_dir

    os.makedirs(attachments_dir, exist_ok=True)
    driver = build_driver(headless=args.headless, attachments_dir=attachments_dir)
    scraped_events: List[Dict] = []

    if os.path.exists(out_json):
        try:
            with open(out_json, "r", encoding="utf-8") as f:
                scraped_events = json.load(f)
            print(f"[INFO] Loaded {len(scraped_events)} existing events from {out_json}")
        except Exception:
            scraped_events = []

    already_scraped_ids = {
        ev.get("Details", {}).get("Event ID") or ev.get("Event ID")
        for ev in scraped_events
        if (ev.get("Details", {}).get("Event ID") or ev.get("Event ID"))
    }

    page_number = 1

    try:
        print("[1] Opening CaleProcure search page...")
        driver.get(START_URL)
        wait_for_searching_overlay(driver)
        
        try:
            h2 = driver.find_element(By.XPATH, "//*[self::h2 or self::h3][normalize-space()='EVENTS']")
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", h2)
        except Exception:
            pass

        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//table[@id='datatable-ready']/tbody/tr[.//td]"))
        )
        time.sleep(2)

        row_index = 0
        search_window = driver.current_window_handle

        while len(scraped_events) < target_count:
            driver.switch_to.window(search_window)

            try:
                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "datatable-ready")))
            except Exception:
                print("  [INFO] Search table detached or refreshed, reconnecting...")
                driver.get(START_URL)
                wait_for_searching_overlay(driver)
                WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "datatable-ready")))
                time.sleep(2)

            table = driver.find_element(By.ID, "datatable-ready")
            rows = table.find_elements(By.XPATH, ".//tbody/tr[.//td]")
            if row_index >= len(rows):
                print(f"[INFO] Page {page_number}: Reached end of rows ({len(rows)}), checking for next page...")
                if args.max_pages > 0 and page_number >= args.max_pages:
                    print(f"[INFO] Reached max-pages limit ({args.max_pages}). Stopping.")
                    break

                next_btn = get_next_page_button(driver)
                if next_btn:
                    page_number += 1
                    print(f"\n[INFO] Advancing to page {page_number}...")
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", next_btn)
                    time.sleep(2)
                    wait_for_searching_overlay(driver)
                    try:
                        WebDriverWait(driver, 20).until(
                            EC.presence_of_element_located((By.XPATH, "//table[@id='datatable-ready']/tbody/tr[.//td]"))
                        )
                    except Exception:
                        pass
                    time.sleep(2)
                    row_index = 0
                    continue
                else:
                    print("[INFO] No more pages available. Scrape complete.")
                    break

            row = rows[row_index]
            row_index += 1

            try:
                try:
                    name_cell = row.find_element(By.XPATH, ".//*[@data-if-label='tdEventName'] | .//td[3]")
                    event_name = clean_text(get_text(name_cell)) or "Unknown Event Name"
                except Exception:
                    event_name = "Unknown Event Name"

                cell = row.find_element(By.XPATH, ".//td[contains(@id,'AUC_ID_COL') or @data-if-label='tdEventId']")
                event_id_guess = clean_text(get_text(cell)) or f"event_{row_index}"

                target_str = f"/{int(target_count)}" if target_count != float("inf") else ""
                print(f"\n[Page {page_number} | Row {row_index} | Total {len(scraped_events) + 1}{target_str}] Examining ID={event_id_guess} | {event_name[:50]}")
                sys.stdout.flush()

                if event_id_guess in already_scraped_ids:
                    print(f"  [SKIP] Event {event_id_guess} already scraped!")
                    continue

                # Click cell (or inner anchor) to open detail tab and obtain BUSINESS_UNIT
                click_target = cell
                links = cell.find_elements(By.TAG_NAME, "a")
                if links:
                    click_target = links[0]
                driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", click_target)
                time.sleep(1.5)

                if len(driver.window_handles) < 2:
                    print("  [WARN] New tab did not open, skipping row")
                    continue

                detail_window = [w for w in driver.window_handles if w != search_window][-1]
                driver.switch_to.window(detail_window)

                # Instantly extract BUSINESS_UNIT and AUC_ID from tab URL
                raw_url = driver.current_url
                for _ in range(10):
                    if "BUSINESS_UNIT=" in raw_url:
                        break
                    time.sleep(0.5)
                    raw_url = driver.current_url

                bu_match = re.search(r"BUSINESS_UNIT=([^&]+)", raw_url)
                auc_match = re.search(r"AUC_ID=([^&]+)", raw_url)

                if bu_match and auc_match:
                    clean_url = f"https://caleprocure.ca.gov/event/{bu_match.group(1)}/{auc_match.group(1)}"
                    driver.get(clean_url)
                    try:
                        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.XPATH, "//*[normalize-space()='Details']")))
                        time.sleep(1.5)
                    except TimeoutException:
                        print(f"  [WARN] Timed out waiting for canonical URL {clean_url}")
                        driver.close()
                        driver.switch_to.window(search_window)
                        continue
                else:
                    try:
                        WebDriverWait(driver, 20).until(lambda d: "loading.html" not in d.current_url)
                        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.XPATH, "//*[normalize-space()='Details']")))
                    except Exception:
                        print(f"  [WARN] Could not load details for {event_id_guess}")
                        driver.close()
                        driver.switch_to.window(search_window)
                        continue

                event_data: Dict[str, object] = {}
                event_data["Event Name"] = event_name
                
                details = extract_details(driver)
                event_data["Details"] = details
                event_id = details.get("Event ID", event_id_guess)

                contact = extract_contact(driver)
                if contact: event_data["Contact Information"] = contact

                prebid = extract_prebid(driver)
                if prebid: event_data["Pre Bid Conference"] = prebid

                desc = extract_description(driver)
                if desc: event_data["Description"] = desc

                unspsc = extract_unspsc(driver)
                if unspsc: event_data["UNSPSC Codes"] = unspsc

                attachments = get_attachments(driver, event_id=event_id, attachments_dir=attachments_dir)
                event_data["Attachments"] = attachments
                event_data["Detail URL"] = driver.current_url

                # If user requested attachments only, skip events without attachments
                if args.attachments_only and not attachments:
                    print(f"  [SKIP] Event {event_id} has no attachments (--attachments-only active)")
                    driver.close()
                    driver.switch_to.window(search_window)
                    time.sleep(1.0)
                    continue

                scraped_events.append(event_data)
                already_scraped_ids.add(event_id)
                att_info = f"{len(attachments)} attachments" if attachments else "no attachments"
                progress_str = f"{len(scraped_events)}/{int(target_count)}" if target_count != float("inf") else f"{len(scraped_events)}"
                print(f"  [OK] Saved event {event_id} ({att_info}) [Total: {progress_str}]")

                # Intermediate save after each valid event
                with open(out_json, "w", encoding="utf-8") as f:
                    json.dump(scraped_events, f, ensure_ascii=False, indent=2)
                sys.stdout.flush()

                driver.close()
                driver.switch_to.window(search_window)
                time.sleep(1.0)

            except Exception as row_err:
                print(f"  [ERROR] Failed to process row {row_index}: {row_err}")
                try:
                    for h in driver.window_handles:
                        if h != search_window:
                            driver.switch_to.window(h)
                            driver.close()
                    driver.switch_to.window(search_window)
                except Exception:
                    pass
                time.sleep(1.0)
                continue

    finally:
        driver.quit()
        total_attachments = sum(len(ev.get("Attachments", [])) for ev in scraped_events)
        print(f"\n==========================================")
        print(f"[SUMMARY] Total events scraped: {len(scraped_events)}")
        print(f"[SUMMARY] Total attachments downloaded: {total_attachments}")
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(scraped_events, f, ensure_ascii=False, indent=2)
        print(f"[OK] Saved events JSON to {out_json}")
        print(f"==========================================")
        sys.stdout.flush()

if __name__ == "__main__":
    main()
