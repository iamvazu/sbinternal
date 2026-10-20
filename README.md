# STRONGERbuilt Procurement Data Pipeline

Scrapes public bids from every platform California agencies use, plus the official state portals of Oregon,
Florida, Texas, Georgia, Virginia and Pennsylvania, normalizes them into one schema, and serves
them to **Bid Intelligence** (AI analysis, search, client and certified-firm matching).

```
scrapers/<source>/   ──►  data/bronze/<source>/   ──►  data/silver/bids.jsonl   ──►  bidintel/data/bidintel.db
   (code only)            BRONZE: exactly what          SILVER: one canonical          GOLD: enrichment, AI analysis,
                          the scraper produced          bid schema, every source       search, matching, dashboard
                          (JSON, attachments, raw)      (pipeline/adapters/)           (bidintel/)
```

| Layer | Path | Owner | Rule |
|---|---|---|---|
| Scrapers | `scrapers/caleprocure/`, `scrapers/opengov/`, `scrapers/caltrans_des/`, ... | one folder per **platform** | write only to `data/bronze/<source>/` |
| Bronze | `data/bronze/<source>/` | scraper | never edited by hand; source of truth for reprocessing |
| Silver | `data/silver/bids.jsonl` + `_manifest.json` | `pipeline/adapters/<source>.py` | schema in `pipeline/schema.py`; validated; rebuilt from bronze any time |
| Gold | `bidintel/data/bidintel.db` | `bidintel/ingest.py` | reads **only** silver; no source-specific code |
| Reference | `data/reference/` | | certified-firm directory (SB/MB/SB-PW/DVBE) used by Bid Matching |

## Run it

```bash
python -m pipeline.run --status                 # what every layer holds
python -m pipeline.run                          # bronze -> silver -> gold (no scraping)
python -m pipeline.run --scrape opengov         # re-scrape one source first
python -m pipeline.run --scrape all --analyze 25
cd bidintel && python server.py                 # dashboard: http://127.0.0.1:8765 (restart after a pipeline run)
```

## Sources

Each bid is labelled with its real source everywhere in the app (`source` + `level`): Cal eProcure bids are
`Cal eProcure · State`, OpenGov bids are `OpenGov · Local`. Ids are prefixed per source (`OG-301748`) so they never
collide. The full registry, including verified targets not yet built, is `pipeline/sources.py`.

| Source | Level | Status | Scraper |
|---|---|---|---|
| Cal eProcure | State | active | `scrapers/caleprocure/` |
| OpenGov | Local | active (46 agencies + County of Napa) | `scrapers/opengov/` |
| Caltrans DES | State | active (50 contracts, $1.23B) | `scrapers/caltrans_des/` |
| PlanetBids | Local | **blocked**: AWS WAF bot control on its data API (11 portals verified) | needs a sanctioned data route |
| CSU (Jaggaer, all 23 campuses) | State | active | `scrapers/csu/` |
| Bonfire (MTC; add agencies as config) | Local | active | `scrapers/bonfire/` |
| BidNet, Biddingo, QuestCDN, CivicPlus, agency portals | Local | planned | see `pipeline/sources.py` |

**Other states** (`state` field, `OR-`/`FL-`/`TX-`/`GA-`/`VA-`/`PA-` ids; times converted to Pacific, local time kept):

| Source | State | Level | Notes | Scraper |
|---|---|---|---|---|
| OregonBuys | OR | State + Local | rate-limits bursts; paced 2.5 s with back-off | `scrapers/oregonbuys/` |
| Florida MFMP | FL | State | public JSON; UNSPSC; attachments | `scrapers/mfmp/` |
| Texas ESBD | TX | State + Local (SmartBuy members) | public JSON; NIGP, est. value, attachments | `scrapers/tx_esbd/` |
| Georgia GPR | GA | State + Local | NIGP; attachments | `scrapers/ga_gpr/` |
| Virginia eVA | VA | State + Local | public Solr feed via visible Chrome; documents behind reCAPTCHA (linked, not scraped) | `scrapers/eva/` |
| PA eMarketplace | PA | State | WebForms; public FileDownload attachments | `scrapers/pa_emarketplace/` |

Arizona and Washington are deferred. California SB/DVBE certifications do not apply on out-of-state bids: the
matcher drops CA program credit and CA geography there, and flags the state's own program instead.

Adding a source: see `scrapers/README.md`.

## Other folders

- `bidintel/`: the gold layer and app (see `bidintel/README.md`)
- `docs/`: Jetson AI setup, data and certification strategy, build prompts
