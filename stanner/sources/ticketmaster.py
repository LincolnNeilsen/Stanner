"""Ticketmaster Discovery: upcoming concerts. One call per artist (5k/day budget)."""

from datetime import datetime

from .. import config
from ..http import RateLimiter, get
from . import event

NAME = "ticketmaster"
API = "https://app.ticketmaster.com/discovery/v2"
_limit = RateLimiter(0.25)  # 5 req/s


def _when(ev) -> datetime | None:
    start = (ev.get("dates") or {}).get("start") or {}
    if start.get("dateTime"):
        return datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
    if start.get("localDate"):
        return datetime.fromisoformat(start["localDate"] + "T00:00:00+00:00")
    return None


def fetch(artists: list[dict], since) -> list[dict]:
    """Upcoming events have no 'since' filter; every run re-reads them and dedupe keeps the first."""
    if not config.TICKETMASTER_API_KEY:
        raise RuntimeError("TICKETMASTER_API_KEY is not set")
    from ..bq import now

    rows = []
    for a in artists:
        d = get(f"{API}/events.json", limiter=_limit, params={
            "apikey": config.TICKETMASTER_API_KEY, "keyword": a["name"],
            "classificationName": "music", "size": 200, "sort": "date,asc",
        }).json()
        for ev in (d.get("_embedded") or {}).get("events", []):
            names = {x["name"].casefold() for x in (ev.get("_embedded") or {}).get("attractions", [])}
            if a["name"].casefold() not in names:
                continue  # keyword search is fuzzy; require an exact attraction match
            venue = ((ev.get("_embedded") or {}).get("venues") or [{}])[0]
            rows.append(event(NAME, f"{a['id']}:{ev['id']}", a["id"], "concert", ev["name"], ev.get("url"),
                              event_time=_when(ev), published_at=now(), payload={
                                  "venue": venue.get("name"),
                                  "city": (venue.get("city") or {}).get("name"),
                                  "country": (venue.get("country") or {}).get("countryCode"),
                                  "status": ((ev.get("dates") or {}).get("status") or {}).get("code"),
                              }))
    return rows
