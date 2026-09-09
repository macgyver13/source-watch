#!/usr/bin/env python3
"""Verify Source Watch public artifacts are present and internally valid."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "site" / "static"
WATCH = ROOT / "config" / "watch.yaml"
REQUIRED = ["feed.json", "feed.xml", "items.jsonl", "projects.json", "sources.json", "watch.json"]
ITEM_KEYS = ["id", "title", "source_url", "source_type", "event_type", "observed_at", "evidence"]


def load_serving() -> tuple[str, str]:
    if not WATCH.exists():
        return "static", ""
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(WATCH.read_text()) or {}
    except Exception:
        return "static", ""
    serving = data.get("serving") if isinstance(data.get("serving"), dict) else {}
    mode = str(serving.get("mode") or "static").strip().lower()
    url = str(serving.get("service_url") or "").strip()
    if url and not url.endswith("/"):
        url += "/"
    return mode, url


def check_items(items, jsonl_text: str) -> None:
    if items is None or not isinstance(items, list):
        raise SystemExit("feed items must be a list (empty is OK for a blank template)")
    for item in items:
        for key in ITEM_KEYS:
            if not item.get(key):
                raise SystemExit(f"item {item.get('id')} missing {key}")
    line_count = len(jsonl_text.splitlines())
    if line_count != len(items):
        raise SystemExit(f"items.jsonl line count {line_count} != feed items {len(items)}")


def fetch_service(base: str, name: str) -> str:
    request = Request(base + name, headers={"Accept": "*/*", "User-Agent": "source-watch-verify"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def main() -> int:
    mode, service_url = load_serving()
    if mode == "service":
        if not service_url:
            raise SystemExit("serving.mode: service requires serving.service_url")
        try:
            feed = json.loads(fetch_service(service_url, "feed.json"))
            jsonl_text = fetch_service(service_url, "items.jsonl")
        except Exception as exc:
            raise SystemExit(f"unreachable service {service_url}: {exc}")
        items = feed.get("items")
        check_items(items, jsonl_text)
        print(f"verified {len(items)} items against {service_url}")
        return 0

    missing = [name for name in REQUIRED if not (STATIC / name).exists()]
    if missing:
        raise SystemExit(f"missing public artifacts: {missing}")
    feed = json.loads((STATIC / "feed.json").read_text())
    jsonl_text = (STATIC / "items.jsonl").read_text()
    items = feed.get("items")
    check_items(items, jsonl_text)
    print(f"verified {len(items)} items and {len(REQUIRED)} public artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
