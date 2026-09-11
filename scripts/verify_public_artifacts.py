#!/usr/bin/env python3
"""Verify Source Watch public artifacts are present and internally valid."""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from service_client import ServiceError, no_redirect_opener
from watch_config import load_serving

STATIC = ROOT / "site" / "static"
WATCH = ROOT / "config" / "watch.yaml"
REQUIRED = ["feed.json", "feed.xml", "items.jsonl", "projects.json", "sources.json", "watch.json"]
ITEM_KEYS = ["id", "title", "source_url", "source_type", "event_type", "observed_at", "evidence"]


def fetch_service(base: str, name: str) -> str:
    request = Request(base + name, headers={"Accept": "*/*", "User-Agent": "source-watch-verify"})
    try:
        with no_redirect_opener()(request, timeout=30) as response:
            status = getattr(response, "status", None) or response.getcode()
            if status != 200:
                raise SystemExit(f"{base}{name} returned {status}")
            return response.read().decode("utf-8")
    except ServiceError as exc:
        raise SystemExit(f"unreachable service {base}{name}: {exc}") from exc
    except HTTPError as exc:
        raise SystemExit(f"unreachable service {base}{name}: {exc}") from exc
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(f"unreachable service {base}{name}: {exc}") from exc


def check_all(read: Callable[[str], str]) -> int:
    def read_json(name: str):
        try:
            return json.loads(read(name))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SystemExit(f"{name} is not valid JSON: {exc}") from exc

    feed = read_json("feed.json")
    if not isinstance(feed, dict) or feed.get("schema_version") != "source-watch.feed.v0":
        raise SystemExit("feed.json must have schema_version source-watch.feed.v0")
    items = feed.get("items")
    if not isinstance(items, list):
        raise SystemExit("feed items must be a list (empty is OK for a blank template)")
    for item in items:
        for key in ITEM_KEYS:
            if not item.get(key):
                raise SystemExit(f"item {item.get('id')} missing {key}")

    projects_doc = read_json("projects.json")
    if not isinstance(projects_doc, dict) or projects_doc.get("schema_version") != "source-watch.projects.v0":
        raise SystemExit("projects.json must have schema_version source-watch.projects.v0")
    projects = projects_doc.get("projects")
    if not isinstance(projects, list) or any(not isinstance(project, dict) for project in projects):
        raise SystemExit("projects.json must contain a list 'projects'")

    sources_doc = read_json("sources.json")
    if not isinstance(sources_doc, dict) or sources_doc.get("schema_version") != "source-watch.sources.v0":
        raise SystemExit("sources.json must have schema_version source-watch.sources.v0")
    sources = sources_doc.get("sources")
    if not isinstance(sources, list) or any(not isinstance(source, dict) for source in sources):
        raise SystemExit("sources.json must contain a list 'sources'")

    watch = read_json("watch.json")
    if not isinstance(watch, dict) or not str(watch.get("name") or "").strip():
        raise SystemExit("watch.json must be an object containing name")

    xml = read("feed.xml")
    try:
        rss = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise SystemExit(f"feed.xml is not valid XML: {exc}") from exc
    if rss.tag != "rss":
        raise SystemExit("feed.xml root must be rss")
    channel = rss.find("channel")
    if channel is None:
        raise SystemExit("feed.xml must contain a channel")

    jsonl_text = read("items.jsonl")
    jsonl_items = []
    for line_number, line in enumerate(jsonl_text.splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"items.jsonl line {line_number} is not valid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise SystemExit(f"items.jsonl line {line_number} must be an object")
        jsonl_items.append(row)
    if jsonl_items != items:
        raise SystemExit("items.jsonl rows must exactly match feed.json items")

    feed_ids = [str(item.get("id") or "") for item in items]
    rss_ids = [(node.text or "") for node in channel.findall("item/guid")]
    if rss_ids != feed_ids:
        raise SystemExit("feed.xml item guids must match feed.json items")

    project_names = {str(project.get("name") or "") for project in projects}
    source_ids = {str(source.get("id") or "") for source in sources}
    if "" in project_names or "" in source_ids:
        raise SystemExit("projects and sources must have non-empty names and ids")
    for project in projects:
        linked = project.get("sources")
        if not isinstance(linked, list) or any(str(source_id) not in source_ids for source_id in linked):
            raise SystemExit(f"project {project.get('id')} references an unknown source")
    if any(str(source.get("project") or "") not in project_names for source in sources):
        raise SystemExit("every source must reference a known project")
    if any(str(item.get("project") or "") not in project_names for item in items):
        raise SystemExit("every feed item must reference a known project")
    source_keys = {
        (
            str(source.get("url") or ""),
            str(source.get("source_type") or ""),
            str(source.get("project") or ""),
        )
        for source in sources
    }
    if any(
        (
            str(item.get("source_url") or ""),
            str(item.get("source_type") or ""),
            str(item.get("project") or ""),
        )
        not in source_keys
        for item in items
    ):
        raise SystemExit("every feed item must have a matching source")
    return 0


def main() -> int:
    serving = load_serving(WATCH)
    if serving["mode"] == "service":
        base = serving["service_url"]

        def read(name: str) -> str:
            return fetch_service(base, name)

        check_all(read)
        print(f"verified service artifacts against {base}")
        return 0

    missing = [name for name in REQUIRED if not (STATIC / name).exists()]
    if missing:
        raise SystemExit(f"missing public artifacts: {missing}")

    def read_static(name: str) -> str:
        return (STATIC / name).read_text()

    check_all(read_static)
    feed = json.loads(read_static("feed.json"))
    print(f"verified {len(feed.get('items') or [])} items and {len(REQUIRED)} public artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
