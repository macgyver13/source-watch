# Agent guide: Source Watch

This file is for an agent standing up or refreshing a watch instance.
Humans can follow it too. Read `README.md` for product context.

## Repo map

| Ref | What it is |
|---|---|
| `main` | Blank reusable template. Empty seeds, empty feed. |
| `preview/*` | Filled instance previews. Complete working watches. Do **not** copy their `config/` or `data/` into the template or another instance. |

This template is the starting point for any instance. Domain names, tags, and seeds belong in the instance fork, not here.

## Do not

- Copy `feed.json`, `items.jsonl`, `projects.json`, `sources.json`, or week pages from a filled instance into a new one.
- Create a **Cloudflare Worker** for a `serving.mode: static` instance. Static instances use **legacy Pages** (`Continue to Pages` at the bottom of Create application).
- Deploy a `serving.mode: service` instance as Pages. Service instances are a Worker with static assets plus D1, never Pages.
- Point Pages production at a filled preview branch if this repo is meant to stay a template.
- Put domain tags in the template `config/`. Those belong in the instance fork.

## Stand up an instance

1. Fork this repo, or branch from `main`.
2. Fill `config/watch.yaml`: name, `base_url` (the `*.pages.dev` host once known, or the Worker origin in service mode), description, `default_tag`, preferred chips, hidden tags, `relevance` (`always_match` / `required_any` / `context_any`), optional `discovered_after`, optional topics, optional `serving` (`mode: static|service`, `service_url` required when `mode` is `service`).

3. Fill `config/source-seeds.yaml` with this instance's docs, repos, PRs, crates, and optional live collectors (`github_repository_searches`, `github_pull_request_searches`, `delving_topic_searches`, `delving_category_listings`).
4. Optional per seed:
   - `discovered_at` — fallback when live GitHub `created_at` is unavailable (docs, crates, `--seed-only`) or older than `watch.yaml` `discovered_after`. Live GitHub overwrites this for seeded repos/PRs on or after that floor. Weeks use this.
   - `activity_at` — last known movement. Live refresh overwrites if GitHub is newer.
   - `live_activity: false` — skip `pushed_at` on noisy monorepos. Does not skip `created_at` for discovery; set `discovered_after` so pre-topic repo birth dates do not open weeks. Add `github_pull_requests` for the PRs that actually matter.
