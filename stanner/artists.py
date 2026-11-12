"""The list of followed artists that every fetcher iterates over."""

import json

from . import bq, config

CACHE = config.CACHE_DIR / "followed_artists.json"


def followed(offline: bool = False) -> list[dict]:
    """Followed tree from BigQuery, or the local cache written by the MusicBrainz crawl."""
    if offline:
        if not CACHE.exists():
            raise RuntimeError("No local artist cache. Run: python -m stanner run musicbrainz --dry-run")
        return json.loads(CACHE.read_text(encoding="utf-8"))
    rows = bq.query(f"""
        SELECT a.id, a.name, a.kind, a.youtube_channel_id, a.subreddit, f.depth,
               p.name AS parent_name
        FROM `{bq.tbl('followed_artists')}` f
        JOIN `{bq.tbl('artists')}` a ON a.id = f.id
        LEFT JOIN `{bq.tbl('artists')}` p ON p.id = f.parent_id
    """)
    return [dict(r.items()) for r in rows]


def write_cache(artists: dict, links: list, follows: list[str]):
    """Mirror of the followed_artists view, computed locally (used by --dry-run and seeding)."""
    children = {}
    for l in links:
        children.setdefault(l["parent_id"], []).append(l["child_id"])
    out, seen = [], {}
    frontier = [(m, None, 0) for m in follows]
    while frontier:
        aid, parent, depth = frontier.pop(0)
        if aid in seen or aid not in artists:
            continue
        seen[aid] = True
        a = artists[aid]
        out.append({
            "id": aid, "name": a["name"], "kind": a["kind"],
            "youtube_channel_id": a.get("youtube_channel_id"), "subreddit": a.get("subreddit"),
            "depth": depth, "parent_name": artists[parent]["name"] if parent else None,
        })
        frontier += [(c, aid, depth + 1) for c in children.get(aid, [])]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
