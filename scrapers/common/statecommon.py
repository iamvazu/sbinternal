"""Shared plumbing for the multi-state scrapers (OR, FL, TX, GA, VA, PA).

Every scraper: identifies itself honestly, pauses between requests, retries with back-off, writes only to its
bronze folder, keeps a raw copy of what each portal returned, downloads only *public* attachments (verified, size
capped), and re-fetches a record only when its fingerprint changed.
"""
import hashlib
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

PROJECT = Path(__file__).resolve().parents[2]
UA = "STRONGERbuilt-BidIntelligence/1.0 (public bid data; contact via STRONGERbuilt)"
# Some portals sniff for a "supported browser". This UA is still honest (it names us) but browser-compatible.
UA_BROWSER = ("Mozilla/5.0 (compatible; STRONGERbuilt-BidIntelligence/1.0; public bid data) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0 Safari/537.36")
MAX_ATTACHMENT_BYTES = 150 * 2**20  # plan sets above this stay on the portal; the record keeps the link
PACIFIC = ZoneInfo("America/Los_Angeles")
ZONES = {"OR": "America/Los_Angeles", "FL": "America/New_York", "TX": "America/Chicago", "GA": "America/New_York",
         "VA": "America/New_York", "PA": "America/New_York", "CA": "America/Los_Angeles"}


class Bronze:
    """data/bronze/<source>/ : records JSON, attachments/<id>/, raw/<id>.json, log."""

    def __init__(self, source, records_name):
        self.source = source
        self.dir = PROJECT / "data" / "bronze" / source
        self.att = self.dir / "attachments"
        self.raw = self.dir / "raw"
        self.records_path = self.dir / records_name
        for p in (self.dir, self.att, self.raw):
            p.mkdir(parents=True, exist_ok=True)
        self.log = logging.getLogger(source)
        if not self.log.handlers:
            fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
            for h in (logging.FileHandler(self.dir / f"{source}_scraper.log", encoding="utf-8"), logging.StreamHandler()):
                h.setFormatter(fmt)
                self.log.addHandler(h)
            self.log.setLevel(logging.INFO)

    def load(self, key):
        if not self.records_path.exists():
            return {}
        return {r[key]: r for r in json.loads(self.records_path.read_text(encoding="utf-8"))}

    def save(self, records):
        # atomic: the pipeline may read this file while a long scrape checkpoints it
        tmp = self.records_path.with_suffix(".saving")
        tmp.write_text(json.dumps(list(records.values()), indent=1, ensure_ascii=False), encoding="utf-8")
        for attempt in range(10):  # Windows refuses to replace a file another process is reading; wait it out
            try:
                tmp.replace(self.records_path)
                return
            except PermissionError:
                time.sleep(3)
        raise RuntimeError(f"could not replace {self.records_path.name}: it stayed open in another program ({tmp.name} kept)")

    def save_raw(self, rid, obj):
        (self.raw / f"{safe_name(rid)}.json").write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        return (self.raw / f"{safe_name(rid)}.json").relative_to(PROJECT).as_posix()


def session(browser_ua=False):
    s = requests.Session()
    s.headers.update({"User-Agent": UA_BROWSER if browser_ua else UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def call(s, method, url, log=None, tries=4, pause=1.0, **kw):
    """Polite request: fixed pause, retries with back-off on network errors and 5xx."""
    kw.setdefault("timeout", 120)
    for attempt in range(tries):
        try:
            r = s.request(method, url, **kw)
            if r.status_code < 500:
                time.sleep(pause)
                return r
            raise requests.HTTPError(f"{r.status_code}")
        except Exception as e:
            if log:
                log.warning("%s %s attempt %d: %s", method, url[:100], attempt + 1, e)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"giving up on {url}")


def safe_name(s):
    s = re.sub(r"[^\w.\-() ]+", "_", unquote(str(s))).strip(" ._")
    return s[:150] or "file"


def download(s, bronze, rid, file_name, url, method="GET", data=None, log=None, server_name=False, params=None, pause=0.5):
    """Download one public attachment into attachments/<rid>/. Never stores an HTML error page as a file.
    server_name=True: name the file from the server's Content-Disposition (when link text is generic)."""
    dest_dir = bronze.att / safe_name(rid)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name(file_name)
    rec = {"file_name": dest.name, "download_url": url, "local_path": None, "size_bytes": None, "error": None}
    if dest.exists() and dest.stat().st_size > 0:
        rec.update(local_path=dest.relative_to(PROJECT).as_posix(), size_bytes=dest.stat().st_size)
        return rec
    try:
        r = call(s, method, url, log=log, data=data, params=params, stream=True, pause=pause)
        if r.status_code != 200:
            rec["error"] = f"HTTP {r.status_code}"
            return rec
        cd = r.headers.get("content-disposition") or ""
        m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
        if server_name and m:
            dest = dest_dir / safe_name(m.group(1))
            rec["file_name"] = dest.name
            if dest.exists() and dest.stat().st_size > 0:
                rec.update(local_path=dest.relative_to(PROJECT).as_posix(), size_bytes=dest.stat().st_size)
                return rec
        size = int(r.headers.get("content-length") or 0)
        if size > MAX_ATTACHMENT_BYTES:
            rec["error"] = f"not downloaded: {size / 2**20:.0f} MB (over the {MAX_ATTACHMENT_BYTES // 2**20} MB cap); open on the portal"
            return rec
        head = b""
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                if not head:
                    head = chunk[:200]
                fh.write(chunk)
        if head.lstrip().lower().startswith((b"<!doctype html", b"<html")) and not dest.suffix.lower().endswith(("htm", "html")):
            dest.unlink(missing_ok=True)
            rec["error"] = "portal returned a web page instead of the file"
            return rec
        rec.update(local_path=dest.relative_to(PROJECT).as_posix(), size_bytes=dest.stat().st_size)
    except Exception as e:
        dest.unlink(missing_ok=True)
        rec["error"] = str(e)[:200]
    return rec


def html_text(h):
    if not h:
        return ""
    t = BeautifulSoup(str(h), "html.parser").get_text("\n")
    t = t.replace("\xa0", " ").replace("¿", "'")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", t)).strip()


def to_pacific(dt):
    """aware datetime -> 'YYYY-MM-DDTHH:MM' in Pacific time (the app's clock), for consistent countdowns."""
    return dt.astimezone(PACIFIC).strftime("%Y-%m-%dT%H:%M") if dt else None


def local_dt(text, fmts, state):
    """Parse a portal's local date-time string in the state's own zone; returns an aware datetime or None."""
    if not text:
        return None
    text = re.sub(r"\s+", " ", str(text).strip())
    text = re.sub(r"\s*(ET|CT|PT|EST|EDT|CST|CDT|PST|PDT)$", "", text)
    for f in fmts:
        try:
            return datetime.strptime(text, f).replace(tzinfo=ZoneInfo(ZONES[state]))
        except ValueError:
            continue
    return None


def fingerprint(*parts):
    return hashlib.sha1(json.dumps(parts, default=str, sort_keys=True).encode()).hexdigest()[:16]


def now_iso():
    return datetime.now().isoformat(timespec="seconds")
