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
- Create a **Cloudflare Worker**. Use **legacy Pages** (`Continue to Pages` at the bottom of Create application).
- Point Pages production at a filled preview branch if this repo is meant to stay a template.
- Put domain tags in the template `config/`. Those belong in the instance fork.

## Stand up an instance

1. Fork this repo, or branch from `main`.
2. Fill `config/watch.yaml`: name, `base_url` (the `*.pages.dev` host once known), description, `default_tag`, preferred chips, hidden tags, `relevance` (`always_match` / `required_any` / `context_any`), optional topics.
3. Fill `config/source-seeds.yaml` with this instance's docs, repos, PRs, crates, and optional `live_collectors.github_repository_searches`.
4. Optional per seed:
   - `discovered_at` — ISO time the source appeared (tracker issue `created_at`, or leave unset so live GitHub uses repo `created_at`). Weeks use this.
   - `activity_at` — last known movement. Live refresh overwrites if GitHub is newer.
   - `live_activity: false` — skip `pushed_at` on noisy monorepos. Add `github_pull_requests` for the PRs that actually matter.
5. Generate artifacts (never commit another project's feed):

```bash
GITHUB_TOKEN=$(gh auth token) python3 scripts/build_seed_feed.py
python3 scripts/sync_hugo_content.py
python3 scripts/verify_public_artifacts.py
python3 -m unittest discover -s tests -v
hugo --source site --minify
```

Seed-only (no GitHub HTTP):

```bash
python3 scripts/build_seed_feed.py --seed-only
```

`config/source-seeds.yaml` is the pipeline input. Cloudflare Pages only runs Hugo from `site/` and serves whatever is already in `site/static/`. Changing seeds does nothing on the deployed site until you run the pipeline and commit the generated artifacts.

6. Cloudflare Pages:
   - Create application → **Continue to Pages** (legacy).
   - Repo: this instance. Production branch: the instance's `main`.
   - Root `site`, build `hugo --minify -b $CF_PAGES_URL`, output `public`.
   - `HUGO_VERSION=0.164.0` on Production **and** Preview.
   - After the hostname exists, set `config/watch.yaml` `base_url` to `https://<project>.pages.dev/` and re-sync Hugo.

## Host locally

Needs Python 3, PyYAML (`pip3 install pyyaml`), and Hugo (`hugo version`). Pages pins `HUGO_VERSION=0.164.0`; a current extended build is fine locally.

```bash
python3 scripts/build_seed_feed.py --seed-only
python3 scripts/sync_hugo_content.py
python3 scripts/verify_public_artifacts.py
hugo server --source site
```

Open http://localhost:1313/. Empty seeds still render the chrome with an empty feed.

Live GitHub timestamps and repository searches:

```bash
GITHUB_TOKEN=$(gh auth token) python3 scripts/build_seed_feed.py
python3 scripts/sync_hugo_content.py
hugo server --source site
```

Stop with Ctrl-C. `hugo server` does not run the Python pipeline.

## Dates

| Field | Meaning | UI |
|---|---|---|
| `discovered_at` | When the source appeared | Weeks, "new this week" |
| `activity_at` | Last real movement | Home feed, project cards, week row timestamps |
| `observed_at` | Last crawl | Not shown as the event time |

Live GitHub search hits: `discovered_at` = repo `created_at`, `activity_at` = `pushed_at`. Do not use crawl time as discovery.

## Candidate discovery

`live_collectors.github_repository_searches` run GitHub repo search at build time. Hits that pass `watch.yaml` `relevance` become feed items with `status: candidate` and `event_type: source_discovered`. They are public matches, not the accepted seeded catalog. To promote one, add it under `seeded_sources` and rebuild.

## UI this engine adds

- Project cards list linked sources (PRs as `#123` → that PR).
- Sources page: search + repo/PR/docs/crate chips.
- Weeks rail: per-week item counts. Rows sort by activity.
- Header `nav` styles do not leak onto the week rail.

## Checks before you stop

- `config/watch.yaml` name is this instance, not Example Watch.
- `config/source-seeds.yaml` has this instance's sources only.
- `python3 -m unittest discover -s tests -v` passes.
- `site/static/watch.json` chips match `config/watch.yaml`.
- Pages project type is Pages (`*.pages.dev`), not a Worker.
