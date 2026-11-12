"""One-time historical seed. Separate path from the scheduled job:

  1. `seed fetch`: bulk-download history to data/raw/ (resumable; existing files are skipped).
  2. `seed load`:  raw files -> normalized Parquet in data/seed/ -> BigQuery load jobs -> dedupe.

The fetch time is saved in data/raw/manifest.json and becomes each source's first watermark,
so the first live run picks up exactly where the seed ended (minus the 1-day overlap).
"""

import json
from datetime import datetime

from . import artists as artists_mod
from . import bq, config
from .sources import musicbrainz, setlistfm, youtube

RAW = config.RAW_DIR
OUT = config.DATA_DIR / "seed"
MANIFEST = RAW / "manifest.json"


def _save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fetch(sources=("musicbrainz", "youtube", "setlistfm")):
    f = config.load_follows()
    if not f["follow"]:
        raise SystemExit("config/follows.toml has no followed artists")
    started = bq.now().isoformat()

    print("MusicBrainz tree...")
    arts, links, raw = musicbrainz.crawl(f["follow"], f["overrides"])
    _save(RAW / "musicbrainz" / "artists_raw.json", raw)
    _save(RAW / "musicbrainz" / "artists.json", arts)
    _save(RAW / "musicbrainz" / "links.json", links)
    tree = artists_mod.write_cache(arts, links, f["follow"])

    for a in tree:
        aid, name = a["id"], a["name"]
        if "musicbrainz" in sources:
            p = RAW / "musicbrainz" / "release_groups" / f"{aid}.json"
            if not p.exists():
                print(f"  release groups: {name}")
                _save(p, musicbrainz.release_groups(aid))
        if "youtube" in sources and a.get("youtube_channel_id"):
            p = RAW / "youtube" / f"{a['youtube_channel_id']}.json"
            if not p.exists():
                print(f"  youtube back catalog: {name}")
                _save(p, {"artist_id": aid, "items": youtube.seed_fetch(a["youtube_channel_id"])})
        if "setlistfm" in sources:
            p = RAW / "setlistfm" / f"{aid}.json"
            if not p.exists():
                print(f"  setlist.fm: {name}")
                _save(p, setlistfm.seed_fetch(aid))

    m = _load(MANIFEST) if MANIFEST.exists() else {}
    for s in sources:
        m.setdefault(s, started)  # keep the earliest fetch time if fetch is re-run
    _save(MANIFEST, m)
    print("raw files saved under", RAW)


def _events_by_source() -> dict[str, list[dict]]:
    out = {"musicbrainz": [], "youtube": [], "setlistfm": []}
    for p in (RAW / "musicbrainz" / "release_groups").glob("*.json"):
        out["musicbrainz"] += [musicbrainz.to_event(rg, p.stem) for rg in _load(p)]
    for p in (RAW / "youtube").glob("*.json"):
        d = _load(p)
        out["youtube"] += youtube.seed_events(d["items"], d["artist_id"], p.stem)
    for p in (RAW / "setlistfm").glob("*.json"):
        out["setlistfm"] += [setlistfm.to_event(s, p.stem) for s in _load(p)]
    return out


def _write_parquet(path, rows, fields):
    import pyarrow as pa
    import pyarrow.parquet as pq

    types = {"STRING": pa.string(), "TIMESTAMP": pa.timestamp("us", tz="UTC"), "BOOL": pa.bool_(),
             "INT64": pa.int64()}
    schema = pa.schema([(n, types[t]) for n, t in fields])
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    return path


def load():
    if not MANIFEST.exists():
        raise SystemExit("No raw seed data. Run: python -m stanner seed fetch")
    manifest = _load(MANIFEST)
    ts = bq.now()

    arts = _load(RAW / "musicbrainz" / "artists.json")
    links = _load(RAW / "musicbrainz" / "links.json")
    follows = config.load_follows()["follow"]
    bq.load_parquet("artists", _write_parquet(OUT / "artists.parquet",
                    [{**a, "updated_at": ts} for a in arts.values()], bq.ARTIST_FIELDS),
                    bq.ARTIST_FIELDS, truncate=True)
    bq.load_parquet("artist_links", _write_parquet(OUT / "artist_links.parquet", links, bq.LINK_FIELDS),
                    bq.LINK_FIELDS, truncate=True)
    bq.load_parquet("seed_follows", _write_parquet(OUT / "seed_follows.parquet",
                    [{"mbid": m, "note": arts.get(m, {}).get("name"), "loaded_at": ts} for m in follows],
                    bq.FOLLOW_FIELDS), bq.FOLLOW_FIELDS, truncate=True)

    for source, rows in _events_by_source().items():
        started = bq.now()
        path = _write_parquet(OUT / f"events_{source}.parquet", rows, bq.EVENT_FIELDS)
        new = 0
        if rows:
            bq.load_parquet("events_staging", path, bq.EVENT_FIELDS, truncate=True)
            new = bq.merge_staging()
        wm = datetime.fromisoformat(manifest[source]) if source in manifest else None
        bq.record_run(run_id=f"seed-{source}-{started:%Y%m%d%H%M%S}", source=source, mode="seed",
                      started_at=started, finished_at=bq.now(), status="ok", rows_fetched=len(rows),
                      rows_new=new, watermark=wm, error=None)
        print(f"seed {source}: {len(rows)} rows, {new} new, watermark={wm}")
