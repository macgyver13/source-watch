#!/usr/bin/env python3
"""Draft a discussion state from posts using a chat completions endpoint."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import discussion_state as ds
import verify_discussion as vd

CompleteFn = Callable[[list[dict]], str]
RESPONSE_SHAPE = {
    "claims": [
        {
            "polarity": "benefit|blocker",
            "target": "<catalog id>",
            "quote": "<verbatim>",
            "text": "<summary>",
        }
    ],
    "position": {
        "prefers": ["<catalog id>"],
        "basis": "stated|inferred",
        "confidence": 0.0,
    },
    "questions": [{"text": "<one line>"}],
}


def system_message(catalog: dict) -> str:
    lines = [
        "You extract atomic arguments from one forum post.",
        "Answer with JSON only.",
        "Allowed polarity values are benefit and blocker.",
        "Allowed target ids are exactly the catalog ids below.",
        "A quote must be copied verbatim from the post body, contiguous, at most 200 characters, no ellipsis inside.",
        "Catalog (id | name | kind | gloss):",
    ]
    options = catalog.get("options") if isinstance(catalog.get("options"), list) else []
    for option in options:
        if not isinstance(option, dict):
            continue
        lines.append(
            f"{option.get('id', '')} | {option.get('name', '')} | {option.get('kind', '')} | {option.get('gloss', '')}"
        )
    return "\n".join(lines)


def user_message(post: dict, max_body_chars: int) -> str:
    body = post.get("body_text") if isinstance(post.get("body_text"), str) else ""
    if len(body) > max_body_chars:
        body = body[:max_body_chars]
    return (
        f"post_number: {post.get('post_number')}\n"
        f"author: {post.get('author')}\n"
        f"created_at: {post.get('created_at')}\n"
        f"url: {post.get('url')}\n"
        f"body_text:\n{body}\n\n"
        "Respond with JSON only, of this shape:\n"
        f"{json.dumps(RESPONSE_SHAPE, indent=2)}\n"
        "position may be null."
    )


def strip_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_model_json(text: str) -> dict:
    data = json.loads(strip_fences(text))
    if not isinstance(data, dict):
        raise ValueError("model JSON must be an object")
    return data


def catalog_ids(catalog: dict) -> tuple[set[str], set[str]]:
    option_ids: set[str] = set()
    prefer_ids: set[str] = set()
    options = catalog.get("options") if isinstance(catalog.get("options"), list) else []
    for option in options:
        if not isinstance(option, dict) or not isinstance(option.get("id"), str):
            continue
        option_ids.add(option["id"])
        if option.get("kind") == "option":
            prefer_ids.add(option["id"])
    return option_ids, prefer_ids


def _coerce_claims(
    raw_claims: object,
    post: dict,
    state: dict,
    option_ids: set[str],
    log: dict,
) -> None:
    if not isinstance(raw_claims, list):
        return
    participant = post.get("author") if isinstance(post.get("author"), str) else ""
    post_url = post.get("url") if isinstance(post.get("url"), str) else ""
    date = ds.normalize_ts(str(post.get("created_at") or ""))
    for raw in raw_claims:
        if not isinstance(raw, dict):
            continue
        quote = raw.get("quote")
        if not isinstance(quote, str) or not quote:
            continue
        if len(quote) > ds.QUOTE_MAX:
            quote = quote[: ds.QUOTE_MAX].rstrip()
        if not quote:
            continue
        polarity = raw.get("polarity")
        if not isinstance(polarity, str):
            continue
        polarity = polarity.lower()
        if polarity not in ds.POLARITIES:
            continue
        target = raw.get("target")
        if target not in option_ids:
            log["target_out_of_catalog"] += 1
            continue
        claim: dict = {
            "id": ds.next_id(state, "claim"),
            "hash": ds.claim_hash(participant, post_url, quote),
            "polarity": polarity,
            "target": target,
            "participant": participant,
            "post_url": post_url,
            "quote": quote,
            "date": date,
            "status": "open",
        }
        text = raw.get("text")
        if isinstance(text, str):
            text = text[: ds.TEXT_MAX]
            if text:
                claim["text"] = text
        state["claims"].append(claim)


def _coerce_position(
    raw_position: object,
    post: dict,
    state: dict,
    prefer_ids: set[str],
) -> None:
    if raw_position is None:
        return
    if not isinstance(raw_position, dict):
        return
    prefers_raw = raw_position.get("prefers")
    prefers: list[str] = []
    if isinstance(prefers_raw, list):
        seen: set[str] = set()
        for item in prefers_raw:
            if item in prefer_ids and item not in seen:
                prefers.append(item)
                seen.add(item)
    basis = raw_position.get("basis")
    if basis not in ds.BASES:
        basis = "inferred"
    position = {
        "id": ds.next_id(state, "position"),
        "participant": post.get("author") if isinstance(post.get("author"), str) else "",
        "prefers": prefers,
        "basis": basis,
        "post_url": post.get("url") if isinstance(post.get("url"), str) else "",
        "date": ds.normalize_ts(str(post.get("created_at") or "")),
    }
    confidence = raw_position.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        pass
    else:
        clamped = float(confidence)
        if clamped < 0:
            clamped = 0.0
        elif clamped > 1:
            clamped = 1.0
        position["confidence"] = clamped
    state["positions"].append(position)


def _coerce_questions(raw_questions: object, post: dict, state: dict) -> None:
    if not isinstance(raw_questions, list):
        return
    raised_by = post.get("author") if isinstance(post.get("author"), str) else ""
    post_url = post.get("url") if isinstance(post.get("url"), str) else ""
    date = ds.normalize_ts(str(post.get("created_at") or ""))
    for raw in raw_questions:
        if not isinstance(raw, dict):
            continue
        text = raw.get("text")
        if not isinstance(text, str):
            continue
        text = text[: ds.TEXT_MAX].strip()
        if not text:
            continue
        state["questions"].append(
            {
                "id": ds.next_id(state, "question"),
                "text": text,
                "raised_by": raised_by,
                "post_url": post_url,
                "date": date,
                "status": "open",
            }
        )


def _empty_state(catalog: dict) -> dict:
    discussion = deepcopy(catalog.get("discussion")) if isinstance(catalog.get("discussion"), dict) else {}
    options = deepcopy(catalog.get("options")) if isinstance(catalog.get("options"), list) else []
    return {
        "schema_version": catalog.get("schema_version", ds.SCHEMA_VERSION),
        "discussion": discussion,
        "options": options,
        "claims": [],
        "positions": [],
        "questions": [],
    }


def draft(
    posts: list[dict],
    catalog: dict,
    complete: CompleteFn,
    *,
    model: str = "",
    base_url: str = "",
    max_body_chars: int = 6000,
    limit: int | None = None,
) -> tuple[dict, dict]:
    started = time.perf_counter()
    if limit is not None:
        posts = posts[:limit]
    state = _empty_state(catalog)
    option_ids, prefer_ids = catalog_ids(catalog)
    sys_msg = system_message(catalog)
    log = {
        "model": model,
        "base_url": base_url,
        "posts_total": len(posts),
        "posts_drafted": 0,
        "parse_failures": [],
        "target_out_of_catalog": 0,
        "claims": 0,
        "positions": 0,
        "questions": 0,
        "wall_seconds": 0.0,
    }
    newest_created: str | None = None
    newest_post_n: int | None = None
    for post in posts:
        post_n = post.get("post_number")
        messages = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_message(post, max_body_chars)},
        ]
        parsed = None
        error = None
        for attempt in range(2):
            try:
                content = complete(messages)
            except Exception as exc:
                error = str(exc)
                content = None
            if content is not None:
                try:
                    parsed = parse_model_json(content)
                    error = None
                    break
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    error = str(exc)
            if attempt == 0:
                messages.append({"role": "user", "content": "Return JSON only, no prose."})
        if parsed is None:
            log["parse_failures"].append({"post": post_n, "error": error or "unparseable response"})
            continue
        _coerce_claims(parsed.get("claims"), post, state, option_ids, log)
        _coerce_position(parsed.get("position"), post, state, prefer_ids)
        _coerce_questions(parsed.get("questions"), post, state)
        log["posts_drafted"] += 1
        created = post.get("created_at")
        if isinstance(created, str) and created:
            if newest_post_n is None or (isinstance(post_n, int) and post_n > newest_post_n):
                newest_created = created
                newest_post_n = post_n if isinstance(post_n, int) else newest_post_n
    if newest_created is not None and isinstance(state.get("discussion"), dict):
        state["discussion"]["updated_at"] = ds.normalize_ts(newest_created)
    log["claims"] = len(state["claims"])
    log["positions"] = len(state["positions"])
    log["questions"] = len(state["questions"])
    log["wall_seconds"] = round(time.perf_counter() - started, 3)
    return state, log


def complete_chat(
    messages: list[dict],
    *,
    base_url: str,
    model: str,
    api_key: str,
    timeout: float,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = json.dumps(
        {"model": model, "temperature": 0, "messages": messages}
    ).encode("utf-8")

    def _open(send_auth: bool) -> dict:
        headers = {"Content-Type": "application/json"}
        if send_auth:
            headers["Authorization"] = f"Bearer {api_key}"
        request = Request(url, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("chat completion response must be an object")
        return data

    try:
        data = _open(send_auth=True)
    except HTTPError as exc:
        if exc.code == 401 and not api_key:
            data = _open(send_auth=False)
        else:
            raise
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("chat completion missing choices[0].message.content") from exc
    if not isinstance(content, str):
        raise ValueError("chat completion content must be a string")
    return content


def main() -> int:
    parser = argparse.ArgumentParser(description="Draft discussion claims from posts via a chat model.")
    parser.add_argument("--posts", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default="http://localhost:8080/v1")
    parser.add_argument("--api-key-env", default="MAPLE_API_KEY")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-body-chars", type=int, default=6000)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    try:
        catalog = ds.load(args.catalog)
        posts = vd.load_posts(args.posts)
    except ds.DiscussionStateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    api_key = os.environ.get(args.api_key_env, "")

    def complete(messages: list[dict]) -> str:
        return complete_chat(
            messages,
            base_url=args.base_url,
            model=args.model,
            api_key=api_key,
            timeout=args.timeout,
        )

    state, log = draft(
        posts,
        catalog,
        complete,
        model=args.model,
        base_url=args.base_url,
        max_body_chars=args.max_body_chars,
        limit=args.limit,
    )
    if args.log is not None:
        args.log.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    errors = ds.validate(state)
    if errors:
        for error in errors:
            print(error)
        return 1
    args.out.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"ok {args.out} ({log['claims']} claims, {log['positions']} positions, "
        f"{log['questions']} questions, {log['posts_drafted']}/{log['posts_total']} posts)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
