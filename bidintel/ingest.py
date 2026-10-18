"""Gold loader: silver bids (data/silver/bids.jsonl, every source in one schema) -> bidintel.db.
Extracts attachment text and builds the full-text index used by search, AI analysis and matching.
Source-specific logic lives in pipeline/adapters/, never here.

    python -m pipeline.build_silver && python ingest.py     # or simply: python -m pipeline.run
    python ingest.py --rebuild                              # wipe extracted text and redo everything
"""
import argparse
import io
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from config import PROJECT, SILVER_BIDS
from db import connect

MAX_PDF_PAGES = 150
CHUNK_CHARS = 1500


def parse_date(value):
    """'09/10/2026 4:09PM PDT' -> '2026-09-10T16:09' (Pacific local time, tz dropped)."""
    if not value:
        return None
    value = re.sub(r"\s+(PDT|PST|PT)$", "", value.strip())
    for fmt in ("%m/%d/%Y %I:%M%p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            continue
    return None


def clean(text: str) -> str:
    text = text.replace("¿", "-").replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk(text: str, size: int = CHUNK_CHARS):
    paras, buf = re.split(r"\n\s*\n", text), ""
    for p in paras:
        if len(buf) + len(p) > size and buf:
            yield buf.strip()
            buf = ""
        while len(p) > size:
            yield p[:size]
            p = p[size:]
        buf += p + "\n\n"
    if buf.strip():
        yield buf.strip()


# ---------------------------------------------------------------- text extraction

def pdf_text(data: bytes):
    import pymupdf
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        pages = doc.page_count
        parts = [doc.load_page(i).get_text() for i in range(min(pages, MAX_PDF_PAGES))]
    return "\n".join(parts), pages


def docx_text(data: bytes):
    import docx
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts), None


def xlsx_text(data: bytes):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets[:8]:
        parts.append(f"## Sheet: {ws.title}")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i > 400:
                break
            vals = [str(v).strip() for v in row if v not in (None, "")]
            if vals:
                parts.append(" | ".join(vals))
    return "\n".join(parts), None


def zip_text(data: bytes):
    parts = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist()[:60]:
            parts.append(f"## Zip member: {info.filename}")
            ext = info.filename.rsplit(".", 1)[-1].lower()
            if ext in EXTRACTORS and ext != "zip" and info.file_size < 40_000_000:
                try:
                    parts.append(EXTRACTORS[ext](zf.read(info))[0])
                except Exception as exc:  # one bad member must not sink the archive
                    parts.append(f"[unreadable: {exc}]")
    return "\n".join(parts), None


EXTRACTORS = {"pdf": pdf_text, "docx": docx_text, "xlsx": xlsx_text, "zip": zip_text}


def extract(path: Path):
    ext = path.suffix.lower().lstrip(".")
    if ext not in EXTRACTORS:
        return None, None, f"unsupported .{ext}"
    try:
        text, pages = EXTRACTORS[ext](path.read_bytes())
        return clean(text), pages, None
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"




# ---------------------------------------------------------------- silver -> gold

