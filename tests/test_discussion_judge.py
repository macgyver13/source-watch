#!/usr/bin/env python3
"""Tests for typed discussion judge primitives and catalogue loading."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import discussion_judge as dj


class ScriptedComplete:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def __call__(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("unexpected extra model call")
        return self.responses.pop(0)


def _judge(responses: list[str]) -> tuple[dj.JsonModelJudge, ScriptedComplete]:
    complete = ScriptedComplete(responses)
    return dj.JsonModelJudge(complete, name="scripted", model="script"), complete


class DiscussionJudgeTests(unittest.TestCase):
    def test_choose_returns_label_probs_and_confidence(self) -> None:
        judge, _ = _judge(
            [
                json.dumps(
                    {
                        "choice": "benefit",
                        "probabilities": {"benefit": 0.8, "blocker": 0.2},
                    }
                )
            ]
        )
        verdict = judge.choose({"quote": "ships faster"}, ["benefit", "blocker"])
        self.assertEqual(
            verdict, ("benefit", {"benefit": 0.8, "blocker": 0.2}, 0.8)
        )
        choice, probs, confidence = verdict
        self.assertEqual(choice, "benefit")
        self.assertEqual(probs, {"benefit": 0.8, "blocker": 0.2})
        self.assertEqual(confidence, 0.8)

    def test_choose_rejects_label_outside_options(self) -> None:
        judge, _ = _judge(
            [
                json.dumps(
                    {
                        "choice": "maybe",
                        "probabilities": {"benefit": 0.5, "blocker": 0.5},
                    }
                )
            ]
        )
        with self.assertRaises(dj.JudgeError) as ctx:
            judge.choose({"quote": "ships faster"}, ["benefit", "blocker"])
        self.assertIn("maybe", str(ctx.exception))

    def test_choose_normalizes_and_fills_probabilities(self) -> None:
        judge, _ = _judge(
            [json.dumps({"choice": "benefit", "probabilities": {"benefit": 0.5}})]
        )
        _, probs, _ = judge.choose({"quote": "ships faster"}, ["benefit", "blocker"])
        self.assertEqual(probs["blocker"], 0.0)
        self.assertAlmostEqual(sum(probs.values()), 1.0)
        self.assertEqual(probs["benefit"], 1.0)

    def test_choose_rejects_unknown_probability_label(self) -> None:
        judge, _ = _judge(
            [
                json.dumps(
                    {
                        "choice": "benefit",
                        "probabilities": {"benefit": 0.7, "other": 0.3},
                    }
                )
            ]
        )
        with self.assertRaises(dj.JudgeError) as ctx:
            judge.choose({"quote": "ships faster"}, ["benefit", "blocker"])
        self.assertIn("other", str(ctx.exception))

    def test_truth_returns_probability(self) -> None:
        judge, _ = _judge([json.dumps({"probability": 0.25})])
        self.assertEqual(judge.truth({"quote": "n"}, "the author concedes"), 0.25)
        judge_high, _ = _judge([json.dumps({"probability": 1.4})])
        with self.assertRaises(dj.JudgeError):
            judge_high.truth({"quote": "n"}, "the author concedes")
        judge_text, _ = _judge([json.dumps({"probability": "high"})])
        with self.assertRaises(dj.JudgeError):
            judge_text.truth({"quote": "n"}, "the author concedes")

    def test_score_is_probability_weighted_level_index(self) -> None:
        judge, _ = _judge(
            [
                json.dumps(
                    {
                        "level": 3,
                        "probabilities": {"0": 0, "1": 0, "2": 0.25, "3": 0.75},
                    }
                )
            ]
        )
        verdict = judge.score(
            {"quote": "n"},
            ["reverse", "drop context", "narrow", "fair"],
        )
        self.assertEqual(verdict.value, 2.75)
        self.assertEqual(verdict.confidence, 0.75)

    def test_parser_rejects_prose_and_non_object(self) -> None:
        fenced = (
            "```json\n"
            '{"choice": "benefit", "probabilities": {"benefit": 0.8, "blocker": 0.2}}\n'
            "```"
        )
        judge, _ = _judge(["sorry, I cannot help", "[1, 2]", fenced])
        with self.assertRaises(dj.JudgeError):
            judge.choose({"quote": "n"}, ["benefit", "blocker"])
        with self.assertRaises(dj.JudgeError):
            judge.choose({"quote": "n"}, ["benefit", "blocker"])
        choice, _, _ = judge.choose({"quote": "n"}, ["benefit", "blocker"])
        self.assertEqual(choice, "benefit")

    def test_state_value_is_truncated_and_oversized_state_rejected(self) -> None:
        judge, complete = _judge(
            [
                json.dumps(
                    {
                        "choice": "benefit",
                        "probabilities": {"benefit": 1, "blocker": 0},
                    }
                )
            ]
        )
        long_value = "x" * 5000
        judge.choose({"quote": long_value}, ["benefit", "blocker"])
        user = complete.calls[0][1]["content"]
        self.assertIn("x" * 1000, user)
        self.assertNotIn("x" * 1001, user)
        oversized = {f"k{i:02d}": "y" * dj.STATE_VALUE_MAX for i in range(12)}
        with self.assertRaises(dj.JudgeError) as ctx:
            judge.choose(oversized, ["benefit", "blocker"])
        self.assertIn(str(dj.STATE_MAX_BYTES), str(ctx.exception))

    def test_catalogue_covers_eight_questions(self) -> None:
        questions = dj.load_questions()
        self.assertEqual(
            list(questions),
            [
                "polarity",
                "target",
                "duplicate_of",
                "responds_to",
                "explicit_preference",
                "concedes",
                "quote_fairness",
                "track_worthiness",
            ],
        )
        expected = {
            "polarity": "choose",
            "target": "choose",
            "duplicate_of": "choose",
            "responds_to": "choose",
            "explicit_preference": "choose",
            "concedes": "truth",
            "quote_fairness": "score",
            "track_worthiness": "score",
        }
        for qid, primitive in expected.items():
            question = questions[qid]
            self.assertEqual(question.primitive, primitive)
            if primitive == "choose":
                self.assertTrue(question.labels or question.label_source)
            elif primitive == "score":
                self.assertGreaterEqual(len(question.levels), 2)
            else:
                self.assertTrue(question.statement)

    def test_ask_requires_dynamic_labels_and_declared_inputs(self) -> None:
        judge, _ = _judge([])
        questions = dj.load_questions()
        with self.assertRaises(dj.JudgeError) as labels_ctx:
            judge.ask(
                questions["target"],
                {"quote": "n", "options": "p2mr", "post_excerpt": "body"},
            )
        self.assertIn("labels", str(labels_ctx.exception))
        with self.assertRaises(dj.JudgeError) as input_ctx:
            judge.ask(questions["polarity"], {"target": "p2mr"})
        self.assertIn("quote", str(input_ctx.exception))

    def test_judge_from_env_errors(self) -> None:
        with self.assertRaises(dj.JudgeError) as unset_ctx:
            dj.judge_from_env({})
        self.assertIn("anthropic", str(unset_ctx.exception))
        self.assertIn("jev", str(unset_ctx.exception))
        self.assertIn("ollama", str(unset_ctx.exception))
        self.assertIn("openai-compatible", str(unset_ctx.exception))
        with self.assertRaises(dj.JudgeError) as unknown_ctx:
            dj.judge_from_env({"DISCUSSION_JUDGE": "nope"})
        self.assertIn("nope", str(unknown_ctx.exception))
        with self.assertRaises(dj.JudgeError) as model_ctx:
            dj.judge_from_env({"DISCUSSION_JUDGE": "ollama"})
        self.assertIn("DISCUSSION_JUDGE_MODEL", str(model_ctx.exception))


if __name__ == "__main__":
    unittest.main()
