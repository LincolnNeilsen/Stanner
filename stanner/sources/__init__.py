"""Each source module exposes:

    NAME: str
    fetch(artists, since) -> list[event dict]      # live path
    (optional) seed_fetch(artists) / seed_events()  # one-time bulk path, see stanner/seed.py
"""

from datetime import datetime


def event(source, source_id, artist_id, type_, title, url, *, published_at=None, event_time=None,
          payload=None) -> dict:
    from ..bq import dumps, now

    return {
        "source": source,
        "source_id": str(source_id),
        "artist_id": artist_id,
        "type": type_,
        "title": (title or "")[:1000],
        "url": url,
        "event_time": event_time,
        "published_at": published_at,
        "ingested_at": now(),
        "payload": dumps(payload or {}),
    }


def parse_date(s: str | None) -> datetime | None:
    """MusicBrainz-style partial dates: YYYY, YYYY-MM, YYYY-MM-DD."""
    from datetime import timezone

    if not s:
        return None
    parts = (s.split("-") + ["01", "01"])[:3]
    try:
        return datetime(int(parts[0]), int(parts[1] or 1), int(parts[2] or 1), tzinfo=timezone.utc)
    except ValueError:
        return None
