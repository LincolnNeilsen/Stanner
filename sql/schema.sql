-- Stanner schema for dataset {ds}. Run with: python -m stanner setup
-- Statements are separated by a line containing only ";;".
-- Sandbox note: no partitioning on events. The sandbox forces a 60-day partition expiration,
-- which would delete historical (seeded) partitions immediately. Clustering only.

-- ===== Bronze / raw-ish tables =====
CREATE TABLE IF NOT EXISTS `{ds}.seed_follows` (
  mbid STRING NOT NULL,
  note STRING,
  loaded_at TIMESTAMP
)
;;
CREATE TABLE IF NOT EXISTS `{ds}.artists` (
  id STRING NOT NULL,          -- = MusicBrainz MBID
  name STRING,                 -- English display name when MusicBrainz has one
  mb_name STRING,              -- name as stored in MusicBrainz
  mbid STRING,
  kind STRING,                 -- group | person | other
  youtube_channel_id STRING,
  subreddit STRING,
  updated_at TIMESTAMP
)
;;
CREATE TABLE IF NOT EXISTS `{ds}.artist_links` (
  parent_id STRING NOT NULL,
  child_id STRING NOT NULL,
  relation STRING,             -- member | subgroup | alias
  ended BOOL
)
;;
CREATE TABLE IF NOT EXISTS `{ds}.events_staging` (
  source STRING NOT NULL,
  source_id STRING NOT NULL,
  artist_id STRING,
  type STRING,
  title STRING,
  url STRING,
  event_time TIMESTAMP,
  published_at TIMESTAMP,
  ingested_at TIMESTAMP,
  payload STRING               -- JSON text; parsed into events.payload (JSON) on merge
)
;;
CREATE TABLE IF NOT EXISTS `{ds}.events` (
  source STRING NOT NULL,
  source_id STRING NOT NULL,
  artist_id STRING,
  type STRING,                 -- release | announcement | video | concert | appearance | news
  title STRING,
  url STRING,
  event_time TIMESTAMP,        -- when the thing happens (concert date, release date)
  published_at TIMESTAMP,      -- when the source published it
  ingested_at TIMESTAMP,       -- when this pipeline first stored it
  payload JSON
)
CLUSTER BY artist_id, type
;;
CREATE TABLE IF NOT EXISTS `{ds}.ingest_runs` (
  run_id STRING,
  source STRING,
  mode STRING,                 -- live | seed
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  status STRING,               -- ok | error
  rows_fetched INT64,
  rows_new INT64,
  watermark TIMESTAMP,         -- items published before this are known to be ingested
  error STRING
)
;;
-- ===== Silver =====
-- Full followed tree: seed follows -> members / sub-units -> their members ... any depth.
CREATE OR REPLACE VIEW `{ds}.followed_artists` AS
WITH RECURSIVE tree AS (
  SELECT mbid AS id, CAST(NULL AS STRING) AS parent_id, mbid AS root_id, 0 AS depth
  FROM `{ds}.seed_follows`
  UNION ALL
  SELECT l.child_id, l.parent_id, t.root_id, t.depth + 1
  FROM tree t
  JOIN `{ds}.artist_links` l ON l.parent_id = t.id
  WHERE t.depth < 6
)
SELECT
  id,
  ARRAY_AGG(parent_id IGNORE NULLS ORDER BY depth LIMIT 1)[SAFE_OFFSET(0)] AS parent_id,
  ARRAY_AGG(DISTINCT root_id) AS root_ids,
  MIN(depth) AS depth
FROM tree
GROUP BY id
;;
CREATE OR REPLACE VIEW `{ds}.events_enriched` AS
SELECT e.*, a.name AS artist_name, a.kind AS artist_kind
FROM `{ds}.events` e
LEFT JOIN `{ds}.artists` a ON a.id = e.artist_id
;;
-- ===== Gold =====
CREATE OR REPLACE VIEW `{ds}.upcoming_concerts` AS
SELECT artist_name, title, event_time, url, source,
       JSON_VALUE(payload, '$.venue') AS venue, JSON_VALUE(payload, '$.city') AS city,
       JSON_VALUE(payload, '$.country') AS country
FROM `{ds}.events_enriched`
WHERE type = 'concert' AND event_time >= CURRENT_TIMESTAMP()
ORDER BY event_time
;;
CREATE OR REPLACE VIEW `{ds}.feed_last_7d` AS
SELECT artist_name, type, title, url, source, published_at, event_time
FROM `{ds}.events_enriched`
WHERE published_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
ORDER BY published_at DESC
;;
CREATE OR REPLACE VIEW `{ds}.monthly_release_counts` AS
SELECT DATE_TRUNC(DATE(COALESCE(event_time, published_at)), MONTH) AS month,
       artist_name, COUNT(*) AS releases
FROM `{ds}.events_enriched`
WHERE type = 'release'
GROUP BY month, artist_name
;;
-- This year's monthly activity vs. the trailing 10-year average for the same calendar month.
CREATE OR REPLACE VIEW `{ds}.monthly_vs_10yr` AS
WITH m AS (
  SELECT EXTRACT(YEAR FROM published_at) AS yr, EXTRACT(MONTH FROM published_at) AS mo, type,
         COUNT(*) AS n
  FROM `{ds}.events`
  WHERE published_at IS NOT NULL
  GROUP BY yr, mo, type
)
SELECT cur.mo AS month, cur.type, cur.n AS this_year,
       COALESCE(SUM(h.n), 0) / 10 AS avg_prev_10yr
FROM m cur
LEFT JOIN m h
  ON h.mo = cur.mo AND h.type = cur.type AND h.yr BETWEEN cur.yr - 10 AND cur.yr - 1
WHERE cur.yr = EXTRACT(YEAR FROM CURRENT_DATE())
GROUP BY month, cur.type, this_year
;;
-- Pipeline health: last run per source. Flat columns so dashboards can use them directly.
-- rows_stored = rows currently in events (total_rows_new also counts rows later purged/reloaded).
CREATE OR REPLACE VIEW `{ds}.ingest_status` AS
WITH r AS (
  SELECT source,
         MAX(IF(status = 'ok', finished_at, NULL)) AS last_ok,
         ARRAY_AGG(STRUCT(status, finished_at, rows_new, error) ORDER BY finished_at DESC LIMIT 1)[OFFSET(0)] AS last_run,
         COUNTIF(status = 'error' AND finished_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)) AS errors_24h,
         SUM(rows_new) AS total_rows_new
  FROM `{ds}.ingest_runs`
  GROUP BY source
)
SELECT r.source, r.last_ok, r.last_run,
       r.last_run.status AS last_status, r.last_run.finished_at AS last_finished,
       r.last_run.rows_new AS last_new, r.last_run.error AS last_note,
       r.errors_24h, r.total_rows_new, IFNULL(e.n, 0) AS rows_stored
FROM r
LEFT JOIN (SELECT source, COUNT(*) AS n FROM `{ds}.events` GROUP BY source) e USING (source)
;;
CREATE OR REPLACE VIEW `{ds}.daily_growth` AS
SELECT DATE(ingested_at) AS day, source, COUNT(*) AS rows_ingested
FROM `{ds}.events`
GROUP BY day, source
