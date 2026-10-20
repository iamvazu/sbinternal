"""Single source of truth for where each pipeline layer lives."""
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SCRAPERS = PROJECT / "scrapers"            # code only: one folder per source/platform
DATA = PROJECT / "data"
BRONZE = DATA / "bronze"                   # exactly what each scraper produced (JSON, attachments, raw API objects)
SILVER = DATA / "silver"                   # one canonical bid schema for every source
SILVER_BIDS = SILVER / "bids.jsonl"
SILVER_MANIFEST = SILVER / "_manifest.json"
REFERENCE = DATA / "reference"             # non-bid reference data (certified-firm directory, ...)
GOLD_APP = PROJECT / "bidintel"            # gold: bidintel.db + enrichment, AI, matching, dashboard


def rel(path) -> str:
    """Project-relative path with forward slashes (portable between Windows and the Jetson)."""
    # abspath, not resolve(): attachment folders may be symlinks to an external drive (Jetson), and the stored
    # path must stay inside the project either way
    return Path(os.path.abspath(path)).relative_to(PROJECT).as_posix()
