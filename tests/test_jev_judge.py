#!/usr/bin/env python3
"""Tests for the provisional Jev judge backend."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import discussion_judge as dj


class FakeJevTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, kind: str, body: dict) -> dict:
        self.calls.append((kind, body))
        if kind == "choice":
            return {
                "label": "benefit",
                "probabilities": {"benefit": 0.8, "blocker": 0.2},
                "confidence": 0.8,
            }
        if kind == "noul":
            return {"probability": 0.25}
        if kind == "score":
            return {"probabilities": {"0": 0, "1": 0, "2": 0.25, "3": 0.75}}
        raise AssertionError(f"unexpected jev kind {kind!r}")


class JevJudgeTests(unittest.TestCase):
    def test_jev_without_key_fails_with_expected_message(self) -> None:
        with self.assertRaises(dj.JudgeError) as ctx:
            dj.judge_from_env({"DISCUSSION_JUDGE": "jev"})
        self.assertEqual(str(ctx.exception), "TYPESAFE_API_KEY is not set")

    def test_jev_default_transport_reports_unpublished_endpoint(self) -> None:
        judge = dj.JevJudge(api_key="fake")
        with self.assertRaises(dj.JudgeError) as ctx:
            judge.choose({"quote": "n"}, ["benefit", "blocker"])
        self.assertIn("not published", str(ctx.exception))

    def test_jev_primitives_round_trip_with_fake_transport(self) -> None:
        transport = FakeJevTransport()
        judge = dj.JevJudge(api_key="fake", transport=transport)
        state = {"quote": "ships faster", "post_excerpt": "body"}
        choice, probs, confidence = judge.choose(state, ["benefit", "blocker"])
        self.assertEqual(choice, "benefit")
        self.assertEqual(probs, {"benefit": 0.8, "blocker": 0.2})
        self.assertEqual(confidence, 0.8)
        self.assertEqual(judge.truth(state, "the author concedes"), 0.25)
        verdict = judge.score(state, ["reverse", "drop context", "narrow", "fair"])
        self.assertEqual(verdict.value, 2.75)
        self.assertEqual(verdict.confidence, 0.75)
        kinds = [kind for kind, _ in transport.calls]
        self.assertEqual(kinds, ["choice", "noul", "score"])
        for kind, body in transport.calls:
            self.assertEqual(body["kind"], kind)
            self.assertEqual(body["state"]["quote"], "ships faster")
            self.assertIn("post_excerpt", body["state"])

    def test_jev_rejects_oversized_body(self) -> None:
        def boom(kind: str, body: dict) -> dict:
            raise AssertionError("transport should not run")

        judge = dj.JevJudge(api_key="fake", transport=boom)
        state = {f"k{i:02d}": "x" * 900 for i in range(8)}
        labels = [f"label-{i:04d}-padding" for i in range(400)]
        with self.assertRaises(dj.JudgeError) as ctx:
            judge.choose(state, labels)
        self.assertIn(str(dj.JEV_MAX_BODY_BYTES), str(ctx.exception))

    def test_format_answer_uses_two_decimal_confidence_and_score(self) -> None:
        questions = dj.load_questions()
        self.assertEqual(
            dj._format_answer(
                questions["polarity"],
                dj.Verdict("blocker", {"benefit": 0.4, "blocker": 0.6}, 0.6),
            ),
            "polarity choose -> blocker (confidence 0.60)",
        )
        self.assertEqual(
            dj._format_answer(
                questions["quote_fairness"],
                dj.Verdict(2.8, {"0": 0.0, "1": 0.0, "2": 0.15, "3": 0.85}, 0.85),
            ),
            "quote_fairness score -> 2.80 (confidence 0.85)",
        )

    def test_answer_record_truth_includes_probabilities_and_confidence(self) -> None:
        questions = dj.load_questions()
        self.assertEqual(
            dj._answer_record(questions["concedes"], 0.05),
            {
                "primitive": "truth",
                "value": 0.05,
                "probabilities": {"true": 0.05, "false": 0.95},
                "confidence": 0.95,
            },
        )


if __name__ == "__main__":
    unittest.main()
