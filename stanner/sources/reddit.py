"""Fan subreddit RSS (subreddit set per artist in config/follows.toml)."""

from calendar import timegm
from datetime import datetime, timezone

import feedparser

from ..classify import classify
from ..http import RateLimiter, get
from . import event

NAME = "reddit"
_limit = RateLimiter(3.0)


def fetch(artists: list[dict], since) -> list[dict]:
    rows = []
    for a in artists:
        sub = (a.get("subreddit") or "").removeprefix("r/").strip()
        if not sub:
            continue
        r = get(f"https://www.reddit.com/r/{sub}/new/.rss", limiter=_limit, ok404=True)
        if r is None:
            continue
        for e in feedparser.parse(r.content).entries:
            ts = e.get("updated_parsed") or e.get("published_parsed")
            if not ts:
                continue
            published = datetime.fromtimestamp(timegm(ts), tz=timezone.utc)
            if since and published < since:
                continue
            flair = " ".join(t.get("term", "") for t in e.get("tags", []))
            rows.append(event(NAME, f"{a['id']}:{e.id}", a["id"], classify(f"{flair} {e.title}"),
                              e.title, e.link, published_at=published,
                              payload={"subreddit": sub, "author": e.get("author")}))
    return rows
