#!/usr/bin/env python3
"""Verify Source Watch public artifacts are present and internally valid."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "site" / "static"
REQUIRED = ["feed.json", "feed.xml", "items.jsonl", "projects.json", "sources.json", "watch.json"]


def main() -> int:
    missing = [name for name in REQUIRED if not (STATIC / name).exists()]
    if missing:
        raise SystemExit(f"missing public artifacts: {missing}")
    feed = json.loads((STATIC / "feed.json").read_text())
    items = feed.get("items", [])
    if not items:
        raise SystemExit("feed has no items")
    for item in items:
        for key in ["id", "title", "source_url", "source_type", "event_type", "observed_at", "evidence"]:
            if not item.get(key):
                raise SystemExit(f"item {item.get('id')} missing {key}")
    line_count = len((STATIC / "items.jsonl").read_text().splitlines())
    if line_count != len(items):
        raise SystemExit(f"items.jsonl line count {line_count} != feed items {len(items)}")
    print(f"verified {len(items)} items and {len(REQUIRED)} public artifacts")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
