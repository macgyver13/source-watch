#!/usr/bin/env python3
"""Tests for per-post discussion drafting coercion and parse handling."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import discussion_state as ds
import draft_discussion as dd

CATALOG = {
    "schema_version": ds.SCHEMA_VERSION,
    "discussion": {
        "id": "draft-test",
        "title": "Draft test",
        "sources": [{"platform": "discourse", "url": "https://forum.example/t/1", "cursor": 2}],
        "updated_at": "2026-03-02T09:15:00Z",
    },
    "options": [
        {"id": "format-a", "name": "Format A", "kind": "option", "gloss": "Compact format"},
        {"id": "format-b", "name": "Format B", "kind": "option", "gloss": "Nested format"},
    ],
    "claims": [],
    "positions": [],
    "questions": [],
}

BODY = ("Format A keeps the registry small for constrained clients. " * 8).strip()
POST = {
    "post_number": 1,
    "author": "ada",
    "author_name": "Ada",
    "created_at": "2026-03-02T09:15:00Z",
    "updated_at": "2026-03-02T09:15:00Z",
    "url": "https://forum.example/t/1/1",
    "body_text": BODY,
    "content_hash": "0" * 64,
    "reply_to_post_number": None,
}
POST2 = {
    **POST,
    "post_number": 2,
    "author": "brin",
    "url": "https://forum.example/t/1/2",
    "created_at": "2026-03-02T11:40:00Z",
    "body_text": "Format B needs a new parser.",
}


def _payload(claims, position=None, questions=None) -> str:
    return json.dumps({"claims": claims, "position": position, "questions": questions or []})


class ScriptedComplete:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def __call__(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("unexpected extra model call")
        return self.responses.pop(0)


class DraftDiscussionTests(unittest.TestCase):
    def test_over_long_quote_is_truncated_and_claim_kept(self) -> None:
        quote = BODY[:260]
        self.assertGreater(len(quote), 200)
        complete = ScriptedComplete(
            [_payload([{"polarity": "benefit", "target": "format-a", "quote": quote, "text": "keeps registry small"}])]
        )
        state, log = dd.draft([POST], CATALOG, complete, model="fake")
        self.assertEqual(log["parse_failures"], [])
        self.assertEqual(len(state["claims"]), 1)
        claim = state["claims"][0]
        truncated = quote[:200].rstrip()
        self.assertEqual(len(claim["quote"]), 200)
        self.assertEqual(claim["quote"], truncated)
        self.assertEqual(claim["hash"], ds.claim_hash("ada", POST["url"], truncated))

    def test_out_of_catalog_target_is_dropped(self) -> None:
        complete = ScriptedComplete(
            [
                _payload(
                    [
                        {
                            "polarity": "benefit",
                            "target": "not-an-option",
                            "quote": "Format A keeps the registry small",
                            "text": "bad target",
                        }
                    ]
                )
            ]
        )
        state, log = dd.draft([POST], CATALOG, complete, model="fake")
        self.assertEqual(state["claims"], [])
        self.assertEqual(log["target_out_of_catalog"], 1)

    def test_unparseable_response_records_a_parse_failure(self) -> None:
        complete = ScriptedComplete(
            [
                "this is not json",
                "still not json",
                _payload(
                    [
                        {
                            "polarity": "blocker",
                            "target": "format-b",
                            "quote": "Format B needs a new parser",
                            "text": "parser cost",
                        }
                    ]
                ),
            ]
        )
        state, log = dd.draft([POST, POST2], CATALOG, complete, model="fake")
        self.assertEqual(len(log["parse_failures"]), 1)
        self.assertEqual(log["parse_failures"][0]["post"], 1)
        self.assertEqual(log["posts_drafted"], 1)
        self.assertEqual(len(state["claims"]), 1)
        self.assertEqual(state["claims"][0]["participant"], "brin")

    def test_output_validates(self) -> None:
        complete = ScriptedComplete(
            [
                _payload(
                    [
                        {
                            "polarity": "Benefit",
                            "target": "format-a",
                            "quote": "Format A keeps the registry small",
                            "text": "small registry",
                        },
                        {
                            "polarity": "blocker",
                            "target": "not-an-option",
                            "quote": "Format A keeps the registry small",
                            "text": "dropped",
                        },
                    ],
                    position={"prefers": ["format-a", "not-an-option"], "basis": "stated", "confidence": 0.8},
                    questions=[{"text": "Does Format A support nested groups?"}],
                )
            ]
        )
        state, log = dd.draft([POST], CATALOG, complete, model="fake")
        self.assertEqual(ds.validate(state), [])
        self.assertEqual(len(state["claims"]), 1)
        self.assertEqual(state["claims"][0]["polarity"], "benefit")
        self.assertEqual(state["positions"][0]["prefers"], ["format-a"])
        self.assertEqual(len(state["questions"]), 1)
        self.assertEqual(log["target_out_of_catalog"], 1)


if __name__ == "__main__":
    unittest.main()
