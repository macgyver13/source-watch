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

import argparse
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
GITHUB_API = "https://api.github.com"
GITHUB_SEARCH_API = f"{GITHUB_API}/search/repositories"
PR_URL_RE = re.compile(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$", re.I)
DELVING_ORIGIN = "https://delvingbitcoin.org"
DELVING_TOPIC_URL_RE = re.compile(
    r"https://delvingbitcoin\.org/t/(?:[^/]+/)?(\d+)/?$", re.I
)
ABOUT_CATEGORY_TOPIC_RE = re.compile(r"^About the .+ category$", re.I)


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


def optional_iso(value) -> str | None:
    text = str(value or "").strip()
    return text if text and parse_iso(text) else None


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




def github_get_json(path: str) -> dict | None:
    request = Request(f"{GITHUB_API}{path}", headers=github_headers())
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"warning: GitHub GET {path} failed: {exc}", file=sys.stderr)
        return None
    return payload if isinstance(payload, dict) else None

def delving_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": "source-watch-live-collector",
    }


def delving_get_json(url: str) -> dict | None:
    request = Request(url, headers=delving_headers())
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"warning: Delving GET {url} failed: {exc}", file=sys.stderr)
        return None
    return payload if isinstance(payload, dict) else None


def discourse_tag_names(tags) -> list[str]:
    names: list[str] = []
    for tag in tags or []:
        if isinstance(tag, dict):
            name = str(tag.get("name") or tag.get("slug") or "").strip()
        else:
            name = str(tag).strip()
        if name and name not in names:
            names.append(name)
    return names


def is_about_category_topic(topic: dict) -> bool:
    return bool(ABOUT_CATEGORY_TOPIC_RE.match(str(topic.get("title") or "").strip()))


def delving_topic_url(topic: dict) -> str | None:
    tid = topic.get("id")
    if tid is None or str(tid).strip() == "":
        return None
    slug = str(topic.get("slug") or "").strip()
    if slug:
        return f"{DELVING_ORIGIN}/t/{slug}/{tid}"
    return f"{DELVING_ORIGIN}/t/{tid}"


def delving_topic_key(url: str) -> str | None:
    match = DELVING_TOPIC_URL_RE.match(str(url or ""))
    return match.group(1) if match else None


def topic_matches_relevance_rules(topic: dict, watch: dict | None = None) -> bool:
    haystack = " ".join([
        str(topic.get("title") or ""),
        str(topic.get("excerpt") or topic.get("blurb") or ""),
        " ".join(discourse_tag_names(topic.get("tags"))),
    ])
    return haystack_matches_relevance_rules(haystack, watch)


def search_delving_topics(query: str, max_results: int = 10) -> list[dict]:
    limit = max(1, min(int(max_results), 25))
    params = urlencode({"q": query})
    payload = delving_get_json(f"{DELVING_ORIGIN}/search.json?{params}") or {}
    topics = list(payload.get("topics") or [])
    blurbs: dict[object, str] = {}
    for post in payload.get("posts") or []:
        tid = post.get("topic_id")
        if tid is None:
            continue
        blurb = str(post.get("blurb") or "").strip()
        if not blurb:
            continue
        if post.get("post_number") == 1 or tid not in blurbs:
            blurbs[tid] = blurb
    out: list[dict] = []
    seen: set[object] = set()
    for topic in topics:
        tid = topic.get("id")
        if tid is None or tid in seen:
            continue
        seen.add(tid)
        if not str(topic.get("excerpt") or "").strip() and blurbs.get(tid):
            topic = {**topic, "excerpt": blurbs[tid]}
        out.append(topic)
        if len(out) >= limit:
            break
    return out


def list_delving_category(category: str, max_results: int = 30) -> list[dict]:
    path = str(category).strip().lstrip("#").strip("/")
    if not path:
        return []
    limit = max(1, min(int(max_results), 30))
    payload = delving_get_json(f"{DELVING_ORIGIN}/c/{path}/l/latest.json") or {}
    topics = (payload.get("topic_list") or {}).get("topics") or []
    return list(topics)[:limit]



def later_iso(*values: str | None) -> str | None:
    stamps = [value for value in values if value]
    return max(stamps) if stamps else None


