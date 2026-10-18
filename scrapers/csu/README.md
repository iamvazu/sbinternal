# CSU scraper (California State University: 23 campuses + Chancellor's Office)

```bash
cd scrapers/csu && python csu_scraper.py      # ~15 s; or: python -m pipeline.run --scrape csu
```

Source: CSU's public bid site on Jaggaer (`bids.sciquest.com/apps/Router/PublicEvent?CustomerOrg=CalState`).
Plain HTTP: the listing form is posted with PageSize=200. No browser, no bot challenge.

**Captured:** status, title, open / close date-time, type, number (campus prefix → campus), buyer name + email,
and the full **event PDF** (public signed link; downloaded and text-indexed).
**Not captured:** specs, Q&A and attachments inside CSUBUY need a free CSU supplier account. They're noted, not accessed.

First run (2026-10-17): 7 open solicitations (Cal State LA, Long Beach, San Marcos, Monterey Bay, San José State,
Dominguez Hills, Chancellor's Office), all event PDFs downloaded.
