"""Fan subreddit RSS (subreddit set per artist in config/follows.toml).

Reddit rate-limits unauthenticated RSS hard (429 after ~1 request), so all subreddits are
fetched through combined feeds (r/a+b+c), one request per chunk.

Only update-style posts are kept (e.g. "261112 Twitter Update - ...", "Official Teaser"); fan
art, memes and discussion are dropped. A post naming a member is filed under that member.
"""

from calendar import timegm
from datetime import datetime, timezone

import feedparser

from ..artists import relations
from ..classify import has_word, is_update_post, topic
from ..http import RateLimiter, get
from . import event

NAME = "reddit"
CHUNK = 25
_limit = RateLimiter(10.0)


def _targets(title: str, group: dict, rel: dict) -> list[dict]:
    """Members named in the title, else the subreddit's own artist."""
    named = [c for c in rel[group["id"]]["children"] if any(has_word(title, n) for n in rel[c["id"]]["names"])]
    return named or [group]


def fetch(artists: list[dict], since) -> list[dict]:
    rel = relations(artists)
    by_sub = {}
    for a in artists:
        sub = (a.get("subreddit") or "").removeprefix("r/").strip()
        if sub:
            by_sub.setdefault(sub.lower(), []).append(a)
    subs = sorted(by_sub)
    rows, dropped = [], 0
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
            if not is_update_post(e.title):
                dropped += 1
                continue
            for group in by_sub[sub]:
                for a in _targets(e.title, group, rel):
                    rows.append(event(NAME, f"{a['id']}:{e.id}", a["id"], "news", e.title, e.link,
                                      published_at=published,
                                      payload={"subreddit": sub, "topic": topic(e.title), "author": e.get("author")}))
    print(f"  reddit: kept {len(rows)}, dropped {dropped} fan/discussion posts")
    return rows
