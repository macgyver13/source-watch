"""Secondary matcher bucket and answered_by reporting for drafting-only runs."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import eval_discussion as ev  # noqa: E402

POST = "https://example.org/t/x/1"


def claim(cid: str, quote: str, target: str = "o1", polarity: str = "benefit") -> dict:
    return {
        "id": cid,
        "participant": "alice",
        "post_url": POST,
        "quote": quote,
        "target": target,
        "polarity": polarity,
        "status": "open",
        "date": "2026-01-01T00:00:00Z",
    }


def state(claims: list[dict]) -> dict:
    return {
        "discussion": {"id": "d", "sources": []},
        "options": [{"name": "o1", "kind": "option"}],
        "claims": claims,
        "positions": [],
        "questions": [],
    }


class SecondaryBucketTest(unittest.TestCase):
    def test_off_by_default(self) -> None:
        gold = state([claim("c1", "reduces the spending size by about 32 bytes")])
        cand = state([claim("k1", "saves roughly 32 bytes at spend time")])
        result = ev.evaluate(gold, cand)
        self.assertEqual(result["counts"]["matched_claims"], 0)
        self.assertEqual(result["counts"]["matched_claims_secondary"], 0)
        self.assertIsNone(result["claim_recall_with_secondary"])

    def test_secondary_matches_paraphrase(self) -> None:
        gold = state([claim("c1", "reduces the spending size by about 32 bytes")])
        cand = state([claim("k1", "saves roughly 32 bytes at spend time")])
        result = ev.evaluate(gold, cand, secondary_threshold=0.1)
        self.assertEqual(result["counts"]["matched_claims"], 0)
        self.assertEqual(result["counts"]["matched_claims_secondary"], 1)
        self.assertEqual(result["claim_recall"], 0.0)
        self.assertEqual(result["claim_recall_with_secondary"], 1.0)
        self.assertEqual(result["unmatched"]["gold_claim_ids"], [])

    def test_secondary_requires_target_and_polarity(self) -> None:
        gold = state([claim("c1", "reduces the spending size by about 32 bytes")])
        cand = state(
            [claim("k1", "saves roughly 32 bytes at spend time", polarity="blocker")]
        )
        result = ev.evaluate(gold, cand, secondary_threshold=0.1)
        self.assertEqual(result["counts"]["matched_claims_secondary"], 0)

    def test_primary_threshold_unchanged(self) -> None:
        quote = "reduces the spending size by about 32 bytes"
        gold = state([claim("c1", quote)])
        cand = state([claim("k1", quote)])
        result = ev.evaluate(gold, cand, secondary_threshold=0.1)
        self.assertEqual(result["counts"]["matched_claims"], 1)
        self.assertEqual(result["counts"]["matched_claims_secondary"], 0)


class AnsweredByReportingTest(unittest.TestCase):
    def test_na_when_candidate_has_no_links(self) -> None:
        g1 = claim("c1", "first point")
        g2 = claim("c2", "second point")
        g1["answered_by"] = "c2"
        gold = state([g1, g2])
        cand = state([claim("k1", "first point"), claim("k2", "second point")])
        result = ev.evaluate(gold, cand)
        self.assertIsNone(result["answered_by_recall"])
        self.assertIn("drafting-only", result["answered_by_note"])
        self.assertEqual(result["counts"]["candidate_answered_links"], 0)

    def test_scored_when_candidate_has_links(self) -> None:
        g1 = claim("c1", "first point")
        g2 = claim("c2", "second point")
        g1["answered_by"] = "c2"
        gold = state([g1, g2])
        k1 = claim("k1", "first point")
        k2 = claim("k2", "second point")
        k1["answered_by"] = "k2"
        cand = state([k1, k2])
        result = ev.evaluate(gold, cand)
        self.assertEqual(result["answered_by_recall"], 1.0)
        self.assertEqual(result["answered_by_note"], "")


if __name__ == "__main__":
    unittest.main()
