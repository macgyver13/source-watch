#!/usr/bin/env python3
"""Render slim Hugo content pages from Source Watch public JSON artifacts.

Dashboard, atlas, week, and sources pages are hydrated client-side from
feed.json / projects.json / sources.json. Markdown here only needs to exist
so Hugo emits the URLs, plus week section pages so the week rail can list them.

hugo.toml is generated from config/watch.yaml (baseURL, title, description,
topics). Topic pages are emitted only when watch.topics is non-empty.
"""
from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
STATIC = SITE / "static"
CONTENT = SITE / "content"
WATCH_CONFIG = ROOT / "config" / "watch.yaml"


def parse_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        raise SystemExit(f"PyYAML is required to parse {path}")


def load_watch() -> dict:
    if not WATCH_CONFIG.exists():
        return {}
    return parse_yaml(WATCH_CONFIG)


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "item"


def fm(title: str, extra: str = "") -> str:
    return f"---\ntitle: {json.dumps(title)}\n{extra}---\n\n"


def week_title(slug: str) -> str:
    match = re.match(r"(\d{4})-W(\d{1,2})$", slug)
    if match:
        return f"Week {int(match.group(2))}, {match.group(1)}"
    return slug


def item_iso_week(item: dict) -> str | None:
    raw = str(item.get("discovered_at") or item.get("event_time") or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.strftime("%G-W%V")



def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def write_hugo_toml(watch: dict) -> None:
    title = str(watch.get("name") or "Source Watch")
    base_url = str(watch.get("base_url") or "https://example.com/")
    if not base_url.endswith("/"):
        base_url += "/"
    description = str(watch.get("description") or "").strip()
    lines = [
        f"baseURL = {toml_str(base_url)}",
        'locale = "en-us"',
        f"title = {toml_str(title)}",
        "enableRobotsTXT = true",
        "disablePathToLower = true",
        "",
        "[params]",
        f"  description = {toml_str(description)}",
        "",
        "[markup]",
        "  [markup.goldmark]",
        "    [markup.goldmark.renderer]",
        "      unsafe = true",
        "",
    ]
    write(SITE / "hugo.toml", "\n".join(lines))


def sync_topics(watch: dict) -> None:
    topics_root = CONTENT / "topics"
    topics = [t for t in (watch.get("topics") or []) if isinstance(t, dict)]
    slugs: set[str] = set()
    if not topics:
        write(
            topics_root / "_index.md",
            fm("Topics")
            + "Add optional topic tiles in config/watch.yaml.\n",
        )
    else:
        links: list[str] = []
        for topic in topics:
            slug = str(topic.get("slug") or slugify(str(topic.get("title") or "topic")))
            slugs.add(slug)
            title = str(topic.get("title") or slug)
            blurb = str(topic.get("blurb") or "").strip()
            note = str(topic.get("note") or "").strip()
            extra = ""
            if blurb:
                extra += f"blurb: {json.dumps(blurb)}\n"
            if note:
                extra += f"note: {json.dumps(note)}\n"
            body_parts = [p for p in (blurb, note) if p]
            body = "\n\n".join(body_parts) + ("\n" if body_parts else "")
            write(topics_root / slug / "_index.md", fm(title, extra) + body)
            links.append(f"- [{title}](/topics/{slug}/)")
        write(topics_root / "_index.md", fm("Topics") + "\n".join(links) + "\n")

    if topics_root.exists():
        for path in topics_root.iterdir():
            if path.is_dir() and path.name not in slugs:
                shutil.rmtree(path)


def copy_watch_data(watch: dict) -> None:
    dest = SITE / "data" / "watch.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if WATCH_CONFIG.exists():
        shutil.copyfile(WATCH_CONFIG, dest)
    else:
        try:
            import yaml  # type: ignore
            dest.write_text(yaml.safe_dump(watch or {}, sort_keys=False))
        except Exception:
            dest.write_text("name: Source Watch\ntopics: []\n")


def main() -> int:
    watch = load_watch()
    write_hugo_toml(watch)
    copy_watch_data(watch)

    feed = json.loads((STATIC / "feed.json").read_text())
    items = feed.get("items", [])

    write(CONTENT / "_index.md", fm(str(watch.get("name") or "Source Watch")))
    write(CONTENT / "activity" / "_index.md", fm("Activity"))
    write(CONTENT / "projects" / "_index.md", fm("Projects"))
    write(CONTENT / "sources" / "_index.md", fm("Sources"))

    week_root = CONTENT / "weeks"
    week_root.mkdir(parents=True, exist_ok=True)
    wanted = {slug for slug in (item_iso_week(item) for item in items) if slug}
    existing = {path.name for path in week_root.iterdir() if path.is_dir()}
    for slug in existing - wanted:
        shutil.rmtree(week_root / slug)
    for slug in wanted:
        write(week_root / slug / "_index.md", fm(week_title(slug)))
    week_dirs = sorted(wanted, reverse=True)
    weeks_note = "" if week_dirs else "No activity yet.\n"
    write(week_root / "_index.md", fm("Weeks") + weeks_note)

    sync_topics(watch)

    by_tag: dict[str, list] = defaultdict(list)
    by_type: dict[str, list] = defaultdict(list)
    for item in items:
        by_type[item.get("source_type", "unknown")].append(item)
        for tag in item.get("tags", []):
            by_tag[tag].append(item)

    def link_list(rows: list[dict]) -> str:
        lines = []
        for item in rows:
            title = item.get("title") or item.get("id") or "item"
            url = item.get("source_url") or "#"
            lines.append(f"- [{title}]({url})")
        return "\n".join(lines) + ("\n" if lines else "")

    empty_note = "No activity yet.\n"
    write(CONTENT / "latest" / "_index.md", fm("Latest activity") + (link_list(items) or empty_note))
    tags_body = "\n".join(
        [f"## {tag}\n\n{link_list(rows)}" for tag, rows in sorted(by_tag.items())]
    )
    write(CONTENT / "tags" / "_index.md", fm("Tags") + ((tags_body + "\n") if tags_body else empty_note))
    types_body = "\n".join(
        [f"## {st}\n\n{link_list(rows)}" for st, rows in sorted(by_type.items())]
    )
    write(CONTENT / "source-types" / "_index.md", fm("Source types") + ((types_body + "\n") if types_body else empty_note))
    write(CONTENT / "recently-changed" / "_index.md", fm("Recently changed") + (link_list(items) or empty_note))
    write(CONTENT / "newly-discovered" / "_index.md", fm("Newly discovered") + (link_list(items) or empty_note))
    write(
        CONTENT / "needs-human-source-seeding" / "_index.md",
        fm("Needs human source seeding") + "No live collector events queued for human seeding.\n",
    )

    print(f"rendered slim Hugo content from {len(items)} items, weeks={week_dirs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
