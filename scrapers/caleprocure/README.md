CaleProcure Events Scraper

Part of the data pipeline: run from `scrapers/caleprocure/`. By default output goes to the bronze layer
`data/bronze/caleprocure/` (`Caleprocure_Events_Data.json` + `attachments/`). Excel exports from the portal go in
`exports/` (the targeted scraper uses the newest one). Then run `python -m pipeline.run` from the project root.
This repository contains a Selenium-based scraper for extracting detailed bid opportunity information from the California State procurement portal (CaleProcure).

Features
•	Navigates the CaleProcure event search page and iterates through event rows.
•	Opens each event’s detail page in a separate tab and extracts structured data.
•	Captures core fields: Event Name, Event ID, Dept, Format/Type, Event Version, Published Date, Event End Date.
•	Extracts Contact Information, Pre-Bid Conference details, Description text, and UNSPSC codes.
•	Follows the "View Event Package" link and collects attachment download URLs from the "Download Attachment" popup.
•	Handles common UI issues: loading screen, blocked popup notices, stale elements, and other intermittent Selenium errors.
•	Supports partial/segmented scraping using a row range argument so large runs can be split across multiple executions.
•	Writes results as a structured JSON file.

Command-Line Usage
Basic run (scrape all rows until completion or an unrecoverable error).
    python Caleprocure_events_scraper.py --out-json caleprocure_all.json
Run in headless mode:
    python Caleprocure_events_scraper.py --out-json caleprocure_all.json --headless
Limit maximum pages (for testing):
    python Caleprocure_events_scraper.py --out-json test.json --max-pages 2
Limit number of events (for testing):
    python Caleprocure_events_scraper.py --out-json test.json --limit 50

Output Format
The output JSON file is a list of objects. Each object can contain:
•	`Event Name`
•	`Details` (sub-object: Event ID, Dept, Format/Type, Event Version, Published Date, Event End Date)
•	`Contact Information` (sub-object)
•	`Pre Bid Conference` (sub-object)
•	`Description`
•	`UNSPSC Codes` (list of sub-objects)
•	`Attachments` (list of attachment URLs from Download Attachment buttons)
•	`Detail URL` (URL of the event details page)
