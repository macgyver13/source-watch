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
        self.assertIn("Prefer link_recall_by_post", result["answered_by_note"])


class LinkByPostTest(unittest.TestCase):
    def _linked(self, prefix: str) -> dict:
        a = claim(prefix + "1", "the first point")
        b = claim(prefix + "2", "a reply to it")
        b["post_url"] = POST + "/b"
        a["answered_by"] = prefix + "2"
        return state([a, b])

    def test_links_match_by_post_even_when_ids_differ(self) -> None:
        gold = self._linked("c")
        cand = self._linked("k")
        result = ev.evaluate(gold, cand)
        # id-based scoring can still match here because the quotes are identical;
        # what matters is that the post-pair measure is reported and agrees.
        self.assertEqual(result["link_recall_by_post"], 1.0)
        self.assertEqual(result["link_precision_by_post"], 1.0)
        self.assertEqual(result["counts"]["matched_link_post_pairs"], 1)

    def test_link_precision_counts_extra_links(self) -> None:
        gold = self._linked("c")
        cand = self._linked("k")
        extra_a = claim("k3", "another point")
        extra_b = claim("k4", "another reply")
        extra_b["post_url"] = POST + "/d"
        extra_a["post_url"] = POST + "/c"
        extra_a["answered_by"] = "k4"
        cand["claims"].extend([extra_a, extra_b])
        result = ev.evaluate(gold, cand)
        self.assertEqual(result["link_recall_by_post"], 1.0)
        self.assertEqual(result["link_precision_by_post"], 0.5)

    def test_na_when_candidate_has_no_links(self) -> None:
        gold = self._linked("c")
        cand = state([claim("k1", "the first point")])
        result = ev.evaluate(gold, cand)
        self.assertIsNone(result["link_recall_by_post"])
        self.assertIsNone(result["answered_by_recall"])


if __name__ == "__main__":
    unittest.main()
