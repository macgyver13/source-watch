# Source Watch

Source Watch is a feed-first public-source tracker. Fill the config, generate artifacts, and host the Hugo site on Cloudflare Pages (static mode) or a Cloudflare Worker with D1 (service mode).

Primary artifacts:

- `feed.json` — canonical latest structured feed
- `feed.xml` — RSS feed
- `items.jsonl` — normalized item stream
- `projects.json` — project catalog
- `sources.json` — monitored source catalog
- `watch.json` — instance identity for the client (name, chips, hidden tags, topics)

The Hugo site under `site/` renders the public website from these artifacts.

Committed `feed.json`, `feed.xml`, `items.jsonl`, `projects.json`, and `sources.json` ship empty so a fork starts blank. Fill `config/source-seeds.yaml` and run the pipeline to populate them. Do not commit another project's live feed into this template.

## How to use

1. Fork or copy this repository.
2. Fill `config/watch.yaml` (name, base URL, description, tags, relevance, optional topics, optional `serving`).
3. Fill `config/source-seeds.yaml` with the docs pages, repositories, PRs, and crates to track, plus any live GitHub repository/PR searches or Delving topic collectors.
4. **static:** run the local pipeline so `site/static/` has fresh artifacts, then point Cloudflare Pages or GitHub Pages at `site/`.
   **service:** do not publish those JSON files. Follow **Cloudflare Worker (service mode)** below; the collector ingests into D1.

Seeds are pipeline input. In static mode Pages/Hugo only compile `site/` and serve JSON already in `site/static/`. In service mode a seed or YAML change lands on the next ingest.

Agent-oriented stand-up (first deploy, secrets, `/admin`): see `AGENTS.md`.

Static instances refresh the feed locally (or with your own CI). Service instances use `.github/workflows/refresh-feed.yml` via Worker cron or `workflow_dispatch`.


## Scope

This project aggregates public source metadata and activity. Inclusion is not endorsement, technical review, security assessment, production-readiness judgment, or a canonical roadmap.

## Host locally

Needs Python 3, [PyYAML](https://pyyaml.org/) (`pip3 install pyyaml`), and [Hugo](https://gohugo.io/). Pages pins `HUGO_VERSION=0.164.0`; a current extended build is fine locally.

```bash
python3 scripts/build_seed_feed.py --seed-only
python3 scripts/sync_hugo_content.py
python3 scripts/verify_public_artifacts.py
hugo server --source site
```

Open http://localhost:1313/. Empty seeds still render the site chrome with an empty feed.

`hugo server` does not run the Python pipeline. Re-run the scripts after config or seed changes, then refresh the browser.

## Local pipeline

```bash
python3 scripts/build_seed_feed.py
python3 scripts/sync_hugo_content.py
python3 scripts/verify_public_artifacts.py
hugo --source site --minify
```

`python3 scripts/build_seed_feed.py` refreshes seeded GitHub repo/PR
timestamps and runs live collectors
(`github_repository_searches`, `github_pull_request_searches`,
`delving_topic_searches`, `delving_category_listings`). Repository search
does not see PRs inside an already-seeded repo; PR search does. Each
collector emits **candidate** `source_discovered` items alongside seeds.
A candidate is a search or category hit that passed `watch.yaml`
`relevance` and, if set, `discovered_after`; it is not yet in the accepted
`seeded_sources` catalog. Hits whose name, URL, or text contains
`source-watch` (this engine and forks) are dropped.
Seeded GitHub repos/PRs take live `created_at` as `discovered_at` when that
stamp is on or after `discovered_after`; older `created_at` keeps the seed
date. Activity moves if GitHub or Delving is newer. Docs, crates, and
`--seed-only` keep seed or first-seen discovery.

Seed-only (no GitHub or Delving HTTP):

```bash
python3 scripts/build_seed_feed.py --seed-only
# or: SOURCE_WATCH_SKIP_LIVE=1 python3 scripts/build_seed_feed.py
```

## Cloudflare Pages (static mode)

- Root directory: `site`
- Build command: `hugo --minify`
- Build output directory: `public`
- Environment: `HUGO_VERSION=0.164.0`


Pages builds Hugo from `site/`. Feed artifacts in `site/static/` come from the Python pipeline above. Do not create a Worker for a `serving.mode: static` instance.

## Cloudflare Worker (service mode)

Set `serving.mode: service` in `config/watch.yaml`. The Worker serves Hugo assets, live `/feed.json` from D1, and `/admin`. The Python collector POSTs into the service instead of writing `site/static/`.

Numbered first deploy, secret matrix, and operator notes: `AGENTS.md` **Service mode**. Short path:

1. `npx wrangler d1 create source-watch` → paste `database_id` into `wrangler.jsonc`.
2. `npx wrangler d1 migrations apply source-watch --remote`
3. `npx wrangler secret put ADMIN_TOKEN` and `INGEST_TOKEN` (collector env `SOURCE_WATCH_INGEST_TOKEN` must be the **same value** as `INGEST_TOKEN`). Optional `GITHUB_DISPATCH_TOKEN` plus `vars.GITHUB_DISPATCH_REPO` for hourly refresh.
4. `python3 scripts/sync_hugo_content.py`. Former static site: `python3 scripts/promote_to_service.py` then sync again.
5. Build `hugo --source site --minify`, deploy `npx wrangler deploy`, `HUGO_VERSION=0.164.0`.
6. Set `serving.service_url` and `base_url` to `https://<worker>.<subdomain>.workers.dev/`. Ingest: `SOURCE_WATCH_INGEST_TOKEN=… python3 scripts/build_seed_feed.py`. `--seed-only` requires `--allow-partial-ingest`.

`GET /admin` is public chrome; APIs need `ADMIN_TOKEN`. Failed admin auths: 10 per IP per minute, then 429. Hide/exclude update the public feed immediately; include terms and seed additions apply on the next collect.

Local Worker: `.dev.vars` with `ADMIN_TOKEN` / `INGEST_TOKEN`, Node 22+, `npx wrangler d1 migrations apply source-watch --local`, `npx wrangler dev --test-scheduled`, ingest against `http://localhost:8787/`.



## GitHub Pages

Use the same Hugo settings: build from `site/` with `hugo --minify`,
`HUGO_VERSION=0.164.0`, output `public`.

A typical setup is a GitHub Pages workflow (or the Pages UI) that publishes
the Hugo output, or a `gh-pages` branch containing the built `public/`
directory.

## Config

- `config/watch.yaml` — instance identity: name, base URL, description, default tag, preferred chips, hidden tags, relevance rules, optional `discovered_after` (ISO date; drop live hits and ignore GitHub `created_at` before this), optional topic tiles, optional `serving` (`mode: static|service`; `service_url` required in service mode). Chips, hidden tags, and name ship in `watch.json` for the client. `relevance` filters live collector hits (`always_match` short-circuits accept; `required_any` / `context_any` must appear in the GitHub description/topics, PR title/body, or Delving title/excerpt/tags).
- `config/source-seeds.yaml` — seeded sources and live collectors. Pipeline input only; not read at request time.


## Tests

```bash
python3 -m unittest discover -s tests -v
```
