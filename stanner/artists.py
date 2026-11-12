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
        SELECT a.id, a.name, a.mb_name, a.kind, a.youtube_channel_id, a.subreddit, f.depth,
               p.name AS parent_name, IFNULL(pl.parent_ids, []) AS parent_ids
        FROM `{bq.tbl('followed_artists')}` f
        JOIN `{bq.tbl('artists')}` a ON a.id = f.id
        LEFT JOIN `{bq.tbl('artists')}` p ON p.id = f.parent_id
        LEFT JOIN (SELECT child_id, ARRAY_AGG(DISTINCT parent_id) AS parent_ids
                   FROM `{bq.tbl('artist_links')}` GROUP BY child_id) pl ON pl.child_id = a.id
    """)
    return [dict(r.items()) for r in rows]


def relations(artists: list[dict]) -> dict:
    """id -> {"names": own names, "related": parent + member names, "children": [artist dicts]}."""
    by_id = {a["id"]: a for a in artists}
    out = {a["id"]: {"names": _names(a), "related": [], "children": []} for a in artists}
    for a in artists:
        for pid in a.get("parent_ids") or []:
            if pid in by_id:
                out[pid]["children"].append(a)
                out[pid]["related"] += _names(a)
                out[a["id"]]["related"] += _names(by_id[pid])
    return out


def _names(a: dict) -> list[str]:
    return list(dict.fromkeys(n for n in (a.get("name"), a.get("mb_name")) if n))


def write_cache(artists: dict, links: list, follows: list[str]):
    """Mirror of the followed_artists view, computed locally (used by --dry-run and seeding)."""
    children, parents = {}, {}
    for l in links:
        children.setdefault(l["parent_id"], []).append(l["child_id"])
        parents.setdefault(l["child_id"], []).append(l["parent_id"])
    out, seen = [], {}
    frontier = [(m, None, 0) for m in follows]
    while frontier:
        aid, parent, depth = frontier.pop(0)
        if aid in seen or aid not in artists:
            continue
        seen[aid] = True
        a = artists[aid]
        out.append({
            "id": aid, "name": a["name"], "mb_name": a.get("mb_name"), "kind": a["kind"],
            "youtube_channel_id": a.get("youtube_channel_id"), "subreddit": a.get("subreddit"),
            "depth": depth, "parent_name": artists[parent]["name"] if parent else None,
            "parent_ids": sorted(set(parents.get(aid, []))),
        })
        frontier += [(c, aid, depth + 1) for c in children.get(aid, [])]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
