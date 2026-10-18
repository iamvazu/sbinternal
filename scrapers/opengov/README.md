# OpenGov Procurement scraper (California local agencies)

Collects every open California opportunity on procurement.opengov.com, with **all fields and all
attachments**. Code and config live here (`scrapers/opengov/`); everything it produces goes to the
bronze layer `data/bronze/opengov/`, and `pipeline/adapters/opengov.py` maps it into the shared silver schema.

```bash
cd scrapers/opengov
python opengov_scraper.py --portals          # public agency portals in agencies.txt (no login)
python opengov_scraper.py                    # vendor "open bids, state = CA" list (needs a working login in .env)
python opengov_scraper.py --inventory        # every datapoint captured and its fill rate
cd ../.. && python -m pipeline.run           # bronze -> silver -> gold
```

Here: `opengov_scraper.py`, `agencies.txt`, `.env` (credentials, git-ignored), `.chrome_profile/` (Cloudflare clearance).
Bronze output in `data/bronze/opengov/`:

| Output | Contents |
|---|---|
| `OpenGov_Events_Data.json` | One record per opportunity. Same keys as `Caleprocure_Events_Data.json` plus OpenGov extras |
| `attachments/<project_id>/` | Every attachment, addendum file and the solicitation PDF snapshot |
| `raw/<project_id>.json` | The complete raw OpenGov project object (~150 fields), so nothing is lost |
| `agencies.txt` | CA agency portal codes. The vendor-list mode adds every new agency it sees |

## How it works (and why it's reliable)

OpenGov server-renders its whole application state into `window.__data`. Every project page contains the
complete project object, so the scraper reads that instead of scraping the screen. Pages
are loaded directly by URL (`/portal/<agency>?status=open&page=N&limit=50`, `/portal/<agency>/projects/<id>`), which
removes the click-and-go-back loop that made the old scraper duplicate rows. Attachments are signed Amazon S3
links (valid about 20 hours) and are downloaded directly.

**Cloudflare.** The site sits behind a Cloudflare check. The scraper uses a real, visible Chrome window with a
persistent profile (`.chrome_profile/`). If "Just a moment..." appears, click the checkbox once. The
clearance is remembered and the run continues. The scraper does not try to bypass the check.

**Login.** Credentials were moved out of the code into `.env` (git-ignored). **The password that was in the old
script is rejected by OpenGov** ("Incorrect email address and password"), so update `OPENGOV_PASSWORD` in `.env`.
The scraper stops after one failed attempt so the account can't be locked out. Until then, `--portals` mode needs no login.

