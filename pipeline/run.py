"""Pipeline orchestrator:  scrape (bronze) -> build_silver (silver) -> ingest + enrich (gold) [-> AI].

    python -m pipeline.run                         # silver + gold from whatever is already in bronze
    python -m pipeline.run --scrape opengov        # re-scrape one source first (opens Chrome for OpenGov)
    python -m pipeline.run --scrape all --analyze 25
    python -m pipeline.run --status                # what each layer holds right now

Each step is a separate process so one source failing never corrupts another, and every layer can be
rebuilt from the one below it.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime

from pipeline.paths import BRONZE, GOLD_APP, PROJECT, SILVER_MANIFEST
from pipeline.sources import SOURCES, active


def sh(cmd, cwd):
    print(f"\n$ {' '.join(cmd)}   (in {cwd.relative_to(PROJECT) if cwd != PROJECT else '.'})", flush=True)
    r = subprocess.run(cmd, cwd=cwd)
    if r.returncode:
        raise SystemExit(f"step failed ({r.returncode}): {' '.join(cmd)}")


def scrape(keys):
    for key in keys:
        meta = SOURCES[key]
        script, *args = meta["scraper"].split()
        path = PROJECT / script
        sh([sys.executable, path.name, *args], path.parent)


def status():
    print("BRONZE")
    for key, meta in SOURCES.items():
        d = BRONZE / key
        files = sum(1 for _ in d.rglob("*") if _.is_file()) if d.exists() else 0
        print(f"  {meta['label']:16} {meta['status']:8} {files:6} files" + ("" if d.exists() else "  (no data yet)"))
    if SILVER_MANIFEST.exists():
        m = json.loads(SILVER_MANIFEST.read_text(encoding="utf-8"))
        print(f"SILVER  built {m['built_at']}: {m['total']} bids")
        for k, v in m["sources"].items():
            print(f"  {v['label']:16} {v['records']:5} bids, {v['attachments_on_disk']}/{v['attachments']} attachments on disk")
    db = GOLD_APP / "data" / "bidintel.db"
    if db.exists():
        import sqlite3
        c = sqlite3.connect(db)
        print("GOLD")
        cols = {r[1] for r in c.execute("PRAGMA table_info(events)")}
        st = "COALESCE(state, 'CA')" if "state" in cols else "'CA'"
        for state, src, lvl, n in c.execute(f"SELECT {st}, source, level, COUNT(*) FROM events GROUP BY 1, 2, 3 ORDER BY 1, 2"):
            print(f"  {state:3} {src:16} {lvl or '':6} {n:5} bids")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scrape", nargs="+", metavar="SOURCE", help=f"re-scrape first: {', '.join(active())} or 'all'")
    ap.add_argument("--analyze", type=int, default=0, help="AI-analyze N soonest-closing open bids after loading")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        return status()
    t = datetime.now()
    if a.scrape:
        scrape(list(active()) if a.scrape == ["all"] else a.scrape)
    sh([sys.executable, "-m", "pipeline.build_silver"], PROJECT)
    sh([sys.executable, "ingest.py"], GOLD_APP)
    sh([sys.executable, "enrich.py"], GOLD_APP)
    if a.analyze:
        sh([sys.executable, "analyze.py", "--limit", str(a.analyze)], GOLD_APP)
    print(f"\npipeline finished in {(datetime.now() - t).seconds}s. Restart bidintel/server.py to serve the new data.")
    status()


if __name__ == "__main__":
    main()
