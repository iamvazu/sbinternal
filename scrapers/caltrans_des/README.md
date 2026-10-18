# Caltrans DES scraper (state highway & bridge construction)

Advertised construction contracts from Caltrans' Division of Engineering Services
(https://ppmoe.dot.ca.gov/cc?id=cc_advertisement). These contracts aren't on Cal eProcure.

```bash
cd scrapers/caltrans_des
python caltrans_des_scraper.py            # all advertised contracts, about 2 min; resumable
cd ../.. && python -m pipeline.run        # bronze -> silver -> gold
# or in one step from the project root:  python -m pipeline.run --scrape caltrans_des
```

**How:** the portal is a ServiceNow Service Portal. Each page's data is public JSON at
`/api/now/sp/page?id=<page>`, so this is plain HTTP: no browser, no bot challenge, a 1-second pause between
contracts. The list is checked for completeness (it falls back to per-district queries if it hits the 100-row cap).

**Captured per contract (public, no login):**
- **Contract:** District EA, work description, county-route-postmile, advertisement and bid-open dates, engineer's
  estimate, license callout and classes, working days, DBE / DVBE / SBE goals and status.
- **Bid line items:** every item, with quantities and units.
- **Bidder inquiries:** questions and Caltrans' responses.
- **Subcontractor opt-ins:** firm, contact, SBE/DBE/DVBE status, services offered.
- **Primes advertising for help:** prime, estimator contact, services needed and requirements. These are teaming leads.
- **Plan holders:** who downloaded the bid package.
- **Award and bid-document list:** file names, tags (plans, specs, forms, supplemental) and sizes.
- **Raw:** the full portal JSON for each contract, in `data/bronze/caltrans_des/raw/<EA>.json`.

**Not captured: the document files.** Plans, specs and forms need a free Caltrans contractor login (the
"Bid Book" button goes to `csm_login`). They're listed with size and a "login required" note and aren't downloaded.
With a STRONGERbuilt Caltrans account this can be added. Nothing here tries to get around the login.

**Dates:** Caltrans publishes only the bid-open *date*, so `due_at` is set to midnight that day. Countdowns
never overstate the time left.

First run (2026-10-17): 50 contracts, $1.23B in estimates, 5,591 bid items, 1,255 subcontractor opt-ins,
226 primes seeking subs, 459 bidder inquiries, 565 plan holders, 354 documents listed.
