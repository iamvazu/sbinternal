# PIEE Solicitation Module scraper (DoD)

```bash
cd scrapers/piee && python piee_scraper.py [--all] [--limit 5]     # opens a visible Chrome window
```

Public, unauthenticated Notice Search (`piee.eb.mil/sol/xhtml/unauth/...`): JSF/MyFaces with a client-side DataTable,
so a visible Chrome drives it, with no stealth flags. Every open notice is read with all 13 columns (notice id, response
date and zone, type, subject, description, restricted, status, posted, PSC, NAICS, office, set-aside, updates).

PIEE publishes its notices to SAM.gov. On 2026-10-20, 330 of 373 open notices were already on SAM, so by default only
the **PIEE-only** notices (43) get their detail page (contacts, DoDAAC, office address, place of performance, full
description) and public attachments. `--all` does every notice. Adapter: `pipeline/adapters/piee.py` (ids `PIEE-<id>`).