**Coverage.** `--portals` covers the agencies in `agencies.txt` (46, taken from the old scraper's results). The
vendor list (login) is the complete statewide list. Run it once with a working login and `agencies.txt` grows to
every CA agency on OpenGov, so later `--portals` runs are complete too.

## What changed from the original scraper (removed 2026-10-17)

| Original | Now |
|---|---|
| Hard-coded email + password in source | `.env`, git-ignored |
| Login broke: OpenGov's 2026 sign-in screen added a "Vendor Login" step | Handled; fails fast on a wrong password |
| Screen-scraped about 10 fields; 219 rows but only 129 unique (page-reset bug) | Reads the full project object (~150 fields); one record per project ID |
| No attachments (the `.bin` files were saved HTML pages) | All attachments + the solicitation PDF downloaded and verified |
| Headless, blocked by Cloudflare | Visible Chrome with a persistent profile; you click the check once if asked |
| Re-scrapes everything every run | Resumable; re-captures only new or changed projects (`lastUpdatedAt`) |

## Datapoints captured

First full run (2026-10-17, public portal mode, 46 agencies): **297 open projects, 2,001 attachments,
all downloaded (12 GB), 0 errors.** Regenerate any time with `python opengov_scraper.py --inventory`.

**Normalized record** (`OpenGov_Events_Data.json`). The agency hides contact blocks on some projects, and
those hide flags are respected:

```
297 OpenGov records

NORMALIZED FIELDS (share of records with a value)
  Description                           100%
  Detail URL                            100%
  Details.Agency City                   100%
  Details.Department                    100%
  Details.Department Code               100%
  Details.Dept                          100%
  Details.Event End Date                100%
  Details.Event ID                      100%
  Details.Format/Type                   100%
  Details.Last Updated                  100%
  Details.Listed                        100%
  Details.Published Date                100%
  Details.State                         100%
  Details.Status                        100%
  Event Name                            100%
  Raw Path                              100%
  Scraped At                            100%
  Source                                100%
  Submission Requirements               100%
  Solicitation Text                     100%
  Details.Vendors Notified               99%
  Attachments                            97%
  Details.Solicitation Number            92%
  Details.Question Deadline              91%
  Details.Sealed Bid                     88%
  Commodity Codes                        80%
  Contact Information.Address            74%
  Contact Information.Contact Name       74%
  Contact Information.Email              74%
  Contact Information.Phone              74%
  Contact Information.Title              74%
  Evaluation Criteria                    49%
  Price Items                            48%
  Details.Addenda                        42%
  Timeline                               40%
  Procurement Contact.Email              36%
  Procurement Contact.Name               36%
  Procurement Contact.Phone              36%
  Procurement Contact.Title              36%
  Details.Pre-Bid Meeting                32%
  Details.Q&A Response Deadline          29%
  UNSPSC Codes                           16%
```

Beyond the Cal eProcure-compatible keys, each record adds `Procurement Contact`, `Commodity Codes` (each tagged
NIGP / NAICS / UNSPSC; UNSPSC codes are also copied into `UNSPSC Codes`), `Timeline`, `Evaluation Criteria`
(scoring weights), `Price Items` (bid line items), `Submission Requirements`, and `Solicitation Text` (the full
RFP/IFB body, which OpenGov stores as structured sections rather than only as a PDF).

**Raw OpenGov project object** (`raw/<id>.json`), every key that held data:

```
RAW OPENGOV PROJECT FIELDS: 118 distinct keys with data across 297 projects
  auctionMaxFractionDigits(297), contactAddress1(297), contactCity(297), contactCountry(297), contactCountryCode(297), contactDisplayName(297), contactEmail(297), contactFirstName(297), contactFullName(297), contactInitials(297), contactLastName(297), contactPhone(297), contactPhoneComplete(297), contactPhoneCountry(297), contactState(297), contactTitle(297), contactZipCode(297), contact_id(297), created_at(297), department(297), departmentHead(297), departmentHeadTitle(297), departmentName(297), department_id(297), government(297), government_id(297), id(297), lastUpdatedAt(297), postedAt(297), poster_id(297), procurementAddress1(297), procurementCity(297), procurementCountry(297), procurementCountryCode(297), procurementDisplayName(297), procurementEmail(297), procurementFirstName(297), procurementFullName(297), procurementInitials(297), procurementLastName(297), procurementPhone(297), procurementPhoneComplete(297), procurementPhoneCountry(297), procurementState(297), procurementTitle(297), procurementZipCode(297), procurement_contact_id(297), projectSections(297), proposalDeadline(297), questionnaires(297), rawSummary(297), releaseProjectDate(297), status(297), summary(297), template(297), template_id(297), timelineConfig(297), title(297), type(297), updated_at(297), user(297), user_id(297), wasPosted(297), criteria(296), notifyVendorsOnPosting(295), notifiedVendors(294), subscribersNotified(287), qaEnabled(277), upfrontQuestions(276), financialId(272), attachments(271), qaDeadline(270), questionLogics(268), hasSealedBid(261), showPlanholders(255), streamingRefreshDate(251), sectionDescriptions(242), categories(238), documentAttachment(226), document_attachment_id(226), hideProcurementContact(189), background(180), rawBackground(180), evaluationPhases(148), standard_document_id(147), priceTables(143), addendums(124), timelines(119), lastAddendum(106), preProposalDate(94), preProposalLocation(94), preProposalText(94), postScheduledAt(89), qaResponseDeadline(87), hideContactAddress(86), hideContact(78), intake_id(67), contactAddress2(66), procurementAddress2(59), isAutoBidUnsealed(53), contractorSelectedDate(51), isPublicBidPricingResult(49), showBids(39), showBidsWithPricing(39), recordCloseDate(35), isPublicBidResult(32), useSectionDividers(18), contactPhoneExt(12), procurementPhoneExt(11), isPublicBidTotalOnly(9), hideTimeline(5), allowSubstitutionRequests(5), project_association_id(3), isBidPricingUnsealed(3), isBidUnsealed(3), useManualNumbering(2), isEmergency(2), solicitationClosedDate(2)
```

```
ATTACHMENTS: 2001 files, 2001 downloaded, 12196 MB, 0 errors
```


## In the pipeline

OpenGov bids flow through the same silver schema as every source and are always labelled **OpenGov · Local**:

| Column | Cal eProcure | OpenGov |
|---|---|---|
| `events.id` | `0000040342` | `OG-301748` (prefixed, so IDs never collide) |
| `events.source` / `level` | `Cal eProcure` / `State` | `OpenGov` / `Local` |
| `events.extra` | empty | solicitation #, Q&A deadline, procurement contact, code sets, evaluation criteria, price items, submission requirements |
| `unspsc` | UNSPSC | UNSPSC codes when the agency used them (16% of projects) |
| `attachments` + `chunks` | PDFs/DOCX/XLSX/ZIP | the same, plus the structured solicitation text as indexed text |

Why merge rather than keep them separate: STRONGERbuilt's job (find bids, analyze them, match certified firms,
chase deadlines) is the same for a county IFB as for a state IFB. One schema means the AI analysis, search, Ask,
Client Match and the 21k-firm Bid Matching all work on OpenGov bids with no extra code. The differences are kept
as data, not as separate systems:
- **Level:** state SB/DVBE preferences don't automatically apply to cities and counties. The matcher labels a
  local small-business preference as "Agency small-business preference (see bid)" rather than the state 5%, and
  only when the documents mention one. OpenGov bids with federal funding (about half) can carry DBE goals.
- **Codes:** NIGP and NAICS titles are used as search text; UNSPSC codes feed the commodity matcher directly.
- **Raw:** the complete OpenGov object is kept in `data/bronze/opengov/raw/` so nothing is lost if a field becomes useful later.
