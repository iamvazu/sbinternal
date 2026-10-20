import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def clean(text):
    if not text:
        return text
    text = str(text).replace("¿", "-").replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_pacific(value):
    """'09/10/2026 4:09PM PDT' -> '2026-09-10T16:09' (Pacific local time; the zone suffix is dropped)."""
    if not value:
        return None
    value = re.sub(r"\s+(PDT|PST|PT)$", "", str(value).strip())
    for fmt in ("%m/%d/%Y %I:%M%p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            continue
    return None


def contact(role, name=None, title=None, email=None, phone=None):
    c = {"role": role, "name": name, "title": title, "email": email, "phone": phone}
    return c if any(c[k] for k in ("name", "email", "phone")) else None


# ---- multi-state helpers -------------------------------------------------------------------------------------
PACIFIC = ZoneInfo("America/Los_Angeles")
ZONES = {"CA": "America/Los_Angeles", "OR": "America/Los_Angeles", "WA": "America/Los_Angeles",
         "AZ": "America/Phoenix", "TX": "America/Chicago",
         "FL": "America/New_York", "GA": "America/New_York", "VA": "America/New_York", "PA": "America/New_York"}


def local_to_pacific(value, fmts, state):
    """Agency-local time text -> '%Y-%m-%dT%H:%M' on the app's Pacific clock (None if it does not parse)."""
    if not value:
        return None
    value = re.sub(r"\s+", " ", str(value).strip())
    for fmt in fmts:
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=ZoneInfo(ZONES[state]))
            return dt.astimezone(PACIFIC).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            continue
    return None


def utc_to_pacific(value):
    """'2026-10-22T12:00:00.000+00:00' / '...Z' -> Pacific '%Y-%m-%dT%H:%M'."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PACIFIC).strftime("%Y-%m-%dT%H:%M")


def html_to_text(h):
    if not h:
        return h
    from bs4 import BeautifulSoup
    return clean(BeautifulSoup(str(h), "html.parser").get_text("\n"))


def attachment(a, project):
    """Bronze attachment dict -> silver attachment (local_path kept only if the file is on this machine)."""
    lp = a.get("local_path")
    p = project / lp if lp else None
    ok = bool(p and p.is_file())
    return {"file_name": a.get("file_name"), "url": a.get("download_url"),
            "local_path": lp.replace("\\", "/") if ok else None, "size_bytes": a.get("size_bytes"),
            "error": a.get("error") or (None if ok else "file missing on disk")}
