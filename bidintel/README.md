# STRONGERbuilt Bid Intelligence

Internal tool: Cal eProcure opportunities → searchable, AI-analyzed, matched to STRONGERbuilt clients.

```
scrapers (repo root) ──► ingest.py ──► enrich.py ──► analyze.py ──► server.py + web/index.html
   JSON + attachments     SQLite+FTS5    rules +        local LLM       Command Center · Bids · Client Match
                          full text      evidence       (Jetson)        Certification Lens · Ask
```

## Quick start (Windows or Jetson)

```bash
pip install pymupdf python-docx openpyxl beautifulsoup4
cd bidintel
python -m pipeline.build_silver   # (from the project root) bronze -> silver
python ingest.py            # silver -> gold; extracts attachment text (slow first time, cached afterwards)
python enrich.py            # seconds
python analyze.py --limit 5 # AI analysis of the 5 soonest-closing open bids
python analyze.py --brief   # portfolio brief
python server.py            # http://127.0.0.1:8765
```

The dashboard works before any AI runs. Rule signals, matching and the certification lens are instant.
Bids get AI analysis in batches (`analyze.py --all`) or on demand from the bid drawer.

## Gold layer of the data pipeline

bidintel is the **gold** layer: `ingest.py` reads only `data/silver/bids.jsonl` (one schema for every source,
built by `pipeline/adapters/`), so there is no source-specific code here. Every bid carries its own `source`
(`Cal eProcure`, `OpenGov`, ...) and `level` (`State` | `Local`), shown on every row and in the Source filter.
Non-Cal-eProcure ids are prefixed (`OG-301748`). Source-only fields (solicitation #, Q&A deadline, procurement
contact, NIGP/NAICS codes, evaluation criteria, price items...) sit in `events.extra`. See `../README.md`.

```bash
cd .. && python -m pipeline.run       # bronze -> silver -> gold (ingest + enrich)
```

## Bid Matching: bids ↔ 21k certified businesses

```bash
python firms.py              # load Caleprocure_businesses_final.xls (~3 s): 22.6k rows -> 21.4k firms
python firms.py --embed      # optional semantic layer: ~1 h on this PC's CPU, minutes on the Jetson; resumable
python eval_match.py         # quality report: coverage, P@10, codes-hidden ablation, latency
```

The **Bid Matching** tab (next to Client Match) works in two modes:
- **By bid:** ranked certified firms with role filters (Prime, DVBE partner, SB subcontractor, DBE partner), the reasons and
  warnings behind each score, a score breakdown, contact details, CSV export, an AI shortlist review, and "Add as client".
- **By business:** search any firm and see its best open bids.

How a score is built (`matcher.py`):
1. **Recall:** firms sharing a UNSPSC family or tighter with the bid; BM25 over firm keywords and names, with the query
   expanded by terms *learned from the firms themselves* (e.g. class 461916 → sprinkler, extinguisher, suppression);
   and nearest neighbours in embedding space when vectors exist.
2. **Relevance:** hierarchical UNSPSC match weighted by code rarity, keyword relevance (BM25 plus title coverage), and
   semantic similarity calibrated per bid.
3. **Fit:** location (bid addresses → county via a zip/city→county map learned from the firm data; HQ boilerplate
   suppressed; weighted higher for on-site work), industry type, and certification programs (SB 5%, set-asides,
   DVBE goal, SB-PW, DBE).
4. **Eligibility:** CSLB license held vs. required (names mapped to classes), microbusiness on a large bid → sub
   role, certification expiring before the due date.
5. **Roles:** Prime candidate, DVBE partner, DBE partner, SB subcontractor.

Ethnicity, race, gender identity, LGBQTIA and disability columns are never loaded or used.

## Configuration (environment variables)

| Variable | Default | |
|---|---|---|
| `BI_LLM_URL` | `http://localhost:11434` | Point at the Jetson: `http://<jetson-ip>:11434` |
| `BI_LLM_MODEL` | `gemma4:26b` | Any pulled Ollama model |
| `BI_LLM_BACKEND` | `ollama` | `openai` for any OpenAI-compatible endpoint |
| `BI_LLM_API_KEY` | | For hosted free tiers |
| `BI_LLM_NUM_CTX` | `16384` | Model context window |
| `BI_CONTEXT_CHARS` | `36000` | Document text per bid prompt |
| `BI_HOST` / `BI_PORT` | `127.0.0.1` / `8765` | `0.0.0.0` to serve the office LAN |

## Files

| File | Role |
|---|---|
| `ingest.py` | Events, UNSPSC and attachments into SQLite; text from PDF/DOCX/XLSX/ZIP; FTS5 chunks |
| `enrich.py` | Rule-based facts with evidence: SB/DVBE/DBE, licenses, bonds, pre-bids, counties, category |
| `analyze.py` | Schema-locked per-bid AI analysis + portfolio brief; cached by input hash |
| `insights.py` | Deadlines, stats, client matching, retrieval |
| `llm.py` | Ollama / OpenAI-compatible client |
| `server.py` | JSON API + dashboard + attachment serving |
| `clients.json` | Client profiles. The four shipped ones are samples; edit them in the Client Match tab |

Guides are in `../docs/`: Jetson setup, data and certification strategy, and the master build prompt.

**Security note:** the server has no login. Keep `BI_HOST=127.0.0.1`, or only expose it on a trusted office network.
