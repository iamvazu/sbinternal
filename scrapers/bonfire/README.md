# Bonfire (Euna) scraper: one engine for every agency on <portal>.bonfirehub.com

```bash
cd scrapers/bonfire && python bonfire_scraper.py          # all portals in pipeline/sources.py
python bonfire_scraper.py --portals mtc --limit 3
```

Add an agency: put its subdomain in `SOURCES["bonfire"]["portals"]` in `pipeline/sources.py`.

- **Listing:** the portal's public JSON (`/PublicPortal/getOpenPublicOpportunitiesSectionData`), plain HTTP.
- **Detail pages:** behind Cloudflare. A visible Chrome with a persistent profile (`.chrome_profile/`) reads them.
  The check has cleared by itself every time so far; if a checkbox appears, click it once. It's never bypassed.

**Captured:** reference #, type, status, open / questions-due / close dates, contact, full description, important
events, document list (name, type, description, date), requested submission items, **document takers** (vendors
who downloaded: likely competition), prime and subcontractor lists, and the raw page JSON
(`data/bronze/bonfire/raw/`). Document **files** need a free Euna Supplier Network login. They're listed, not downloaded.

First run (2026-10-17): MTC, 11 opportunities, 29-134 document takers each.
