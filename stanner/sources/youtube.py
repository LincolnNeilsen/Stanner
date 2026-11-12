"""YouTube: channel RSS for live (no key), Data API playlistItems for the back catalog (seed)."""

from datetime import datetime

import feedparser

from .. import config
from ..classify import classify_video
from ..http import RateLimiter, get
from . import event

NAME = "youtube"
API = "https://www.googleapis.com/youtube/v3"
_limit = RateLimiter(0.5)


def resolve_handle(handle: str) -> str | None:
    """'@name', 'user/name' or 'c/name' -> UC... channel id (needs YOUTUBE_API_KEY)."""
    if not config.YOUTUBE_API_KEY:
        return None
    if handle.startswith("@"):
        params = {"forHandle": handle}
    elif handle.startswith("user/"):
        params = {"forUsername": handle[5:]}
    else:
        params = {"forHandle": "@" + handle.split("/", 1)[1]}
    d = get(f"{API}/channels", params={"part": "id", "key": config.YOUTUBE_API_KEY, **params},
            limiter=_limit).json()
    items = d.get("items") or []
    return items[0]["id"] if items else None


def _event(video_id, title, published: datetime, artist_id, channel_id, extra=None):
    return event(NAME, video_id, artist_id, classify_video(title), title,
                 f"https://www.youtube.com/watch?v={video_id}", published_at=published,
                 payload={"channel_id": channel_id, **(extra or {})})


def fetch(artists: list[dict], since) -> list[dict]:
    rows = []
    for a in artists:
        ch = a.get("youtube_channel_id")
        if not ch:
            continue
        r = get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": ch}, limiter=_limit, ok404=True)
        if r is None:
            print(f"  youtube: channel {ch} not found for {a['name']}")
            continue
        for e in feedparser.parse(r.content).entries:
            published = datetime.fromisoformat(e.published)
            if since and published < since:
                continue
            rows.append(_event(e.yt_videoid, e.title, published, a["id"], ch,
                               {"views": (e.get("media_statistics") or {}).get("views")}))
    return rows


# ---------- seed (back catalog) ----------
def seed_fetch(channel_id: str) -> list[dict]:
    """All uploads for a channel via the uploads playlist. ~1 quota unit per 50 videos."""
    if not config.YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is required for the YouTube back-catalog seed")
    playlist = "UU" + channel_id[2:]
    items, token = [], None
    while True:
        params = {"part": "snippet", "playlistId": playlist, "maxResults": 50, "key": config.YOUTUBE_API_KEY}
        if token:
            params["pageToken"] = token
        r = get(f"{API}/playlistItems", params=params, limiter=_limit, ok404=True)
        if r is None:
            return items
        d = r.json()
        items += d.get("items", [])
        token = d.get("nextPageToken")
        if not token:
            return items


def seed_events(items: list[dict], artist_id: str, channel_id: str) -> list[dict]:
    rows = []
    for it in items:
        s = it["snippet"]
        vid = s["resourceId"]["videoId"]
        published = datetime.fromisoformat(s["publishedAt"].replace("Z", "+00:00"))
        rows.append(_event(vid, s["title"], published, artist_id, channel_id))
    return rows
