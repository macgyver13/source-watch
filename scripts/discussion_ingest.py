#!/usr/bin/env python3
"""Ingest discussion threads into normalized JSONL format.

Supports Discourse topics (Task I1) and GitHub PRs/issues (Task I2).
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from build_seed_feed import (
    delving_get_json,
    wait_delving_slot,
    delving_headers,
    github_headers,
)
from urllib.request import Request, urlopen
from urllib.error import HTTPError

DELVING_ORIGIN = "https://delvingbitcoin.org"
DISCOURSE_TOPIC_URL_RE = re.compile(
    r"^(https?://[^/]+)/t/(?:([^/]+)/)?(\d+)(?:/(\d+))?/?$", re.I
)
GITHUB_TARGET_RE = re.compile(
    r"^(?:https?://github\.com/)?([^/#]+)/([^/#]+)(?:/(?:pull|issues)/|#)(\d+)/?$",
    re.I,
)


class HTMLTextExtractor(HTMLParser):
    """Strip HTML tags while preserving structural breaks and unescaping entities."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._in_script = False
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in ("script", "style"):
            self._in_script = True
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr"):
            self._chunks.append("\n\n")
        elif tag in ("br", "li"):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("script", "style"):
            self._in_script = False
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr"):
            self._chunks.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self._in_script and not self._in_style:
            self._chunks.append(data)

    def get_text(self) -> str:
        raw = "".join(self._chunks)
        unescaped = html.unescape(raw)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in unescaped.splitlines()]
        text = "\n".join(lines)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def normalize_html_to_text(html_content: str) -> str:
    """Normalize HTML or rich text to plain text."""
    if not html_content:
        return ""
    extractor = HTMLTextExtractor()
    extractor.feed(html_content)
    return extractor.get_text()


def compute_content_hash(text: str) -> str:
    """Compute sha256 hex digest of normalized body text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_discourse_target(target: str) -> tuple[str, int, str | None]:
    """Parse a Discourse target into (origin, topic_id, slug)."""
    target = target.strip()
    match = DISCOURSE_TOPIC_URL_RE.match(target)
    if match:
        origin = match.group(1).rstrip("/")
        slug = match.group(2)
        topic_id = int(match.group(3))
        return origin, topic_id, slug

    if target.isdigit():
        return DELVING_ORIGIN, int(target), None

    raise ValueError(f"Cannot parse Discourse target: {target!r}")


def fetch_discourse_topic(origin: str, topic_id: int) -> dict[str, Any]:
    """Fetch Discourse topic metadata and initial post stream."""
    url = f"{origin}/t/{topic_id}.json"
    data = delving_get_json(url)
    if not data or not isinstance(data, dict):
        raise RuntimeError(f"Failed to fetch Discourse topic from {url}")
    return data


def fetch_discourse_posts_by_ids(
    origin: str, topic_id: int, post_ids: list[int]
) -> list[dict[str, Any]]:
    """Fetch Discourse posts by post IDs in batches of up to 20."""
    if not post_ids:
        return []

    collected: list[dict[str, Any]] = []
    chunk_size = 20
    for i in range(0, len(post_ids), chunk_size):
        chunk = post_ids[i : i + chunk_size]
        query = "&".join(f"post_ids[]={pid}" for pid in chunk)
        url = f"{origin}/t/{topic_id}/posts.json?{query}"
        data = delving_get_json(url)
        if data and isinstance(data, dict):
            posts = data.get("post_stream", {}).get("posts", [])
            if isinstance(posts, list):
                collected.extend(posts)
                continue

        # Fallback to individual post endpoint
        for pid in chunk:
            post_url = f"{origin}/posts/{pid}.json"
            post_data = delving_get_json(post_url)
            if post_data and isinstance(post_data, dict):
                collected.append(post_data)
            else:
                print(f"warning: failed to fetch post {pid} from {post_url}", file=sys.stderr)

    return collected


def normalize_discourse_post(
    post: dict[str, Any], origin: str, topic_id: int, slug: str | None
) -> dict[str, Any]:
    """Convert a raw Discourse post object to the normalized JSONL record schema."""
    post_number = int(post["post_number"])
    author = str(post.get("username") or "")
    author_name = str(post.get("name") or author)
    created_at = str(post.get("created_at") or "")
    updated_at = str(post.get("updated_at") or created_at)

    if slug:
        canonical_url = f"{origin}/t/{slug}/{topic_id}/{post_number}"
    else:
        canonical_url = f"{origin}/t/{topic_id}/{post_number}"

    cooked = str(post.get("cooked") or "")
    body_text = normalize_html_to_text(cooked)
    content_hash = compute_content_hash(body_text)

    reply_to = post.get("reply_to_post_number")
    reply_to_post_number = int(reply_to) if reply_to is not None else None

    return {
        "post_number": post_number,
        "author": author,
        "author_name": author_name,
        "created_at": created_at,
        "updated_at": updated_at,
        "url": canonical_url,
        "body_text": body_text,
        "content_hash": content_hash,
        "reply_to_post_number": reply_to_post_number,
    }


def find_stream_gaps(post_numbers: set[int]) -> list[int]:
    """Return sorted list of missing post numbers between 1 and max(post_numbers)."""
    if not post_numbers:
        return []
    max_num = max(post_numbers)
    return [n for n in range(1, max_num + 1) if n not in post_numbers]


def ingest_discourse(
    target: str,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Ingest a Discourse topic and return (posts, gaps)."""
    origin, topic_id, target_slug = parse_discourse_target(target)
    topic_data = fetch_discourse_topic(origin, topic_id)

    slug = str(topic_data.get("slug") or target_slug or topic_id)
    post_stream = topic_data.get("post_stream", {})
    initial_posts = post_stream.get("posts", [])
    stream_ids = post_stream.get("stream", [])

    fetched_posts_by_id: dict[int, dict[str, Any]] = {}
    for p in initial_posts:
        pid = p.get("id")
        if pid is not None:
            fetched_posts_by_id[pid] = p

    missing_ids = [pid for pid in stream_ids if pid not in fetched_posts_by_id]
    if missing_ids:
        additional_posts = fetch_discourse_posts_by_ids(origin, topic_id, missing_ids)
        for p in additional_posts:
            pid = p.get("id")
            if pid is not None:
                fetched_posts_by_id[pid] = p

    all_raw_posts = list(fetched_posts_by_id.values())
    normalized_posts = [
        normalize_discourse_post(p, origin, topic_id, slug) for p in all_raw_posts
    ]
    normalized_posts.sort(key=lambda p: p["post_number"])

    seen_numbers = {p["post_number"] for p in normalized_posts}
    gaps = find_stream_gaps(seen_numbers)

    return normalized_posts, gaps


