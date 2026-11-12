"""Google News RSS per artist. Third-party, so every item is type "news" (topic kept as a hint).

Each headline must pass classify.relevant(): it has to name the artist and have music context
(members: their group named). This drops common words ("once or twice"), bylines, and gossip.
"""

from calendar import timegm
from datetime import datetime, timezone

import feedparser

from .. import config
from ..artists import relations
from ..classify import relevant, topic
from ..http import RateLimiter, get
from . import event

NAME = "news"
_limit = RateLimiter(2.0)
_overrides = None


def _override(a: dict) -> dict:
    global _overrides
    if _overrides is None:
        _overrides = config.load_follows()["overrides"]
    return _overrides.get(a["id"], {})


def query_for(a: dict) -> str:
    if q := _override(a).get("news_query"):
        return q
    # Members' names are often ambiguous ("DK", "Joshua"), so pin them to their parent group.
    if a.get("depth", 0) > 0 and a.get("parent_name"):
        return f'"{a["name"]}" "{a["parent_name"]}"'
    return f'"{a["name"]}"'


def is_strict(a: dict) -> bool:
    """Every artist needs music context by default; set news_strict = false in follows.toml to relax."""
    ov = _override(a).get("news_strict")
    return True if ov is None else bool(ov)


def is_member(a: dict) -> bool:
    return a.get("depth", 0) > 0 and bool(a.get("parent_ids"))


def headline(title: str, outlet: str | None) -> str:
    """Google News titles end with ' - Outlet'; drop it so bylines/outlet names can't match."""
    if outlet and title.endswith(f" - {outlet}"):
        return title[: -len(outlet) - 3]
    return title.rsplit(" - ", 1)[0] if " - " in title else title


def fetch(artists: list[dict], since) -> list[dict]:
    rel = relations(artists)
    rows, dropped = [], 0
    for a in artists:
        r = get("https://news.google.com/rss/search", limiter=_limit,
                params={"q": query_for(a) + " when:7d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
        for e in feedparser.parse(r.content).entries:
            if not e.get("published_parsed"):
                continue
            published = datetime.fromtimestamp(timegm(e.published_parsed), tz=timezone.utc)
            if since and published < since:
                continue
            outlet = (e.get("source") or {}).get("title")
            head = headline(e.title, outlet)
            if not relevant(head, rel[a["id"]]["names"], rel[a["id"]]["related"], is_strict(a), is_member(a)):
                dropped += 1
                continue
            # Same article can match several artists; key by artist so each gets it in their feed.
            rows.append(event(NAME, f"{a['id']}:{e.get('id') or e.link}", a["id"], "news", head, e.link,
                              published_at=published,
                              payload={"outlet": outlet, "topic": topic(head), "query": query_for(a)}))
    print(f"  news: kept {len(rows)}, dropped {dropped} irrelevant")
    return rows
