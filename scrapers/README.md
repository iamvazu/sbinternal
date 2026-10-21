# Scrapers

One folder per **platform** (the software agencies post bids on), not per agency. Agencies on the same
platform are configuration. For example, the 11 PlanetBids agencies are one engine with 11 portal ids.

```
scrapers/
  caleprocure/     Cal eProcure (all CA state agencies)            -> data/bronze/caleprocure/
  opengov/         OpenGov Procurement (CA local agencies)         -> data/bronze/opengov/
  caltrans_des/    Caltrans DES construction contracts (state)     -> data/bronze/caltrans_des/
  csu/             CSU, all campuses (Jaggaer)                     -> data/bronze/csu/
  bonfire/         Bonfire / Euna portals (MTC, ...)               -> data/bronze/bonfire/
  planetbids/      blocked (AWS WAF on the data API), see pipeline/sources.py

  # other states (official state portals; found via publicbidtracker.com/about)
  common/          shared plumbing: honest UA, pacing + back-off, Bronze writer, verified downloads, time zones
  oregonbuys/      OregonBuys (OR state + local, Periscope BuySpeed) -> data/bronze/oregonbuys/
  mfmp/            MyFloridaMarketPlace (FL state agencies)         -> data/bronze/mfmp/
  tx_esbd/         Texas ESBD (TX state + SmartBuy members)         -> data/bronze/tx_esbd/
  ga_gpr/          Georgia Procurement Registry (state + local)     -> data/bronze/ga_gpr/
  eva/             Virginia eVA (state, universities, localities)   -> data/bronze/eva/
  pa_emarketplace/ Pennsylvania eMarketplace (Commonwealth)         -> data/bronze/pa_emarketplace/

  # federal (docs/FEDERAL_SCRAPING_PROMPT.md)
  sam/             SAM.gov Contract Opportunities (public extract)  -> data/bronze/sam/
  piee/            PIEE Solicitation Module (DoD, PIEE-only detail) -> data/bronze/piee/
  fedconnect/      FedConnect public opportunities                 -> data/bronze/fedconnect/
  dibbs/           DLA DIBBS RFQs (daily download files)           -> data/bronze/dibbs/
  usaspending/     award history (reference, not bids)             -> data/bronze/usaspending/ + data/reference/usaspending/
```

Out-of-state bids carry `state` (OR/FL/TX/GA/VA/PA). The matcher doesn't credit California SB/DVBE programs,
CSLB licenses or CA service areas on them, and says so. Each state's own program (COBID, OSD, HUB, GOSBO,
SWaM, Small Diverse Business) is noted on the bid. The master brief for these scrapers is
`docs/MULTISTATE_SCRAPING_PROMPT.md`.

## Contract every scraper follows

1. **Write only to `data/bronze/<source>/`**: a records JSON, `attachments/<source_id>/`, and optionally `raw/`.
   Never write into another source's folder or into silver/gold.
2. **Capture everything**: every field the platform exposes (keep the raw object if it has one) and every
   attachment. Thin listings (title + date) aren't enough for AI analysis or matching.
3. **Be resumable**: skip records already captured unless they changed; save progress regularly.
4. **No secrets in code**: credentials go in the scraper folder's `.env` (git-ignored).
5. **Respect site protections**: real browser and human-in-the-loop where a site uses Cloudflare or a CAPTCHA.
   Never bypass them.

## Adding a source (example: PlanetBids)

1. `scrapers/planetbids/planetbids_scraper.py`: reads the portal list from `pipeline/sources.py`
   (`SOURCES["planetbids"]["portals"]`), writes `data/bronze/planetbids/PlanetBids_Events_Data.json` + attachments.
2. `pipeline/adapters/planetbids.py`: a `records()` generator that maps bronze records to `pipeline/schema.py`
   (`id` = `PB-<portal>-<bid id>`, `source_label` = "PlanetBids", `level` = "Local").
3. In `pipeline/sources.py`, set `"status": "active"` and add `"scraper"` and `"adapter"`.
4. `python -m pipeline.run --scrape planetbids`. Search, AI analysis, Client Match, Bid Matching and the
   dashboard's Source filter pick it up with no other changes.

## About the external repo (ManirajPatha/Electronic-Advertising-Bidding)

Reviewed 2026-10-17 (clone in `../repo_review/`, outside this project, not executed):
- 27 scrapers; the 11 PlanetBids ones are the same file with a different portal id.
- Portal ids verified from its code and recorded in `pipeline/sources.py`. Several ids in the earlier summary
  were wrong (Menlo Park is 46202, Santa Rosa 20314, Burlingame 46106).
- Most capture listing fields only. Just 3 of 27 handle attachments, and the samples are 2-60 records from Nov 2025.
- **The repo has no license file**, so its code is all-rights-reserved by default. Use it as a map of targets
  (URLs and portal ids are facts). Rebuild engines to the contract above, or get the author's written permission
  before copying code.
