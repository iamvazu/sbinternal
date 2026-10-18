import re
from datetime import datetime


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
