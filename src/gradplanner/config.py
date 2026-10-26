"""Project-wide settings: paths and the Bulletin year we are working from."""

from pathlib import Path

# The Bulletin edition all data comes from. Change this (and re-scrape) next year.
BULLETIN_YEAR = "2026-2027"
BULLETIN_BASE_URL = "https://bulletin.wfu.edu"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw" / BULLETIN_YEAR          # cached HTML pages
STAGING_DIR = DATA_DIR / "staging"                    # parsed JSON before the DB step
OVERRIDES_DIR = DATA_DIR / "overrides"                # hand-made fixes (committed to git)

# Generic, non-personal User-Agent. Do not put anyone's email or name here.
USER_AGENT = "WFU-GradPlanner-ClassProject/0.1 (educational, non-commercial)"

# Minimum seconds between two requests to the Bulletin site.
REQUEST_DELAY_SECONDS = 2.0
