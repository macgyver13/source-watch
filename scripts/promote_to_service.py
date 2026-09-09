#!/usr/bin/env python3
"""Validate serving.mode: service and purge generated static artifacts."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCH = ROOT / "config" / "watch.yaml"
STATIC = ROOT / "site" / "static"
CONTENT = ROOT / "site" / "content"
DATA_PUBLIC = ROOT / "data" / "public"

STATIC_ARTIFACTS = [
    "feed.json",
    "feed.xml",
    "items.jsonl",
    "projects.json",
    "sources.json",
    "watch.json",
]
DERIVED_DIRS = [
    "latest",
    "tags",
    "source-types",
    "recently-changed",
    "newly-discovered",
    "needs-human-source-seeding",
]
YAML_BLOCK = """serving:
  mode: service
  service_url: "https://<worker>.<subdomain>.workers.dev/"
"""


def load_mode() -> str:
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(WATCH.read_text()) or {}
    except Exception:
        raise SystemExit(f"PyYAML is required to parse {WATCH}")
    serving = data.get("serving") if isinstance(data.get("serving"), dict) else {}
    return str(serving.get("mode") or "static").strip().lower()


def collect_targets() -> list[Path]:
    paths: list[Path] = []
    for name in STATIC_ARTIFACTS:
        path = STATIC / name
        if path.exists():
            paths.append(path)
    if DATA_PUBLIC.exists():
        for path in sorted(DATA_PUBLIC.rglob("*")):
            if path.is_file():
                paths.append(path)
    weeks = CONTENT / "weeks"
    if weeks.exists():
        for path in sorted(weeks.iterdir()):
            if path.is_dir() and path.name != "live":
                paths.append(path)
    for name in DERIVED_DIRS:
        path = CONTENT / name
        if path.exists():
            paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge static artifacts after promoting to service mode.")
    parser.add_argument("--dry-run", action="store_true", help="list paths without deleting")
    args = parser.parse_args()
    if load_mode() != "service":
        print("serving.mode is not service. Paste this into config/watch.yaml, then re-run:")
        print(YAML_BLOCK, end="")
        return 1
    targets = collect_targets()
    for path in targets:
        rel = path.relative_to(ROOT)
        if args.dry_run:
            print(f"would remove {rel}")
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        print(f"removed {rel}")
    print("run python3 scripts/sync_hugo_content.py next")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
