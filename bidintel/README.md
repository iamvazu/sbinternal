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
python ingest.py            # ~2-5 min first time (1.9 GB of attachments), incremental afterwards
python enrich.py            # seconds
python analyze.py --limit 5 # AI analysis of the 5 soonest-closing open bids
python analyze.py --brief   # portfolio brief
python server.py            # http://127.0.0.1:8765
```

The dashboard works before any AI runs. Rule signals, matching and the certification lens are instant.
Bids get AI analysis in batches (`analyze.py --all`) or on demand from the bid drawer.

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
