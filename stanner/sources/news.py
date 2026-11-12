"""Google News RSS per artist: announcements, collabs, talk/variety show appearances."""

from calendar import timegm
from datetime import datetime, timezone

import feedparser

from .. import config
from ..classify import classify
from ..http import RateLimiter, get
from . import event

NAME = "news"
_limit = RateLimiter(2.0)


_overrides = None


def query_for(a: dict) -> str:
    global _overrides
    if _overrides is None:
        _overrides = config.load_follows()["overrides"]
    if q := _overrides.get(a["id"], {}).get("news_query"):
        return q
    # Members' names are often ambiguous ("DK", "Joshua"), so pin them to their parent group.
    if a.get("depth", 0) > 0 and a.get("parent_name"):
        return f'"{a["name"]}" "{a["parent_name"]}"'
    return f'"{a["name"]}"'


def fetch(artists: list[dict], since) -> list[dict]:
    rows = []
    for a in artists:
        r = get("https://news.google.com/rss/search", limiter=_limit,
                params={"q": query_for(a) + " when:7d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
        for e in feedparser.parse(r.content).entries:
            if not e.get("published_parsed"):
                continue
            published = datetime.fromtimestamp(timegm(e.published_parsed), tz=timezone.utc)
            if since and published < since:
                continue
            # Same article can match several artists; key by artist so each gets it in their feed.
            rows.append(event(NAME, f"{a['id']}:{e.get('id') or e.link}", a["id"], classify(e.title),
                              e.title, e.link, published_at=published,
                              payload={"outlet": (e.get("source") or {}).get("title"), "query": query_for(a)}))
    return rows
