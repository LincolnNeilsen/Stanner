"""BigQuery access. Uses load jobs only (the sandbox has no streaming inserts and no DML)."""

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config

EVENT_FIELDS = [
    ("source", "STRING"),
    ("source_id", "STRING"),
    ("artist_id", "STRING"),
    ("type", "STRING"),
    ("title", "STRING"),
    ("url", "STRING"),
    ("event_time", "TIMESTAMP"),
    ("published_at", "TIMESTAMP"),
    ("ingested_at", "TIMESTAMP"),
    ("payload", "STRING"),
]
ARTIST_FIELDS = [
    ("id", "STRING"),
    ("name", "STRING"),
    ("mb_name", "STRING"),
    ("mbid", "STRING"),
    ("kind", "STRING"),
    ("youtube_channel_id", "STRING"),
    ("subreddit", "STRING"),
    ("updated_at", "TIMESTAMP"),
]
LINK_FIELDS = [("parent_id", "STRING"), ("child_id", "STRING"), ("relation", "STRING"), ("ended", "BOOL")]
FOLLOW_FIELDS = [("mbid", "STRING"), ("note", "STRING"), ("loaded_at", "TIMESTAMP")]
RUN_FIELDS = [
    ("run_id", "STRING"),
    ("source", "STRING"),
    ("mode", "STRING"),
    ("started_at", "TIMESTAMP"),
    ("finished_at", "TIMESTAMP"),
    ("status", "STRING"),
    ("rows_fetched", "INT64"),
    ("rows_new", "INT64"),
    ("watermark", "TIMESTAMP"),
    ("error", "STRING"),
]

_client = None


def client():
    global _client
    if _client is None:
        from google.cloud import bigquery

        if not config.GCP_PROJECT:
            raise RuntimeError("GCP_PROJECT is not set (see .env.example)")
        _client = bigquery.Client(project=config.GCP_PROJECT, location=config.BQ_LOCATION)
    return _client


def ds() -> str:
    return f"{config.GCP_PROJECT}.{config.BQ_DATASET}"


def tbl(name: str) -> str:
    return f"{ds()}.{name}"


def _schema(fields):
    from google.cloud import bigquery

    return [bigquery.SchemaField(n, t) for n, t in fields]


def _jsonable(row: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


def query(sql: str, params: dict | None = None):
    from google.cloud import bigquery

    cfg = None
    if params:
        cfg = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter(k, "STRING", v) for k, v in params.items()]
        )
    return list(client().query(sql, job_config=cfg).result())


def setup():
    """Create dataset, tables and views. Safe to re-run."""
    from google.cloud import bigquery

    c = client()
    dataset = bigquery.Dataset(ds())
    dataset.location = config.BQ_LOCATION
    c.create_dataset(dataset, exists_ok=True)
    # Try to remove default table expiration. The sandbox enforces 60 days and may refuse.
    try:
        d = c.get_dataset(ds())
        d.default_table_expiration_ms = None
        c.update_dataset(d, ["default_table_expiration_ms"])
    except Exception as e:  # noqa: BLE001
        print(f"note: could not clear dataset expiration (expected in sandbox): {e}")

    sql = (config.ROOT / "sql" / "schema.sql").read_text(encoding="utf-8").replace("{ds}", ds())
    for stmt in sql.split("\n;;"):
        body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
        if body:
            c.query(stmt).result()
            print("ok:", body.splitlines()[0][:90])


