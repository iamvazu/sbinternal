# MyFloridaMarketPlace scraper (Florida state agencies)

```bash
cd scrapers/mfmp && python mfmp_scraper.py [--limit 5] [--refresh] [--no-attachments]
```

Source: `vendor.myfloridamarketplace.com` (the Vendor Bid System advertisements), Florida's official system.
Plain HTTP against the site's own public JSON:

- **Listing:** POST `/mfmp/pub/search/bids` with `{"status":["OPEN"], "pageSize":100, ...}`. Covers all ad types:
  ITB, RFP, ITN, single source, agency decisions, grants ...
- **Detail:** GET `/mfmp/pub/search/bids/detail?id=<advertisementId>`. Gives the HTML description, **UNSPSC** codes,
  response contact and address, and indicators (cone of silence, minority encouraged ...).
- **Files:** `/mfmp/bids/detail/attachment/download?attachmentId=` (public).

Output: `data/bronze/mfmp/MFMP_Data.json`, `attachments/<AD-id>/` and `raw/`. Adapter: `pipeline/adapters/mfmp.py`
(ids `FL-AD-<n>`; portal times are true UTC and are converted to Pacific). Program: Florida OSD (MBE/WBE/SDVBE).
