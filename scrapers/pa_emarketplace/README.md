# Pennsylvania eMarketplace scraper (Commonwealth agencies)

```bash
cd scrapers/pa_emarketplace && python pa_emarketplace_scraper.py [--limit 5] [--refresh] [--no-attachments]
```

Source: `emarketplace.state.pa.us`, the Commonwealth's official solicitation board (ASP.NET WebForms, public).

- **Listing:** `Search.aspx`, then one postback with the page-size dropdown set to "ALL". That returns every open
  solicitation in one grid.
- **Detail:** `Solicitations.aspx?SID=`. Labelled cells are parsed; for radio and checkbox fields only the *checked*
  option is kept (e.g. Advertisement Type).
- **Files:** `FileDownload.aspx?file=..&OriginalFileName=..` (public).

Times are Eastern; the adapter converts them to Pacific and keeps the original in `extra`. Adapter:
`pipeline/adapters/pa_emarketplace.py` (ids `PA-<solicitation #>`; all State).
Program: DGS Small Diverse Business / Small Business.
