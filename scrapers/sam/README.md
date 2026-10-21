# SAM.gov scraper (Contract Opportunities, all federal agencies)

```bash
cd scrapers/sam && python sam_scraper.py                 # notices + attachments for open notices in CA OR FL TX GA VA PA
python sam_scraper.py --states all                        # attachments for every open notice (long; run on the Jetson)
python sam_scraper.py --no-attachments | --limit 20 | --refresh
```

**Notices.** SAM's official public daily extract (`falextracts.s3.amazonaws.com/Contract Opportunities/datagov/
ContractOpportunitiesFullCSV.csv`, about 200 MB, every active notice, 47 columns, refreshed around 03:30 UTC). It is
downloaded only when its Last-Modified changes. On 2026-10-20 it held 70,997 active notices, 12,655 of them open and
biddable (combined synopsis/solicitation, solicitation, presolicitation, sources sought, special notices).
Award notices and closed notices stay in bronze as history.

**Attachments.** For open notices, the notice's public resource list (`/api/prod/opps/v3/opportunities/<id>/resources`,
the same JSON sam.gov's public notice page reads), then each file's public download (it redirects to SAM's signed
S3 link). Only `accessLevel=public` and not export-controlled files are downloaded. Controlled ones are listed with
their status and never requested. The order is soonest-due first, with a 1 s pause, resumable.

Output: `data/bronze/sam/SAM_Data.json` (~300 MB, git-ignored, rebuilt daily), `raw/`, `attachments/<noticeId>/`.
Adapter: `pipeline/adapters/sam.py` (ids `SAM-<noticeId>`, level Federal, state US, open notices only).
A free SAM API key (`SAM_API_KEY`) would add the official `api.sam.gov` route. The extract already carries every
search field.
