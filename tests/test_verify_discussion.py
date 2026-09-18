#!/usr/bin/env python3
"""Tests for mechanical discussion claim verification."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "discussions"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import discussion_state as ds
import verify_discussion as vd

FIXTURE_DIRS = (
    FIXTURES / "options-debate",
    FIXTURES / "single-proposal",
)


def _load_fixture(directory: Path) -> tuple[dict, list[dict]]:
    state = ds.load(directory / "state.json")
    posts = vd.load_posts(directory / "posts.jsonl")
    return state, posts


class VerifyDiscussionTests(unittest.TestCase):
    def test_fixture_states_verify_clean(self) -> None:
        for directory in FIXTURE_DIRS:
            state, posts = _load_fixture(directory)
            self.assertEqual(vd.verify(state, posts), [])

    def test_flipped_author_fails_exactly_one_claim(self) -> None:
        state, posts = _load_fixture(FIXTURES / "options-debate")
        bad = copy.deepcopy(posts)
        post1 = next(post for post in bad if post["post_number"] == 1)
        post1["author"] = "notada"
        expected_ids = [
            claim["id"]
            for claim in state["claims"]
            if claim.get("post_url") == post1["url"] and "hidden" not in claim
        ]
        failures = vd.verify(state, bad)
        self.assertEqual(len(failures), len(expected_ids))
        self.assertEqual(
            failures,
            [
                f"claim {cid}: participant ada does not match post author notada"
                for cid in expected_ids
            ],
        )

    def test_internal_elision_fails(self) -> None:
        state, posts = _load_fixture(FIXTURES / "options-debate")
        state = copy.deepcopy(state)
        posts = copy.deepcopy(posts)
        claim = next(row for row in state["claims"] if "hidden" not in row)
        words = claim["quote"].split()
        rewritten = f"{words[0]} ... {words[-1]}"
        claim["quote"] = rewritten
        claim["hash"] = ds.claim_hash(claim["participant"], claim["post_url"], rewritten)
        post = next(row for row in posts if row["url"] == claim["post_url"])
        post["body_text"] = rewritten + " " + post["body_text"]
        failures = vd.verify(state, posts)
        self.assertEqual(failures, [f"claim {claim['id']}: quote contains internal elision"])

    def test_unknown_target_fails(self) -> None:
        state, posts = _load_fixture(FIXTURES / "options-debate")
        state = copy.deepcopy(state)
        claim = next(row for row in state["claims"] if "hidden" not in row)
        claim["target"] = "nope"
        failures = vd.verify(state, posts)
        self.assertEqual(failures, [f"claim {claim['id']}: unknown target nope"])


if __name__ == "__main__":
    unittest.main()