def ingest_attachment(conn, eid, a):
    """Extract (or reuse cached) text for one attachment and index it. The stored path is always refreshed,
    so moving bronze folders never forces a re-extraction."""
    fname = a.get("file_name")
    lp = a.get("local_path")
    path = PROJECT / lp if lp else None
    size = path.stat().st_size if path and path.is_file() else None
    ext = Path(fname or "").suffix.lower().lstrip(".")
    row = conn.execute("SELECT size_bytes, text FROM attachments WHERE event_id=? AND file_name=?", (eid, fname)).fetchone()
    if row and row["size_bytes"] == size and row["text"] is not None:
        text = row["text"]
        conn.execute("UPDATE attachments SET local_path=?, download_url=? WHERE event_id=? AND file_name=?",
                     (lp, a.get("url"), eid, fname))
    else:
        if size is not None:
            text, pages, err = extract(path)
        else:
            text, pages, err = None, None, a.get("error") or "file missing on disk"
        conn.execute("INSERT OR REPLACE INTO attachments VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (eid, fname, lp, ext, size, a.get("url"), pages, len(text or ""), text, err))
        print(f"  {eid}  {(fname or '')[:70]:70}  {'ERR ' + err if err else f'{len(text or ''):>8} chars'}")
    for body in chunk(text or ""):
        conn.execute("INSERT INTO chunks VALUES (?,?,?)", (eid, fname, body))


def ingest_document(conn, eid, doc):
    """Solicitation text a source provides as data (e.g. OpenGov), indexed like an attachment."""
    text = clean(doc["text"])
    conn.execute("INSERT OR REPLACE INTO attachments VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (eid, doc["name"], None, "txt", None, None, None, len(text), text, None))
    for body in chunk(text):
        conn.execute("INSERT INTO chunks VALUES (?,?,?)", (eid, doc["name"], body))


def gold_extra(b):
    """events.extra: source-specific fields + the canonical fields the bid drawer shows."""
    extra = dict(b.get("extra") or {})
    extra.setdefault("Solicitation Number", b.get("solicitation_number"))
    extra.setdefault("Department", b.get("department"))
    extra.setdefault("Agency City", b.get("agency_city"))
    extra.setdefault("Question Deadline", b.get("question_deadline"))
    extra.setdefault("Pre-Bid Meeting", b.get("prebid_meeting"))
    extra["Platform"] = b.get("platform")
    extra["Contacts"] = b.get("contacts") or []
    extra["Codes"] = b.get("codes") or []
    extra["Raw Ref"] = b.get("raw_ref")
    return {k: v for k, v in extra.items() if v not in (None, "", [], {})}


def ingest(rebuild=False):
    conn = connect()
    now = datetime.now().isoformat(timespec="seconds")
    bids = [json.loads(line) for line in SILVER_BIDS.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"{len(bids)} silver bids from {SILVER_BIDS.name}")
    if rebuild:
        conn.execute("DELETE FROM attachments")
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM unspsc")

    for b in bids:
        eid = b["id"]
        buyer = next(iter(b.get("contacts") or []), {})
        conn.execute(
            """INSERT INTO events (id,name,dept,dept_code,format,version,status,published,end_date,description,
                   contact_name,contact_email,contact_phone,detail_url,raw,first_seen,last_seen,source,level,extra)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, dept=excluded.dept, dept_code=excluded.dept_code,
                   format=excluded.format, version=excluded.version, status=excluded.status,
                   published=excluded.published, end_date=excluded.end_date, description=excluded.description,
                   contact_name=excluded.contact_name, contact_email=excluded.contact_email,
                   contact_phone=excluded.contact_phone, detail_url=excluded.detail_url, raw=excluded.raw,
                   last_seen=excluded.last_seen, source=excluded.source, level=excluded.level, extra=excluded.extra""",
            (eid, b["title"], b.get("agency"), b.get("agency_code"), b.get("procurement_type"), b.get("version"),
             b.get("status"), b.get("published_at"), b.get("due_at"), b.get("description"),
             buyer.get("name"), buyer.get("email"), buyer.get("phone"), b.get("url"),
             json.dumps({k: v for k, v in b.items() if k != "documents"}), now, now,
             b["source_label"], b["level"], json.dumps(gold_extra(b))))
        for c in b.get("codes") or []:
            if c.get("system") == "UNSPSC" and c.get("code"):
                desc = c.get("title") or ""
                conn.execute("INSERT OR REPLACE INTO unspsc VALUES (?,?,?,?)", (eid, c["code"], desc, desc.split(" - ")[0].strip()))
        head = clean(b.get("description") or "")
        other = [c for c in b.get("codes") or [] if c.get("system") != "UNSPSC"]
        if other:
            head += "\n\nCommodity categories: " + "; ".join(f"{c['system']} {c['code']} {c.get('title') or ''}" for c in other)
        for body in chunk(head):
            conn.execute("INSERT INTO chunks VALUES (?,?,?)", (eid, "Description", body))
        for doc in b.get("documents") or []:
            ingest_document(conn, eid, doc)
        for a in b.get("attachments") or []:
            ingest_attachment(conn, eid, a)
        conn.commit()

    s = conn.execute("""SELECT (SELECT COUNT(*) FROM events) e, (SELECT COUNT(*) FROM attachments) a,
                        (SELECT COUNT(*) FROM attachments WHERE error IS NOT NULL) ae,
                        (SELECT COUNT(*) FROM chunks) c""").fetchone()
    by = conn.execute("SELECT source, level, COUNT(*) FROM events GROUP BY 1, 2").fetchall()
    print(f"gold: {s['e']} events ({', '.join(f'{r[0]} {r[1]}: {r[2]}' for r in by)}), "
          f"{s['a']} attachments ({s['ae']} unreadable), {s['c']} search chunks")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ingest(ap.parse_args().rebuild)
    sys.exit(0)
