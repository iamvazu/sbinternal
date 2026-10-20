"""Source registry: every procurement source the pipeline knows about.

Organised by PLATFORM, not by agency: one scraper engine per platform, with the agencies as config.
(The 11 PlanetBids scrapers in the reviewed GitHub repo are byte-identical apart from the portal id,
so they become one engine + the portal list below.)

status: "active"  = scraper + adapter exist and run in the pipeline
        "planned" = verified target (URL / portal id checked 2026-10-17), engine not built yet
"""

SOURCES = {
    "caleprocure": {
        "label": "Cal eProcure", "platform": "Cal eProcure (PeopleSoft)", "level": "State", "status": "active",
        "id_prefix": "",  # keeps the existing Cal eProcure event ids (0000040342, 10A2853 ...)
        "scraper": "scrapers/caleprocure/Caleprocure_targeted_scraper.py",
        "adapter": "pipeline.adapters.caleprocure",
        "notes": "All California state agencies. Selenium; event detail + attachments + UNSPSC.",
    },
    "opengov": {
        "label": "OpenGov", "platform": "OpenGov Procurement", "level": "Local", "status": "active",
        "id_prefix": "OG-",
        "scraper": "scrapers/opengov/opengov_scraper.py --portals",
        "adapter": "pipeline.adapters.opengov",
        "notes": "CA cities, counties, transit, districts. Reads window.__data; signed-S3 attachments.",
    },
    "planetbids": {
        "label": "PlanetBids", "platform": "PlanetBids Vendor Portal", "level": "Local", "status": "blocked",
        "id_prefix": "PB-",
        "notes": "2026-10-17: AWS WAF bot control. The 'Human Verification' check can be solved by hand, but the data "
                 "API (api-external.prod.planetbids.com/papi) still returns 403 to an automated browser, so the app "
                 "renders blank. Not bypassed on purpose. Needs a sanctioned route: PlanetBids/agency data access, "
                 "vendor-account exports, or a licensed aggregator feed.",
        "url_template": "https://vendors.planetbids.com/portal/{portal_id}/bo/bo-search",
        "portals": {  # verified against the repo's code (several ids in the earlier summary were wrong)
            "14590": "City of Richmond", "46202": "City of Menlo Park", "20314": "City of Santa Rosa",
            "46106": "City of Burlingame", "46991": "City of Millbrae", "39472": "City of San Pablo",
            "45968": "County of Mendocino", "14599": "County of Stanislaus",
            "48397": "Santa Clara Valley Water District", "48421": "Bay Area Clean Water Agencies",
            "18057": "Ohlone Community College District",
        },
    },
    "caltrans_des": {
        "label": "Caltrans DES", "platform": "Caltrans Division of Engineering Services", "level": "State",
        "status": "active", "id_prefix": "CT-", "url": "https://ppmoe.dot.ca.gov",
        "scraper": "scrapers/caltrans_des/caltrans_des_scraper.py",
        "adapter": "pipeline.adapters.caltrans_des",
        "notes": "Highway/bridge construction contracts. Public ServiceNow JSON (no browser). Bid documents need a "
                 "free Caltrans contractor login and are listed, not downloaded.",
    },
    "csu": {
        "label": "CSU", "platform": "Jaggaer (SciQuest)", "level": "State", "status": "active", "id_prefix": "CSU-",
        "url": "https://bids.sciquest.com/apps/Router/PublicEvent?CustomerOrg=CalState",
        "scraper": "scrapers/csu/csu_scraper.py", "adapter": "pipeline.adapters.csu",
        "notes": "All 23 CSU campuses + Chancellor's Office. Plain HTTP; event PDF downloaded. Specs/Q&A need a CSU supplier login.",
    },
    "bonfire": {
        "label": "Bonfire", "platform": "Bonfire Hub (Euna)", "level": "Local", "status": "active", "id_prefix": "BF-",
        "portals": {"mtc": "Metropolitan Transportation Commission"},
        "url_template": "https://{portal_id}.bonfirehub.com/portal/",
        "scraper": "scrapers/bonfire/bonfire_scraper.py", "adapter": "pipeline.adapters.bonfire",
        "notes": "Listing = public JSON; detail pages behind Cloudflare -> visible Chrome, human-in-the-loop. "
                 "Document files need a Euna Supplier Network login. Add agencies to 'portals'.",
    },
    # ---- other states (official state portals; sources found via publicbidtracker.com/about) ----------------
    # California SB/DVBE certifications do not apply here; each state runs its own program (see "program").
    "oregonbuys": {
        "label": "OregonBuys", "platform": "Periscope BuySpeed", "level": "State", "state": "OR", "status": "active",
        "id_prefix": "OR-", "url": "https://oregonbuys.gov/bso/view/search/external/advancedSearchBid.xhtml?openBids=true",
        "program": "COBID (MBE/WBE/SDV/ESB)",
        "scraper": "scrapers/oregonbuys/oregonbuys_scraper.py", "adapter": "pipeline.adapters.oregonbuys",
        "notes": "Oregon state agencies + cities, counties, school/transit districts. JSF/PrimeFaces paging; rate-limits "
                 "bursts (serves empty pages), so the scraper paces 2.5 s and backs off.",
    },
    "mfmp": {
        "label": "Florida MFMP", "platform": "MyFloridaMarketPlace (VBS)", "level": "State", "state": "FL",
        "status": "active", "id_prefix": "FL-", "url": "https://vendor.myfloridamarketplace.com/search/bids",
        "program": "OSD (MBE/WBE/SDVBE)",
        "scraper": "scrapers/mfmp/mfmp_scraper.py", "adapter": "pipeline.adapters.mfmp",
        "notes": "Florida state agencies. Public JSON search + detail; UNSPSC codes; public attachments.",
    },
    "tx_esbd": {
        "label": "Texas ESBD", "platform": "Texas SmartBuy (NetSuite)", "level": "State", "state": "TX",
        "status": "active", "id_prefix": "TX-", "url": "https://www.txsmartbuy.gov/esbd",
        "program": "HUB",
        "scraper": "scrapers/tx_esbd/tx_esbd_scraper.py", "adapter": "pipeline.adapters.tx_esbd",
        "notes": "Texas state agencies (numeric agency no.) + SmartBuy members (letter-prefixed = local). Public JSON services.",
    },
    "ga_gpr": {
        "label": "Georgia GPR", "platform": "Georgia Procurement Registry", "level": "State", "state": "GA",
        "status": "active", "id_prefix": "GA-", "url": "https://ssl.doas.state.ga.us/gpr/",
        "program": "GOSBO small/minority business",
        "scraper": "scrapers/ga_gpr/ga_gpr_scraper.py", "adapter": "pipeline.adapters.ga_gpr",
        "notes": "Georgia state agencies + counties, cities, K-12. DataTables JSON listing, HTML detail, public files.",
    },
    "eva": {
        "label": "Virginia eVA", "platform": "eVA (CGI)", "level": "State", "state": "VA", "status": "active",
        "id_prefix": "VA-", "url": "https://mvendor.cgieva.com/Vendor/public/AllOpportunities.jsp",
        "program": "SBSD SWaM",
        "scraper": "scrapers/eva/eva_scraper.py", "adapter": "pipeline.adapters.eva",
        "notes": "Virginia agencies, universities, localities. Public Solr feed read from a visible Chrome (AWS firewall "
                 "admits real browsers only). Detail pages/documents are reCAPTCHA-gated: not scraped, linked instead.",
    },
    "pa_emarketplace": {
        "label": "PA eMarketplace", "platform": "PA eMarketplace (ASP.NET)", "level": "State", "state": "PA",
        "status": "active", "id_prefix": "PA-", "url": "https://www.emarketplace.state.pa.us/Search.aspx",
        "program": "DGS Small Diverse / Small Business",
        "scraper": "scrapers/pa_emarketplace/pa_emarketplace_scraper.py", "adapter": "pipeline.adapters.pa_emarketplace",
        "notes": "Commonwealth agencies. WebForms postback (all rows), detail page, public FileDownload attachments.",
    },
    "bidnet": {
        "label": "BidNet Direct", "platform": "BidNet Direct", "level": "Local", "status": "planned", "id_prefix": "BN-",
        "portals": {"cityofsantaclara": "City of Santa Clara", "cityofukiah": "City of Ukiah"},
        "url_template": "https://www.bidnetdirect.com/california/{portal_id}/",
    },
    "biddingo": {
        "label": "Biddingo", "platform": "Biddingo", "level": "Local", "status": "planned", "id_prefix": "BG-",
        "portals": {"sanjose": "City of San Jose", "santaclaracounty": "County of Santa Clara"},
        "notes": "biddingo.com/sanjose and biddingousa.com/santaclaracounty",
    },
    "questcdn": {
        "label": "QuestCDN", "platform": "QuestCDN", "level": "Local", "status": "planned", "id_prefix": "QC-",
        "portals": {"eldorado": "County of El Dorado"}, "url": "https://qcpi.questcdn.com/cdn/posting/",
    },
    "civicplus": {
        "label": "CivicPlus Bids", "platform": "CivicPlus (Bids.aspx)", "level": "Local", "status": "planned",
        "id_prefix": "CP-",
        "portals": {"https://www.cityoforinda.gov/Bids.aspx": "City of Orinda",
                    "https://www.lakecountyca.gov/bids.aspx": "County of Lake",
                    "https://humboldtgov.org/Bids.aspx": "County of Humboldt",
                    "https://www.napacounty.gov/Bids.aspx": "County of Napa (also on OpenGov: countyofnapa)"},
    },
    "agency_portals": {
        "label": "Agency portals", "platform": "Single-agency systems", "level": "Local", "status": "planned",
        "id_prefix": "AG-",
        "portals": {"https://placer.bidsandtenders.net": "County of Placer (Bids&Tenders)",
                    "https://www.beaconbid.com/solicitations/yolo-county/open": "County of Yolo (BeaconBid)",
                    "https://www.cccplanroom.com": "County of Contra Costa (planroom)",
                    "https://esupplier.sonomacounty.ca.gov": "County of Sonoma (PeopleSoft eSupplier)"},
    },
}


def active():
    return {k: v for k, v in SOURCES.items() if v["status"] == "active"}


def label_for(source_key):
    return SOURCES.get(source_key, {}).get("label", source_key)


def state_of(source_key):
    return SOURCES.get(source_key, {}).get("state", "CA")
