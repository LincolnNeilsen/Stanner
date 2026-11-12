"""setlist.fm: past concerts, keyed directly by MusicBrainz ID."""

from datetime import datetime, timezone

from .. import config
from ..http import RateLimiter, get
from . import event

NAME = "setlistfm"
API = "https://api.setlist.fm/rest/1.0"
_limit = RateLimiter(0.6)  # ~2 req/s allowed
SEED_MAX_PAGES = 25  # 20 setlists per page


def _headers():
    if not config.SETLIST_FM_API_KEY:
        raise RuntimeError("SETLIST_FM_API_KEY is not set")
    return {"x-api-key": config.SETLIST_FM_API_KEY, "Accept": "application/json"}


def page(mbid: str, p: int) -> dict | None:
    r = get(f"{API}/artist/{mbid}/setlists", params={"p": p}, headers=_headers(), limiter=_limit, ok404=True)
    return r.json() if r is not None else None


def _date(s: dict) -> datetime:
    return datetime.strptime(s["eventDate"], "%d-%m-%Y").replace(tzinfo=timezone.utc)


def to_event(s: dict, artist_id: str) -> dict:
    venue = s.get("venue") or {}
    city = venue.get("city") or {}
    title = f"{(s.get('tour') or {}).get('name') or 'Concert'} @ {venue.get('name', '?')}, {city.get('name', '?')}"
    when = _date(s)
    n_songs = sum(len(x.get("song", [])) for x in (s.get("sets") or {}).get("set", []))
    return event(NAME, s["id"], artist_id, "concert", title, s.get("url"), event_time=when, published_at=when,
                 payload={"venue": venue.get("name"), "city": city.get("name"),
                          "country": (city.get("country") or {}).get("code"),
                          "tour": (s.get("tour") or {}).get("name"), "songs": n_songs})


def fetch(artists: list[dict], since) -> list[dict]:
    """Newest first; page until we pass the watermark."""
    rows = []
    for a in artists:
        p = 1
        while True:
            d = page(a["id"], p)
            if not d or not d.get("setlist"):
                break
            older = False
            for s in d["setlist"]:
                if since and _date(s) < since:
                    older = True
                    break
                rows.append(to_event(s, a["id"]))
            # No watermark yet (seed not run): only take the newest page.
            if older or not since or p * d["itemsPerPage"] >= d["total"]:
                break
            p += 1
    return rows


def seed_fetch(mbid: str) -> list[dict]:
    out = []
    for p in range(1, SEED_MAX_PAGES + 1):
        d = page(mbid, p)
        if not d or not d.get("setlist"):
            break
        out += d["setlist"]
        if p * d["itemsPerPage"] >= d["total"]:
            break
    return out
