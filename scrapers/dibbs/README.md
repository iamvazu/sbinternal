# DIBBS scraper (DLA Internet Bid Board System)

```bash
cd scrapers/dibbs && python dibbs_scraper.py [--days 30] [--no-documents]
```

DIBBS shows the standard **DoD Notice and Consent Banner**. The scraper accepts it once per host (www and dibbs2), the
same "OK" every vendor clicks. Rather than paging the very heavy ASP.NET grid (about 1,600 RFQs a day), it reads DLA's
**published daily RFQ download files** (Acquisition Downloads → RFQ Download files):

| File | Content |
|---|---|
| `in<yymmdd>.txt` | index, fixed width 140: solicitation, NSN/part, PR, return-by, PDF, qty, unit, nomenclature |
| `bq<yymmdd>.zip` → `bq*.txt` | batch-quote CSV, 121 fields per DLA's *Batch File Format* page: set-aside code (Y SB, H HUBZone, R SDVOSB, L WOSB, A 8(a), E EDWOSB, N none), solicitation type (F/P/I), HUBZone preference, lines |
| `bq<yymmdd>.zip` → `as*.txt` | approved sources: NSN, CAGE, part number |
| `ca<yymmdd>.zip` | every solicitation PDF of the day (~400 MB) |

`in`/`bq` are kept in `raw/`. PDFs are extracted only for solicitations still open, and the day's `ca` zip is then
deleted. DLA gives the return-by **date only**: the countdown uses the start of that day so it never overstates time
left. Adapter: `pipeline/adapters/dibbs.py` (ids `DIBBS-<solicitation>`, open RFQs only).
