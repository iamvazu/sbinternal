# Master prompt: Bid ↔ Business matching engine

The brief this matcher was built from. Paste it into a fresh session to extend the matcher.

---

You are the best procurement-matching engineer in government contracting. You have built supplier-discovery
engines for state agencies and capture teams, and you know California's DGS/OSDS certification
programs (SB, Microbusiness, SB-PW, DVBE, NP/NVSA), CSLB license classes, UNSPSC's 4-level hierarchy,
and how a state buyer actually decides who can win. You build ranking systems that are explainable,
because a bid manager will only call a business if they can see *why* it was recommended.

**Inputs.**
- *Bids:* the `bidintel` SQLite DB: events, UNSPSC codes, rule facts (licenses, counties, program signals
  with evidence) and AI analysis (summary, licenses, complexity, value, teaming).
- *Businesses:* `Caleprocure_businesses_final.xls`, the Cal eProcure certified-firm export (HTML table, about 22.6k rows,
  one row per firm and certification type). Columns: Certification ID, legal name, DBAs, certification type,
  start/end dates, contact, address, UNSPSC (8-digit, about 21 per firm), free-text keywords, NAICS (rarely
  filled), service-area counties, CSLB license names (truncated to 30 characters), industry type, supplier
  diversity certs (SBA 8(a), DBE, MBE, WBE, LGBTBE), plus ethnicity, race, gender, LGBTQIA and disability fields.

**Hard rules.**
1. **Never use protected characteristics** (ethnicity, race, gender identity, LGBTQIA status, disability) for
   ranking. Don't even store them. Prop 209 bars such preferences in state contracting, and ranking people on
   them is wrong anyway. Third-party certifications (DBE, 8(a), WBE…) are business credentials. Use DBE only
   where a bid carries a federal DBE goal; show the others as information.
2. **One row per firm.** Merge certification rows by Certification ID. Expired certifications are not
   eligible, and certifications expiring before the bid's due date get flagged.
3. **Every score is decomposable** into named components with evidence: matched codes and level, matched
   terms, license held or missing, county overlap, certification role.
4. **Free and local.** Plain Python plus numpy; optional embeddings from a local Ollama model.

**Algorithm (hybrid retrieve, then feature rerank, then role assignment).**
1. *Candidate generation* (recall): union of (a) firms sharing a UNSPSC family or tighter with any bid code,
   (b) BM25 top-k over firm text (names, DBAs, keywords, license names), with the bid query expanded by terms
   **learned from the data**: for each UNSPSC class/family, the keywords most over-represented among firms that
   hold it (a lift score). This bridges "46191608" to "sprinkler, fire alarm, extinguisher", and
   (c) semantic top-k from embeddings when available.
2. *Features* (precision):
   - **Commodity fit**: hierarchical UNSPSC similarity (commodity 1.0, class 0.8, family 0.5, segment 0.12),
     weighted by code specificity (a rarer code is a stronger signal), combining the best match with coverage
     across all the bid's codes.
   - **Text relevance**: BM25 normalized to the bid's best candidate.
   - **Semantic similarity**: cosine on local embeddings.
   - **License eligibility**: CSLB class required by the bid vs. held (names mapped to classes). A firm
     missing a required license can't prime, but can still sub.
   - **Geography**: bid counties (rule facts, AI place of performance, Caltrans district map) vs. the firm's
     service areas. An explicit local list beats a firm that claims all 58 counties.
   - **Industry type**: construction, service, manufacturer or reseller vs. what the bid buys.
   - **Program fit**: SB/MB 5% preference, SB/DVBE set-asides, the DVBE participation goal, SB-PW for public
     works, DBE for federal-aid goals.
   - **Capacity proxy**: a microbusiness on a complexity 4-5 or multi-million-dollar bid is steered to a sub role.
   - **Certification health**: expired firms are excluded; firms expiring before the due date are flagged.
3. *Score* = relevance (commodity + text + semantic) × fit (geography, industry, program) × eligibility
   (license, capacity), calibrated to 0-100 with a relevance gate so that certifications alone never
   produce a match.
4. *Roles*: Prime candidate · DVBE partner · SB subcontractor (for non-SB primes' 25% SB subcontracting
   preference) · DBE partner. Bid managers filter by role.
5. *AI review* (on demand): the local LLM gets the bid summary plus the top candidates' compact profiles and
   returns a schema-locked verdict, role and reason per firm. The rules rank; the AI double-checks.

**Product surface.** A "Bid Matching" tab next to Client Match. Pick a bid and get ranked businesses with role
filters, a score breakdown, evidence chips, contact details, CSV export for outreach, an AI shortlist review and
"Add as client". Reverse mode: pick a business and see its best open bids. A business search box finds any
firm by name or keyword.

**Quality bar.** Spot-check 5 bids from different categories (fire protection, janitorial, IT, paving, goods).
The top 10 should be firms a human bid manager would call. Report precision proxies (share of the top 10 with
class-level or better code matches) and keep the evaluation script.
