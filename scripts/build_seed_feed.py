#!/usr/bin/env python3
"""Build Source Watch v0 public feed artifacts from seeded sources.

The generated artifacts are static, but discovery metadata must be stable across
runs. This script loads the previous public artifacts, merges the current seeded
source set into them, and preserves first-discovery timestamps for known items,
sources, and projects.

Instance identity (name, base URL, description, default tag, relevance) comes
from config/watch.yaml. Seeds and live collectors come from
config/source-seeds.yaml.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
WATCH_CONFIG = ROOT / "config" / "watch.yaml"
CONFIG = ROOT / "config" / "source-seeds.yaml"
OUT = ROOT / "data" / "public"
STATIC = ROOT / "site" / "static"
GITHUB_SEARCH_API = "https://api.github.com/search/repositories"

GENERATED_SUMMARY_RE = re.compile(r"^seeded monitored source for ", re.I)
GENERATED_QUERY_RE = re.compile(r"^github repository matched .+ live collector query:", re.I)

EMPTY_WATCH = {
    "name": "Source Watch",
    "base_url": "https://example.com/",
    "description": "",
    "default_tag": "",
    "preferred_chips": [],
    "hidden_tags": [],
    "relevance": {"always_match": [], "required_any": [], "context_any": []},
    "topics": [],
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return parsedate_to_datetime(value)
        except Exception:
            return None


def parse_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        raise SystemExit(f"PyYAML is required to parse {path}")


def normalize_relevance(raw) -> dict:
    rel = raw if isinstance(raw, dict) else {}
    return {
        "always_match": [str(t) for t in (rel.get("always_match") or [])],
        "required_any": [str(t) for t in (rel.get("required_any") or [])],
        "context_any": [str(t) for t in (rel.get("context_any") or [])],
    }


def normalize_watch(data: dict | None) -> dict:
    data = data or {}
    watch = {
        "name": str(data.get("name") or EMPTY_WATCH["name"]),
        "base_url": str(data.get("base_url") or EMPTY_WATCH["base_url"]),
        "description": str(data.get("description") or "").strip(),
        "default_tag": str(data.get("default_tag") or "").strip(),
        "preferred_chips": list(data.get("preferred_chips") or []),
        "hidden_tags": list(data.get("hidden_tags") or []),
        "relevance": normalize_relevance(data.get("relevance")),
        "topics": list(data.get("topics") or []),
    }
    if not watch["base_url"].endswith("/"):
        watch["base_url"] += "/"
    return watch


def watch_from_cfg(cfg: dict) -> dict:
    """Allow tests to pass watch fields on the seeds cfg dict."""
    payload = {}
    if cfg.get("name") or cfg.get("project_name"):
        payload["name"] = cfg.get("name") or cfg.get("project_name")
    if cfg.get("base_url"):
        payload["base_url"] = cfg["base_url"]
    if cfg.get("description") or cfg.get("scope_note"):
        payload["description"] = cfg.get("description") or cfg.get("scope_note")
    for key in ("default_tag", "preferred_chips", "hidden_tags", "relevance", "topics"):
        if key in cfg:
            payload[key] = cfg[key]
    return normalize_watch(payload)


def load_watch(path: Path | None = None) -> dict:
    path = path or WATCH_CONFIG
    if not path.exists():
        return normalize_watch({})
    return normalize_watch(parse_yaml(path))


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "item"


def source_url_for(entry: dict, kind: str) -> str:
    if "url" in entry:
        return entry["url"]
    if "repo" in entry:
        return "https://github.com/" + entry["repo"]
    if kind == "crates":
        return entry.get("url") or "https://crates.io/crates/" + entry["name"]
    raise ValueError(f"cannot derive URL for {entry}")


def source_type_for(kind: str) -> str:
    return {
        "docs_pages": "docs_page",
        "github_repositories": "github_repository",
        "github_pull_requests": "github_pull_request",
        "crates": "package_crate",
    }.get(kind, kind)


def title_for(entry: dict, kind: str) -> str:
    if "name" in entry:
        return entry["name"]
    if kind == "github_pull_requests" and "url" in entry:
        match = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)/?$", entry["url"])
        if match:
            return f"{match.group(1)} #{match.group(2)}"
    if "repo" in entry:
        return entry["repo"]
    return entry.get("url", entry.get("id", "Seeded source"))


def is_generated_summary(summary: str | None) -> bool:
    text = (summary or "").strip()
    if not text:
        return False
    return bool(GENERATED_SUMMARY_RE.match(text) or GENERATED_QUERY_RE.match(text))


def summary_for(entry: dict, kind: str, old_item: dict | None = None) -> str:
    summary = str(entry.get("summary") or "").strip()
    if summary and not is_generated_summary(summary):
        return summary
    old_summary = str((old_item or {}).get("summary") or "").strip()
    if old_summary and not is_generated_summary(old_summary):
        return old_summary
    title = title_for(entry, kind)
    source_type = source_type_for(kind)
    if source_type == "github_pull_request":
        return f"Tracked pull request: {title}."
    if source_type == "github_repository":
        return f"Public repository: {title}."
    if source_type == "package_crate":
        return f"Published crate: {title}."
    if source_type == "docs_page":
        return f"Public documentation: {title}."
    return title


def merge_tags(watch: dict, *tag_lists) -> list[str]:
    tags: list[str] = []
    default_tag = (watch.get("default_tag") or "").strip()
    if default_tag:
        tags.append(default_tag)
    for lst in tag_lists:
        for tag in lst or []:
            text = str(tag).strip()
            if text and text not in tags:
                tags.append(text)
    return tags


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "source-watch-live-collector",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def search_github_repositories(query: str, max_results: int = 10) -> list[dict]:
    params = urlencode({"q": query, "per_page": max(1, min(int(max_results), 25)), "sort": "updated", "order": "desc"})
    request = Request(f"{GITHUB_SEARCH_API}?{params}", headers=github_headers())
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"warning: GitHub repository search failed for query {query!r}: {exc}", file=sys.stderr)
        return []
    return payload.get("items", [])


def repo_excluded_by_query_terms(repo: dict, query: str) -> bool:
    negative_terms = [term.lower() for term in re.findall(r"(?<!\S)-([a-zA-Z0-9_]+)", query)]
    if not negative_terms:
        return False
    haystack_parts = [
        str(repo.get("full_name", "")),
        str(repo.get("description", "")),
        " ".join(str(topic) for topic in repo.get("topics", [])),
    ]
    haystack = " ".join(haystack_parts).lower()
    return any(term in haystack for term in negative_terms)


def relevance_from_watch(watch: dict | None) -> dict:
    watch = watch or {}
    if isinstance(watch.get("relevance"), dict):
        return normalize_relevance(watch["relevance"])
    return normalize_relevance(watch)


def repo_matches_relevance_rules(repo: dict, watch: dict | None = None) -> bool:
    """Filter GitHub search hits using watch.relevance.

    always_match short-circuits True if any term appears in description/topics.
    If required_any is empty, accept all remaining results (after negative query
    terms). If required_any is set, one of those terms must appear; if
    context_any is also non-empty, one context term must appear as well.
    Empty description/topics still fails required_any when that list is set.
    """
    rel = relevance_from_watch(watch)
    content_parts = [
        str(repo.get("description", "")),
        " ".join(str(topic) for topic in repo.get("topics", [])),
    ]
    haystack = " ".join(content_parts).lower()

    always_match = [term.lower() for term in rel["always_match"] if term]
    if always_match and any(term in haystack for term in always_match):
        return True

    required_any = [term.lower() for term in rel["required_any"] if term]
    if not required_any:
        return True

    if not haystack.strip():
        return False
    if not any(term in haystack for term in required_any):
        return False

    context_any = [term.lower() for term in rel["context_any"] if term]
    if context_any and not any(term in haystack for term in context_any):
        return False
    return True


def append_or_replace_project(projects: dict[str, dict], project: dict) -> None:
    current = projects.setdefault(project["id"], project)
    if current is not project:
        current["tags"] = sorted(set(current.get("tags", [])) | set(project.get("tags", [])))
        current["sources"] = sorted(set(current.get("sources", [])) | set(project.get("sources", [])))
        current["latest_discovered_at"] = max(
            current.get("latest_discovered_at", current.get("discovered_at", "")),
            project.get("latest_discovered_at", project.get("discovered_at", "")),
        )
        current["activity_at"] = max(current.get("activity_at", ""), project.get("activity_at", ""))
        current["last_observed_activity"] = max(
            current.get("last_observed_activity", ""),
            project.get("last_observed_activity", ""),
        )


def build_seeded_item(
    entry: dict,
    kind: str,
    observed_at: str,
    existing_items: dict[str, dict],
    existing_projects: dict[str, dict],
    existing_sources: dict[str, dict],
    watch: dict,
) -> tuple[dict, dict, dict]:
    url = source_url_for(entry, kind)
    source_id = entry.get("id") or slugify(url)
    project = entry.get("project") or entry.get("repo") or entry.get("name") or source_id
    tags = merge_tags(watch, entry.get("tags", []))
    source_type = source_type_for(kind)
    item_id = f"seed:{source_id}"
    old_item = existing_items.get(item_id, {})
    discovered_at = discovery_time(old_item, observed_at)
    item = {
        "id": item_id,
        "title": title_for(entry, kind),
        "summary": summary_for(entry, kind, old_item),
        "source_url": url,
        "source_type": source_type,
        "event_type": "source_seeded",
        "project": project,
        "tags": tags,
        "status": "seeded",
        "discovered_at": discovered_at,
        "event_time": discovered_at,
        "activity_at": item_activity_at({**old_item, "event_time": discovered_at}),
        "observed_at": observed_at,
        "last_seen_at": observed_at,
        "confidence": "seeded_source",
        "evidence": [{"url": url, "retrieved_at": observed_at}],
    }

    old_source = existing_sources.get(source_id, {})
    source_discovered_at = discovery_time(old_source, observed_at)
    source = {
        "id": source_id,
        "name": title_for(entry, kind),
        "url": url,
        "source_type": source_type,
        "project": project,
        "tags": tags,
        "confidence": "seeded_source",
        "discovered_at": source_discovered_at,
        "first_seen": source_discovered_at,
        "last_checked": observed_at,
    }

    pslug = slugify(project)
    old_project = existing_projects.get(pslug, {})
    project_discovered_at = discovery_time(old_project, observed_at)
    project_record = {
        "id": pslug,
        "name": project,
        "tags": sorted(set(tags)),
        "sources": [source_id],
        "discovered_at": project_discovered_at,
        "first_seen": project_discovered_at,
        "activity_at": max(old_project.get("activity_at") or old_project.get("last_observed_activity") or observed_at, item["activity_at"]),
        "last_observed_activity": observed_at,
        "latest_discovered_at": max(old_project.get("latest_discovered_at", project_discovered_at), discovered_at),
    }
    return item, source, project_record


def build_github_repo_item(
    collector: dict,
    repo: dict,
    observed_at: str,
    existing_items: dict[str, dict],
    existing_projects: dict[str, dict],
    existing_sources: dict[str, dict],
    watch: dict,
) -> tuple[dict, dict, dict]:
    full_name = repo["full_name"]
    project = collector.get("project") or full_name
    repo_slug = slugify(full_name)
    source_id = f"gh-search:{collector['id']}:{repo_slug}"
    old_item = existing_items.get(source_id, {})
    discovered_at = discovery_time(old_item, observed_at)
    topics = [str(topic) for topic in repo.get("topics", [])]
    tags = merge_tags(watch, collector.get("tags", []), topics)
    desc = str(repo.get("description") or "").strip()
    old_summary = str(old_item.get("summary") or "").strip()
    if desc:
        summary = desc
    elif old_summary and not is_generated_summary(old_summary):
        summary = old_summary
    else:
        summary = f"Public repository: {full_name}."
    activity_at = (
        repo.get("pushed_at")
        or repo.get("updated_at")
        or repo.get("created_at")
        or discovered_at
    )
    item = {
        "id": source_id,
        "title": full_name,
        "summary": summary,
        "source_url": repo["html_url"],
        "source_type": "github_repository",
        "event_type": "source_discovered",
        "project": project,
        "tags": tags,
        "status": "candidate",
        "discovered_at": discovered_at,
        "event_time": discovered_at,
        "activity_at": activity_at,
        "observed_at": observed_at,
        "last_seen_at": observed_at,
        "confidence": "github_search",
        "evidence": [{
            "url": repo["html_url"],
            "retrieved_at": observed_at,
            "query": collector["query"],
        }],
    }

    old_source = existing_sources.get(source_id, {})
    source_discovered_at = discovery_time(old_source, observed_at)
    source = {
        "id": source_id,
        "name": full_name,
        "url": repo["html_url"],
        "source_type": "github_repository",
        "project": project,
        "tags": tags,
        "confidence": "github_search",
        "discovered_at": source_discovered_at,
        "first_seen": source_discovered_at,
        "last_checked": observed_at,
    }

    pslug = slugify(project)
    old_project = existing_projects.get(pslug, {})
    project_discovered_at = discovery_time(old_project, observed_at)
    project_record = {
        "id": pslug,
        "name": project,
        "tags": sorted(set(tags)),
        "sources": [source_id],
        "discovered_at": project_discovered_at,
        "first_seen": project_discovered_at,
        "activity_at": max(old_project.get("activity_at") or old_project.get("last_observed_activity") or activity_at, activity_at),
        "last_observed_activity": observed_at,
        "latest_discovered_at": max(old_project.get("latest_discovered_at", project_discovered_at), discovered_at),
    }
    return item, source, project_record


def load_existing_artifacts() -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
    """Load previous static artifacts so discovery dates do not refresh each run."""
    existing_items: dict[str, dict] = {}
    existing_projects: dict[str, dict] = {}
    existing_sources: dict[str, dict] = {}

    feed_path = OUT / "feed.json"
    projects_path = OUT / "projects.json"
    sources_path = OUT / "sources.json"
    if feed_path.exists():
        for item in json.loads(feed_path.read_text()).get("items", []):
            if item.get("id"):
                existing_items[item["id"]] = item
    if projects_path.exists():
        for project in json.loads(projects_path.read_text()).get("projects", []):
            if project.get("id"):
                existing_projects[project["id"]] = project
    if sources_path.exists():
        for source in json.loads(sources_path.read_text()).get("sources", []):
            if source.get("id"):
                existing_sources[source["id"]] = source
    return existing_items, existing_projects, existing_sources


def discovery_time(old: dict, observed_at: str) -> str:
    """Return the stable first-seen timestamp for a previously known record."""
    return (
        old.get("discovered_at")
        or old.get("first_seen")
        or old.get("event_time")
        or old.get("observed_at")
        or observed_at
    )


def item_activity_at(item: dict) -> str:
    """Best single timeline date for activity-oriented views."""
    return (
        item.get("source_updated_at")
        or item.get("source_published_at")
        or item.get("activity_at")
        or item.get("event_time")
        or item.get("discovered_at")
        or item.get("observed_at")
        or item.get("last_seen_at")
        or utc_now_iso()
    )


def build_items(cfg: dict, github_repo_fetcher=None, watch: dict | None = None) -> tuple[list[dict], dict, dict]:
    if watch is None:
        resolved = watch_from_cfg(cfg)
    else:
        resolved = normalize_watch(watch or {})
    observed_at = utc_now_iso()
    existing_items, existing_projects, existing_sources = load_existing_artifacts()
    sources: dict[str, dict] = {}
    projects: dict[str, dict] = {}
    items: list[dict] = []
    for kind, entries in cfg.get("seeded_sources", {}).items():
        for entry in entries or []:
            item, source, project = build_seeded_item(
                entry,
                kind,
                observed_at,
                existing_items,
                existing_projects,
                existing_sources,
                resolved,
            )
            items.append(item)
            sources[source["id"]] = source
            append_or_replace_project(projects, project)

    for collector in cfg.get("live_collectors", {}).get("github_repository_searches", []) or []:
        query = collector.get("query", "").strip()
        collector_id = collector.get("id", "").strip()
        if not query or not collector_id:
            continue
        if github_repo_fetcher is None:
            results = search_github_repositories(query, collector.get("max_results", 10))
        else:
            results = github_repo_fetcher(query)
        for repo in results:
            if not repo.get("full_name") or not repo.get("html_url"):
                continue
            if repo_excluded_by_query_terms(repo, query):
                continue
            if not repo_matches_relevance_rules(repo, resolved):
                continue
            item, source, project = build_github_repo_item(
                collector,
                repo,
                observed_at,
                existing_items,
                existing_projects,
                existing_sources,
                resolved,
            )
            items.append(item)
            sources[source["id"]] = source
            append_or_replace_project(projects, project)
    return items, projects, sources


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def write_rss(path: Path, items: list[dict], watch: dict) -> None:
    now = datetime.now(timezone.utc)
    def esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                 .replace('"', "&quot;"))
    title = watch.get("name") or "Source Watch"
    link = watch.get("base_url") or "https://example.com/"
    description = watch.get("description") or "Public-source activity feed."
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        f'<title>{esc(title)}</title>',
        f'<link>{esc(link)}</link>',
        f'<description>{esc(description)}</description>',
        f'<lastBuildDate>{format_datetime(now)}</lastBuildDate>',
    ]
    for item in items:
        pub_dt = parse_iso(item.get("discovered_at") or item.get("event_time")) or now
        parts.extend([
            '<item>',
            f'<title>{esc(item["title"])}</title>',
            f'<link>{esc(item["source_url"])}</link>',
            f'<guid isPermaLink="false">{esc(item["id"])}</guid>',
            f'<description>{esc(item["summary"])}</description>',
            f'<pubDate>{format_datetime(pub_dt)}</pubDate>',
            '</item>',
        ])
    parts.append('</channel></rss>')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n")


def watch_client_payload(watch: dict) -> dict:
    return {
        "name": watch.get("name") or "Source Watch",
        "default_tag": watch.get("default_tag") or "",
        "preferred_chips": list(watch.get("preferred_chips") or []),
        "hidden_tags": list(watch.get("hidden_tags") or []),
        "topics": list(watch.get("topics") or []),
    }


def main() -> int:
    watch = load_watch()
    cfg = parse_yaml(CONFIG)
    items, projects, sources = build_items(cfg, watch=watch)
    items = sorted(items, key=lambda x: x.get("discovered_at", ""), reverse=True)
    description = watch.get("description") or cfg.get("scope_note") or "Public-source activity feed."
    feed = {
        "schema_version": "source-watch.feed.v0",
        "title": watch.get("name") or "Source Watch",
        "description": description,
        "generated_at": utc_now_iso(),
        "items": items,
    }
    projects_list = sorted(projects.values(), key=lambda x: x.get("latest_discovered_at") or x.get("discovered_at", ""), reverse=True)
    sources_list = sorted(sources.values(), key=lambda x: x["name"].lower())
    client_watch = watch_client_payload(watch)
    for base in (OUT, STATIC):
        write_json(base / "feed.json", feed)
        write_json(base / "projects.json", {"schema_version": "source-watch.projects.v0", "projects": projects_list})
        write_json(base / "sources.json", {"schema_version": "source-watch.sources.v0", "sources": sources_list})
        (base / "items.jsonl").write_text("".join(json.dumps(i, sort_keys=True) + "\n" for i in items))
        write_rss(base / "feed.xml", items, watch)
        write_json(base / "watch.json", client_watch)
    print(f"wrote {len(items)} feed items, {len(projects_list)} projects, {len(sources_list)} sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
