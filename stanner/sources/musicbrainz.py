"""MusicBrainz: artist tree (members, sub-units, aliases) and release groups.

Tree walk goes *downward* only. From a parent's point of view, children are relations with
direction == "backward" for "member of band" and "subgroup" (verified against live data).
"""

import re

from .. import config
from ..http import RateLimiter, get
from . import event, parse_date

NAME = "musicbrainz"
API = "https://musicbrainz.org/ws/2"
_limit = RateLimiter(1.1)  # MusicBrainz allows 1 req/s

CHILD_RELATIONS = {"member of band": "member", "subgroup": "subgroup"}
_CHANNEL_RX = re.compile(r"youtube\.com/channel/(UC[\w-]{22})")
_HANDLE_RX = re.compile(r"youtube\.com/(@[\w.\-]+|user/[\w.\-]+|c/[\w.\-]+)")


def lookup(mbid: str) -> dict:
    return get(f"{API}/artist/{mbid}", params={"inc": "artist-rels+url-rels+aliases", "fmt": "json"},
               limiter=_limit).json()


def _english_name(a: dict) -> str:
    for al in a.get("aliases") or []:
        if al.get("locale") == "en" and al.get("primary"):
            return al["name"]
    return a["name"]


def _youtube_channel(a: dict) -> str | None:
    handle = None
    for r in a.get("relations") or []:
        res = (r.get("url") or {}).get("resource", "")
        if r.get("type") != "youtube" or r.get("ended"):
            continue
        if m := _CHANNEL_RX.search(res):
            return m.group(1)
        if (m := _HANDLE_RX.search(res)) and not handle:
            handle = m.group(1)
    if handle:
        from .youtube import resolve_handle

        return resolve_handle(handle)
    return None


def crawl(follows: list[str], overrides: dict) -> tuple[dict, list, dict]:
    """BFS from seed follows. Returns (artists by id, links, raw lookups by id)."""
    artists, links, raw = {}, [], {}
    queue = [(m, 0) for m in follows]
    while queue and len(artists) < config.MAX_ARTISTS:
        mbid, depth = queue.pop(0)
        if mbid in artists:
            continue
        a = lookup(mbid)
        raw[mbid] = a
        ov = overrides.get(mbid, {})
        kind = (a.get("type") or "other").lower()
        artists[mbid] = {
            "id": mbid, "mbid": mbid, "mb_name": a["name"],
            "name": ov.get("name") or _english_name(a),
            "kind": kind if kind in ("group", "person") else "other",
            "youtube_channel_id": ov.get("youtube_channel_id") or _youtube_channel(a),
            "subreddit": ov.get("subreddit"),
        }
        print(f"  [{depth}] {artists[mbid]['name']} ({kind})")
        if depth >= config.MAX_DEPTH:
            continue
        for r in a.get("relations") or []:
            other = r.get("artist")
            if not other:
                continue
            rel = None
            if r["type"] in CHILD_RELATIONS and r["direction"] == "backward":
                rel = CHILD_RELATIONS[r["type"]]
            elif r["type"] == "is person" and kind == "person":
                rel = "alias"  # stage names / solo personas of a person
            if rel:
                links.append({"parent_id": mbid, "child_id": other["id"], "relation": rel,
                              "ended": bool(r.get("ended"))})
                queue.append((other["id"], depth + 1))
    return artists, links, raw


def release_groups(mbid: str) -> list[dict]:
    out, offset = [], 0
    while True:
        d = get(f"{API}/release-group", params={"artist": mbid, "limit": 100, "offset": offset, "fmt": "json"},
                limiter=_limit).json()
        out += d["release-groups"]
        offset += len(d["release-groups"])
        if not d["release-groups"] or offset >= d["release-group-count"]:
            return out


def to_event(rg: dict, artist_id: str) -> dict:
    from ..bq import now

    when = parse_date(rg.get("first-release-date"))
    type_ = "announcement" if when and when > now() else "release"
    kinds = [rg.get("primary-type")] + (rg.get("secondary-types") or [])
    return event(NAME, rg["id"], artist_id, type_, rg["title"],
                 f"https://musicbrainz.org/release-group/{rg['id']}",
                 published_at=when, event_time=when,
                 payload={"kinds": [k for k in kinds if k], "first_release_date": rg.get("first-release-date")})


def fetch(artists: list[dict], since) -> list[dict]:
    """Release groups are not filterable by date, so fetch all; dedupe drops the known ones."""
    rows = []
    for a in artists:
        rows += [to_event(rg, a["id"]) for rg in release_groups(a["id"])]
    return rows
