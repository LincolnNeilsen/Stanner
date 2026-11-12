# Stanner — Handoff Notes

Context for continuing this project in a new Claude session or on a new machine.
Read this first, then the plan below.

## Goal
CS 452 "Build a Pipeline" assignment (https://github.com/byu-cs-452/byu-cs-452-class-content/blob/main/pipeline/build_a_pipeline.md).
A free data pipeline that tracks the music artists the user follows: song releases, song announcements, collaborations (ads, talk shows, variety shows), new videos/vlogs, concert and tour dates, and solo activity of group members. Instructor has approved the music-artist source idea.
Later goal (not in scope now): a multi-user web app where people make accounts and follow artists. Keep that in a separate `plans.md` when started.

## Assignment requirements (summary)
- One-time bulk seed of historical data, saved raw locally, then bulk-loaded (different path from the scheduled job).
- Automatic recurring ingestion with no manual trigger. Cadence is our choice; hourly batch is the standard.
- Idempotent (feeds overlap): dedupe by unique IDs / MERGE.
- Document gap handling (seed vs. live lag) and API-down behavior.
- Secrets never in the repo.
- Cost accounting: report actual spend (expected $0).
- Deliverables: architecture diagram and 30-second demo video. A follow-up checks the pipeline one week later, so it must keep running.

## Decided plan
| Stage | Choice |
|---|---|
| Schedule/ingest | GitHub Actions cron (public repo) + Python |
| Seed | One-off local Python → Parquet → `bq load` from saved raw files |
| Buffer | Skipped (direct writes); mention in writeup |
| Store | BigQuery sandbox (dataset `artist_pipeline`) |
| Visualize | Looker Studio |
| Transform | Plain SQL views (bronze → silver → gold) |

### BigQuery sandbox gotchas
- No streaming inserts. Use load jobs into `events_staging`, then `MERGE` into `events`.
- Sandbox default 60-day table expiration. Set expiration to none where allowed; otherwise note it in the writeup.
- Capacity: 10 GB storage. Load a filtered MusicBrainz slice, not the full dump. Document the math.

### Data sources
- **MusicBrainz** (dump for seed, API at ≤1 req/s live): artists and member/sub-unit relations. Auto-follow the whole tree downward: group → sub-units → their members → their sub-units, any depth. Dedupe by MBID.
- **YouTube**: channel RSS (no key) for new videos; Data API `playlistItems` for back catalog (10k units/day).
- **Ticketmaster Discovery** (free key, 5k/day): upcoming concerts.
- **setlist.fm** (free key): past concerts, history.
- **Google News RSS** per artist + fan subreddit RSS: announcements, collabs, talk/variety shows. Keyword-rule classifier first.
- Songkick and Bandsintown are skipped (need approval).

### Schema (dataset `artist_pipeline`)
- `artists(id, name, mbid, kind[group|person], youtube_channel_id)`
- `artist_links(parent_id, child_id, relation[member|subgroup])`
- `followed_artists`: recursive CTE view over `artist_links`, starting from the user's seed follows. Refreshed daily.
- `events(source, source_id, artist_id, type, title, url, event_time, published_at, ingested_at, payload JSON)`. Partition by `DATE(published_at)`, cluster by `artist_id, type`.
- `events_staging`: same columns, truncated each run.
- `ingest_runs(source, started_at, finished_at, status, rows_new, watermark)`
- `type ∈ release | announcement | video | concert | appearance | news`
- Gold views: `upcoming_concerts`, `feed_last_7d`, `monthly_release_counts`.

### Requirements mapping
- **Idempotency:** staging + `MERGE ... WHEN NOT MATCHED` on `(source, source_id)`; fetch with a watermark − 1 day overlap.
- **Gaps:** each source fetches since its last successful watermark. The first live run starts at the dump date, which closes the seed gap. An API outage makes the run exit non-zero; the next run backfills from the watermark. Empty runs log `status=ok, rows_new=0`.
- **Cadence:** hourly workflow (YouTube, news, Ticketmaster); daily workflow (MusicBrainz, setlist.fm).
- Known gotcha for the writeup: GitHub disables scheduled workflows after 60 days of repo inactivity, and cron runs can be late.

## Build order and status
1. [x] Create folder `C:\Users\lincn\Desktop\prjcts\Stanner`, git init, README, `.gitignore`
2. [x] Create public GitHub repo https://github.com/LincolnNeilsen/Stanner and push `main`
3. [x] Write `.env.example` (placeholders only; not yet committed)
4. [ ] Create GCP project, enable BigQuery sandbox, create dataset `artist_pipeline`
5. [ ] Create service account (BigQuery Data Editor + Job User), download JSON key to `C:\Users\lincn\Desktop\prjcts\secrets\gcp-key.json` (outside repo)
6. [ ] Get API keys: YouTube (enable YouTube Data API v3 in the GCP project), Ticketmaster Discovery, setlist.fm
7. [ ] Create local `.env` from `.env.example`
8. [ ] `schema.sql` (tables + views)
9. [ ] Seed: MusicBrainz dump → filter → Parquet → `bq load`; cache setlist.fm and YouTube back catalog to disk
10. [ ] `ingest/<source>.py` fetchers, sharing one `load_and_merge()` + `ingest_runs` helper
11. [ ] `.github/workflows/hourly.yml` and `daily.yml`; add repo secrets `GCP_SA_KEY`, `YOUTUBE_API_KEY`, `TICKETMASTER_API_KEY`, `SETLIST_FM_API_KEY`
12. [ ] Looker Studio dashboard (growth by source, last-run status, this year vs. 10-year monthly average)
13. [ ] Architecture diagram + 30-second demo video
14. [ ] Keep everything running until the one-week follow-up; record the outcome

## Not in the repo (recreate on a new machine)
- `.env` (real keys) and `secrets/gcp-key.json`. These are git-ignored on purpose. Re-create them from the provider consoles, or move them through a password manager. Do not paste keys into chat or commit them.
- GitHub login (`gh auth login`) and Git Credential Manager login on the new machine.
- Repo secrets live in GitHub and survive a machine change.

## Verification checklist (from the plan)
- Each fetcher run twice locally leaves the `events` row count unchanged the second time.
- A failed run is backfilled by the next good run.
- `gitleaks detect` is clean before publishing; no keys in git history.
- After 3+ scheduled runs, `ingested_at` shows growth without manual triggers.
- Dashboard query returns both seed and live rows.
- `followed_artists` for a group includes its sub-units, their members, and members' solo projects.

## Notes for the next session
- The user's preference: put future web-app plans in `plans.md`, not in this plan.
- The user wants the MusicBrainz sub-unit auto-follow to walk the full tree.
- The user chose BigQuery over Supabase for more storage space.
- Commits: include the Co-Authored-By trailer required by the environment instructions. Commit and push only when the user asks.
