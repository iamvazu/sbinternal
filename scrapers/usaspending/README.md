# USAspending scraper (federal award history)

```bash
cd scrapers/usaspending && python usaspending_scraper.py                       # CA OR FL TX GA VA PA, FY2021 -> now
python usaspending_scraper.py --states all --from-fy 2008                       # everything (Jetson drive)
python usaspending_scraper.py --build-only                                      # rebuild awards.db from the zips
```

Official open API, no key: `POST /api/v2/bulk_download/awards/` per (state of performance, fiscal year, contracts|IDVs),
then poll `/api/v2/download/status` and fetch the zip from `files.usaspending.gov`. **Pacing matters:** on 2026-10-20 the
service started dropping connections after about 10 quick requests. The scraper now waits 60 s between requests, backs
off 2/4/8 minutes, and stops cleanly after 3 straight failures. Finished files are kept and skipped on the next run. A state-year too big for the server
to build in one file (Virginia FY2026: "failed to build") is fetched automatically as two half-years (`_H1`, `_H2`).
The closed fiscal years are final; the current one refreshes weekly.

`build_reference()` turns the zips into `data/reference/usaspending/awards.db`:
- `transactions`: about 55 capture-relevant FPDS columns per action.
- `awards`: one row per award, with total obligated, dates, recipient and UEI, office, NAICS, PSC, set-aside,
  competition, offers and solicitation id.

The app uses it in each federal bid's **Award history** panel. The panel shows prior awards under the same
solicitation (the incumbent and recompete signal) and the market for the same NAICS and office: median award,
set-aside share, average offers and top winners. For "everything", USAspending's Award Data Archive
(`files.usaspending.gov/award_data_archive/`, full per-FY files) is the better route, still to be wired in.
