# Stanner

Artist tracker data pipeline (CS 452). Ingests MusicBrainz, YouTube, news, Reddit, and concert data on a schedule into BigQuery.

## Layout
| Path | What |
|---|---|
| `config/follows.toml` | Artists you follow (MusicBrainz IDs) plus per-artist overrides |
| `sql/schema.sql` | Tables and bronze → silver → gold views |
| `stanner/sources/` | One fetcher per source |
| `stanner/pipeline.py` | Live run: fetch since watermark → staging → dedupe → `ingest_runs` |
| `stanner/seed.py` | One-time seed: bulk fetch → `data/raw/` → Parquet → `bq load` |
| `.github/workflows/` | `hourly.yml` (youtube, news, reddit, ticketmaster), `daily.yml` (musicbrainz, setlistfm) |

## Run
```powershell
python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # fill in values
python -m stanner run musicbrainz --dry-run      # no BigQuery needed; caches the artist tree
python -m stanner run youtube news reddit --dry-run
python -m stanner setup                          # dataset, tables, views
python -m stanner seed fetch                     # history -> data/raw/
python -m stanner seed load                      # data/raw -> Parquet -> BigQuery
python -m stanner run musicbrainz setlistfm      # what daily.yml runs
python -m stanner run youtube news reddit ticketmaster   # what hourly.yml runs
```

## Design notes
- **Artist tree.** Each seed follow is expanded through MusicBrainz `member of band` and `subgroup` relations, to any depth (cap 6). Members' own release groups are their solo activity.
- **Idempotency.** Each run loads into `events_staging` (truncate), then adds only rows whose `(source, source_id)` is not already in `events`. The BigQuery sandbox has no DML, so this is one atomic query job that rewrites `events` as `events UNION ALL new_rows`; with billing enabled, `BQ_USE_DML=true` switches to a real `MERGE`. Running a fetcher twice leaves the row count unchanged.
- **Gaps.** Each source fetches since its last successful watermark minus one day. The seed records its fetch time as the first watermark, so the first live run covers the seed-to-live lag. A failed run is logged as `status=error` without moving the watermark, and the workflow exits non-zero. The next good run backfills from the old watermark. Empty runs log `status=ok, rows_new=0`.
- **Concurrency.** Both workflows share the `bq-writes` concurrency group, so two runs never rewrite `events` at once.
- **Sandbox limits.** `events` is clustered but not partitioned: the sandbox's forced 60-day partition expiration would delete seeded history. Tables in the sandbox also expire 60 days after creation, which is fine for the one-week check.
- **Known limits.** GitHub disables scheduled workflows after 60 days without repo activity, and cron runs can start late. Ticketmaster events are insert-only, so a rescheduled show keeps its first-seen date. Feed sources (YouTube RSS, Google News, Reddit) only expose their latest items, so a gap longer than the feed window cannot be backfilled from them.
- **Cost.** Expected $0: GitHub Actions is free for public repos, BigQuery sandbox is free (10 GB storage, 1 TB queries/month), all APIs are free tiers.
