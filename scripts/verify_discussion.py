#!/usr/bin/env python3
"""Mechanically verify discussion claims against a normalized post stream."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import discussion_state as ds


def _index_posts(posts: list[dict]) -> dict[str, dict]:
    by_url: dict[str, dict] = {}
    for post in posts:
        url = post.get("url")
        if not isinstance(url, str) or not url:
            continue
        existing = by_url.get(url)
        if existing is None:
            by_url[url] = post
            continue
        old_n = existing.get("post_number")
        new_n = post.get("post_number")
        if not isinstance(old_n, int):
            by_url[url] = post
        elif isinstance(new_n, int) and new_n > old_n:
            by_url[url] = post
    return by_url


def _catalog_ids(state: dict) -> tuple[set[str], set[str]]:
    option_ids: set[str] = set()
    for option in state.get("options") or []:
        if isinstance(option, dict) and isinstance(option.get("id"), str):
            option_ids.add(option["id"])
    question_ids: set[str] = set()
    for question in state.get("questions") or []:
        if isinstance(question, dict) and isinstance(question.get("id"), str):
            question_ids.add(question["id"])
    return option_ids, question_ids


def verify(state: dict, posts: list[dict]) -> list[str]:
    """Return failure strings for visible claims, in document order."""
    failures: list[str] = []
    by_url = _index_posts(posts)
    option_ids, question_ids = _catalog_ids(state)
    claims = state.get("claims") if isinstance(state.get("claims"), list) else []
    for claim in claims:
        if not isinstance(claim, dict) or "hidden" in claim:
            continue
        cid = claim.get("id") if isinstance(claim.get("id"), str) else "?"
        post_url = claim.get("post_url")
        post = by_url.get(post_url) if isinstance(post_url, str) else None
        if post is None:
            failures.append(f"claim {cid}: post_url {post_url} is not in the post stream")
        else:
            quote = claim.get("quote") if isinstance(claim.get("quote"), str) else ""
            core = ds.quote_core(quote)
            body = ds.normalize_ws(str(post.get("body_text") or ""))
            if core not in body:
                failures.append(
                    f"claim {cid}: quote is not a verbatim substring of post {post.get('post_number')}"
                )
            if "..." in core or "\u2026" in core:
                failures.append(f"claim {cid}: quote contains internal elision")
            participant = claim.get("participant")
            author = post.get("author")
            if participant != author:
                failures.append(
                    f"claim {cid}: participant {participant} does not match post author {author}"
                )
            created = post.get("created_at")
            if created is not None:
                normalized = ds.normalize_ts(str(created))
                date = claim.get("date")
                if date != normalized:
                    failures.append(
                        f"claim {cid}: date {date} does not match post date {normalized}"
                    )
        target = claim.get("target")
        if target not in option_ids and target not in question_ids:
            failures.append(f"claim {cid}: unknown target {target}")
        participant = claim.get("participant") if isinstance(claim.get("participant"), str) else ""
        quote = claim.get("quote") if isinstance(claim.get("quote"), str) else ""
        url = post_url if isinstance(post_url, str) else ""
        if claim.get("hash") != ds.claim_hash(participant, url, quote):
            failures.append(f"claim {cid}: hash does not match claim_hash")
    return failures


def load_posts(path: Path | str) -> list[dict]:
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ds.DiscussionStateError(f"{path}: {exc}") from exc
    posts: list[dict] = []
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ds.DiscussionStateError(f"{path}:{i}: {exc}") from exc
        if not isinstance(row, dict):
            raise ds.DiscussionStateError(f"{path}:{i}: post must be a mapping")
        posts.append(row)
    return posts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify discussion claims against a normalized post stream."
    )
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--posts", required=True, type=Path)
    args = parser.parse_args()
    try:
        state = ds.load(args.state)
        posts = load_posts(args.posts)
    except ds.DiscussionStateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    schema_errors = ds.validate(state)
    for error in schema_errors:
        print(f"schema {error}")
    failures = verify(state, posts)
    for failure in failures:
        print(failure)
    if schema_errors or failures:
        return 1
    claims = state.get("claims") if isinstance(state.get("claims"), list) else []
    visible = sum(1 for claim in claims if isinstance(claim, dict) and "hidden" not in claim)
    print(f"ok {args.state} ({visible} claims verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