def merge_posts_idempotent(
    existing_posts_raw: list[dict[str, Any]],
    incoming_posts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge newly fetched posts into existing JSONL records idempotently.

    - If an existing post's content_hash changed, rewrites the line preserving previous_hash.
    - Emits only posts newer than highest seen post_number for new additions.
    """
    if not existing_posts_raw:
        return incoming_posts

    existing_by_num: dict[int, dict[str, Any]] = {
        p["post_number"]: dict(p) for p in existing_posts_raw
    }
    highest_seen = max(existing_by_num.keys()) if existing_by_num else 0

    for post in incoming_posts:
        num = post["post_number"]
        if num <= highest_seen:
            if num in existing_by_num:
                old_post = existing_by_num[num]
                if old_post.get("content_hash") != post["content_hash"]:
                    updated = dict(post)
                    updated["previous_hash"] = old_post["content_hash"]
                    existing_by_num[num] = updated
                else:
                    # Content unchanged; keep existing record to preserve any existing previous_hash
                    pass
            else:
                existing_by_num[num] = dict(post)
        else:
            # Post newer than highest seen post_number
            existing_by_num[num] = dict(post)

    merged = [existing_by_num[k] for k in sorted(existing_by_num.keys())]
    return merged


def load_existing_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read existing JSONL records from file."""
    if not path.exists():
        return []
    posts: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                posts.append(json.loads(line))
    return posts


def write_jsonl(path: Path, posts: list[dict[str, Any]]) -> None:
    """Write posts to JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for post in posts:
            f.write(json.dumps(post, ensure_ascii=False) + "\n")


def write_gaps_file(path: Path, gaps: list[int]) -> None:
    """Write sidecar gaps.json file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"gaps": gaps}, f, indent=2)
        f.write("\n")


def parse_github_target(target: str) -> tuple[str, str, int]:
    """Parse a GitHub target into (owner, repo, issue_or_pr_number)."""
    target = target.strip()
    match = GITHUB_TARGET_RE.match(target)
    if not match:
        raise ValueError(f"Cannot parse GitHub target: {target!r}")
    owner, repo, num_str = match.groups()
    return owner, repo, int(num_str)


