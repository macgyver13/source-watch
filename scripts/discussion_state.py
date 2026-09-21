#!/usr/bin/env python3
"""Load and validate Source Watch discussion state documents."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

SCHEMA_VERSION = "source-watch.discussion.v0"
ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "discussion-state.schema.json"

POLARITIES = ("benefit", "blocker")
CLAIM_STATUSES = ("open", "answered", "conceded", "withdrawn")
QUESTION_STATUSES = ("open", "resolved")
OPTION_KINDS = ("option", "mechanism")
BASES = ("inferred", "stated", "self_declared")
HIDDEN_REASONS = ("source_deleted", "source_edited", "disputed_by_participant", "curator")
PLATFORMS = ("discourse", "github")
ID_PREFIXES = {"claim": "c", "position": "p", "question": "q"}
QUOTE_MAX = 200
TEXT_MAX = 280
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_ELLIPSES = ("...", "\u2026")
_HTTP_PREFIXES = ("http://", "https://")
_COLLECTIONS = {"claim": "claims", "position": "positions", "question": "questions"}
_ID_PATTERNS = {
    "claims": re.compile(r"^c\d+$"),
    "positions": re.compile(r"^p\d+$"),
    "questions": re.compile(r"^q\d+$"),
}


class DiscussionStateError(Exception):
    """Raised when a discussion state file cannot be loaded or a timestamp cannot be parsed."""


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def quote_core(quote: str) -> str:
    core = normalize_ws(quote)
    for marker in _ELLIPSES:
        if core.startswith(marker):
            core = core[len(marker) :]
            break
    for marker in _ELLIPSES:
        if core.endswith(marker):
            core = core[: -len(marker)]
            break
    return normalize_ws(core)


def normalize_ts(value: str) -> str:
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise DiscussionStateError(f"unparseable timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc).replace(microsecond=0)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def claim_hash(participant: str, post_url: str, quote: str) -> str:
    payload = "\n".join([participant.strip(), post_url.strip(), quote_core(quote)])
    return sha256(payload.encode("utf-8")).hexdigest()


def post_content_hash(body_text: str) -> str:
    return sha256(body_text.encode("utf-8")).hexdigest()


def next_id(state: dict, kind: str) -> str:
    prefix = ID_PREFIXES.get(kind)
    if prefix is None:
        raise DiscussionStateError(f"unknown id kind {kind!r}")
    collection = state.get(_COLLECTIONS[kind]) or []
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    highest = 0
    for entry in collection:
        if not isinstance(entry, dict):
            continue
        match = pattern.match(str(entry.get("id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}{highest + 1}"


def load(path: Path | str) -> dict:
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise DiscussionStateError(f"{path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DiscussionStateError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DiscussionStateError(f"{path}: document must be a mapping")
    return data


def _err(errors: list[str], message: str) -> None:
    errors.append(message)


def _entry_err(errors: list[str], section: str, index: int, entry_id: str, message: str) -> None:
    slot = entry_id if entry_id else "?"
    errors.append(f"{section}[{index}] {slot}: {message}")


def _is_http_url(value: object) -> bool:
    return isinstance(value, str) and value.startswith(_HTTP_PREFIXES)


def _is_non_bool_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_bool_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _unknown_keys(obj: dict, allowed: set[str], errors: list[str], prefix: str) -> None:
    for key in obj:
        if key not in allowed:
            _err(errors, f"{prefix}unknown key '{key}'")


def _check_hidden(hidden: object, errors: list[str], prefix: str) -> None:
    if not isinstance(hidden, dict):
        _err(errors, f"{prefix}hidden must be a mapping")
        return
    allowed = {"reason", "at", "note"}
    _unknown_keys(hidden, allowed, errors, prefix)
    reason = hidden.get("reason")
    if "reason" not in hidden:
        _err(errors, f"{prefix}missing reason")
    elif reason not in HIDDEN_REASONS:
        _err(errors, f"{prefix}invalid hidden reason {reason!r}")
    at = hidden.get("at")
    if "at" not in hidden:
        _err(errors, f"{prefix}missing at")
    elif not isinstance(at, str) or not TIMESTAMP_RE.match(at):
        _err(errors, f"{prefix}invalid timestamp")
    if "note" in hidden and not isinstance(hidden["note"], str):
        _err(errors, f"{prefix}note must be a string")


def _check_confidence(value: object, errors: list[str], prefix: str) -> None:
    if not _is_non_bool_number(value) or not (0 <= float(value) <= 1):
        _err(errors, f"{prefix}confidence must be between 0 and 1")


def _check_post_url(value: object, errors: list[str], section: str, index: int, entry_id: str) -> None:
    if value is None or value == "":
        _entry_err(errors, section, index, entry_id, "missing post_url")
        return
    if not _is_http_url(value):
        _entry_err(errors, section, index, entry_id, "post_url must start with http:// or https://")


def validate(state: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(state, dict):
        return ["state: document must be a mapping"]

    required = ("schema_version", "discussion", "options", "claims", "positions", "questions")
    allowed = set(required) | {"snapshots"}
    for key in state:
        if key not in allowed:
            _err(errors, f"state: unknown key '{key}'")
    for key in required:
        if key not in state:
            _err(errors, f"state: missing {key}")

    if state.get("schema_version") != SCHEMA_VERSION:
        _err(errors, f"state: schema_version must be {SCHEMA_VERSION!r}")

    discussion = state.get("discussion")
    if "discussion" in state and not isinstance(discussion, dict):
        _err(errors, "state: discussion must be a mapping")
        discussion = None

    for name in ("options", "claims", "positions", "questions"):
        if name in state and not isinstance(state.get(name), list):
            _err(errors, f"state: {name} must be a list")

    snapshots = state.get("snapshots")
    if "snapshots" in state and not isinstance(snapshots, list):
        _err(errors, "state: snapshots must be a list")
        snapshots = None

    if isinstance(discussion, dict):
        _validate_discussion(discussion, errors)

    options = state.get("options") if isinstance(state.get("options"), list) else []
    claims = state.get("claims") if isinstance(state.get("claims"), list) else []
    positions = state.get("positions") if isinstance(state.get("positions"), list) else []
    questions = state.get("questions") if isinstance(state.get("questions"), list) else []

    option_ids: set[str] = set()
    claim_ids = _collect_entry_ids(claims, _ID_PATTERNS["claims"])
    positions_by_id = _collect_entries_by_id(positions, _ID_PATTERNS["positions"])
    question_ids = _collect_entry_ids(questions, _ID_PATTERNS["questions"])

    seen_option: set[str] = set()
    for i, option in enumerate(options):
        oid = _validate_option(option, i, errors, seen_option)
        if oid:
            option_ids.add(oid)

    seen_claim: set[str] = set()
    for i, claim in enumerate(claims):
        _validate_claim_shape(claim, i, errors, seen_claim, option_ids, question_ids)
        if isinstance(claim, dict):
            _validate_answered_by(claim, i, errors, claim_ids)

    seen_position: set[str] = set()
    for i, position in enumerate(positions):
        _validate_position_shape(position, i, errors, seen_position, option_ids)
        if isinstance(position, dict):
            _validate_supersedes(position, i, errors, positions_by_id)

    seen_question: set[str] = set()
    for i, question in enumerate(questions):
        _validate_question_shape(question, i, errors, seen_question)
        if isinstance(question, dict):
            _validate_resolved_by(question, i, errors, claim_ids)

    if isinstance(snapshots, list):
        for i, snap in enumerate(snapshots):
            _validate_snapshot(snap, i, errors)

    return errors


def _collect_entry_ids(entries: list, pattern: re.Pattern[str]) -> set[str]:
    ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        eid = entry.get("id")
        if isinstance(eid, str) and pattern.match(eid):
            ids.add(eid)
    return ids


def _collect_entries_by_id(entries: list, pattern: re.Pattern[str]) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        eid = entry.get("id")
        if isinstance(eid, str) and pattern.match(eid):
            by_id[eid] = entry
    return by_id


def _validate_discussion(discussion: dict, errors: list[str]) -> None:
    allowed = {"id", "title", "sources", "topic", "updated_at"}
    _unknown_keys(discussion, allowed, errors, "discussion: ")
    disc_id = discussion.get("id")
    if "id" not in discussion:
        _err(errors, "discussion: missing id")
    elif not isinstance(disc_id, str) or not SLUG_RE.match(disc_id):
        _err(errors, "discussion: invalid id")
    title = discussion.get("title")
    if "title" not in discussion:
        _err(errors, "discussion: missing title")
    elif not isinstance(title, str) or not title:
        _err(errors, "discussion: title must be a non-empty string")
    if "topic" in discussion and not isinstance(discussion["topic"], str):
        _err(errors, "discussion: topic must be a string")
    if "updated_at" in discussion:
        updated = discussion["updated_at"]
        if not isinstance(updated, str) or not TIMESTAMP_RE.match(updated):
            _err(errors, "discussion: invalid updated_at")
    sources = discussion.get("sources")
    if "sources" not in discussion:
        _err(errors, "discussion: missing sources")
        return
    if not isinstance(sources, list) or not sources:
        _err(errors, "discussion: sources must be a non-empty list")
        return
    seen_urls: set[str] = set()
    for i, source in enumerate(sources):
        prefix = f"discussion.sources[{i}]: "
        if not isinstance(source, dict):
            _err(errors, f"{prefix}must be a mapping")
            continue
        _unknown_keys(source, {"platform", "url", "cursor"}, errors, prefix)
        platform = source.get("platform")
        if "platform" not in source:
            _err(errors, f"{prefix}missing platform")
        elif platform not in PLATFORMS:
            _err(errors, f"{prefix}invalid platform {platform!r}")
        url = source.get("url")
        if "url" not in source:
            _err(errors, f"{prefix}missing url")
        elif not _is_http_url(url):
            _err(errors, f"{prefix}url must start with http:// or https://")
        else:
            if url in seen_urls:
                _err(errors, f"{prefix}duplicate url")
            seen_urls.add(url)
        if "cursor" not in source:
            _err(errors, f"{prefix}missing cursor")
        else:
            cursor = source["cursor"]
            if cursor is not None and (not _is_non_bool_int(cursor) or cursor < 0):
                _err(errors, f"{prefix}cursor must be an integer >= 0 or null")


def _validate_option(option: object, index: int, errors: list[str], seen: set[str]) -> str | None:
    if not isinstance(option, dict):
        _entry_err(errors, "options", index, "?", "must be a mapping")
        return None
    oid = option.get("id") if isinstance(option.get("id"), str) else ""
    slot = oid or "?"
    _unknown_keys(option, {"id", "name", "kind", "gloss", "hidden"}, errors, f"options[{index}] {slot}: ")
    if "id" not in option:
        _entry_err(errors, "options", index, slot, "missing id")
    elif not isinstance(option["id"], str) or not SLUG_RE.match(option["id"]):
        _entry_err(errors, "options", index, slot, "invalid id")
    elif option["id"] in seen:
        _entry_err(errors, "options", index, slot, "duplicate id")
    else:
        seen.add(option["id"])
        oid = option["id"]
        slot = oid
    name = option.get("name")
    if "name" not in option:
        _entry_err(errors, "options", index, slot, "missing name")
    elif not isinstance(name, str) or not name:
        _entry_err(errors, "options", index, slot, "name must be a non-empty string")
    kind = option.get("kind")
    if "kind" not in option:
        _entry_err(errors, "options", index, slot, "missing kind")
    elif kind not in OPTION_KINDS:
        _entry_err(errors, "options", index, slot, f"invalid kind {kind!r}")
    if "gloss" in option and not isinstance(option["gloss"], str):
        _entry_err(errors, "options", index, slot, "gloss must be a string")
    if "hidden" in option:
        _check_hidden(option["hidden"], errors, f"options[{index}] {slot}: ")
    return oid if oid and SLUG_RE.match(oid) else None


def _validate_claim_shape(
    claim: object,
    index: int,
    errors: list[str],
    seen: set[str],
    option_ids: set[str],
    question_ids: set[str],
) -> str | None:
    if not isinstance(claim, dict):
        _entry_err(errors, "claims", index, "?", "must be a mapping")
        return None
    cid = claim.get("id") if isinstance(claim.get("id"), str) else ""
    slot = cid or "?"
    allowed = {
        "id",
        "hash",
        "polarity",
        "target",
        "participant",
        "post_url",
        "quote",
        "date",
        "status",
        "text",
        "answered_by",
        "confidence",
        "concede_confidence",
        "hidden",
    }
    _unknown_keys(claim, allowed, errors, f"claims[{index}] {slot}: ")
    if "id" not in claim:
        _entry_err(errors, "claims", index, slot, "missing id")
    elif not isinstance(claim["id"], str) or not _ID_PATTERNS["claims"].match(claim["id"]):
        _entry_err(errors, "claims", index, slot, "invalid id")
    elif claim["id"] in seen:
        _entry_err(errors, "claims", index, slot, "duplicate id")
    else:
        seen.add(claim["id"])
        cid = claim["id"]
        slot = cid

    for field in ("hash", "polarity", "target", "participant", "quote", "date", "status"):
        if field not in claim:
            _entry_err(errors, "claims", index, slot, f"missing {field}")

    participant = claim.get("participant")
    if "participant" in claim and (not isinstance(participant, str) or not participant):
        _entry_err(errors, "claims", index, slot, "participant must be a non-empty string")

    polarity = claim.get("polarity")
    if "polarity" in claim and polarity not in POLARITIES:
        _entry_err(errors, "claims", index, slot, f"invalid polarity {polarity!r}")

    status = claim.get("status")
    if "status" in claim and status not in CLAIM_STATUSES:
        _entry_err(errors, "claims", index, slot, f"invalid status {status!r}")

    _check_post_url(claim.get("post_url") if "post_url" in claim else None, errors, "claims", index, slot)

    quote = claim.get("quote")
    if "quote" in claim:
        if not isinstance(quote, str) or not quote:
            _entry_err(errors, "claims", index, slot, "quote must be a non-empty string")
        elif len(quote) > QUOTE_MAX:
            _entry_err(errors, "claims", index, slot, f"quote exceeds 200 characters ({len(quote)})")

    date = claim.get("date")
    if "date" in claim and (not isinstance(date, str) or not TIMESTAMP_RE.match(date)):
        _entry_err(errors, "claims", index, slot, "invalid date")

    digest = claim.get("hash")
    post_url = claim.get("post_url") if isinstance(claim.get("post_url"), str) else ""
    if "hash" in claim:
        if not isinstance(digest, str) or not HEX64_RE.match(digest):
            _entry_err(errors, "claims", index, slot, "hash does not match claim_hash(participant, post_url, quote)")
        elif isinstance(participant, str) and isinstance(quote, str) and post_url:
            expected = claim_hash(participant, post_url, quote)
            if digest != expected:
                _entry_err(
                    errors,
                    "claims",
                    index,
                    slot,
                    "hash does not match claim_hash(participant, post_url, quote)",
                )

    target = claim.get("target")
    if "target" in claim:
        if not isinstance(target, str) or not target:
            _entry_err(errors, "claims", index, slot, "missing target")
        elif target not in option_ids and target not in question_ids:
            _entry_err(errors, "claims", index, slot, f"unknown target '{target}'")

    if "text" in claim:
        text = claim["text"]
        if not isinstance(text, str):
            _entry_err(errors, "claims", index, slot, "text must be a string")
        elif len(text) > TEXT_MAX:
            _entry_err(errors, "claims", index, slot, f"text exceeds 280 characters ({len(text)})")

    if "confidence" in claim:
        _check_confidence(claim["confidence"], errors, f"claims[{index}] {slot}: ")
    if "concede_confidence" in claim:
        _check_confidence(
            claim["concede_confidence"], errors, f"claims[{index}] {slot}: concede "
        )
        if claim.get("status") != "conceded":
            errors.append(
                f"claims[{index}] {slot}: concede_confidence needs status conceded"
            )
    if "hidden" in claim:
        _check_hidden(claim["hidden"], errors, f"claims[{index}] {slot}: ")
    return cid if cid and _ID_PATTERNS["claims"].match(cid) else None


def _validate_answered_by(claim: dict, index: int, errors: list[str], claim_ids: set[str]) -> None:
    cid = claim.get("id") if isinstance(claim.get("id"), str) else "?"
    status = claim.get("status")
    if "answered_by" in claim:
        if status != "answered":
            _entry_err(errors, "claims", index, cid, f"answered_by set with status {status!r}")
        answered_by = claim["answered_by"]
        if not isinstance(answered_by, str) or answered_by not in claim_ids or answered_by == cid:
            _entry_err(errors, "claims", index, cid, f"unknown answered_by '{answered_by}'")
    elif status == "answered":
        _entry_err(errors, "claims", index, cid, "status 'answered' requires answered_by")


def _validate_position_shape(
    position: object,
    index: int,
    errors: list[str],
    seen: set[str],
    option_ids: set[str],
) -> str | None:
    if not isinstance(position, dict):
        _entry_err(errors, "positions", index, "?", "must be a mapping")
        return None
    pid = position.get("id") if isinstance(position.get("id"), str) else ""
    slot = pid or "?"
    allowed = {"id", "participant", "prefers", "basis", "post_url", "date", "supersedes", "confidence", "hidden"}
    _unknown_keys(position, allowed, errors, f"positions[{index}] {slot}: ")
    if "id" not in position:
        _entry_err(errors, "positions", index, slot, "missing id")
    elif not isinstance(position["id"], str) or not _ID_PATTERNS["positions"].match(position["id"]):
        _entry_err(errors, "positions", index, slot, "invalid id")
    elif position["id"] in seen:
        _entry_err(errors, "positions", index, slot, "duplicate id")
    else:
        seen.add(position["id"])
        pid = position["id"]
        slot = pid
    for field in ("participant", "prefers", "basis", "post_url", "date"):
        if field not in position:
            _entry_err(errors, "positions", index, slot, f"missing {field}")
    participant = position.get("participant")
    if "participant" in position and (not isinstance(participant, str) or not participant):
        _entry_err(errors, "positions", index, slot, "participant must be a non-empty string")
    prefers = position.get("prefers")
    if "prefers" in position:
        if not isinstance(prefers, list):
            _entry_err(errors, "positions", index, slot, "prefers must be a list")
        else:
            seen_pref: set[str] = set()
            for item in prefers:
                if item not in option_ids:
                    _entry_err(errors, "positions", index, slot, f"unknown prefers '{item}'")
                elif item in seen_pref:
                    _entry_err(errors, "positions", index, slot, f"duplicate prefers '{item}'")
                else:
                    seen_pref.add(item)
    basis = position.get("basis")
    if "basis" in position and basis not in BASES:
        _entry_err(errors, "positions", index, slot, f"invalid basis {basis!r}")
    _check_post_url(position.get("post_url") if "post_url" in position else None, errors, "positions", index, slot)
    date = position.get("date")
    if "date" in position and (not isinstance(date, str) or not TIMESTAMP_RE.match(date)):
        _entry_err(errors, "positions", index, slot, "invalid date")
    if "confidence" in position:
        _check_confidence(position["confidence"], errors, f"positions[{index}] {slot}: ")
    if "hidden" in position:
        _check_hidden(position["hidden"], errors, f"positions[{index}] {slot}: ")
    return pid if pid and _ID_PATTERNS["positions"].match(pid) else None


def _validate_supersedes(position: dict, index: int, errors: list[str], positions_by_id: dict[str, dict]) -> None:
    if "supersedes" not in position:
        return
    pid = position.get("id") if isinstance(position.get("id"), str) else "?"
    target = position["supersedes"]
    if not isinstance(target, str) or target not in positions_by_id or target == pid:
        _entry_err(errors, "positions", index, pid, f"unknown supersedes '{target}'")
        return
    other = positions_by_id[target]
    if other.get("participant") != position.get("participant"):
        _entry_err(errors, "positions", index, pid, f"supersedes {target} belongs to another participant")
        return
    visited: set[str] = set()
    current: str | None = pid if isinstance(pid, str) else None
    while current:
        if current in visited:
            _entry_err(errors, "positions", index, pid, "supersedes cycle")
            return
        visited.add(current)
        row = positions_by_id.get(current)
        if not row:
            return
        nxt = row.get("supersedes")
        current = nxt if isinstance(nxt, str) else None


def _validate_question_shape(question: object, index: int, errors: list[str], seen: set[str]) -> str | None:
    if not isinstance(question, dict):
        _entry_err(errors, "questions", index, "?", "must be a mapping")
        return None
    qid = question.get("id") if isinstance(question.get("id"), str) else ""
    slot = qid or "?"
    allowed = {"id", "text", "raised_by", "post_url", "date", "status", "resolved_by", "hidden"}
    _unknown_keys(question, allowed, errors, f"questions[{index}] {slot}: ")
    if "id" not in question:
        _entry_err(errors, "questions", index, slot, "missing id")
    elif not isinstance(question["id"], str) or not _ID_PATTERNS["questions"].match(question["id"]):
        _entry_err(errors, "questions", index, slot, "invalid id")
    elif question["id"] in seen:
        _entry_err(errors, "questions", index, slot, "duplicate id")
    else:
        seen.add(question["id"])
        qid = question["id"]
        slot = qid
    for field in ("text", "raised_by", "post_url", "date", "status"):
        if field not in question:
            _entry_err(errors, "questions", index, slot, f"missing {field}")
    text = question.get("text")
    if "text" in question and (not isinstance(text, str) or not text):
        _entry_err(errors, "questions", index, slot, "text must be a non-empty string")
    raised_by = question.get("raised_by")
    if "raised_by" in question and (not isinstance(raised_by, str) or not raised_by):
        _entry_err(errors, "questions", index, slot, "raised_by must be a non-empty string")
    _check_post_url(question.get("post_url") if "post_url" in question else None, errors, "questions", index, slot)
    date = question.get("date")
    if "date" in question and (not isinstance(date, str) or not TIMESTAMP_RE.match(date)):
        _entry_err(errors, "questions", index, slot, "invalid date")
    status = question.get("status")
    if "status" in question and status not in QUESTION_STATUSES:
        _entry_err(errors, "questions", index, slot, f"invalid status {status!r}")
    if "hidden" in question:
        _check_hidden(question["hidden"], errors, f"questions[{index}] {slot}: ")
    return qid if qid and _ID_PATTERNS["questions"].match(qid) else None


def _validate_resolved_by(question: dict, index: int, errors: list[str], claim_ids: set[str]) -> None:
    qid = question.get("id") if isinstance(question.get("id"), str) else "?"
    status = question.get("status")
    if "resolved_by" in question:
        if status != "resolved":
            _entry_err(errors, "questions", index, qid, f"resolved_by set with status {status!r}")
        resolved_by = question["resolved_by"]
        if not isinstance(resolved_by, str) or resolved_by not in claim_ids:
            _entry_err(errors, "questions", index, qid, f"unknown resolved_by '{resolved_by}'")
    elif status == "resolved":
        _entry_err(errors, "questions", index, qid, "status 'resolved' requires resolved_by")


def _validate_snapshot(snap: object, index: int, errors: list[str]) -> None:
    prefix = f"snapshots[{index}] ?: "
    if not isinstance(snap, dict):
        _err(errors, f"{prefix}must be a mapping")
        return
    _unknown_keys(snap, {"at", "cursors", "counts"}, errors, prefix)
    at = snap.get("at")
    if "at" not in snap:
        _err(errors, f"{prefix}missing at")
    elif not isinstance(at, str) or not TIMESTAMP_RE.match(at):
        _err(errors, f"{prefix}invalid at")
    cursors = snap.get("cursors")
    if "cursors" not in snap:
        _err(errors, f"{prefix}missing cursors")
    elif not isinstance(cursors, dict):
        _err(errors, f"{prefix}cursors must be a mapping")
    else:
        for url, cursor in cursors.items():
            if cursor is not None and (not _is_non_bool_int(cursor) or cursor < 0):
                _err(errors, f"{prefix}invalid cursor for {url}")
    counts = snap.get("counts")
    expected = {"claims", "positions", "questions", "hidden"}
    if "counts" not in snap:
        _err(errors, f"{prefix}missing counts")
    elif not isinstance(counts, dict):
        _err(errors, f"{prefix}counts must be a mapping")
    else:
        if set(counts.keys()) != expected:
            _err(errors, f"{prefix}counts must include claims, positions, questions, hidden")
        for key, value in counts.items():
            if not _is_non_bool_int(value) or value < 0:
                _err(errors, f"{prefix}invalid count for {key}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate discussion state JSON documents.")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for path in args.paths:
        try:
            state = load(path)
        except DiscussionStateError as exc:
            print(str(exc), file=sys.stderr)
            failed = True
            continue
        issues = validate(state)
        if issues:
            failed = True
            for issue in issues:
                print(issue, file=sys.stderr)
            continue
        claims = state.get("claims") if isinstance(state.get("claims"), list) else []
        positions = state.get("positions") if isinstance(state.get("positions"), list) else []
        questions = state.get("questions") if isinstance(state.get("questions"), list) else []
        print(f"ok {path} ({len(claims)} claims, {len(positions)} positions, {len(questions)} questions)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
