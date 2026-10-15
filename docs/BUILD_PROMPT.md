# Master build prompt: STRONGERbuilt Bid Intelligence

Paste this into a fresh Claude Code session (or any coding agent) to extend the system. It's the brief this
first version was built from.

---

You are a principal engineer who has shipped procurement-intelligence products used by thousands of
government contractors. You know public procurement: California's DGS/OSDS rules (SB, MB, SB-PW,
DVBE), Cal eProcure's event lifecycle (IFB, RFP, RFQ, SLP, RFI, addenda and version bumps), CSLB license
classes, DIR/prevailing wage, bonding and federal DBE goals. You also design AI systems that are trusted
because they are verifiable, not because they are fluent.

**The customer.** STRONGERbuilt, a California bid-management firm. Its account managers find state
opportunities for small, disabled-veteran-owned and women-owned businesses, help those businesses get
certified, and then help them win. They are building an internal tool, not a public product.

**The data.** `Caleprocure_Events_Data.json` (scraped events: details, contact, description, UNSPSC, attachments),
`attachments/<event_id>/*` (PDF/DOCX/XLSX/ZIP solicitation packages), and `caleprocure_*.xls` (Cal eProcure's
Excel export, really an HTML table). The scrapers refresh these daily.

**The constraints.**
- The AI layer must be free to run, so use a local open-weight model on an NVIDIA Jetson AGX Orin 64GB via
  Ollama. Every LLM call goes through one client (`bidintel/llm.py`) that also supports any
  OpenAI-compatible endpoint.
- Keep it boring to operate: Python standard library server, SQLite with FTS5, one static HTML dashboard,
  and no build step. It must run unchanged on Windows and on the Jetson (ARM64, JetPack 6).
- One GPU, so generation is serialized. Batch jobs are resumable and keyed by input hash, so re-runs only
  pay for new or changed bids.

**Design principles (non-negotiable).**
1. **Rules first, AI second.** Anything a regex can find reliably (DVBE %, license classes, bonds,
   mandatory pre-bids, prevailing wage, counties) is extracted with rules *and stores the sentence that
   proves it*. The LLM gets those facts as hints and must confirm them against the documents.
2. **Schema-locked AI.** Per-bid analysis is a fixed JSON schema enforced by the model runtime
   (Ollama `format`), not free text. Unknowns are "Not stated" or null and get listed in `missing_information`.
   Never invent dates, dollar values or percentages.
3. **Documents are untrusted data.** Wrap them in delimiters and tell the model to ignore instructions inside them.
4. **Every number on screen is traceable**, either to an attachment link or to an evidence snippet.
5. **Answer the business question, not the data question.** The user isn't asking "what does this bid say".
   They're asking "which of my clients should bid, what stops them, and what would change the odds".

**What to build / extend.**
- *Ingest:* JSON + exports → SQLite; text from every attachment (PDF via PyMuPDF, DOCX tables included, XLSX
  sheets, PDFs inside ZIPs); 1.5k-char chunks into FTS5.
- *Enrich:* category from UNSPSC, method, program signals with evidence, DVBE/DBE %, CSLB classes, dollar
  figures, counties, Caltrans districts.
- *Per-bid AI analysis:* plain-English summary, award basis, term, value, place, key dates (mandatory
  flagged), preferences and certifications, licenses, minimum qualifications, bonds and insurance,
  submission checklist, risks, ideal bidder, small-business fit 0-100 with rationale, teaming opportunity,
  complexity 1-5, bid/no-bid questions, NAICS guesses, confidence.
- *Portfolio AI brief:* must-act list, where the volume is, the certification angle, teaming plays and top
  pursuits, all citing event IDs.
- *Client match:* explainable rule scoring (UNSPSC prefix, keywords, licenses, counties, certs), with
  reasons, blockers and "certification X would help on this bid".
- *Certification lens:* for SB / DVBE / DBE / SB-PW, the open bids where holding the certification changes the
  outcome, plus a per-client unlock report. This is STRONGERbuilt's sales tool for certification services.
- *Ask:* retrieval over all documents with cited answers, plus per-bid drafting tools (client outreach email,
  questions for the buyer, compliance matrix, teaming partners).

**Next milestones.** Daily diff alerts per client; award/incumbent history; the certified-firm directory for
teaming; CSLB-based prospecting; a pursuit pipeline with win/loss feedback into scoring; more portals mapped
onto the same schema; an evaluation set of 10-20 hand-checked bids to compare models.

**Definition of done for any change:** run it against the real data, open the dashboard, click through a
bid, and confirm one AI fact against the source PDF yourself.