def github_get_json(url: str) -> Any:
    """Fetch JSON from GitHub API with authentication and error handling."""
    req = Request(url, headers=github_headers())
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return None
        print(f"warning: GitHub GET {url} failed: {exc}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"warning: GitHub GET {url} failed: {exc}", file=sys.stderr)
        return None


def fetch_paginated_github(url_base: str) -> list[dict[str, Any]]:
    """Fetch all pages from a GitHub API collection endpoint."""
    items: list[dict[str, Any]] = []
    page = 1
    delim = "&" if "?" in url_base else "?"
    while True:
        url = f"{url_base}{delim}per_page=100&page={page}"
        batch = github_get_json(url)
        if not batch or not isinstance(batch, list):
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def ingest_github(target: str) -> tuple[list[dict[str, Any]], list[int]]:
    """Ingest a GitHub PR or issue into normalized post records."""
    owner, repo, number = parse_github_target(target)
    api_root = f"https://api.github.com/repos/{owner}/{repo}"

    issue_data = github_get_json(f"{api_root}/issues/{number}")
    if not issue_data or not isinstance(issue_data, dict):
        raise RuntimeError(f"Could not find GitHub issue or PR {owner}/{repo}#{number}")

    is_pr = "pull_request" in issue_data and issue_data["pull_request"] is not None

    raw_items: list[dict[str, Any]] = []

    # 1. Main PR/issue body
    main_author = issue_data.get("user", {}).get("login", "")
    main_author_name = issue_data.get("user", {}).get("name") or main_author
    raw_items.append({
        "raw_id": issue_data["id"],
        "author": main_author,
        "author_name": main_author_name,
        "created_at": issue_data["created_at"],
        "updated_at": issue_data.get("updated_at") or issue_data["created_at"],
        "url": issue_data["html_url"],
        "body_text": normalize_html_to_text(issue_data.get("body") or ""),
        "in_reply_to_id": None,
        "sort_key": (issue_data["created_at"], 0, issue_data["id"]),
    })

    # 2. Issue comments
    issue_comments = fetch_paginated_github(f"{api_root}/issues/{number}/comments")
    for c in issue_comments:
        c_author = c.get("user", {}).get("login", "")
        c_author_name = c.get("user", {}).get("name") or c_author
        raw_items.append({
            "raw_id": c["id"],
            "author": c_author,
            "author_name": c_author_name,
            "created_at": c["created_at"],
            "updated_at": c.get("updated_at") or c["created_at"],
            "url": c["html_url"],
            "body_text": normalize_html_to_text(c.get("body") or ""),
            "in_reply_to_id": None,
            "sort_key": (c["created_at"], 1, c["id"]),
        })

    # 3. Review comments (if PR)
    if is_pr:
        review_comments = fetch_paginated_github(f"{api_root}/pulls/{number}/comments")
        for rc in review_comments:
            rc_author = rc.get("user", {}).get("login", "")
            rc_author_name = rc.get("user", {}).get("name") or rc_author
            raw_items.append({
                "raw_id": rc["id"],
                "author": rc_author,
                "author_name": rc_author_name,
                "created_at": rc["created_at"],
                "updated_at": rc.get("updated_at") or rc["created_at"],
                "url": rc["html_url"],
                "body_text": normalize_html_to_text(rc.get("body") or ""),
                "in_reply_to_id": rc.get("in_reply_to_id"),
                "sort_key": (rc["created_at"], 2, rc["id"]),
            })

    # Stable sort by (created_at, type_priority, id)
    raw_items.sort(key=lambda item: item["sort_key"])

    id_to_post_number: dict[int, int] = {}
    for idx, item in enumerate(raw_items, start=1):
        item["post_number"] = idx
        id_to_post_number[item["raw_id"]] = idx

    normalized_posts: list[dict[str, Any]] = []
    for item in raw_items:
        parent_id = item.get("in_reply_to_id")
        reply_to = id_to_post_number.get(parent_id) if parent_id is not None else None
        body_text = item["body_text"]
        normalized_posts.append({
            "post_number": item["post_number"],
            "author": item["author"],
            "author_name": item["author_name"],
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
            "url": item["url"],
            "body_text": body_text,
            "content_hash": compute_content_hash(body_text),
            "reply_to_post_number": reply_to,
        })

    gaps = find_stream_gaps({p["post_number"] for p in normalized_posts})
    return normalized_posts, gaps


def detect_adapter(target: str, adapter_flag: str | None) -> str:
    """Detect whether target is discourse or github."""
    if adapter_flag and adapter_flag != "auto":
        return adapter_flag.lower()
    target_clean = target.strip()
    if "github.com" in target_clean or "#" in target_clean:
        return "github"
    return "discourse"


def main() -> None:
    parser = argparse.ArgumentParser(description="Discussion thread ingest tool.")
    parser.add_argument("target", help="Discussion target (URL, ID, or owner/repo#N)")
    parser.add_argument(
        "--output", "-o", type=Path, default=None, help="Output path for posts.jsonl"
    )
    parser.add_argument(
        "--gaps", type=Path, default=None, help="Output path for sidecar gaps.json"
    )
    parser.add_argument(
        "--adapter",
        choices=["auto", "discourse", "github"],
        default="auto",
        help="Adapter to use (default: auto)",
    )

    args = parser.parse_args()
    adapter = detect_adapter(args.target, args.adapter)

    if adapter == "discourse":
        posts, gaps = ingest_discourse(args.target)
    elif adapter == "github":
        posts, gaps = ingest_github(args.target)
    else:
        raise ValueError(f"Unknown adapter: {adapter}")

    output_path = args.output
    if output_path:
        existing = load_existing_jsonl(output_path)
        final_posts = merge_posts_idempotent(existing, posts)
        write_jsonl(output_path, final_posts)

        gaps_path = args.gaps or (output_path.parent / "gaps.json")
        write_gaps_file(gaps_path, gaps)
    else:
        for p in posts:
            print(json.dumps(p, ensure_ascii=False))


if __name__ == "__main__":
    main()

