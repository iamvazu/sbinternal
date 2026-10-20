# Georgia Procurement Registry scraper

```bash
cd scrapers/ga_gpr && python ga_gpr_scraper.py [--limit 5] [--refresh] [--no-attachments]
```

Source: `ssl.doas.state.ga.us/gpr`, Georgia DOAS's official registry. It covers state agencies plus counties, cities,
K-12 and authorities. The site sniffs for a supported browser, so requests carry an honest, browser-compatible
User-Agent that still names STRONGERbuilt.

- **Listing:** POST `eventSearch` (the page's DataTables request), `eventStatus=OPEN`, 1000 rows.
- **Detail:** `eventDetails?eSourceNumber=&sourceSystemType=`. Gives event info (type, category, government type,
  agency site), start and end dates (Eastern), buyer, description, the NIGP table, conference and files.
- **Files:** `downloadAttachment?attachmentId=` (public).

Adapter: `pipeline/adapters/ga_gpr.py` (ids `GA-<event id>`; level is State when the government type is "state",
otherwise Local; the portal category maps to the app's work types). Program: Georgia GOSBO.
