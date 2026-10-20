# Virginia eVA scraper

```bash
cd scrapers/eva && python eva_scraper.py      # opens a visible Chrome window
```

Source: `mvendor.cgieva.com` (eVA, Virginia's official eProcurement system). It covers state agencies, universities
and localities.

- eVA sits behind an AWS firewall that admits real browsers only. The scraper opens a **visible** Chrome with a
  persistent profile (`.chrome_profile/`) and **does not hide that it is automated**: no stealth flags. If a check is
  shown, a person completes it in the window.
- **Listing:** from inside the loaded `AllOpportunities.jsp` page, it reads eVA's own public Solr feed
  (`solrconnect.jsp`, status Open, 500 per page, paged by cursor). Each record has the short and long description,
  agency and department code, buyer, work location, category, NIGP commodity lines, set-aside (Small/Micro
  Priority), and publish and close dates.
- **Not scraped:** opportunity detail pages and documents are gated by **reCAPTCHA**. Each record links to its page
  for a person to open instead.

eVA's `closedate` has a `Z` suffix but is Virginia local (Eastern) time, and the adapter treats it that way.
Adapter: `pipeline/adapters/eva.py` (ids `VA-IV<n>`; department codes starting "A" or "E" mean State, "L" means Local).
Program: SBSD SWaM.
