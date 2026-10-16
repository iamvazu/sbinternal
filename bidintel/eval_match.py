"""Offline evaluation of the bid <-> business matcher. No hand labels exist yet, so it uses proxies:

  coverage      share of open bids with >=10 qualified firms / >=1 prime candidate
  P@10 (codes)  share of the top 10 whose UNSPSC holds the bid's class (6-digit) or tighter
  ablation      hide the bid's codes from the engine (text + semantic only) and re-measure P@10 against
                the hidden codes: an honest test of the keyword/semantic layers on held-out ground truth
  latency       median ms per bid

    python eval_match.py [--limit 120] [--no-semantic]
"""
import argparse
import statistics
import time
from datetime import datetime

import matcher
from db import connect


def class_hit(ix, i, codes):
    p6 = ix.prefixes[i][6]
    return any(c["code"][:6] in p6 for c in codes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--no-semantic", action="store_true")
    a = ap.parse_args()
    ix = matcher.load_index()
    conn = connect()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    ids = [r[0] for r in conn.execute("SELECT id FROM events WHERE end_date >= ? ORDER BY id", (now,))][:a.limit]
    sem = not a.no_semantic
    cov10 = prime = 0
    p10, p10_ablate, ms, by_cat = [], [], [], {}
    for eid in ids:
        prof = matcher.bid_profile(conn, eid, ix)
        t = time.time()
        res, meta = matcher.run_match(prof, ix, conn, semantic=sem)
        ms.append((time.time() - t) * 1000)
        cov10 += len(res) >= 10
        prime += any("prime" in r["roles"] for r in res)
        if prof["codes"] and len(res) >= 10:
            v = sum(class_hit(ix, r["pos"], prof["codes"]) for r in res[:10]) / 10
            p10.append(v)
            by_cat.setdefault(prof["category"], []).append(v)
            hidden = dict(prof, codes=[])
            res2, _ = matcher.run_match(hidden, ix, conn, semantic=sem)
            if len(res2) >= 10:
                p10_ablate.append(sum(class_hit(ix, r["pos"], prof["codes"]) for r in res2[:10]) / 10)
    n = len(ids)
    print(f"bids evaluated: {n}   semantic: {sem and ix.vectors is not None} ({0 if ix.vectors is None else len(ix.vec_pos)} firm vectors)")
    print(f"coverage  >=10 qualified firms: {cov10 / n:.0%}   >=1 prime candidate: {prime / n:.0%}")
    print(f"P@10 class-or-better (full engine):        {statistics.mean(p10):.2f}  over {len(p10)} bids with codes")
    print(f"P@10 with codes hidden (text+semantic):    {statistics.mean(p10_ablate):.2f}  over {len(p10_ablate)} bids")
    print(f"latency median {statistics.median(ms):.0f} ms, p95 {sorted(ms)[int(len(ms) * .95) - 1]:.0f} ms")
    print("P@10 by category:")
    for cat, v in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        print(f"  {cat:28} {statistics.mean(v):.2f}  (n={len(v)})")


if __name__ == "__main__":
    main()
