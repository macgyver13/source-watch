#!/usr/bin/env python3
"""Extract judged discussion claims from a post stream into discussion state."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from copy import deepcopy
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_SCRIPTS = Path(__file__).resolve().parent
ROOT = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import discussion_judge as dj
import discussion_state as ds
import draft_discussion as dd
import eval_discussion as ev
import verify_discussion as vd

DEFAULT_BASE_URL = "http://127.0.0.1:10100/v1"
DEFAULT_MODEL = "anthropic/claude-opus-5"
DEFAULT_TIMEOUT = 180.0
DEFAULT_MAX_BODY_CHARS = 6000
EXCERPT_CHARS = 900
CATALOG_CHARS = 900
CANDIDATE_CHARS = 900
DUPLICATE_MIN_OVERLAP = 0.35
CONCEDE_MIN = 0.9
STATED_MIN_CONFIDENCE = 0.7
NONE_LABEL = "none"
EPOCH = "1970-01-01T00:00:00Z"
SEEN_VERSION = "source-watch.discussion-seen.v0"
USED_QUESTIONS = (
    "duplicate_of",
    "target",
    "polarity",
    "responds_to",
    "explicit_preference",
    "concedes",
)
DRAFT_SHAPE = {
    "claims": [{"quote": "<verbatim span from the post>", "text": "<one line summary>"}],
    "questions": [{"text": "<one line>"}],
}


class ExtractError(Exception):
    """Raised when the extractor cannot run against the given state, sidecar or post stream."""


def json_only(text: str) -> str:
    stripped = dd.strip_fences(text)
    last: str | None = None
    i = 0
    n = len(stripped)
    while i < n:
        if stripped[i] != "{":
            i += 1
            continue
        start = i
        depth = 0
        in_string = False
        escape = False
        j = i
        closed = False
        while j < n:
            ch = stripped[j]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        last = stripped[start : j + 1]
                        i = j + 1
                        closed = True
                        break
            j += 1
        if not closed:
            break
    return last if last is not None else stripped


def _is_transient(exc: Exception) -> bool:
    """True for errors worth retrying: timeouts, rate limits, server faults."""
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, HTTPError):
        return exc.code == 429 or exc.code >= 500
    if isinstance(exc, URLError):
        return isinstance(exc.reason, (TimeoutError, ConnectionError)) or "timed out" in str(exc.reason)
    return False


EMPTY_CONTENT_RETRIES = 4
EMPTY_CONTENT_BACKOFF = 1.5


def chat_complete(
    messages: list[dict],
    *,
    base_url: str,
    model: str,
    api_key: str,
    timeout: float,
    temperature: float | None,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload_obj: dict = {"model": model, "messages": messages}
    if temperature is not None:
        payload_obj["temperature"] = temperature
    payload = json.dumps(payload_obj).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    last_error = ""
    for attempt in range(EMPTY_CONTENT_RETRIES + 1):
        request = Request(url, data=payload, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as exc:
            # A timeout or a 429/5xx is transient far more often than not, and a
            # single one should not end an extraction that has run for an hour.
            if attempt < EMPTY_CONTENT_RETRIES and _is_transient(exc):
                time.sleep(EMPTY_CONTENT_BACKOFF * (attempt + 1))
                continue
            raise ExtractError(f"chat request failed: {exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExtractError("chat completion missing choices[0].message.content") from exc
        if not isinstance(data, dict):
            raise ExtractError("chat completion missing choices[0].message.content")
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ExtractError("chat completion missing choices[0].message.content") from exc
        if isinstance(content, str) and content.strip():
            return content
        # Some endpoints intermittently return a null or empty content with a normal
        # finish reason. Retrying the same request usually succeeds, and aborting a
        # long extraction over one flaky reply is worse than paying for a retry.
        last_error = (
            "content was null"
            if content is None
            else f"content was {type(content).__name__}"
        )
        if attempt < EMPTY_CONTENT_RETRIES:
            time.sleep(EMPTY_CONTENT_BACKOFF * (attempt + 1))
    raise ExtractError(
        f"chat completion content must be a string ({last_error} after "
        f"{EMPTY_CONTENT_RETRIES + 1} attempts)"
    )


def json_complete(complete: dd.CompleteFn) -> dd.CompleteFn:
    def wrapped(messages: list[dict]) -> str:
        return json_only(complete(messages))

    return wrapped


def draft_system_message() -> str:
    return "\n".join(
        [
            "You extract atomic arguments from one forum post.",
            "Answer with JSON only. No prose, no code fences.",
            "A quote must be copied verbatim from the post body, contiguous, at most 200 characters.",
            "No ellipsis inside a quote. One argument per claim.",
            "Only quote text the author of this post wrote. Skip text the post quotes from someone else.",
            "Add a question only when the post asks something the thread has not settled.",
        ]
    )


def draft_user_message(post: dict, max_body_chars: int) -> str:
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
        f"{json.dumps(DRAFT_SHAPE, indent=2)}\n"
    )


def draft_post(post: dict, complete: dd.CompleteFn, *, max_body_chars: int) -> dict:
    messages = [
        {"role": "system", "content": draft_system_message()},
        {"role": "user", "content": draft_user_message(post, max_body_chars)},
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
                parsed = dd.parse_model_json(content)
                error = None
                break
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                error = str(exc)
        if attempt == 0:
            messages.append({"role": "user", "content": "Return JSON only, no prose."})
    if parsed is None:
        return {"claims": [], "questions": [], "_error": error or "unparseable response"}
    if not isinstance(parsed.get("claims"), list):
        parsed["claims"] = []
    if not isinstance(parsed.get("questions"), list):
        parsed["questions"] = []
    return parsed


def provenance_failures(claim: dict, post: dict) -> list[str]:
    probe = {
        "schema_version": ds.SCHEMA_VERSION,
        "discussion": {},
        "options": [{"id": "probe", "name": "probe", "kind": "option"}],
        "claims": [dict(claim, target="probe")],
        "positions": [],
        "questions": [],
    }
    return vd.verify(probe, [post])


def load_seen(path: Path) -> dict:
    if not path.is_file():
        return {"schema_version": SEEN_VERSION, "posts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtractError(f"cannot load seen file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtractError(f"seen file {path} must be a JSON object")
    posts = data.get("posts")
    if posts is None:
        posts = {}
    if not isinstance(posts, dict):
        raise ExtractError(f"seen file {path} posts must be an object")
    return {"schema_version": data.get("schema_version", SEEN_VERSION), "posts": posts}


def _post_content_hash(post: dict) -> str:
    digest = post.get("content_hash")
    if isinstance(digest, str) and digest:
        return digest
    body = post.get("body_text") if isinstance(post.get("body_text"), str) else ""
    return ds.post_content_hash(body)


def save_seen(path: Path, posts: list[dict]) -> None:
    records: dict[str, dict] = {}
    for post in posts:
        url = post.get("url")
        if not isinstance(url, str) or not url:
            continue
        entry: dict = {"content_hash": _post_content_hash(post)}
        n = post.get("post_number")
        if isinstance(n, int) and not isinstance(n, bool):
            entry["post_number"] = n
        records[url] = entry
    payload = {"schema_version": SEEN_VERSION, "posts": records}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_at_for(state: dict, posts: list[dict]) -> str:
    newest: str | None = None
    for post in posts:
        created = post.get("created_at")
        if not isinstance(created, str) or not created:
            continue
        try:
            ts = ds.normalize_ts(created)
        except ds.DiscussionStateError:
            continue
        if newest is None or ts > newest:
            newest = ts
    if newest is not None:
        return newest
    discussion = state.get("discussion") if isinstance(state.get("discussion"), dict) else {}
    updated = discussion.get("updated_at") if isinstance(discussion, dict) else None
    if isinstance(updated, str) and updated:
        try:
            return ds.normalize_ts(updated)
        except ds.DiscussionStateError:
            pass
    return EPOCH


def hide_stale(state: dict, posts: list[dict], seen: dict, run_at: str) -> dict[str, int]:
    counts = {"source_deleted": 0, "source_edited": 0}
    by_url: dict[str, dict] = {}
    for post in posts:
        url = post.get("url")
        if isinstance(url, str) and url:
            by_url[url] = post
    seen_posts = seen.get("posts") if isinstance(seen.get("posts"), dict) else {}
    options = [row for row in (state.get("options") or []) if isinstance(row, dict)]
    questions = [row for row in (state.get("questions") or []) if isinstance(row, dict)]
    for claim in state.get("claims") or []:
        if not isinstance(claim, dict) or "hidden" in claim:
            continue
        url = claim.get("post_url")
        if not isinstance(url, str) or url not in by_url:
            claim["hidden"] = {
                "reason": "source_deleted",
                "at": run_at,
                "note": f"post_url {url} is not in the post stream",
            }
            counts["source_deleted"] += 1
            continue
        post = by_url[url]
        current_hash = _post_content_hash(post)
        recorded = seen_posts.get(url)
        recorded_hash = None
        if isinstance(recorded, dict):
            raw_hash = recorded.get("content_hash")
            if isinstance(raw_hash, str) and raw_hash:
                recorded_hash = raw_hash
        if recorded_hash is not None:
            if recorded_hash != current_hash:
                claim["hidden"] = {
                    "reason": "source_edited",
                    "at": run_at,
                    "note": (
                        f"content_hash changed from {recorded_hash[:12]} to {current_hash[:12]}"
                    ),
                }
                counts["source_edited"] += 1
            continue
        probe = {
            "schema_version": ds.SCHEMA_VERSION,
            "discussion": {},
            "options": options,
            "claims": [claim],
            "positions": [],
            "questions": questions,
        }
        failures = vd.verify(probe, posts)
        if failures:
            claim["hidden"] = {
                "reason": "source_edited",
                "at": run_at,
                "note": failures[0],
            }
            counts["source_edited"] += 1
    return counts


def _visible(rows: object) -> list[dict]:
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and "hidden" not in row]


def _id_num(entry_id: object) -> int:
    if not isinstance(entry_id, str) or len(entry_id) < 2:
        return 0
    try:
        return int(entry_id[1:])
    except ValueError:
        return 0


def _is_post_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _cap_lines(lines: list[str], cap: int, *, oldest: str) -> tuple[str, int]:
    kept = list(lines)
    dropped = 0
    while kept and len("\n".join(kept)) > cap:
        if oldest == "head":
            kept.pop(0)
        else:
            kept.pop()
        dropped += 1
    return "\n".join(kept), dropped


def target_catalog(state: dict, log: dict | None = None) -> tuple[list[str], str, str]:
    options = _visible(state.get("options") or [])
    questions = _visible(state.get("questions") or [])
    labels: list[str] = []
    for option in options:
        oid = option.get("id")
        if isinstance(oid, str) and oid:
            labels.append(oid)
    for question in questions:
        qid = question.get("id")
        if isinstance(qid, str) and qid:
            labels.append(qid)
    option_lines = [
        f"{option.get('id')} | {option.get('name', '')}"
        for option in options
        if isinstance(option.get("id"), str)
    ]
    question_rows = sorted(
        questions,
        key=lambda row: (str(row.get("date") or ""), _id_num(row.get("id"))),
        reverse=True,
    )
    question_lines = [
        f"{question.get('id')} | {question.get('text', '')}"
        for question in question_rows
        if isinstance(question.get("id"), str)
    ]
    options_text, n_opt = _cap_lines(option_lines, CATALOG_CHARS, oldest="head")
    questions_text, n_q = _cap_lines(question_lines, CATALOG_CHARS, oldest="tail")
    if log is not None:
        if n_opt:
            log["catalog_truncated"] += 1
        if n_q:
            log["catalog_truncated"] += 1
    return labels, options_text, questions_text


def duplicate_candidates(state: dict, quote: str) -> list[dict]:
    scored: list[tuple[float, int, dict]] = []
    for claim in _visible(state.get("claims") or []):
        score = ev.token_f1(claim.get("quote"), quote)
        if score >= DUPLICATE_MIN_OVERLAP:
            scored.append((score, _id_num(claim.get("id")), claim))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [row[2] for row in scored]


def responds_to_candidates(
    state: dict, post: dict, url_to_number: dict[str, int]
) -> list[dict]:
    author = post.get("author")
    reply_to = post.get("reply_to_post_number")
    body = ds.normalize_ws(
        post.get("body_text") if isinstance(post.get("body_text"), str) else ""
    )
    found: list[tuple[int, dict]] = []
    for claim in _visible(state.get("claims") or []):
        if claim.get("participant") == author:
            continue
        claim_url = claim.get("post_url")
        claim_n = url_to_number.get(claim_url) if isinstance(claim_url, str) else None
        on_reply = _is_post_number(reply_to) and claim_n == reply_to
        core = ds.quote_core(claim.get("quote") if isinstance(claim.get("quote"), str) else "")
        quoted = bool(core) and core in body
        if not on_reply and not quoted:
            continue
        found.append((claim_n if _is_post_number(claim_n) else -1, claim))
    found.sort(key=lambda row: -row[0])
    return [row[1] for row in found]


def candidate_lines(claims: list[dict]) -> str:
    return "\n".join(f"{claim.get('id')}: {claim.get('quote', '')}" for claim in claims)


def label_batches(candidates: list[dict]) -> list[list[dict]]:
    if not candidates:
        return []
    batches: list[list[dict]] = []
    current: list[dict] = []
    for candidate in candidates:
        trial = current + [candidate]
        if current and len(candidate_lines(trial)) > CANDIDATE_CHARS:
            batches.append(current)
            current = [candidate]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches


def _excerpt(post: dict) -> str:
    body = post.get("body_text") if isinstance(post.get("body_text"), str) else ""
    return body[:EXCERPT_CHARS]


def _target_name(state: dict, target_id: str) -> str:
    for option in state.get("options") or []:
        if isinstance(option, dict) and option.get("id") == target_id:
            name = option.get("name")
            return name if isinstance(name, str) and name else target_id
    for question in state.get("questions") or []:
        if isinstance(question, dict) and question.get("id") == target_id:
            text = question.get("text")
            return text if isinstance(text, str) and text else target_id
    return target_id


def _claim_hashes(state: dict) -> set[str]:
    hashes: set[str] = set()
    for claim in state.get("claims") or []:
        digest = claim.get("hash") if isinstance(claim, dict) else None
        if isinstance(digest, str) and digest:
            hashes.add(digest)
    return hashes


def _find_claim(state: dict, claim_id: str) -> dict | None:
    for claim in state.get("claims") or []:
        if isinstance(claim, dict) and claim.get("id") == claim_id:
            return claim
    return None


def _latest_position(state: dict, participant: object) -> dict | None:
    rows: list[dict] = []
    for position in state.get("positions") or []:
        if isinstance(position, dict) and position.get("participant") == participant:
            rows.append(position)
    rows.sort(key=lambda row: (str(row.get("date") or ""), _id_num(row.get("id"))))
    return rows[-1] if rows else None


def _visible_counts(state: dict) -> dict[str, int]:
    hidden = 0
    counts = {"claims": 0, "positions": 0, "questions": 0, "hidden": 0}
    for key in ("claims", "positions", "questions"):
        visible = 0
        for row in state.get(key) or []:
            if not isinstance(row, dict):
                continue
            if "hidden" in row:
                hidden += 1
            else:
                visible += 1
        counts[key] = visible
    counts["hidden"] = hidden
    return counts


def _url_to_number(posts: list[dict]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for post in posts:
        url = post.get("url")
        n = post.get("post_number")
        if isinstance(url, str) and _is_post_number(n):
            mapping[url] = n
    return mapping


def _post_date(post: dict) -> str:
    created = post.get("created_at")
    if isinstance(created, str) and created:
        try:
            return ds.normalize_ts(created)
        except ds.DiscussionStateError:
            pass
    return EPOCH


def _empty_log() -> dict:
    return {
        "model": "",
        "judge_model": "",
        "base_url": "",
        "posts_total": 0,
        "posts_processed": 0,
        "cursor_in": 0,
        "cursor_out": 0,
        "drafted_claims": 0,
        "kept_claims": 0,
        "dropped": {
            "verify": 0,
            "duplicate_hash": 0,
            "duplicate_of": 0,
            "no_target": 0,
            "judge_error": 0,
        },
        "drop_reasons": [],
        "questions_added": 0,
        "positions_added": 0,
        "answered_links": 0,
        "conceded": 0,
        "concede_calls": 0,
        "preference_skipped": 0,
        "candidate_batches": 0,
        "catalog_truncated": 0,
        "hidden": {"source_deleted": 0, "source_edited": 0},
        "judge_errors": [],
        "parse_failures": [],
        "wall_seconds": 0.0,
    }


def _first_named(
    batches: list[list[dict]],
    ask,
    log: dict,
) -> str | None:
    for batch in batches:
        log["candidate_batches"] += 1
        verdict = ask(batch)
        value = verdict.value
        if isinstance(value, str) and value != NONE_LABEL:
            return value
    return None


def extract(
    state: dict,
    posts: list[dict],
    *,
    judge: dj.Judge,
    complete: dd.CompleteFn,
    questions: dict[str, dj.Question],
    seen: dict,
    cursor: int | None = None,
    source_url: str | None = None,
    limit: int | None = None,
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
) -> tuple[dict, dict]:
    started = time.perf_counter()
    new_state = deepcopy(state)
    log = _empty_log()
    for key in ("claims", "positions", "questions"):
        if not isinstance(new_state.get(key), list):
            new_state[key] = []
    discussion = new_state.get("discussion")
    if not isinstance(discussion, dict):
        raise ExtractError("state discussion.sources is empty, cannot resolve a cursor")
    sources = discussion.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ExtractError("state discussion.sources is empty, cannot resolve a cursor")
    run_at = run_at_for(new_state, posts)
    hidden_counts = hide_stale(new_state, posts, seen, run_at)
    log["hidden"] = hidden_counts
    if source_url is not None:
        row = next((item for item in sources if isinstance(item, dict) and item.get("url") == source_url), None)
        if row is None:
            raise ExtractError(f"source {source_url} is not in the state")
    else:
        row = sources[0]
        if not isinstance(row, dict):
            raise ExtractError("source is not in the state")
    cursor_in = cursor if cursor is not None else (row["cursor"] or 0)
    if not _is_post_number(cursor_in):
        cursor_in = 0
    work = [
        post
        for post in posts
        if _is_post_number(post.get("post_number")) and post["post_number"] > cursor_in
    ]
    work.sort(key=lambda post: post["post_number"])
    if limit is not None:
        work = work[:limit]
    log["posts_total"] = len(posts)
    log["cursor_in"] = cursor_in
    url_to_number = _url_to_number(posts)
    processed_numbers: list[int] = []

    def ask_post(qid: str, payload: dict, labels: list[str] | None = None):
        try:
            if labels is None:
                return judge.ask(questions[qid], payload)
            return judge.ask(questions[qid], payload, labels=labels)
        except dj.JudgeError as exc:
            log["judge_errors"].append(str(exc))
            return None

    for post in work:
        post_n = post["post_number"]
        processed_numbers.append(post_n)
        parsed = draft_post(post, complete, max_body_chars=max_body_chars)
        if parsed.get("_error"):
            log["parse_failures"].append({"post": post_n, "error": str(parsed["_error"])})
            continue
        author = post.get("author") if isinstance(post.get("author"), str) else ""
        url = post.get("url") if isinstance(post.get("url"), str) else ""
        date = _post_date(post)
        excerpt = _excerpt(post)
        for raw_q in parsed.get("questions") or []:
            if not isinstance(raw_q, dict):
                continue
            text = raw_q.get("text")
            if not isinstance(text, str):
                continue
            text = text.strip()[: ds.TEXT_MAX]
            if not text:
                continue
            norm = ds.normalize_ws(text)
            exists = any(
                ds.normalize_ws(str(question.get("text") or "")) == norm
                for question in _visible(new_state["questions"])
            )
            if exists:
                continue
            new_state["questions"].append(
                {
                    "id": ds.next_id(new_state, "question"),
                    "text": text,
                    "raised_by": author,
                    "post_url": url,
                    "date": date,
                    "status": "open",
                }
            )
            log["questions_added"] += 1
        for raw_claim in parsed.get("claims") or []:
            if not isinstance(raw_claim, dict):
                continue
            quote = raw_claim.get("quote")
            if not isinstance(quote, str) or not quote:
                continue
            quote = quote[: ds.QUOTE_MAX].rstrip()
            if not quote:
                continue
            log["drafted_claims"] += 1
            claim: dict = {
                "id": ds.next_id(new_state, "claim"),
                "hash": ds.claim_hash(author, url, quote),
                "participant": author,
                "post_url": url,
                "quote": quote,
                "date": date,
                "status": "open",
            }
            text = raw_claim.get("text")
            if isinstance(text, str) and text.strip():
                claim["text"] = text.strip()[: ds.TEXT_MAX]
            failures = provenance_failures(claim, post)
            if failures:
                log["dropped"]["verify"] += 1
                log["drop_reasons"].append(failures[0])
                continue
            if claim["hash"] in _claim_hashes(new_state):
                log["dropped"]["duplicate_hash"] += 1
                continue
            appended = False
            try:
                dup_cands = duplicate_candidates(new_state, quote)
                if dup_cands:
                    named = _first_named(
                        label_batches(dup_cands),
                        lambda batch, q=quote: judge.ask(
                            questions["duplicate_of"],
                            {"quote": q, "candidates": candidate_lines(batch)},
                            labels=[str(item.get("id")) for item in batch],
                        ),
                        log,
                    )
                    if named is not None:
                        log["dropped"]["duplicate_of"] += 1
                        log["drop_reasons"].append(f"{claim['id']} duplicates {named}")
                        continue
                labels, options_text, questions_text = target_catalog(new_state, log)
                if not labels:
                    log["dropped"]["no_target"] += 1
                    continue
                if len(labels) == 1:
                    claim["target"] = labels[0]
                else:
                    verdict = judge.ask(
                        questions["target"],
                        {
                            "quote": quote,
                            "options": options_text,
                            "post_excerpt": excerpt,
                        },
                        labels=labels,
                    )
                    claim["target"] = verdict.value
                    claim["confidence"] = round(float(verdict.confidence), 6)
                polarity = judge.ask(
                    questions["polarity"],
                    {"target": _target_name(new_state, str(claim["target"])), "quote": quote},
                )
                claim["polarity"] = polarity.value
                new_state["claims"].append(claim)
                appended = True
                log["kept_claims"] += 1
                resp_cands = responds_to_candidates(new_state, post, url_to_number)
                if resp_cands:
                    named = _first_named(
                        label_batches(resp_cands),
                        lambda batch, q=quote: judge.ask(
                            questions["responds_to"],
                            {
                                "quote": q,
                                "candidates": candidate_lines(batch),
                                "reply_to": "" if post.get("reply_to_post_number") is None else str(post.get("reply_to_post_number")),
                            },
                            labels=[str(item.get("id")) for item in batch],
                        ),
                        log,
                    )
                    if named is not None:
                        earlier = _find_claim(new_state, named)
                        if (
                            earlier is not None
                            and earlier is not claim
                            and earlier.get("status") == "open"
                        ):
                            earlier["status"] = "answered"
                            earlier["answered_by"] = claim["id"]
                            log["answered_links"] += 1
            except dj.JudgeError as exc:
                log["dropped"]["judge_error"] += 1
                log["drop_reasons"].append(str(exc))
                if appended:
                    new_state["claims"].pop()
                    log["kept_claims"] -= 1
                continue
        option_ids = [
            str(option.get("id"))
            for option in _visible(new_state.get("options") or [])
            if option.get("kind") == "option" and isinstance(option.get("id"), str)
        ]
        if not option_ids:
            log["preference_skipped"] += 1
        else:
            _, options_text, _ = target_catalog(new_state, log)
            verdict = ask_post(
                "explicit_preference",
                {
                    "participant": author,
                    "options": options_text,
                    "post_excerpt": excerpt,
                },
                labels=option_ids,
            )
            if (
                isinstance(verdict, dj.Verdict)
                and isinstance(verdict.value, str)
                and verdict.value != NONE_LABEL
            ):
                prefers = [verdict.value]
                basis = (
                    "stated"
                    if float(verdict.confidence) >= STATED_MIN_CONFIDENCE
                    else "inferred"
                )
                latest = _latest_position(new_state, author)
                if latest is not None and latest.get("prefers") == prefers and latest.get("basis") == basis:
                    pass
                else:
                    position = {
                        "id": ds.next_id(new_state, "position"),
                        "participant": author,
                        "prefers": prefers,
                        "basis": basis,
                        "post_url": url,
                        "date": date,
                        "confidence": round(float(verdict.confidence), 6),
                    }
                    if latest is not None and isinstance(latest.get("id"), str):
                        position["supersedes"] = latest["id"]
                    new_state["positions"].append(position)
                    log["positions_added"] += 1
        # A concession only makes sense where someone pushed back. Without this
        # gate the question is asked of every earlier claim by the same author,
        # including their own uncontested statements of fact, which is how a PR
        # description ends up marked as a series of concessions.
        contested: set[str] = set()
        for other in _visible(new_state["claims"]):
            if other.get("participant") == author:
                continue
            target = other.get("target")
            polarity = other.get("polarity")
            for challenged in _visible(new_state["claims"]):
                if challenged.get("participant") != author:
                    continue
                if challenged.get("target") != target:
                    continue
                if polarity and challenged.get("polarity") and polarity == challenged.get("polarity"):
                    continue
                cid = challenged.get("id")
                if isinstance(cid, str):
                    contested.add(cid)
        for claim_row in _visible(new_state["claims"]):
            answered = claim_row.get("answered_by")
            if isinstance(answered, str) and answered:
                cid = claim_row.get("id")
                if isinstance(cid, str):
                    contested.add(cid)

        earlier_open = []
        for existing in _visible(new_state["claims"]):
            if existing.get("participant") != author or existing.get("status") != "open":
                continue
            if existing.get("id") not in contested:
                continue
            existing_n = url_to_number.get(existing.get("post_url")) if isinstance(existing.get("post_url"), str) else None
            if _is_post_number(existing_n) and existing_n < post_n:
                earlier_open.append(existing)
        earlier_open.sort(key=lambda row: _id_num(row.get("id")))
        for existing in earlier_open:
            log["concede_calls"] += 1
            result = ask_post(
                "concedes",
                {
                    "participant": author,
                    "claim_quote": existing.get("quote") if isinstance(existing.get("quote"), str) else "",
                    "post_excerpt": excerpt,
                },
            )
            if isinstance(result, (int, float)) and not isinstance(result, bool) and float(result) >= CONCEDE_MIN:
                existing["status"] = "conceded"
                existing["concede_confidence"] = round(float(result), 6)
                log["conceded"] += 1

    highest = max(processed_numbers) if processed_numbers else cursor_in
    cursor_out = max(cursor_in, highest)
    row["cursor"] = cursor_out
    discussion["updated_at"] = run_at
    log["cursor_out"] = cursor_out
    log["posts_processed"] = len(work)
    hidden_any = hidden_counts["source_deleted"] + hidden_counts["source_edited"] > 0
    if work or hidden_any:
        if not isinstance(new_state.get("snapshots"), list):
            new_state["snapshots"] = []
        resolved_url = row.get("url") if isinstance(row.get("url"), str) else ""
        new_state["snapshots"].append(
            {
                "at": run_at,
                "cursors": {resolved_url: cursor_out},
                "counts": _visible_counts(new_state),
            }
        )
    log["wall_seconds"] = round(time.perf_counter() - started, 3)
    log["judge_model"] = getattr(judge, "model", None) or ""
    return new_state, log


def _from_empty(state: dict) -> dict:
    out = deepcopy(state)
    discussion = out.get("discussion")
    if isinstance(discussion, dict):
        sources = discussion.get("sources")
        if isinstance(sources, list):
            for item in sources:
                if isinstance(item, dict):
                    item["cursor"] = 0
    out["claims"] = []
    out["positions"] = []
    out["questions"] = []
    out["snapshots"] = []
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract judged discussion claims from posts.")
    parser.add_argument("--posts", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--from-empty", action="store_true")
    parser.add_argument("--cursor", type=int)
    parser.add_argument("--source-url")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--seen", type=Path)
    parser.add_argument(
        "--questions",
        type=Path,
        default=ROOT / "schema" / "judge-questions.yaml",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key-env", default="")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--max-body-chars", type=int, default=DEFAULT_MAX_BODY_CHARS)
    parser.add_argument("--judge-model")
    parser.add_argument("--judge-base-url")
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    try:
        state = ds.load(args.state)
        if args.from_empty:
            state = _from_empty(state)
        posts = vd.load_posts(args.posts)
        questions = dj.load_questions(args.questions)
        for qid in USED_QUESTIONS:
            if qid not in questions:
                raise ExtractError(f"judge-questions is missing '{qid}'")
        api_key = os.environ.get(args.api_key_env, "") if args.api_key_env else ""
        draft_complete = json_complete(
            lambda messages: chat_complete(
                messages,
                base_url=args.base_url,
                model=args.model,
                api_key=api_key,
                timeout=args.timeout,
                temperature=args.temperature,
            )
        )
        judge_model = args.judge_model or args.model
        judge_base_url = args.judge_base_url or args.base_url
        if os.environ.get("DISCUSSION_JUDGE"):
            judge = dj.judge_from_env()
            complete_attr = getattr(judge, "complete", None)
            if callable(complete_attr):
                judge.complete = json_complete(complete_attr)
        else:
            judge_complete = json_complete(
                lambda messages: chat_complete(
                    messages,
                    base_url=judge_base_url,
                    model=judge_model,
                    api_key=api_key,
                    timeout=args.timeout,
                    temperature=args.temperature,
                )
            )
            judge = dj.JsonModelJudge(judge_complete, name="chat", model=judge_model)
        seen_path = args.seen if args.seen is not None else Path(str(args.out) + ".seen.json")
        seen = load_seen(seen_path)
        new_state, log = extract(
            state,
            posts,
            judge=judge,
            complete=draft_complete,
            questions=questions,
            seen=seen,
            cursor=args.cursor,
            source_url=args.source_url,
            limit=args.limit,
            max_body_chars=args.max_body_chars,
        )
        log["model"] = args.model
        log["judge_model"] = getattr(judge, "model", None) or judge_model
        log["base_url"] = args.base_url
        errors = ds.validate(new_state)
        if errors:
            for error in errors:
                print(error)
            return 1
        args.out.write_text(
            json.dumps(new_state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        save_seen(seen_path, posts)
        if args.log is not None:
            args.log.write_text(
                json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
    except (ExtractError, ds.DiscussionStateError, dj.JudgeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"ok {args.out} ({log['kept_claims']} claims, {log['positions_added']} positions, "
        f"{log['questions_added']} questions, {log['posts_processed']}/{log['posts_total']} posts, "
        f"cursor {log['cursor_in']} -> {log['cursor_out']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
