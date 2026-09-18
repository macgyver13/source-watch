#!/usr/bin/env python3
"""Tests for discussion state load, validate, and id helpers."""
from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "discussion_state.py"
FIXTURES = ROOT / "tests" / "fixtures" / "discussions"
POST_KEYS = (
    "post_number",
    "author",
    "author_name",
    "created_at",
    "updated_at",
    "url",
    "body_text",
    "content_hash",
    "reply_to_post_number",
)

spec = importlib.util.spec_from_file_location("discussion_state", SCRIPT)
assert spec is not None
discussion_state = cast(Any, importlib.util.module_from_spec(spec))
assert spec.loader is not None
spec.loader.exec_module(cast(ModuleType, discussion_state))

FIXTURE_DIRS = (
    FIXTURES / "options-debate",
    FIXTURES / "single-proposal",
)
PINNED_CLAIM_HASH = "ea066da507b89172d22d5af7fafbb4cb7080a2fbf708207c9d207931ad212296"


class DiscussionStateTests(unittest.TestCase):
    def test_both_fixtures_validate(self) -> None:
        for directory in FIXTURE_DIRS:
            errors = discussion_state.validate(discussion_state.load(directory / "state.json"))
            self.assertEqual(errors, [])

    def test_errors_follow_document_order(self) -> None:
        missing = {
            "schema_version": discussion_state.SCHEMA_VERSION,
            "discussion": {
                "id": "example-order",
                "title": "Order",
                "sources": [{"platform": "github", "url": "https://example.com/a", "cursor": 0}],
            },
            "options": [],
        }
        missing_errors = discussion_state.validate(missing)
        missing_keys = [err for err in missing_errors if err.startswith("state: missing ")]
        self.assertEqual(
            missing_keys,
            ["state: missing claims", "state: missing positions", "state: missing questions"],
        )
        state = discussion_state.load(FIXTURES / "options-debate" / "state.json")
        bad = copy.deepcopy(state)
        bad["claims"][0]["status"] = "answered"
        bad["claims"][0]["answered_by"] = "c99"
        bad["claims"][1]["note"] = "x"
        bad["positions"][0]["supersedes"] = "p9"
        bad["positions"][1]["note"] = "x"
        errors = discussion_state.validate(bad)
        claim_cross = next(i for i, err in enumerate(errors) if err.startswith("claims[0]") and "answered_by" in err)
        claim_shape = next(i for i, err in enumerate(errors) if err.startswith("claims[1]") and "unknown key" in err)
        position_cross = next(i for i, err in enumerate(errors) if err.startswith("positions[0]") and "p9" in err)
        position_shape = next(i for i, err in enumerate(errors) if err.startswith("positions[1]") and "unknown key" in err)
        self.assertLess(claim_cross, claim_shape)
        self.assertLess(claim_shape, position_cross)
        self.assertLess(position_cross, position_shape)

    def test_fixture_posts_match_claim_provenance(self) -> None:
        for directory in FIXTURE_DIRS:
            posts = []
            with (directory / "posts.jsonl").open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    post = json.loads(line)
                    self.assertEqual(tuple(post.keys()), POST_KEYS)
                    self.assertEqual(
                        discussion_state.post_content_hash(post["body_text"]),
                        post["content_hash"],
                    )
                    posts.append(post)
            by_url = {post["url"]: post for post in posts}
            state = discussion_state.load(directory / "state.json")
            for claim in state["claims"]:
                if "hidden" in claim:
                    continue
                post = by_url[claim["post_url"]]
                self.assertIn(
                    discussion_state.quote_core(claim["quote"]),
                    discussion_state.normalize_ws(post["body_text"]),
                )
                self.assertEqual(claim["participant"], post["author"])
                self.assertEqual(claim["date"], discussion_state.normalize_ts(post["created_at"]))

    def test_quote_over_200_chars_names_claim_id(self) -> None:
        state = discussion_state.load(FIXTURES / "options-debate" / "state.json")
        bad = copy.deepcopy(state)
        claim = bad["claims"][0]
        body = next(
            json.loads(line)
            for line in (FIXTURES / "options-debate" / "posts.jsonl").read_text().splitlines()
            if line
        )["body_text"]
        quote = (body * 4)[:201]
        claim["quote"] = quote
        claim["hash"] = discussion_state.claim_hash(claim["participant"], claim["post_url"], quote)
        errors = discussion_state.validate(bad)
        self.assertEqual(len(errors), 1)
        self.assertIn("c1", errors[0])
        self.assertIn("201", errors[0])

    def test_claim_without_post_url_names_claim_id(self) -> None:
        state = discussion_state.load(FIXTURES / "options-debate" / "state.json")
        bad = copy.deepcopy(state)
        del bad["claims"][2]["post_url"]
        errors = discussion_state.validate(bad)
        self.assertEqual(len(errors), 1)
        self.assertIn("c3", errors[0])
        self.assertIn("missing post_url", errors[0])

    def test_supersedes_missing_position_names_position_id(self) -> None:
        state = discussion_state.load(FIXTURES / "options-debate" / "state.json")
        bad = copy.deepcopy(state)
        bad["positions"][1]["supersedes"] = "p9"
        errors = discussion_state.validate(bad)
        self.assertEqual(len(errors), 1)
        self.assertIn("p2", errors[0])
        self.assertIn("p9", errors[0])

    def test_unknown_key_is_rejected(self) -> None:
        state = discussion_state.load(FIXTURES / "options-debate" / "state.json")
        bad = copy.deepcopy(state)
        bad["claims"][0]["note"] = "x"
        errors = discussion_state.validate(bad)
        self.assertTrue(any("c1" in err and "unknown key" in err for err in errors))

    def test_answered_by_requires_existing_answered_claim(self) -> None:
        state = discussion_state.load(FIXTURES / "options-debate" / "state.json")
        missing = copy.deepcopy(state)
        missing["claims"][1]["answered_by"] = "c99"
        errors = discussion_state.validate(missing)
        self.assertTrue(any("c2" in err for err in errors))
        stripped = copy.deepcopy(state)
        del stripped["claims"][1]["answered_by"]
        errors = discussion_state.validate(stripped)
        self.assertTrue(any("c2" in err and "answered_by" in err for err in errors))

    def test_hash_mismatch_is_rejected(self) -> None:
        state = discussion_state.load(FIXTURES / "options-debate" / "state.json")
        bad = copy.deepcopy(state)
        bad["claims"][0]["participant"] = "notada"
        errors = discussion_state.validate(bad)
        self.assertTrue(any("c1" in err and "claim_hash" in err for err in errors))

    def test_claim_hash_is_stable_and_normalizes_quotes(self) -> None:
        participant = "ada"
        url = "https://forum.example/t/101/1"
        quote = "Format A keeps the registry small"
        self.assertEqual(discussion_state.claim_hash(participant, url, quote), PINNED_CLAIM_HASH)
        self.assertEqual(
            discussion_state.claim_hash(participant, url, "  ...Format A keeps   the registry small...  "),
            discussion_state.claim_hash(participant, url, quote),
        )

    def test_next_id_continues_each_sequence(self) -> None:
        state = discussion_state.load(FIXTURES / "options-debate" / "state.json")
        self.assertEqual(discussion_state.next_id(state, "claim"), "c6")
        self.assertEqual(discussion_state.next_id(state, "position"), "p4")
        self.assertEqual(discussion_state.next_id(state, "question"), "q2")
        empty = {"claims": [], "positions": [], "questions": []}
        self.assertEqual(discussion_state.next_id(empty, "claim"), "c1")
        self.assertEqual(discussion_state.next_id(empty, "position"), "p1")
        self.assertEqual(discussion_state.next_id(empty, "question"), "q1")

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "jsonschema not installed")
    def test_published_schema_accepts_fixtures(self) -> None:
        from jsonschema import Draft202012Validator

        schema = json.loads(discussion_state.SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for directory in FIXTURE_DIRS:
            validator.validate(discussion_state.load(directory / "state.json"))


if __name__ == "__main__":
    unittest.main()
