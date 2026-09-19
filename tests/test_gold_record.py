#!/usr/bin/env python3
"""Regression tests that the delving-2749 gold record matches ingested posts."""
from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "discussions" / "delving-2749"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import discussion_state as ds
import verify_discussion as vd


class GoldRecordTests(unittest.TestCase):
    def test_gold_record_validates(self) -> None:
        self.assertEqual(ds.validate(ds.load(FIXTURE / "state.json")), [])

    def test_gold_record_verifies_against_posts(self) -> None:
        state = ds.load(FIXTURE / "state.json")
        posts = vd.load_posts(FIXTURE / "posts.jsonl")
        self.assertEqual(vd.verify(state, posts), [])

    def test_gold_record_claim_status_counts(self) -> None:
        state = ds.load(FIXTURE / "state.json")
        counts = Counter(claim["status"] for claim in state["claims"])
        self.assertEqual(counts["open"], 111)
        self.assertEqual(counts["answered"], 29)
        self.assertEqual(counts["conceded"], 3)
        self.assertEqual(len(state["claims"]), 143)


if __name__ == "__main__":
    unittest.main()
