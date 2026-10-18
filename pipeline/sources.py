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
