# Turning Cal eProcure data into wins for STRONGERbuilt clients

The scraper gives you *opportunities*. A bid-management business also needs to know *who should bid*,
*what stops them*, and *what certification would change the odds*. This is the data model and the
playbook for getting there, plus what is already built in `bidintel/`.

## 1. The certification landscape (get this right with clients)

| Certification | Who issues it | What it gets a client on state work |
|---|---|---|
| **SB**: Small Business | DGS Office of Small Business & DVBE Services (OSDS), free, online at caleprocure.ca.gov | 5% bid preference (capped per bid), access to SB/DVBE Option buys under the informal threshold, counts toward agencies' 25% SB goal |
| **MB**: Microbusiness | DGS OSDS | Same preferences as SB; the smallest firms |
| **SB-PW**: SB for Public Works | DGS OSDS | The SB path for construction primes and subs |
| **DVBE**: Disabled Veteran Business Enterprise | DGS OSDS | 3% DVBE participation goal on most contracts (primes must find DVBE partners), DVBE incentive on many bids |
| **DBE**: Disadvantaged Business Enterprise | CUCP (Caltrans + local agencies) | Counts toward DBE goals on **federally funded** work (Caltrans, transit, airports) |
| **WBE / MBE / DVBE** (utility program) | CPUC Supplier Clearinghouse (GO 156) | PG&E, SCE, SDG&E, SoCalGas supplier-diversity spend. Private utilities, not state agencies |
| **WOSB / EDWOSB** | U.S. SBA | Federal set-asides on SAM.gov |
| **WBENC** | WBEC-West / regional partner | Corporate supply chains |

**There is no "woman-owned business" certification or preference for California state contracts.** Prop 209
(CA Const. art. I §31) bars sex- and race-based preferences in state contracting. So a woman-owned client gets
its state-contract advantage from **SB/MB** status. Its women-owned status helps through the CPUC Clearinghouse
(utilities), SBA WOSB (federal) and WBENC (corporate). DBE is a separate track. In October 2025, USDOT issued
an interim final rule that removed the race- and sex-based presumption of disadvantage. Firms must now show
disadvantage individually, and existing DBEs are being re-evaluated. Check where that rule stands before you
tell any client DBE will help.

Size standards and dollar caps (SB gross-receipts limit, preference caps, the SB/DVBE Option threshold) are
adjusted periodically. Pull current numbers from DGS OSDS before quoting them to a client.

**Your service menu writes itself from this table:** certification readiness → SB/MB/DVBE application →
CPUC/WOSB/WBENC for women-owned clients → bid-ready profile → matched opportunities → pursuit support.

## 2. The data model (what `bidintel/` stores)

```
events ──< attachments (full text)        raw opportunity + every document, text-extracted
   │──< unspsc                            commodity codes (what's being bought)
   │──< chunks (FTS5)                     searchable 1.5k-char passages for retrieval
   │── facts                              rule-extracted signals + the sentence that proves each
   │── analysis                           AI JSON: summary, dates, licenses, quals, fit, risks, checklist
clients (clients.json)                    certifications, UNSPSC prefixes, keywords, CSLB classes, counties
match (computed)                          score + reasons + blockers + "certification would help"
```

The key move is to **put opportunities and clients in the same vocabulary**:

1. **UNSPSC is the shared key.** Cal eProcure tags each bid with UNSPSC codes, and SB/DVBE certification
   profiles also list UNSPSC/keywords. Record each client's codes from their certification profile
   (or pick them during intake). Prefix matching gives you segment → family → class precision.
2. **CSLB license classes** (A, B, C-10, C-16…) are hard gates for construction. Extracted per bid, stored per client.
3. **Geography.** Counties and Caltrans districts are extracted from the text and matched against each
   client's service area.
4. **Program signals.** SB preference, DVBE %, DBE goal, prevailing wage, bonds and mandatory pre-bids
   decide whether a client *can* bid, not just whether they *want* to.

## 3. What to add next (in order of value)

1. **Daily diffs and alerts.** Keep `first_seen`/`last_seen` (already stored) and the event version. Flag
   *new*, *addendum posted* and *closing in 72h* per client. A morning email or Teams digest per account
   manager is the feature clients will notice.
2. **Award and incumbent data.** Scrape Cal eProcure contract awards / bid results for the same departments
   and UNSPSC codes. "Who won last time, at what price" turns a guess into pricing strategy.
3. **Certified-firm directory.** The public SB/DVBE certified firm search on Cal eProcure. Use it to
   (a) find DVBE/SB teaming partners for clients bidding as prime, and (b) check client certs automatically.
4. **Prospecting list (lead gen for STRONGERbuilt itself).** CSLB publishes licensee data. Licensed
   contractors in the classes and counties where bids are flowing, who are *not* in the SB/DVBE directory,
   are your certification-service prospects. The Certification Lens shows them what certification would unlock.
5. **Pursuit pipeline.** Add a `pursuits` table (client, event, stage: matched → go/no-go → writing →
   submitted → won/lost, owner, notes). Win/loss outcomes then feed back into match scoring.
6. **More portals.** The same schema absorbs Caltrans construction bids (dot.ca.gov), UC/CSU, BidSync/PlanetBids
   cities and counties, and SAM.gov. Map each portal's fields onto `events`, then reuse everything else.

## 4. How to use the tool day to day

- **Morning:** open *Command Center*. Check the must-act list, the deadline runway and the AI brief.
- **Account managers:** open *Client Match*, pick a client, then open the top bids and click *Client outreach email*.
- **Bid writers:** in the bid drawer, use the submission checklist, compliance matrix, questions for the buyer
  and the rule signals with source text.
- **Business development:** use the *Certification Lens* and its unlock report ("Client X matches 23 open bids;
  SB status would help on 18") to sell certification services with evidence.
