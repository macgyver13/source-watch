# Source Watch

Source Watch is a feed-first static public-source tracker. Fill the config, generate artifacts, and host the Hugo site on Cloudflare Pages or GitHub Pages. No Workers.

Originally extracted from frost-watch.

Primary artifacts:

- `feed.json` — canonical latest structured feed
- `feed.xml` — RSS feed
- `items.jsonl` — normalized item stream
- `projects.json` — project catalog
- `sources.json` — monitored source catalog
- `watch.json` — instance identity for the client (name, chips, hidden tags, topics)

The Hugo site under `site/` renders the public website from these artifacts.

## How to use

1. Fork or copy this repository.
2. Fill `config/watch.yaml` (name, base URL, description, tags, relevance, optional topics).
3. Fill `config/source-seeds.yaml` with the docs pages, repositories, PRs, and crates to track, plus any live GitHub repository searches.
4. Run the local pipeline below so `site/static/` has fresh artifacts.
5. Point Cloudflare Pages **or** GitHub Pages at `site/`.

This repo is not marked as a GitHub template yet, and it does not ship a GitHub Actions workflow. Refresh the feed locally (or with your own CI) until those are added.

## Scope

This project aggregates public source metadata and activity. Inclusion is not endorsement, technical review, security assessment, production-readiness judgment, or a canonical roadmap.

## Local development

```bash
python3 scripts/build_seed_feed.py
python3 scripts/sync_hugo_content.py
python3 scripts/verify_public_artifacts.py
hugo --source site --minify
```

`config/source-seeds.yaml` supports live GitHub repository discovery under
`live_collectors.github_repository_searches`. Each collector runs a GitHub
repository search during refresh and emits candidate `source_discovered` items
into the structured feed alongside the static seeded sources.

Set `GITHUB_TOKEN` or `GH_TOKEN` if you want authenticated GitHub search
(higher rate limits). The live collector does not require a token, but
unauthenticated calls are tightly rate-limited.

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

- `config/watch.yaml` — instance identity: name, base URL, description, default tag, preferred chips, hidden tags, relevance rules, optional topic tiles.
- `config/source-seeds.yaml` — seeded sources and live collectors.
- `config/publish-rules.yaml` — publication rules and banned assessment terms.
- `config/tag-vocabulary.yaml` — suggested tags for the instance.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