def live_seed_activity(entry: dict, kind: str, github_json_fetcher) -> str | None:
    """Latest GitHub timestamp for a seeded repo or pull request."""
    if github_json_fetcher is None or entry.get("live_activity") is False:
        return None
    if kind == "github_repositories":
        repo = str(entry.get("repo") or "").strip()
        if not repo:
            return None
        payload = github_json_fetcher(f"/repos/{repo}") or {}
        return optional_iso(payload.get("pushed_at") or payload.get("updated_at"))
    if kind == "github_pull_requests":
        match = PR_URL_RE.match(str(entry.get("url") or ""))
        if not match:
            return None
        owner, name, number = match.group(1), match.group(2), match.group(3)
        payload = github_json_fetcher(f"/repos/{owner}/{name}/pulls/{number}") or {}
        return later_iso(
            optional_iso(payload.get("merged_at")),
            optional_iso(payload.get("updated_at")),
            optional_iso(payload.get("closed_at")),
        )
    return None




def excluded_by_query_terms(haystack: str, query: str) -> bool:
    negative_terms = [term.lower() for term in re.findall(r"(?<!\S)-([a-zA-Z0-9_]+)", query)]
    if not negative_terms:
        return False
    text = haystack.lower()
    return any(term in text for term in negative_terms)


def repo_excluded_by_query_terms(repo: dict, query: str) -> bool:
    haystack_parts = [
        str(repo.get("full_name", "")),
        str(repo.get("description", "")),
        " ".join(str(topic) for topic in repo.get("topics", [])),
    ]
    return excluded_by_query_terms(" ".join(haystack_parts), query)



def relevance_from_watch(watch: dict | None) -> dict:
    watch = watch or {}
    if isinstance(watch.get("relevance"), dict):
        return normalize_relevance(watch["relevance"])
    return normalize_relevance(watch)


def haystack_matches_relevance_rules(haystack: str, watch: dict | None = None) -> bool:
    """Filter live-collector hits using watch.relevance against a text haystack."""
    rel = relevance_from_watch(watch)
    text = (haystack or "").lower()

    always_match = [term.lower() for term in rel["always_match"] if term]
    if always_match and any(term in text for term in always_match):
        return True

    required_any = [term.lower() for term in rel["required_any"] if term]
    if not required_any:
        return True

    if not text.strip():
        return False
    if not any(term in text for term in required_any):
        return False

    context_any = [term.lower() for term in rel["context_any"] if term]
    if context_any and not any(term in text for term in context_any):
        return False
    return True


