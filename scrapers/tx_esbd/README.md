# Texas ESBD scraper (Electronic State Business Daily)

```bash
cd scrapers/tx_esbd && python tx_esbd_scraper.py [--limit 5] [--refresh] [--no-attachments]
```

Source: `txsmartbuy.gov/esbd`, Texas's official posting site for state agencies and Texas SmartBuy members (cities,
counties, school districts, universities). Plain HTTP against the site's own public NetSuite JSON services:

- **Listing:** POST `ESBD.Service.ss` `{"page":n,"urlRoot":"esbd","status":"1"}` (posted / open), 24 per page.
- **Detail:** GET `ESBD.Details.Service.ss?identification=<id>`. Gives the description, NIGP codes, contact, estimated
  value, posting requirement, agency certification, TxDOT highway districts, and attachments.
- **Files:** each attachment's public `fileURL`.

Level: a numeric agency number means a state agency; a letter-prefixed one means a SmartBuy member (local).
Due times are Central time; the adapter converts them to Pacific and keeps the original in `extra`.
Adapter: `pipeline/adapters/tx_esbd.py` (ids `TX-<solicitation id>`). Program: Texas HUB, often a HUB subcontracting plan.
