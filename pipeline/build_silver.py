"""Bronze -> silver: run every active source adapter, validate, write data/silver/bids.jsonl + a manifest.

    python -m pipeline.build_silver
"""
import importlib
import json
from collections import Counter
from datetime import datetime

from pipeline import schema
from pipeline.paths import SILVER, SILVER_BIDS, SILVER_MANIFEST
from pipeline.sources import active


def build():
    SILVER.mkdir(parents=True, exist_ok=True)
    manifest = {"built_at": datetime.now().isoformat(timespec="seconds"), "sources": {}}
    seen, out = set(), []
    for key, meta in active().items():
        adapter = importlib.import_module(meta["adapter"])
        n, problems, dupes = 0, Counter(), 0
        for rec in adapter.records():
            errs = schema.validate(rec)
            for e in errs:
                problems[e.split(":")[0]] += 1
            if rec["bid_uid"] in seen or rec["id"] in {r["id"] for r in out[-1:]}:
                dupes += 1
                continue
            seen.add(rec["bid_uid"])
            out.append(rec)
            n += 1
        atts = [a for r in out if r["source"] == key for a in r["attachments"]]
        manifest["sources"][key] = {
            "label": meta["label"], "level": meta["level"], "state": meta.get("state", "CA"), "records": n, "duplicates_dropped": dupes,
            "attachments": len(atts), "attachments_on_disk": sum(1 for a in atts if a["local_path"]),
            "validation_problems": dict(problems),
        }
        print(f"{meta['label']:14} {n:5} bids  {len(atts):5} attachments  problems={dict(problems) or 'none'}")
    ids = Counter(r["id"] for r in out)
    clashes = [i for i, c in ids.items() if c > 1]
    if clashes:  # display ids must be unique across sources, or gold rows would overwrite each other
        raise SystemExit(f"id clash across sources: {clashes[:5]}")
    with open(SILVER_BIDS, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    manifest["total"] = len(out)
    manifest["by_state"] = dict(Counter(r["state"] for r in out).most_common())
    SILVER_MANIFEST.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"silver: {len(out)} bids -> {SILVER_BIDS}  by state: {manifest['by_state']}")
    return manifest


if __name__ == "__main__":
    build()