def load_rows(table: str, rows: list[dict], fields, *, truncate: bool):
    """Batch load job from in-memory rows (live path)."""
    from google.cloud import bigquery

    cfg = bigquery.LoadJobConfig(
        schema=_schema(fields),
        write_disposition="WRITE_TRUNCATE" if truncate else "WRITE_APPEND",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    client().load_table_from_json([_jsonable(r) for r in rows], tbl(table), job_config=cfg).result()


def load_parquet(table: str, path: Path, fields, *, truncate: bool):
    """Batch load job from a local Parquet file (seed path)."""
    from google.cloud import bigquery

    cfg = bigquery.LoadJobConfig(
        schema=_schema(fields),
        write_disposition="WRITE_TRUNCATE" if truncate else "WRITE_APPEND",
        source_format=bigquery.SourceFormat.PARQUET,
    )
    with open(path, "rb") as f:
        client().load_table_from_file(f, tbl(table), job_config=cfg).result()


def _count(table: str) -> int:
    return client().get_table(tbl(table)).num_rows or 0


_COLS = "source, source_id, artist_id, type, title, url, event_time, published_at, ingested_at"
_NEW_ROWS = f"""
  SELECT s.source, s.source_id, s.artist_id, s.type, s.title, s.url, s.event_time,
         s.published_at, s.ingested_at, SAFE.PARSE_JSON(s.payload) AS payload
  FROM (
    SELECT * FROM `{{staging}}` WHERE TRUE
    QUALIFY ROW_NUMBER() OVER (PARTITION BY source, source_id ORDER BY ingested_at) = 1
  ) s
  LEFT JOIN `{{events}}` e ON e.source = s.source AND e.source_id = s.source_id
  WHERE e.source_id IS NULL
"""


def merge_staging() -> int:
    """Insert staging rows whose (source, source_id) is not already in events. Returns rows added.

    Sandbox mode (default): rewrite `events` as (events UNION ALL new rows) with a single atomic
    query job (WRITE_TRUNCATE). Billing mode: a real MERGE ... WHEN NOT MATCHED.
    """
    from google.cloud import bigquery

    staging, events = tbl("events_staging"), tbl("events")
    before = _count("events")
    new_rows = _NEW_ROWS.format(staging=staging, events=events)
    if config.BQ_USE_DML:
        sql = f"""
        MERGE `{events}` t
        USING ({new_rows}) s
        ON t.source = s.source AND t.source_id = s.source_id
        WHEN NOT MATCHED THEN INSERT ({_COLS}, payload)
          VALUES (s.source, s.source_id, s.artist_id, s.type, s.title, s.url, s.event_time,
                  s.published_at, s.ingested_at, s.payload)
        """
        client().query(sql).result()
    else:
        sql = f"SELECT {_COLS}, payload FROM `{events}`\nUNION ALL\n{new_rows}"
        cfg = bigquery.QueryJobConfig(
            destination=events,
            write_disposition="WRITE_TRUNCATE",
            clustering_fields=["artist_id", "type"],
        )
        client().query(sql, job_config=cfg).result()
    return _count("events") - before


def purge(sources: list[str]) -> int:
    """Remove every event from the given sources. Returns rows removed.

    Sandbox: atomic rewrite of `events` without those rows. Billing mode: DELETE.
    """
    from google.cloud import bigquery

    events = tbl("events")
    before = _count("events")
    params = [bigquery.ArrayQueryParameter("s", "STRING", list(sources))]
    if config.BQ_USE_DML:
        client().query(f"DELETE FROM `{events}` WHERE source IN UNNEST(@s)",
                       job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
    else:
        cfg = bigquery.QueryJobConfig(destination=events, write_disposition="WRITE_TRUNCATE",
                                      clustering_fields=["artist_id", "type"], query_parameters=params)
        client().query(f"SELECT * FROM `{events}` WHERE source NOT IN UNNEST(@s)", job_config=cfg).result()
    return before - _count("events")


def last_watermark(source: str) -> datetime | None:
    rows = query(
        f"SELECT MAX(watermark) AS w FROM `{tbl('ingest_runs')}` WHERE source = @s AND status = 'ok'",
        {"s": source},
    )
    return rows[0]["w"] if rows else None


def record_run(**run):
    load_rows("ingest_runs", [run], RUN_FIELDS, truncate=False)


def now() -> datetime:
    return datetime.now(timezone.utc)


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
