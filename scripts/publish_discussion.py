#!/usr/bin/env python3
"""Copy a discussion state into the site and register it in the picker index.

The published copy carries a provenance note so a reader can tell a curated
record from model output before they read a single claim.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import discussion_state as ds  # noqa: E402

SITE = Path(__file__).resolve().parents[1] / "site" / "static" / "discussions"


def publish(state_path: Path, slug: str, label: str, note: str) -> dict:
    state = ds.load(state_path)
    errors = ds.validate(state)
    if errors:
        raise SystemExit("\n".join(errors))
    state["_prototype_note"] = note
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / f"{slug}.json").write_text(
        json.dumps(state, indent=1) + "\n", encoding="utf-8"
    )
    index_path = SITE / "index.json"
    index = {"discussions": []}
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    rows = [row for row in index.get("discussions", []) if row.get("slug") != slug]
    rows.append({"slug": slug, "label": label, "note": note})
    index["discussions"] = rows
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    visible = [c for c in state.get("claims", []) if "hidden" not in c]
    return {
        "slug": slug,
        "claims": len(visible),
        "positions": len(state.get("positions", [])),
        "questions": len(state.get("questions", [])),
        "conceded": sum(1 for c in visible if c.get("status") == "conceded"),
        "answered": sum(1 for c in visible if c.get("answered_by")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a discussion state to the site.")
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--note", required=True)
    args = parser.parse_args()
    summary = publish(args.state, args.slug, args.label, args.note)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
