# FedConnect scraper

```bash
cd scrapers/fedconnect && python fedconnect_scraper.py [--limit 5] [--refresh] [--no-attachments]
```

Public opportunities on FedConnect (Unison PRISM), used by DOE/WAPA, DOI, EPA, NRC, USAID and others for contracts
and funding notices. `Public_Opportunities.aspx` is an ASP.NET grid (10 rows per page). A row's title postback renders
the Opportunity Summary: description, reference number, dates (US/Eastern), set-aside, NAICS, PSC/FSC, agency,
contracting office with address, place of performance, contact and vendor conference. Each document in the tree is a
postback to `Public_OpportunitySummary.aspx` that returns the file. Page state is per view, so each opportunity gets a
fresh session.

Responding needs a free FedConnect account; viewing and documents are public. Silver skips opportunities whose reference
number is also a SAM.gov solicitation. Adapter: `pipeline/adapters/fedconnect.py` (ids `FC-<reference>`).
