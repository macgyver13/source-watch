# Agent guide: Source Watch

This file is for an agent standing up or refreshing a watch instance.
Humans can follow it too. Read `README.md` for product context.

## Repo map

| Ref | What it is |
|---|---|
| `main` | Blank reusable template. Empty seeds, empty feed. |
| `preview/reusable-engine` | Engine + UI from the Silent Payments preview, **without** that instance's config or feed. Pull this. |
| `preview/silent-payments` | Filled Silent Payments Watch. Do **not** copy its `config/` or `data/` into another instance. |
| [macgyver13/frost-watch](https://github.com/macgyver13/frost-watch) | Live FROST instance at https://frost-watch.pages.dev/ |

`preview/reusable-engine` is the branch to merge into `main` or to start a FROST (or any) instance from.

## Do not

- Copy `feed.json`, `items.jsonl`, `projects.json`, `sources.json`, or week pages from frost-watch or silent-payments into a new instance.
- Create a **Cloudflare Worker**. Use **legacy Pages** (`Continue to Pages` at the bottom of Create application).
- Point Pages production at `preview/silent-payments` if this repo is meant to stay a template.
- Put domain tags (FROST, BIP-352, …) in the template `config/`. Those belong in the instance fork.

## Stand up a FROST Watch from this engine

1. Branch from `preview/reusable-engine` (or merge it onto a fork of `main`).
2. Fill `config/watch.yaml` from frost-watch identity: name, `base_url` (the `*.pages.dev` host once known), description, `default_tag: frost`, preferred chips, hidden tags, `relevance` (always_match / required_any / context_any). Copy those knobs from frost-watch, not from silent-payments.
3. Fill `config/source-seeds.yaml` with FROST docs, repos, PRs, crates, and `live_collectors.github_repository_searches`. Start from frost-watch seeds if they are current; do not start from silent-payments seeds.
4. Optional per seed:
   - `discovered_at` — ISO time the source appeared (tracker issue `created_at`, or leave unset so live GitHub uses repo `created_at`). Weeks use this.
   - `activity_at` — last known movement. Live refresh overwrites if GitHub is newer.
   - `live_activity: false` — skip `pushed_at` on noisy monorepos (`bitcoin/bitcoin`, firmware trees). Add `github_pull_requests` for the PRs that actually matter.
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

6. Cloudflare Pages (same as frost-watch):
   - Create application → **Continue to Pages** (legacy).
   - Repo: this instance. Production branch: the instance's `main`.
   - Root `site`, build `hugo --minify -b $CF_PAGES_URL`, output `public`.
   - `HUGO_VERSION=0.164.0` on Production **and** Preview.
   - After the hostname exists, set `config/watch.yaml` `base_url` to `https://<project>.pages.dev/` and re-sync Hugo.

## Port this engine into existing frost-watch

Keep frost-watch `config/`, `data/public/`, and `site/static/{feed,items,projects,sources,watch}.*`.

Take from `preview/reusable-engine`:

- `scripts/build_seed_feed.py`
- `scripts/sync_hugo_content.py`
- `tests/`
- `site/static/app.js`
- `site/static/style.css`
- `site/layouts/weeks/list.html`
- `site/layouts/sources/list.html`

Then run the pipeline **in frost-watch** so FROST dates/feeds stay FROST.

## Dates

| Field | Meaning | UI |
|---|---|---|
| `discovered_at` | When the source appeared | Weeks, "new this week" |
| `activity_at` | Last real movement | Home feed, project cards, week row timestamps |
| `observed_at` | Last crawl | Not shown as the event time |

Live GitHub search hits: `discovered_at` = repo `created_at`, `activity_at` = `pushed_at`. Do not use crawl time as discovery.

## UI this engine adds

- Project cards list linked sources (PRs as `#123` → that PR).
- Sources page: search + repo/PR/docs/crate chips.
- Weeks rail: per-week item counts. Rows sort by activity.
- Header `nav` styles do not leak onto the week rail.

## Checks before you stop

- `config/watch.yaml` name is the instance (FROST Watch), not Example Watch or Silent Payments Watch.
- `config/source-seeds.yaml` has no silent-payments seeds or BIP-352 search list unless this **is** that instance.
- `python3 -m unittest discover -s tests -v` passes.
- `site/static/watch.json` chips match `config/watch.yaml`.
- Pages project type is Pages (`*.pages.dev`), not a Worker.
