"""Local viewer for the Stanner pipeline.

Run:  .venv\\Scripts\\python web\\app.py   then open http://127.0.0.1:5000
Reads BigQuery with your Application Default Credentials (gcloud auth application-default login).
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402
from google.cloud import bigquery  # noqa: E402

from stanner import bq  # noqa: E402

STATIC = Path(__file__).parent / "static"
OFFICIAL = "'musicbrainz', 'youtube', 'setlistfm', 'ticketmaster'"  # first-party / concert sources
app = Flask(__name__, static_folder=None)
_cache: dict = {}


def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def run(sql: str, **params):
    qp = []
    for k, v in params.items():
        if isinstance(v, (list, tuple)):
            qp.append(bigquery.ArrayQueryParameter(k, "STRING", list(v)))
        elif isinstance(v, int):
            qp.append(bigquery.ScalarQueryParameter(k, "INT64", v))
        else:
            qp.append(bigquery.ScalarQueryParameter(k, "STRING", v))
    job = bq.client().query(sql.replace("{ds}", bq.ds()), job_config=bigquery.QueryJobConfig(query_parameters=qp))
    return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in job.result()]


def build_graph():
    arts = {
        a["id"]: {**a, "children": [], "parents": [], "counts": {}, "total": 0, "last": None}
        for a in run("SELECT id, name, mb_name, kind, youtube_channel_id, subreddit FROM `{ds}.artists`")
    }
    seen = set()
    for l in run("SELECT parent_id, child_id, relation, ended FROM `{ds}.artist_links`"):
        p, c = l["parent_id"], l["child_id"]
        if p not in arts or c not in arts or (p, c) in seen:
            continue
        seen.add((p, c))
        arts[p]["children"].append({"id": c, "relation": l["relation"], "ended": l["ended"]})
        arts[c]["parents"].append({"id": p, "relation": l["relation"], "ended": l["ended"]})
    for r in run("""
        SELECT artist_id, type, COUNT(*) AS n,
               MAX(IF(COALESCE(published_at, event_time) <= CURRENT_TIMESTAMP(),
                      COALESCE(published_at, event_time), NULL)) AS last
        FROM `{ds}.events` GROUP BY 1, 2"""):
        a = arts.get(r["artist_id"])
        if not a:
            continue
        a["counts"][r["type"]] = r["n"]
        a["total"] += r["n"]
        if r["last"] and (not a["last"] or r["last"] > a["last"]):
            a["last"] = r["last"]
    seeds = [r["mbid"] for r in run("SELECT mbid FROM `{ds}.seed_follows`") if r["mbid"] in arts]
    return {"seeds": seeds, "artists": arts}


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/graph")
def graph():
    return jsonify(cached("graph", 600, build_graph))


@app.post("/api/events")
def events():
    q = request.get_json(force=True) or {}
    upcoming = bool(q.get("upcoming"))
    if upcoming:
        # Official sources only: scheduled releases/concerts still ahead, then official
        # announcements/teasers from the last 30 days. Future items first, soonest at the top.
        when = f"""e.source IN ({OFFICIAL}) AND (
                     COALESCE(e.event_time, e.published_at) >= CURRENT_TIMESTAMP()
                     OR (e.type = 'announcement'
                         AND e.published_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)))"""
        order = """COALESCE(e.event_time, e.published_at) >= CURRENT_TIMESTAMP() DESC,
                   IF(COALESCE(e.event_time, e.published_at) >= CURRENT_TIMESTAMP(),
                      UNIX_SECONDS(COALESCE(e.event_time, e.published_at)),
                      -UNIX_SECONDS(e.published_at))"""
    else:
        when, order = "COALESCE(e.published_at, e.event_time) <= CURRENT_TIMESTAMP()", "COALESCE(e.published_at, e.event_time) DESC"
    sql = f"""
        SELECT e.source, e.source_id, e.artist_id, e.type, e.title, e.url, e.event_time, e.published_at,
               TO_JSON_STRING(e.payload) AS payload
        FROM `{{ds}}.events` e
        WHERE (COALESCE(ARRAY_LENGTH(@ids), 0) = 0 OR e.artist_id IN UNNEST(@ids))  -- empty array param arrives as NULL
          AND (@type = '' OR e.type = @type)
          AND (@source = '' OR e.source = @source)
          AND (@q = '' OR CONTAINS_SUBSTR(e.title, @q))
          AND {when}
        ORDER BY {order}
        LIMIT @limit OFFSET @offset"""
    rows = run(sql, ids=q.get("ids") or [], type=q.get("type") or "", source=q.get("source") or "",
               q=(q.get("q") or "").strip(), limit=min(int(q.get("limit", 50)), 200),
               offset=max(int(q.get("offset", 0)), 0))
    for r in rows:
        r["payload"] = json.loads(r["payload"]) if r["payload"] else {}
    return jsonify(rows)


@app.get("/api/status")
def status():
    def load():
        return {
            "sources": run("""
                SELECT s.source, s.last_ok, s.last_run.status AS last_status,
                       s.last_run.finished_at AS last_finished, s.last_run.rows_new AS last_new,
                       s.last_run.error AS last_error, s.errors_24h, c.n AS total, c.last_ingested
                FROM `{ds}.ingest_status` s
                LEFT JOIN (SELECT source, COUNT(*) n, MAX(ingested_at) last_ingested
                           FROM `{ds}.events` GROUP BY source) c USING (source)
                ORDER BY source"""),
            "runs": run("""
                SELECT source, mode, started_at, finished_at, status, rows_fetched, rows_new, error
                FROM `{ds}.ingest_runs` ORDER BY started_at DESC LIMIT 25"""),
            "growth": run("""
                SELECT day, source, rows_ingested FROM `{ds}.daily_growth`
                WHERE day >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY) ORDER BY day"""),
        }
    return jsonify(cached("status", 60, load))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