5. Generate artifacts (never commit another project's feed):

```bash
GITHUB_TOKEN=$(gh auth token) python3 scripts/build_seed_feed.py
python3 scripts/sync_hugo_content.py
python3 scripts/verify_public_artifacts.py
python3 -m unittest discover -s tests -v
hugo --source site --minify
```

Seed-only (no GitHub or Delving HTTP):

```bash
python3 scripts/build_seed_feed.py --seed-only
```

`config/source-seeds.yaml` is the pipeline input. In **static** mode Cloudflare Pages only runs Hugo from `site/` and serves JSON already in `site/static/`. Changing seeds does nothing on the deployed site until you run the pipeline and commit those artifacts. In **service** mode the collector ingests into D1; do not commit another instance's feed JSON.

6. Cloudflare hosting depends on `serving.mode`:
   - **static:** Create application → **Continue to Pages** (legacy). Repo: this instance. Production branch: the instance's `main`. Root `site`, build `hugo --minify -b $CF_PAGES_URL`, output `public`. `HUGO_VERSION=0.164.0` on Production **and** Preview. After the hostname exists, set `config/watch.yaml` `base_url` to `https://<project>.pages.dev/` and re-sync Hugo.
   - **service:** Worker with static assets plus D1, never Pages. Follow **Service mode** below.

## Host locally

Needs Python 3, PyYAML (`pip3 install pyyaml`), and Hugo (`hugo version`). Pages pins `HUGO_VERSION=0.164.0`; a current extended build is fine locally. Service mode also needs Node 22+ (`nvm`) and `npm install`.

Static:

```bash
python3 scripts/build_seed_feed.py --seed-only
python3 scripts/sync_hugo_content.py
python3 scripts/verify_public_artifacts.py
hugo server --source site
```

Open http://localhost:1313/. Empty seeds still render the chrome with an empty feed. `hugo server` does not run the Python pipeline.

Live GitHub timestamps, repository searches, pull-request searches, and Delving topic collectors:

```bash
GITHUB_TOKEN=$(gh auth token) python3 scripts/build_seed_feed.py
python3 scripts/sync_hugo_content.py
hugo server --source site
```

Service (local Worker). Put `ADMIN_TOKEN=devadmin` and `INGEST_TOKEN=devingest` in `.dev.vars` (gitignored). Set `serving.mode: service` and `serving.service_url: "http://localhost:8787/"`.

```bash
python3 scripts/sync_hugo_content.py
npx wrangler d1 migrations apply source-watch --local   # once
PATH="$HOME/.nvm/versions/node/v22.16.0/bin:$PATH" npx wrangler dev --test-scheduled
# other shell:
SOURCE_WATCH_INGEST_TOKEN=devingest \
  python3 scripts/build_seed_feed.py --seed-only --allow-partial-ingest
```

Open http://localhost:8787/ and http://localhost:8787/admin (token `devadmin`). Restore `serving.mode: static` and re-run `sync_hugo_content.py` when done. `--seed-only` in service mode requires `--allow-partial-ingest`. Live collect: `GITHUB_TOKEN=$(gh auth token) SOURCE_WATCH_INGEST_TOKEN=devingest python3 scripts/build_seed_feed.py`.

## Service mode

Worker origin hosts Hugo assets, live `/feed.json` (and related artifacts) from D1, and `/admin`. The Python collector POSTs into the Worker. Needs a Workers plan that can finish `renderAll` (a few thousand items exceeds the free-plan 10 ms CPU cap).

### First deploy

1. In `config/watch.yaml` set `serving.mode: service`. Leave `service_url` empty until the hostname exists.
2. `npx wrangler d1 create source-watch` and paste `database_id` into `wrangler.jsonc` (replace the `0000…` placeholder).
3. `npx wrangler d1 migrations apply source-watch --remote`
4. Secrets (same values locally in `.dev.vars`):

| Secret | Where | Used for |
|---|---|---|
| `ADMIN_TOKEN` | `npx wrangler secret put ADMIN_TOKEN` | Bearer token for `/api/admin/*` and the `/admin` UI |
| `INGEST_TOKEN` | `npx wrangler secret put INGEST_TOKEN` | Worker side of collector ingest |
| `SOURCE_WATCH_INGEST_TOKEN` | GitHub Actions secret **and** local env | Collector; **same value as** `INGEST_TOKEN` |
| `GITHUB_DISPATCH_TOKEN` | `npx wrangler secret put GITHUB_DISPATCH_TOKEN` | Worker cron / Refresh now → `workflow_dispatch` (PAT or fine-grained token with `actions:write`) |
| `GITHUB_TOKEN` | Actions provides this | Collector GitHub API (public repo searches) |

5. Set wrangler `vars.GITHUB_DISPATCH_REPO` to `owner/repo` (this instance). Keep `GITHUB_DISPATCH_WORKFLOW=refresh-feed.yml` and `GITHUB_DISPATCH_REF` on the branch that has the workflow.
6. `python3 scripts/sync_hugo_content.py` (writes the service Hugo shell, including `weeks/live`).
7. If this was a static site: `python3 scripts/promote_to_service.py --dry-run` then without `--dry-run`. It deletes generated `site/static` feed JSON, `data/public/*`, dated week pages, and item-derived content dirs. `config/` is untouched. Then `sync_hugo_content.py` again.
8. Deploy: dashboard Build command `hugo --source site --minify`, Deploy command `npx wrangler deploy`, `HUGO_VERSION=0.164.0`. Or locally `npx wrangler deploy`.
9. Set `serving.service_url` and `base_url` to `https://<worker>.<subdomain>.workers.dev/` (trailing slash). Re-sync Hugo if `base_url` changed.
10. First ingest (from a machine with the token, or Actions `workflow_dispatch`):

```bash
SOURCE_WATCH_INGEST_TOKEN=… GITHUB_TOKEN=$(gh auth token) python3 scripts/build_seed_feed.py
python3 scripts/verify_public_artifacts.py
```

`verify_public_artifacts.py` in service mode fetches `{service_url}feed.json` and `items.jsonl`. Unreachable Worker → non-zero.

### Refresh

The Worker cron (`17 * * * *`) is the scheduler. It POSTs GitHub `workflow_dispatch` for `.github/workflows/refresh-feed.yml`. That workflow runs the collector and ingests; it commits nothing. If `GITHUB_DISPATCH_REPO` or `GITHUB_DISPATCH_TOKEN` is empty, cron writes `refresh_skipped` and `/admin` shows `refresh not configured`.

### Admin

`GET /admin` is public HTML with no data. Every API call sends `Authorization: Bearer <ADMIN_TOKEN>` (the page keeps it in `sessionStorage` as `sw_admin_token`). Failed `/api/admin/*` auths are limited to **10 per client IP per minute** (429 + `Retry-After: 60` after that). Successful admin traffic is not limited.

| Action | Effect |
|---|---|
| Hide / Unhide | Item/project/source overlay; public feed updates immediately |
| Exclude | Drops matching **live** hits on the next collect **and** hides current matches in the overlay. Non-GitHub URLs store `url_prefix`. Seeded catalog rows stay in D1; they are hidden, not deleted |
| Include term | Appended to `relevance` on the **next** collect |
| Seed addition | Merged into `seeded_sources` on the **next** collect (does not edit YAML) |
| `discovered_after` | Overrides yaml on the next collect |
| Promote | Patch `status: seeded` on that item only; to keep it across rebuilds add a seed addition or YAML seed |
| Refresh now | Same dispatch as cron; disabled until dispatch is configured |

Promote a candidate for real: Rules → Seed additions (or YAML), then Refresh / next ingest.

## Dates

| Field | Meaning | UI |
|---|---|---|
| `discovered_at` | When the source appeared | Weeks, "new this week", week row timestamps |
| `activity_at` | Last real movement | Home feed, project cards |
| `observed_at` | Last crawl | Not shown as the event time |

Live GitHub search hits **and** seeded GitHub repos/PRs: `discovered_at` = repo/PR `created_at` when that stamp is on or after `watch.yaml` `discovered_after` (unset = no floor). Earlier `created_at` is ignored: live hits are dropped, seeded sources keep yaml/`first-seen`. `activity_at` = repo `pushed_at` or PR `merged_at`/`updated_at`. Live Delving hits: `discovered_at` = topic `created_at`, `activity_at` = `last_posted_at`, with the same floor. Do not use crawl time as discovery.

## Candidate discovery

`live_collectors.github_repository_searches`, `github_pull_request_searches`, `delving_topic_searches`, and `delving_category_listings` run at collect time. Hits that pass `watch.yaml` `relevance` and `discovered_after` become feed items with `status: candidate` and `event_type: source_discovered`. Live hits whose name, URL, or text contains `source-watch` (this engine repo and forks) are dropped. They are public matches, not the accepted `seeded_sources` catalog. To promote one: static — add it under `seeded_sources` and rebuild; service — `/admin` Seed additions (or YAML) then the next ingest. A seeded GitHub repository does **not** hide matching PRs from `github_pull_request_searches`. Delving search-only topics that age out of `max_results` may freeze `activity_at`; that is accepted.


## UI this engine adds

- Project cards list linked sources (PRs as `#123` → that PR).
- Sources page: search + repo/PR/docs/crate chips.
- Weeks rail: per-week item counts. Rows sort by discovery; timestamps are `discovered_at` (always inside that ISO week).
- Header `nav` styles do not leak onto the week rail.

## Checks before you stop

- `config/watch.yaml` name is this instance, not Example Watch.
- `config/source-seeds.yaml` has this instance's sources only.
- `python3 -m unittest discover -s tests -v` passes.
- `site/static/watch.json` chips match `config/watch.yaml` in static mode; in service mode the Worker `/watch.json` does.
- Static instances are Pages (`*.pages.dev`), not a Worker. Service instances are a Worker with D1, not Pages.
- Service first deploy: `database_id` is not the `0000…` placeholder; `d1 migrations apply --remote` ran; `ADMIN_TOKEN` and `INGEST_TOKEN` secrets exist; `SOURCE_WATCH_INGEST_TOKEN` matches `INGEST_TOKEN`; `serving.service_url` is the Worker origin; first ingest printed an `ingest_id`; `GET /feed.json` has items; `GET /api/admin/state` is 401 without a token and 200 with `ADMIN_TOKEN`; `python3 scripts/verify_public_artifacts.py` passes.


