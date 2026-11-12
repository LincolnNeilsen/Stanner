"""Fan subreddit RSS (subreddit set per artist in config/follows.toml).

Reddit rate-limits unauthenticated RSS hard (429 after ~1 request), so all subreddits are
fetched through combined feeds (r/a+b+c), one request per chunk, and each post is mapped
back to its artist by the subreddit it came from.
"""

from calendar import timegm
from datetime import datetime, timezone

import feedparser

from ..classify import classify
from ..http import RateLimiter, get
from . import event

NAME = "reddit"
CHUNK = 25
_limit = RateLimiter(10.0)


def fetch(artists: list[dict], since) -> list[dict]:
    by_sub = {}
    for a in artists:
        sub = (a.get("subreddit") or "").removeprefix("r/").strip()
        if sub:
            by_sub.setdefault(sub.lower(), []).append(a)
    subs = sorted(by_sub)
    rows = []
    for i in range(0, len(subs), CHUNK):
        chunk = subs[i:i + CHUNK]
        r = get(f"https://www.reddit.com/r/{'+'.join(chunk)}/new/.rss", params={"limit": 100}, limiter=_limit)
        for e in feedparser.parse(r.content).entries:
            ts = e.get("updated_parsed") or e.get("published_parsed")
            sub = next((t.get("term", "") for t in e.get("tags", [])), "").lower()
            if not ts or sub not in by_sub:
                continue
            published = datetime.fromtimestamp(timegm(ts), tz=timezone.utc)
            if since and published < since:
                continue
            for a in by_sub[sub]:
                rows.append(event(NAME, f"{a['id']}:{e.id}", a["id"], classify(e.title), e.title, e.link,
                                  published_at=published, payload={"subreddit": sub, "author": e.get("author")}))
    return rows
