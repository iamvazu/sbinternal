# OregonBuys scraper (Oregon state agencies + local governments)

```bash
cd scrapers/oregonbuys && python oregonbuys_scraper.py [--limit 5] [--refresh] [--no-attachments]
# or: python -m pipeline.run --scrape oregonbuys
```

Source: `oregonbuys.gov` (Periscope BuySpeed), the official Oregon eProcurement system. Public, no sign-in.

- **Listing:** `advancedSearchBid.xhtml?openBids=true`, paged with the page's own PrimeFaces AJAX calls (25 rows per page).
- **Detail:** `bidDetail.sda?docId=<bid>`, which gives the full header (organization, department, purchaser, opening date,
  procurement method, discipline, pre-bid conference ...) and items with NIGP codes.
- **Files:** the page's public `downloadFile` form POST. Names come from Content-Disposition.

**Rate limit:** OregonBuys serves *empty pages* for several minutes after a burst (about 10 quick bids). The scraper uses
a fresh session per bid, waits 2.5 s between requests, rejects empty pages (they would otherwise be saved as blank
records), and backs off 60/120/180 s. A full run of about 175 bids takes about 25 minutes.

Output: `data/bronze/oregonbuys/OregonBuys_Data.json`, `attachments/<bid>/` and `raw/<bid>.json`. Adapter:
`pipeline/adapters/oregonbuys.py` (ids `OR-<bid>`; level comes from the organization name; Pacific time already).
Matching note: Oregon uses COBID certification (MBE/WBE/SDV/ESB). California SB/DVBE doesn't apply.
