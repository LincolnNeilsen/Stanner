"""Live (scheduled) run: fetch since watermark -> load to staging -> dedupe into events -> log run."""

import json
import traceback
import uuid
from datetime import timedelta

from . import artists as artists_mod
from . import bq, config
from .sources import musicbrainz, news, reddit, setlistfm, ticketmaster, youtube

SOURCES = {m.NAME: m for m in (youtube, news, reddit, ticketmaster, musicbrainz, setlistfm)}


def refresh_artist_tree(dry_run: bool) -> list[dict]:
    """Daily: re-walk the MusicBrainz tree and snapshot artists / links / follows."""
    f = config.load_follows()
    if not f["follow"]:
        raise RuntimeError("config/follows.toml has no followed artists")
    print("Walking MusicBrainz tree...")
    arts, links, _ = musicbrainz.crawl(f["follow"], f["overrides"])
    tree = artists_mod.write_cache(arts, links, f["follow"])
    if not dry_run:
        ts = bq.now()
        bq.load_rows("artists", [{**a, "updated_at": ts} for a in arts.values()], bq.ARTIST_FIELDS, truncate=True)
        bq.load_rows("artist_links", links, bq.LINK_FIELDS, truncate=True)
        bq.load_rows("seed_follows", [{"mbid": m, "note": arts.get(m, {}).get("name"), "loaded_at": ts}
                                      for m in f["follow"]], bq.FOLLOW_FIELDS, truncate=True)
    print(f"{len(arts)} artists, {len(links)} links")
    return tree


def run_source(name: str, dry_run: bool = False) -> bool:
    mod = SOURCES[name]
    started = bq.now()
    run = {"run_id": uuid.uuid4().hex, "source": name, "mode": "live", "started_at": started,
           "rows_fetched": 0, "rows_new": 0, "watermark": None, "error": None}
    try:
        if name == "musicbrainz":
            arts = refresh_artist_tree(dry_run)
        else:
            arts = artists_mod.followed(offline=dry_run)
        wm = None if dry_run else bq.last_watermark(name)
        since = wm - timedelta(days=config.OVERLAP_DAYS) if wm else None
        print(f"[{name}] {len(arts)} artists, since={since}")
        rows = mod.fetch(arts, since)
        run["rows_fetched"] = len(rows)
        if dry_run:
            for r in rows[:15]:
                print(f"  {r['type']:<12} {str(r['published_at'])[:10]}  {r['title'][:80]}")
            print(f"[{name}] dry run: {len(rows)} rows fetched (not written)")
            out = config.CACHE_DIR / f"dryrun_{name}.json"
            out.write_text(json.dumps(rows, default=str, ensure_ascii=False, indent=1), encoding="utf-8")
            return True
        if rows:
            bq.load_rows("events_staging", rows, bq.EVENT_FIELDS, truncate=True)
            run["rows_new"] = bq.merge_staging()
        # Watermark = start of this run: everything published before it has now been seen.
        run.update(status="ok", watermark=started)
        print(f"[{name}] fetched={len(rows)} new={run['rows_new']}")
        return True
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        # Watermark stays at the last good run, so the next run backfills this gap.
        run.update(status="error", error=f"{type(e).__name__}: {e}"[:2000])
        return False
    finally:
        if not dry_run:
            run["finished_at"] = bq.now()
            try:
                bq.record_run(**run)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
