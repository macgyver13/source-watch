# Source Watch

Source Watch is a feed-first static public-source tracker. Fill the config, generate artifacts, and host the Hugo site on Cloudflare Pages or GitHub Pages. No Workers.

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
2. Fill `config/watch.yaml` (name, base URL, description, tags, relevance, optional topics).
3. Fill `config/source-seeds.yaml` with the docs pages, repositories, PRs, and crates to track, plus any live GitHub repository searches or Delving topic collectors.
4. Run the local pipeline below so `site/static/` has fresh artifacts.
5. Point Cloudflare Pages **or** GitHub Pages at `site/`.

Seeds are build-time input. Pages/Hugo only compile `site/` and serve the JSON already in `site/static/`. Editing `config/source-seeds.yaml` does not change a deployed site until you run the pipeline and commit those artifacts.

Agent-oriented stand-up: see `AGENTS.md`.

This repo is not marked as a GitHub template yet, and it does not ship a GitHub Actions workflow. Refresh the feed locally (or with your own CI) until those are added.

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
`activity_at` timestamps and runs live collectors
(`github_repository_searches`, `delving_topic_searches`,
`delving_category_listings`). Each collector emits **candidate**
`source_discovered` items alongside seeds. A candidate is a search or
category hit that passed `watch.yaml` `relevance`; it is not yet in the
accepted `seeded_sources` catalog. Discovery dates on seeds stay put;
activity moves if GitHub or Delving is newer.

Seed-only (no GitHub or Delving HTTP):

```bash
python3 scripts/build_seed_feed.py --seed-only
# or: SOURCE_WATCH_SKIP_LIVE=1 python3 scripts/build_seed_feed.py
```

## Cloudflare Pages

- Root directory: `site`
- Build command: `hugo --minify`
- Build output directory: `public`
- Environment: `HUGO_VERSION=0.164.0`

Pages builds Hugo from `site/`. Feed artifacts in `site/static/` come from the Python pipeline above.

## GitHub Pages

Use the same Hugo settings: build from `site/` with `hugo --minify`,
`HUGO_VERSION=0.164.0`, output `public`.

A typical setup is a GitHub Pages workflow (or the Pages UI) that publishes
the Hugo output, or a `gh-pages` branch containing the built `public/`
directory.

## Config

- `config/watch.yaml` — instance identity: name, base URL, description, default tag, preferred chips, hidden tags, relevance rules, optional topic tiles. Chips, hidden tags, and name ship in `watch.json` for the client. `relevance` filters live collector hits (`always_match` short-circuits accept; `required_any` / `context_any` must appear in the GitHub description/topics or Delving title/excerpt/tags).
- `config/source-seeds.yaml` — seeded sources and live collectors. Pipeline input only; not read at request time.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