def repo_matches_relevance_rules(repo: dict, watch: dict | None = None) -> bool:
    """Filter GitHub search hits using watch.relevance.

    always_match short-circuits True if any term appears in description/topics.
    If required_any is empty, accept all remaining results (after negative query
    terms). If required_any is set, one of those terms must appear; if
    context_any is also non-empty, one context term must appear as well.
    Empty description/topics still fails required_any when that list is set.
    """
    content_parts = [
        str(repo.get("description", "")),
        " ".join(str(topic) for topic in repo.get("topics", [])),
    ]
    return haystack_matches_relevance_rules(" ".join(content_parts), watch)



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
    github_json_fetcher=None,
) -> tuple[dict, dict, dict]:
    url = source_url_for(entry, kind)
    source_id = entry.get("id") or slugify(url)
    project = entry.get("project") or entry.get("repo") or entry.get("name") or source_id
    tags = merge_tags(watch, entry.get("tags", []))
    source_type = source_type_for(kind)
    item_id = f"seed:{source_id}"
    old_item = existing_items.get(item_id, {})
    seed_discovered = optional_iso(entry.get("discovered_at"))
    seed_activity = optional_iso(entry.get("activity_at"))
    live_activity = live_seed_activity(entry, kind, github_json_fetcher)
    discovered_at = seed_discovered or discovery_time(old_item, observed_at)
    activity_at = later_iso(seed_activity, live_activity) or item_activity_at({
        **old_item,
        "event_time": discovered_at,
        "discovered_at": discovered_at,
    })
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
        "activity_at": activity_at,
        "observed_at": observed_at,
        "last_seen_at": observed_at,
        "confidence": "seeded_source",
        "evidence": [{"url": url, "retrieved_at": observed_at}],
    }

    old_source = existing_sources.get(source_id, {})
    source_discovered_at = seed_discovered or discovery_time(old_source, observed_at)
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
    project_discovered_at = seed_discovered or discovery_time(old_project, observed_at)
    project_activity = activity_at
    old_latest = old_project.get("latest_discovered_at") or old_project.get("discovered_at") or ""
    latest_discovered_at = discovered_at if seed_discovered else (
        max(old_latest, discovered_at) if old_latest else discovered_at
    )
    project_record = {
        "id": pslug,
        "name": project,
        "tags": sorted(set(tags)),
        "sources": [source_id],
        "discovered_at": project_discovered_at,
        "first_seen": project_discovered_at,
        "activity_at": project_activity,
        "last_observed_activity": observed_at,
        "latest_discovered_at": latest_discovered_at,
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
    created_at = optional_iso(repo.get("created_at"))
    discovered_at = created_at or discovery_time(old_item, observed_at)
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
    source_discovered_at = created_at or discovery_time(old_source, observed_at)
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
    project_discovered_at = created_at or discovery_time(old_project, observed_at)
    project_record = {
        "id": pslug,
        "name": project,
        "tags": sorted(set(tags)),
        "sources": [source_id],
        "discovered_at": project_discovered_at,
        "first_seen": project_discovered_at,
        "activity_at": activity_at,
        "last_observed_activity": observed_at,
        "latest_discovered_at": discovered_at,
    }
    return item, source, project_record

def build_delving_topic_item(
    collector: dict,
    topic: dict,
    observed_at: str,
    existing_items: dict[str, dict],
    existing_projects: dict[str, dict],
    existing_sources: dict[str, dict],
    watch: dict,
    confidence: str,
    evidence_extra: dict,
) -> tuple[dict, dict, dict]:
    url = delving_topic_url(topic) or ""
    title = str(topic.get("title") or "").strip() or f"Delving topic {topic.get('id')}"
    project = collector.get("project") or title
    source_id = f"delving-search:{collector['id']}:{topic['id']}"
    old_item = existing_items.get(source_id, {})
    created_at = optional_iso(topic.get("created_at"))
    discovered_at = created_at or discovery_time(old_item, observed_at)
    discourse_tags = discourse_tag_names(topic.get("tags"))
    tags = merge_tags(watch, collector.get("tags", []), discourse_tags)
    excerpt = str(topic.get("excerpt") or topic.get("blurb") or "").strip()
    excerpt = re.sub(r"\s+", " ", excerpt)
    old_summary = str(old_item.get("summary") or "").strip()
    if excerpt:
        summary = excerpt
    elif old_summary and not is_generated_summary(old_summary):
        summary = old_summary
    else:
        summary = f"Delving Bitcoin topic: {title}."
    activity_at = (
        optional_iso(topic.get("last_posted_at"))
        or optional_iso(topic.get("bumped_at"))
        or created_at
        or discovered_at
    )
    evidence = {"url": url, "retrieved_at": observed_at, **evidence_extra}
    item = {
        "id": source_id,
        "title": title,
        "summary": summary,
        "source_url": url,
        "source_type": "delving_topic",
        "event_type": "source_discovered",
        "project": project,
        "tags": tags,
        "status": "candidate",
        "discovered_at": discovered_at,
        "event_time": discovered_at,
        "activity_at": activity_at,
        "observed_at": observed_at,
        "last_seen_at": observed_at,
        "confidence": confidence,
        "evidence": [evidence],
    }

    old_source = existing_sources.get(source_id, {})
    source_discovered_at = created_at or discovery_time(old_source, observed_at)
    source = {
        "id": source_id,
        "name": title,
        "url": url,
        "source_type": "delving_topic",
        "project": project,
        "tags": tags,
        "confidence": confidence,
        "discovered_at": source_discovered_at,
        "first_seen": source_discovered_at,
        "last_checked": observed_at,
    }

    pslug = slugify(project)
    old_project = existing_projects.get(pslug, {})
    project_discovered_at = created_at or discovery_time(old_project, observed_at)
    project_record = {
        "id": pslug,
        "name": project,
        "tags": sorted(set(tags)),
        "sources": [source_id],
        "discovered_at": project_discovered_at,
        "first_seen": project_discovered_at,
        "activity_at": activity_at,
        "last_observed_activity": observed_at,
        "latest_discovered_at": discovered_at,
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


def skip_live_collectors() -> bool:
    return os.environ.get("SOURCE_WATCH_SKIP_LIVE", "").strip().lower() in ("1", "true")


def skip_live_github_searches() -> bool:
    return skip_live_collectors()


def build_items(
    cfg: dict,
    github_repo_fetcher=None,
    watch: dict | None = None,
    github_json_fetcher=None,
    skip_searches: bool | None = None,
    delving_search_fetcher=None,
    delving_category_fetcher=None,
) -> tuple[list[dict], dict, dict]:
    if watch is None:
        resolved = watch_from_cfg(cfg)
    else:
        resolved = normalize_watch(watch or {})
    if skip_searches is None:
        skip_searches = skip_live_collectors()
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
                github_json_fetcher=github_json_fetcher,
            )
            items.append(item)
            sources[source["id"]] = source
            append_or_replace_project(projects, project)

    if skip_searches:
        return items, projects, sources

    def github_repo_key(url: str) -> str | None:
        match = re.match(r"https://github\.com/([^/]+)/([^/]+)(?:/|$)", str(url or ""), re.I)
        if not match:
            return None
        return f"{match.group(1)}/{match.group(2)}".lower()

    seen_repos = {key for key in (github_repo_key(item.get("source_url", "")) for item in items) if key}

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
            repo_key = str(repo["full_name"]).lower()
            if repo_key in seen_repos:
                continue
            if repo_excluded_by_query_terms(repo, query):
                continue
            if not repo_matches_relevance_rules(repo, resolved):
                continue
            seen_repos.add(repo_key)

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

    seen_topics = {key for key in (delving_topic_key(item.get("source_url", "")) for item in items) if key}

    def consume_delving_topic(collector: dict, topic: dict, confidence: str, evidence_extra: dict) -> None:
        url = delving_topic_url(topic)
        if not url or not str(topic.get("title") or "").strip() or topic.get("id") is None:
            return
        if is_about_category_topic(topic):
            return
        tid = str(topic["id"])
        if tid in seen_topics:
            return
        haystack = " ".join([
            str(topic.get("title") or ""),
            str(topic.get("excerpt") or topic.get("blurb") or ""),
            " ".join(discourse_tag_names(topic.get("tags"))),
        ])
        query = str(collector.get("query") or "")
        if query and excluded_by_query_terms(haystack, query):
            return
        if not topic_matches_relevance_rules(topic, resolved):
            return
        seen_topics.add(tid)
        item, source, project = build_delving_topic_item(
            collector,
            topic,
            observed_at,
            existing_items,
            existing_projects,
            existing_sources,
            resolved,
            confidence,
            evidence_extra,
        )
        items.append(item)
        sources[source["id"]] = source
        append_or_replace_project(projects, project)

    for collector in cfg.get("live_collectors", {}).get("delving_topic_searches", []) or []:
        query = collector.get("query", "").strip()
        collector_id = collector.get("id", "").strip()
        if not query or not collector_id:
            continue
        if delving_search_fetcher is None:
            results = search_delving_topics(query, collector.get("max_results", 10))
        else:
            results = delving_search_fetcher(query)
        for topic in results:
            consume_delving_topic(collector, topic, "delving_search", {"query": query})

    for collector in cfg.get("live_collectors", {}).get("delving_category_listings", []) or []:
        category = str(collector.get("category") or "").strip().lstrip("#")
        collector_id = collector.get("id", "").strip()
        if not category or not collector_id:
            continue
        if delving_category_fetcher is None:
            results = list_delving_category(category, collector.get("max_results", 30))
        else:
            results = delving_category_fetcher(category)
        for topic in results:
            consume_delving_topic(collector, topic, "delving_category", {"category": category})

    return items, projects, sources



def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n")


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


def load_existing_preferred_chips() -> list:
    """Keep atlas chips if yaml is empty but a previous watch.json still has them."""
    for path in (STATIC / "watch.json", OUT / "watch.json"):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        chips = data.get("preferred_chips") if isinstance(data, dict) else None
        if chips:
            return list(chips)
    return []


def resolve_preferred_chips(watch: dict) -> list:
    yaml_chips = list(watch.get("preferred_chips") or [])
    if yaml_chips:
        return yaml_chips
    existing = load_existing_preferred_chips()
    if existing:
        return existing
    return []


def watch_client_payload(watch: dict) -> dict:
    return {
        "name": watch.get("name") or "Source Watch",
        "default_tag": watch.get("default_tag") or "",
        "preferred_chips": resolve_preferred_chips(watch),
        "hidden_tags": list(watch.get("hidden_tags") or []),
        "topics": list(watch.get("topics") or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Source Watch public feed artifacts.")
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Skip live HTTP (GitHub searches/timestamps and Delving collectors).",
    )
    args = parser.parse_args()
    seed_only = args.seed_only or skip_live_collectors()
    watch = load_watch()
    cfg = parse_yaml(CONFIG)
    items, projects, sources = build_items(
        cfg,
        watch=watch,
        github_json_fetcher=None if seed_only else github_get_json,
        skip_searches=seed_only,
    )
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
    mode = "seed-only" if seed_only else "live collector refresh"
    print(f"wrote {len(items)} feed items, {len(projects_list)} projects, {len(sources_list)} sources ({mode})")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
