#!/usr/bin/env python3
"""Tests for discussion state evaluation against a gold record."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
GOLD_PATH = ROOT / "tests" / "fixtures" / "discussions" / "delving-2749" / "state.json"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import discussion_state as ds
import eval_discussion as ev

_METRICS = (
    "claim_precision",
    "claim_recall",
    "polarity_accuracy",
    "target_accuracy",
    "position_recall",
    "position_basis_agreement",
    "answered_by_recall",
)


class EvalDiscussionTests(unittest.TestCase):
    def test_gold_against_itself_scores_one(self) -> None:
        gold = ds.load(GOLD_PATH)
        result = ev.evaluate(gold, copy.deepcopy(gold))
        for key in _METRICS:
            self.assertEqual(result[key], 1.0, key)

    def test_removed_and_flipped_claims_score_as_expected(self) -> None:
        gold = ds.load(GOLD_PATH)
        n = len([claim for claim in gold["claims"] if "hidden" not in claim])
        candidate = copy.deepcopy(gold)
        candidate["claims"] = candidate["claims"][:-5]
        for claim in candidate["claims"][:2]:
            claim["polarity"] = "blocker" if claim["polarity"] == "benefit" else "benefit"
        result = ev.evaluate(gold, candidate)
        self.assertEqual(result["claim_recall"], (n - 5) / n)
        self.assertEqual(result["claim_precision"], 1.0)
        self.assertEqual(result["polarity_accuracy"], (n - 5 - 2) / (n - 5))

    def test_answered_by_recall_drops_when_link_removed(self) -> None:
        gold = ds.load(GOLD_PATH)
        k = sum(1 for claim in gold["claims"] if "answered_by" in claim and "hidden" not in claim)
        candidate = copy.deepcopy(gold)
        for claim in candidate["claims"]:
            if "answered_by" in claim:
                del claim["answered_by"]
                break
        result = ev.evaluate(gold, candidate)
        self.assertEqual(result["answered_by_recall"], (k - 1) / k)

    def test_threshold_rejects_a_paraphrase(self) -> None:
        gold = ds.load(GOLD_PATH)
        candidate = copy.deepcopy(gold)
        claim = candidate["claims"][0]
        claim_id = claim["id"]
        claim["quote"] = "unrelated commentary about lunch weather and train delays"
        result = ev.evaluate(gold, candidate)
        self.assertIn(claim_id, result["unmatched"]["gold_claim_ids"])
        self.assertIn(claim_id, result["unmatched"]["candidate_claim_ids"])

    def test_non_ascii_letters_do_not_split_tokens(self) -> None:
        gold = copy.deepcopy(ds.load(GOLD_PATH))
        candidate = copy.deepcopy(gold)
        gold["claims"][0]["quote"] = "naïve"
        candidate["claims"][0]["quote"] = "na ve"
        result = ev.evaluate(gold, candidate)
        self.assertIn(gold["claims"][0]["id"], result["unmatched"]["gold_claim_ids"])
        self.assertIn(candidate["claims"][0]["id"], result["unmatched"]["candidate_claim_ids"])


if __name__ == "__main__":
    unittest.main()
