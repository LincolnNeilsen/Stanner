import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
FOLLOWS_FILE = ROOT / "config" / "follows.toml"

GCP_PROJECT = os.getenv("GCP_PROJECT", "")
BQ_DATASET = os.getenv("BQ_DATASET", "artist_pipeline")
BQ_LOCATION = os.getenv("BQ_LOCATION", "US")
# The BigQuery sandbox does not allow DML (MERGE/INSERT). With billing enabled, set BQ_USE_DML=true.
BQ_USE_DML = os.getenv("BQ_USE_DML", "false").lower() == "true"

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
TICKETMASTER_API_KEY = os.getenv("TICKETMASTER_API_KEY", "")
SETLIST_FM_API_KEY = os.getenv("SETLIST_FM_API_KEY", "")
USER_AGENT = os.getenv(
    "MUSICBRAINZ_USER_AGENT", "Stanner/0.1 (https://github.com/LincolnNeilsen/Stanner)"
)

# Overlap applied to every watermark so late-arriving items are re-seen (dedupe makes this safe).
OVERLAP_DAYS = 1
# Safety cap on the MusicBrainz tree walk.
MAX_ARTISTS = int(os.getenv("MAX_ARTISTS", "300"))
MAX_DEPTH = 6


def load_follows() -> dict:
    """Returns {"follow": [mbid...], "overrides": {mbid: {...}}} from config/follows.toml."""
    if not FOLLOWS_FILE.exists():
        return {"follow": [], "overrides": {}}
    data = tomllib.loads(FOLLOWS_FILE.read_text(encoding="utf-8"))
    follow, overrides = [], {}
    for a in data.get("artist", []):
        mbid = a["mbid"].strip()
        if a.get("follow", True):
            follow.append(mbid)
        overrides[mbid] = {k: v for k, v in a.items() if k not in ("mbid", "follow")}
    return {"follow": follow, "overrides": overrides}
