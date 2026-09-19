#!/usr/bin/env python3
"""Typed judge primitives over a short state dict, with JSON model backends."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, Mapping, NamedTuple, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

BACKENDS = ("anthropic", "jev", "ollama", "openai-compatible")
PRIMITIVES = ("choose", "score", "truth")
STATE_VALUE_MAX = 1000
STATE_MAX_BYTES = 8000
DEFAULT_TIMEOUT = 180.0
DEFAULT_MAX_TOKENS = 512
QUESTIONS_PATH = Path(__file__).resolve().parents[1] / "schema" / "judge-questions.yaml"
CompleteFn = Callable[[list[dict]], str]

CHOOSE_SHAPE = {"choice": "<label>", "probabilities": {"<label>": 0.0}}
TRUTH_SHAPE = {"probability": 0.0}
SCORE_SHAPE = {"level": 0, "probabilities": {"0": 0.0}}

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PREAMBLE = (
    "You are a typed judge for a technical discussion tracker.\n"
    "Answer with JSON only. No prose, no code fences.\n"
    "Judge only the text given. Do not use outside knowledge about the participants."
)
_DEFAULT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "ollama": "http://localhost:11434",
    "openai-compatible": "http://localhost:8080/v1",
}
_DEFAULT_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai-compatible": "MAPLE_API_KEY",
}


class JudgeError(Exception):
    """Raised when a judge backend is misconfigured or returns an answer of the wrong type."""


class Verdict(NamedTuple):
    value: str | float
    probabilities: dict[str, float]
    confidence: float


class Question(NamedTuple):
    id: str
    primitive: str
    inputs: tuple[str, ...]
    instructions: str
    labels: tuple[str, ...]
    label_source: str | None
    extra_labels: tuple[str, ...]
    levels: tuple[str, ...]
    statement: str


def _is_non_bool_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _expected_backends() -> str:
    return ", ".join(BACKENDS)


def normalize_state(state: dict) -> dict[str, str]:
    if not isinstance(state, dict):
        raise JudgeError("state must be a dict of short strings")
    out: dict[str, str] = {}
    for key, value in state.items():
        if not isinstance(key, str):
            raise JudgeError(f"state key {key!r} must be a string")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise JudgeError(f"state value for '{key}' must be a string or a number")
        if isinstance(value, str):
            out[key] = value[:STATE_VALUE_MAX]
        else:
            out[key] = str(value)
    rendered = render_state(out)
    n = len(rendered.encode("utf-8"))
    if n > STATE_MAX_BYTES:
        raise JudgeError(f"state is {n} bytes, over the {STATE_MAX_BYTES} byte cap")
    return out


def render_state(state: dict[str, str]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in state.items())


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


def parse_json_object(text: str) -> dict:
    try:
        data = json.loads(strip_fences(text))
    except json.JSONDecodeError as exc:
        raise JudgeError(f"judge reply is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise JudgeError("judge reply must be a JSON object")
    return data


def _probabilities(raw: object, labels: Sequence[str]) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise JudgeError("probabilities must be a JSON object")
    allowed = set(labels)
    collected: dict[str, float] = {}
    for key, value in raw.items():
        if key not in allowed:
            raise JudgeError(f"probability for unknown label '{key}'")
        if not _is_non_bool_number(value) or not (0 <= float(value) <= 1):
            raise JudgeError(f"probability for '{key}' must be between 0 and 1")
        collected[str(key)] = float(value)
    probs = {label: collected.get(label, 0.0) for label in labels}
    total = sum(probs.values())
    if total <= 0:
        raise JudgeError("probabilities sum to zero")
    return {label: round(p / total, 6) for label, p in probs.items()}


def _score_from_probabilities(probs: dict[str, float]) -> float:
    return round(sum(int(index) * p for index, p in probs.items()), 6)


class Judge:
    name: str = ""
    model: str | None = None

    def choose(self, state: dict, options: Sequence[str], *, instructions: str = "") -> Verdict:
        raise NotImplementedError

    def truth(self, state: dict, statement: str, *, instructions: str = "") -> float:
        raise NotImplementedError

    def score(self, state: dict, rubric: Sequence[str], *, instructions: str = "") -> Verdict:
        raise NotImplementedError

    def ask(
        self,
        question: Question,
        state: dict,
        *,
        labels: Sequence[str] | None = None,
    ) -> Verdict | float:
        if question.label_source:
            if labels is None:
                raise JudgeError(
                    f"question '{question.id}' needs labels from {question.label_source}"
                )
            effective = list(labels) + list(question.extra_labels)
        else:
            effective = list(question.labels)
        seen: set[str] = set()
        for label in effective:
            if label in seen:
                raise JudgeError(f"duplicate label '{label}'")
            seen.add(label)
        if not isinstance(state, dict):
            raise JudgeError("state must be a dict of short strings")
        for key in question.inputs:
            if key not in state:
                raise JudgeError(f"question '{question.id}' needs state key '{key}'")
        if question.primitive == "choose":
            return self.choose(state, effective, instructions=question.instructions)
        if question.primitive == "truth":
            return self.truth(state, question.statement, instructions=question.instructions)
        if question.primitive == "score":
            return self.score(state, question.levels, instructions=question.instructions)
        raise JudgeError(f"unknown primitive '{question.primitive}'")


def _messages(system: str, state: dict) -> list[dict]:
    normalized = normalize_state(state)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": render_state(normalized)},
    ]


def _choose_system(instructions: str, labels: Sequence[str]) -> str:
    return "\n".join(
        [
            _PREAMBLE,
            f"Question: {instructions}",
            f"Allowed labels: {', '.join(labels)}",
            "Respond with this JSON shape:",
            json.dumps(CHOOSE_SHAPE),
            "probabilities must cover every allowed label and sum to 1.",
        ]
    )


def _truth_system(instructions: str, statement: str) -> str:
    lines = [_PREAMBLE]
    if instructions:
        lines.append(f"Question: {instructions}")
    lines.extend(
        [
            f"Statement: {statement}",
            "Answer the probability that the statement is true.",
            json.dumps(TRUTH_SHAPE),
        ]
    )
    return "\n".join(lines)


def _score_system(instructions: str, rubric: Sequence[str]) -> str:
    level_lines = [f"{i}: {text}" for i, text in enumerate(rubric)]
    return "\n".join(
        [
            _PREAMBLE,
            f"Question: {instructions}",
            "Levels, lowest first:",
            *level_lines,
            "Respond with this JSON shape:",
            json.dumps(SCORE_SHAPE),
            "probabilities are over the level indexes and sum to 1.",
        ]
    )


class JsonModelJudge(Judge):
    def __init__(
        self,
        complete: CompleteFn,
        *,
        name: str = "json-model",
        model: str | None = None,
    ) -> None:
        self.complete = complete
        self.name = name
        self.model = model

    def choose(self, state: dict, options: Sequence[str], *, instructions: str = "") -> Verdict:
        labels = list(options)
        if len(labels) < 2:
            raise JudgeError("choose needs at least two options")
        data = parse_json_object(
            self.complete(_messages(_choose_system(instructions, labels), state))
        )
        choice = data.get("choice")
        if not isinstance(choice, str) or choice not in labels:
            raise JudgeError(f"choice '{choice}' is not one of: {', '.join(labels)}")
        if "probabilities" not in data:
            raise JudgeError("judge reply is missing probabilities")
        probs = _probabilities(data["probabilities"], labels)
        return Verdict(choice, probs, probs[choice])

    def truth(self, state: dict, statement: str, *, instructions: str = "") -> float:
        if not statement:
            raise JudgeError("truth needs a statement")
        data = parse_json_object(
            self.complete(_messages(_truth_system(instructions, statement), state))
        )
        value = data.get("probability")
        if not _is_non_bool_number(value) or not (0 <= float(value) <= 1):
            raise JudgeError("probability must be a number between 0 and 1")
        return float(value)

    def score(self, state: dict, rubric: Sequence[str], *, instructions: str = "") -> Verdict:
        levels = list(rubric)
        if len(levels) < 2:
            raise JudgeError("score needs at least two rubric levels")
        labels = [str(i) for i in range(len(levels))]
        data = parse_json_object(
            self.complete(_messages(_score_system(instructions, levels), state))
        )
        if "level" in data:
            level = data["level"]
            n = len(levels)
            if not isinstance(level, int) or isinstance(level, bool) or not (0 <= level < n):
                raise JudgeError(f"level {level} is outside 0..{n - 1}")
        if "probabilities" not in data:
            raise JudgeError("judge reply is missing probabilities")
        probs = _probabilities(data["probabilities"], labels)
        return Verdict(_score_from_probabilities(probs), probs, max(probs.values()))


def _http_json(url: str, payload: dict, headers: dict[str, str], timeout: float, name: str) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except (URLError, TimeoutError) as exc:
        raise JudgeError(f"{name} backend request failed: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"{name} backend request failed: {exc}") from exc
    if not isinstance(data, dict):
        raise JudgeError(f"{name} backend response must be a JSON object")
    return data


def openai_complete(*, base_url: str, model: str, api_key: str, timeout: float) -> CompleteFn:
    url = base_url.rstrip("/") + "/chat/completions"

    def complete(messages: list[dict]) -> str:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        data = _http_json(
            url,
            {"model": model, "temperature": 0, "messages": messages},
            headers,
            timeout,
            "openai-compatible",
        )
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise JudgeError(
                "openai-compatible backend response is missing choices[0].message.content"
            ) from exc
        if not isinstance(content, str):
            raise JudgeError(
                "openai-compatible backend response is missing choices[0].message.content"
            )
        return content

    return complete


def ollama_complete(*, base_url: str, model: str, timeout: float) -> CompleteFn:
    url = base_url.rstrip("/") + "/api/chat"

    def complete(messages: list[dict]) -> str:
        data = _http_json(
            url,
            {
                "model": model,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
                "messages": messages,
            },
            {"Content-Type": "application/json"},
            timeout,
            "ollama",
        )
        try:
            content = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise JudgeError("ollama backend response is missing message.content") from exc
        if not isinstance(content, str):
            raise JudgeError("ollama backend response is missing message.content")
        return content

    return complete


def anthropic_complete(
    *,
    base_url: str,
    model: str,
    api_key: str,
    timeout: float,
    max_tokens: int,
) -> CompleteFn:
    # unverified: no ANTHROPIC_API_KEY on this machine, shape is from the published Messages API
    url = base_url.rstrip("/") + "/v1/messages"

    def complete(messages: list[dict]) -> str:
        system_parts: list[str] = []
        user_parts: list[str] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if role == "system":
                system_parts.append(content)
            elif role == "user":
                user_parts.append(content)
        data = _http_json(
            url,
            {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": 0,
                "system": "\n\n".join(system_parts),
                "messages": [{"role": "user", "content": "\n\n".join(user_parts)}],
            },
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout,
            "anthropic",
        )
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise JudgeError("anthropic backend response is missing content text")
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    return text
        raise JudgeError("anthropic backend response is missing content text")

    return complete


def _question_err(index: int, qid: str, message: str) -> JudgeError:
    slot = qid if qid else "?"
    return JudgeError(f"judge-questions[{index}] {slot}: {message}")


def load_questions(path: Path | str = QUESTIONS_PATH) -> dict[str, Question]:
    import yaml  # type: ignore

    data = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise JudgeError("judge-questions must be a mapping with a questions list")
    loaded: dict[str, Question] = {}
    for index, item in enumerate(data["questions"]):
        if not isinstance(item, dict):
            raise _question_err(index, "?", "id must be a lower snake case slug")
        qid = item.get("id")
        prefix_id = qid if isinstance(qid, str) else ""
        if not isinstance(qid, str) or not _SLUG_RE.match(qid):
            raise _question_err(index, prefix_id, "id must be a lower snake case slug")
        if qid in loaded:
            raise _question_err(index, qid, f"duplicate question id '{qid}'")
        primitive = item.get("primitive")
        if primitive not in PRIMITIVES:
            raise _question_err(index, qid, f"unknown primitive '{primitive}'")
        inputs = item.get("inputs")
        if (
            not isinstance(inputs, list)
            or not inputs
            or not all(isinstance(key, str) for key in inputs)
        ):
            raise _question_err(index, qid, "inputs must be a non-empty list of state keys")
        instructions = item.get("instructions")
        if not isinstance(instructions, str) or instructions == "":
            raise _question_err(index, qid, "instructions must be a non-empty string")
        labels_raw = item.get("labels")
        label_source_raw = item.get("label_source")
        extra_raw = item.get("extra_labels")
        levels_raw = item.get("levels")
        statement_raw = item.get("statement")
        has_labels = (
            isinstance(labels_raw, list)
            and len(labels_raw) >= 2
            and all(isinstance(label, str) for label in labels_raw)
        )
        has_source = isinstance(label_source_raw, str) and label_source_raw != ""
        labels: tuple[str, ...] = ()
        label_source: str | None = None
        extra_labels: tuple[str, ...] = ()
        levels: tuple[str, ...] = ()
        statement = statement_raw if isinstance(statement_raw, str) else ""
        if extra_raw is not None and primitive != "choose":
            raise _question_err(index, qid, "extra_labels only applies to choose")
        if primitive == "choose":
            if has_labels and has_source:
                raise _question_err(
                    index, qid, "choose takes labels or a label_source, not both"
                )
            if not has_labels and not has_source:
                raise _question_err(index, qid, "choose needs labels or a label_source")
            if has_labels:
                labels = tuple(labels_raw)
            else:
                label_source = label_source_raw
            if extra_raw is None:
                extra_labels = ()
            elif isinstance(extra_raw, list) and all(isinstance(label, str) for label in extra_raw):
                extra_labels = tuple(extra_raw)
            else:
                raise _question_err(index, qid, "choose needs labels or a label_source")
        elif primitive == "truth":
            if not statement:
                raise _question_err(index, qid, "truth needs a statement")
        else:
            if (
                not isinstance(levels_raw, list)
                or len(levels_raw) < 2
                or not all(isinstance(level, str) for level in levels_raw)
            ):
                raise _question_err(index, qid, "score needs at least two levels")
            levels = tuple(levels_raw)
        loaded[qid] = Question(
            id=qid,
            primitive=primitive,
            inputs=tuple(inputs),
            instructions=instructions,
            labels=labels,
            label_source=label_source,
            extra_labels=extra_labels,
            levels=levels,
            statement=statement,
        )
    return loaded


def _env_timeout(env: Mapping[str, str]) -> float:
    raw = env.get("DISCUSSION_JUDGE_TIMEOUT")
    if raw is None or raw == "":
        return DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError as exc:
        raise JudgeError("DISCUSSION_JUDGE_TIMEOUT must be a number") from exc


def _env_max_tokens(env: Mapping[str, str]) -> int:
    raw = env.get("DISCUSSION_JUDGE_MAX_TOKENS")
    if raw is None or raw == "":
        return DEFAULT_MAX_TOKENS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_MAX_TOKENS


def judge_from_env(env: Mapping[str, str] | None = None) -> Judge:
    if env is None:
        env = os.environ
    backend = (env.get("DISCUSSION_JUDGE") or "").strip()
    if not backend:
        raise JudgeError(
            f"DISCUSSION_JUDGE is not set (expected one of: {_expected_backends()})"
        )
    if backend not in ("anthropic", "ollama", "openai-compatible"):
        raise JudgeError(
            f"unknown DISCUSSION_JUDGE backend '{backend}' (expected one of: {_expected_backends()})"
        )
    model = (env.get("DISCUSSION_JUDGE_MODEL") or "").strip()
    if not model:
        raise JudgeError(f"DISCUSSION_JUDGE_MODEL is not set for backend '{backend}'")
    base_url = (env.get("DISCUSSION_JUDGE_BASE_URL") or _DEFAULT_BASE_URLS[backend]).strip()
    timeout = _env_timeout(env)
    key_env_name = (env.get("DISCUSSION_JUDGE_API_KEY_ENV") or _DEFAULT_KEY_ENV.get(backend, "")).strip()
    api_key = env.get(key_env_name, "") if key_env_name else ""
    if backend == "openai-compatible":
        complete = openai_complete(
            base_url=base_url, model=model, api_key=api_key, timeout=timeout
        )
    elif backend == "ollama":
        complete = ollama_complete(base_url=base_url, model=model, timeout=timeout)
    else:
        complete = anthropic_complete(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout=timeout,
            max_tokens=_env_max_tokens(env),
        )
    return JsonModelJudge(complete, name=backend, model=model)


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise JudgeError(f"invalid JSONL in {path}: {exc}") from exc
    return rows


def _option_name(options: list[dict], target: object) -> str:
    raw = str(target) if target is not None else ""
    for option in options:
        if isinstance(option, dict) and option.get("id") == target:
            name = option.get("name")
            return name if isinstance(name, str) and name else raw
    return raw


def _visible_claims(state: dict) -> list[dict]:
    claims = state.get("claims")
    if not isinstance(claims, list):
        return []
    return [claim for claim in claims if isinstance(claim, dict) and "hidden" not in claim]


def _build_smoke_state(post: dict, posts: list[dict], state: dict, claim: dict) -> tuple[dict, list[str], list[str]]:
    options = state.get("options") if isinstance(state.get("options"), list) else []
    option_rows = [row for row in options if isinstance(row, dict)]
    option_ids = [row["id"] for row in option_rows if isinstance(row.get("id"), str)]
    option_lines = [
        f"{row.get('id', '')} | {row.get('name', '')} | {row.get('gloss', '')}"
        for row in option_rows
    ]
    url_to_number = {
        row.get("url"): row.get("post_number")
        for row in posts
        if isinstance(row.get("url"), str)
    }
    current_n = post.get("post_number")
    prior: list[tuple[int, dict]] = []
    for row in _visible_claims(state):
        number = url_to_number.get(row.get("post_url"))
        if isinstance(number, int) and isinstance(current_n, int) and number < current_n:
            prior.append((number, row))
    prior.sort(key=lambda item: item[0], reverse=True)
    nearest = prior[:5]
    candidate_ids = [row["id"] for _, row in nearest if isinstance(row.get("id"), str)]
    candidate_lines = [
        f"{row.get('id', '')}: {row.get('quote', '')}" for _, row in nearest
    ]
    reply_to = post.get("reply_to_post_number")
    authors = {row.get("author") for row in posts if isinstance(row.get("author"), str)}
    discussion = state.get("discussion") if isinstance(state.get("discussion"), dict) else {}
    smoke = {
        "title": discussion.get("title", ""),
        "participant": post.get("author", ""),
        "date": post.get("created_at", ""),
        "target": _option_name(option_rows, claim.get("target")),
        "quote": claim.get("quote", ""),
        "claim_quote": claim.get("quote", ""),
        "reply_to": "none" if reply_to is None or reply_to == "" else str(reply_to),
        "options": "\n".join(option_lines),
        "candidates": "\n".join(candidate_lines),
        "participants": str(len(authors)),
        "posts_count": str(len(posts)),
        "post_excerpt": post.get("body_text", "") if isinstance(post.get("body_text"), str) else "",
    }
    return smoke, option_ids, candidate_ids


def _format_answer(question: Question, result: Verdict | float) -> str:
    if question.primitive == "truth":
        return f"{question.id} {question.primitive} -> {result}"
    assert isinstance(result, Verdict)
    return (
        f"{question.id} {question.primitive} -> {result.value} "
        f"(confidence {result.confidence})"
    )


def _answer_record(question: Question, result: Verdict | float) -> dict:
    if question.primitive == "truth":
        return {"primitive": question.primitive, "value": result}
    assert isinstance(result, Verdict)
    return {
        "primitive": question.primitive,
        "value": result.value,
        "probabilities": result.probabilities,
        "confidence": result.confidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Answer catalogue judge questions for one discussion post."
    )
    parser.add_argument("--posts", required=True, type=Path)
    parser.add_argument("--post-number", required=True, type=int)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    parser.add_argument("--question", action="append", default=[])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        posts = _load_jsonl(args.posts)
        try:
            state = json.loads(args.state.read_text())
        except json.JSONDecodeError as exc:
            raise JudgeError(f"invalid state JSON in {args.state}: {exc}") from exc
        if not isinstance(state, dict):
            raise JudgeError("state must be a JSON object")
        post = next((row for row in posts if row.get("post_number") == args.post_number), None)
        if post is None:
            raise JudgeError(f"post {args.post_number} is not in {args.posts}")
        claim = next(
            (
                row
                for row in _visible_claims(state)
                if row.get("post_url") == post.get("url")
            ),
            None,
        )
        if claim is None:
            raise JudgeError(
                f"state has no visible claim on post {args.post_number}, pick another post"
            )
        smoke, option_ids, candidate_ids = _build_smoke_state(post, posts, state, claim)
        questions = load_questions(args.questions)
        selected = list(questions.values())
        if args.question:
            wanted = set(args.question)
            selected = [question for question in selected if question.id in wanted]
        judge = judge_from_env()
        answers: dict[str, dict] = {}
        errors: dict[str, str] = {}
        for question in selected:
            labels: Sequence[str] | None = None
            if question.label_source == "options":
                labels = option_ids
            elif question.label_source == "candidates":
                if len(candidate_ids) < 2:
                    print(
                        f"{question.id} {question.primitive} -> skipped: fewer than two candidate claims"
                    )
                    continue
                labels = candidate_ids
            try:
                result = judge.ask(question, smoke, labels=labels)
            except JudgeError as exc:
                print(f"{question.id} {question.primitive} -> error: {exc}")
                errors[question.id] = str(exc)
                continue
            print(_format_answer(question, result))
            answers[question.id] = _answer_record(question, result)
        if args.out is not None:
            payload = {
                "backend": judge.name,
                "model": judge.model,
                "post_number": args.post_number,
                "post_url": post.get("url"),
                "answers": answers,
                "errors": errors,
            }
            args.out.write_text(json.dumps(payload, indent=2) + "\n")
        return 1 if errors else 0
    except JudgeError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
