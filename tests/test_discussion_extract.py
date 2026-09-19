#!/usr/bin/env python3
"""Offline tests for judged discussion claim extraction."""
from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "discussions"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import discussion_extract as dx
import discussion_judge as dj
import discussion_state as ds
import verify_discussion as vd

QUESTIONS = dj.load_questions()
_BY_INSTRUCTIONS = {q.instructions: q.id for q in QUESTIONS.values()}
OPTIONS = FIXTURES / "options-debate"
SINGLE = FIXTURES / "single-proposal"


class ScriptedJudge(dj.Judge):
    """Answers catalogue questions from a per-id plan, keyed by the catalogue instruction text."""

    name = "scripted"

    def __init__(self, plan: dict) -> None:
        self.plan = plan
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def choose(self, state: dict, options, *, instructions: str = "") -> dj.Verdict:
        qid = _BY_INSTRUCTIONS[instructions]
        labels = tuple(options)
        self.calls.append((qid, labels))
        handler = self.plan.get(qid)
        if handler is None:
            return dj.Verdict(dx.NONE_LABEL, {dx.NONE_LABEL: 0.5}, 0.5)
        value, confidence = handler(state, list(options))
        return dj.Verdict(value, {value: confidence}, confidence)

    def truth(self, state: dict, statement: str, *, instructions: str = "") -> float:
        qid = _BY_INSTRUCTIONS[instructions]
        self.calls.append((qid, ()))
        handler = self.plan.get(qid)
        if handler is None:
            return 0.0
        return handler(state, ())

    def score(self, state: dict, rubric, *, instructions: str = "") -> dj.Verdict:
        raise AssertionError("extraction must not call score")


class ScriptedDraft:
    """complete(messages) -> canned JSON for the post_number found in the user message."""

    def __init__(self, by_post: dict[int, dict] | None = None) -> None:
        self.by_post = by_post or {}
        self.calls: list[list[dict]] = []

    def __call__(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        n = _post_number_from_messages(messages)
        payload = self.by_post.get(n, {"claims": [], "questions": []})
        return json.dumps(payload)


def _post_number_from_messages(messages: list[dict]) -> int | None:
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content") if isinstance(msg.get("content"), str) else ""
        for line in content.splitlines():
            if line.startswith("post_number:"):
                raw = line.split(":", 1)[1].strip()
                try:
                    return int(raw)
                except ValueError:
                    return None
    return None


def empty_from(state: dict) -> dict:
    out = deepcopy(state)
    for row in out.get("discussion", {}).get("sources", []):
        if isinstance(row, dict):
            row["cursor"] = 0
    out["claims"] = []
    out["positions"] = []
    out["questions"] = []
    out["snapshots"] = []
    return out


def run_extract(state, posts, draft, judge, **kwargs):
    seen = kwargs.pop("seen", {"schema_version": dx.SEEN_VERSION, "posts": {}})
    return dx.extract(
        state,
        posts,
        judge=judge,
        complete=draft,
        questions=QUESTIONS,
        seen=seen,
        **kwargs,
    )


def _claim_ids(state: dict) -> list[str]:
    return [claim["id"] for claim in state["claims"] if isinstance(claim, dict)]


class DiscussionExtractTests(unittest.TestCase):
    def test_extract_from_empty_state_validates_and_verifies(self) -> None:
        posts = vd.load_posts(OPTIONS / "posts.jsonl")
        state = empty_from(ds.load(OPTIONS / "state.json"))
        draft = ScriptedDraft(
            {
                1: {
                    "claims": [
                        {
                            "quote": "Format A keeps the registry small",
                            "text": "A stays small",
                        }
                    ],
                    "questions": [],
                },
                3: {
                    "claims": [
                        {
                            "quote": "Nested groups are worth the extra code",
                            "text": "Nested groups worth it",
                        }
                    ],
                    "questions": [],
                },
            }
        )

        def target(payload, labels):
            quote = payload["quote"]
            if "Nested groups" in quote:
                return "format-b", 0.9
            return "format-a", 0.9

        def polarity(payload, labels):
            return "benefit", 0.85

        judge = ScriptedJudge(
            {
                "target": target,
                "polarity": polarity,
            }
        )
        out, log = run_extract(state, posts, draft, judge)
        self.assertEqual(ds.validate(out), [])
        self.assertEqual(vd.verify(out, posts), [])
        ids = _claim_ids(out)
        self.assertEqual(ids, [f"c{i}" for i in range(1, len(ids) + 1)])
        self.assertEqual(out["discussion"]["sources"][0]["cursor"], 4)
        self.assertEqual(log["cursor_out"], 4)
        self.assertEqual(out["claims"][0]["quote"], "Format A keeps the registry small")
        self.assertEqual(out["claims"][0]["target"], "format-a")
        self.assertEqual(out["claims"][1]["quote"], "Nested groups are worth the extra code")
        self.assertEqual(out["claims"][1]["target"], "format-b")

    def test_answered_link_and_preference_chain(self) -> None:
        posts = deepcopy(vd.load_posts(OPTIONS / "posts.jsonl"))
        posts[2]["reply_to_post_number"] = 1
        state = empty_from(ds.load(OPTIONS / "state.json"))
        draft = ScriptedDraft(
            {
                1: {
                    "claims": [
                        {
                            "quote": "Format A keeps the registry small",
                            "text": "A stays small",
                        }
                    ],
                    "questions": [],
                },
                3: {
                    "claims": [
                        {
                            "quote": "Nested groups are worth the extra code",
                            "text": "Nested groups worth it",
                        }
                    ],
                    "questions": [],
                },
            }
        )

        def target(payload, labels):
            if "Nested groups" in payload["quote"]:
                return "format-b", 0.9
            return "format-a", 0.9

        def polarity(payload, labels):
            return "benefit", 0.85

        def responds_to(payload, labels):
            if "c1" in labels:
                return "c1", 0.9
            return dx.NONE_LABEL, 0.5

        def explicit_preference(payload, labels):
            excerpt = payload.get("post_excerpt", "")
            if "Changing my mind" in excerpt or "now prefer Format A" in excerpt:
                return "format-a", 0.5
            if "prefer Format B" in excerpt:
                return "format-b", 0.9
            return dx.NONE_LABEL, 0.5

        judge = ScriptedJudge(
            {
                "target": target,
                "polarity": polarity,
                "responds_to": responds_to,
                "explicit_preference": explicit_preference,
            }
        )
        out, _log = run_extract(state, posts, draft, judge)
        self.assertEqual(ds.validate(out), [])
        self.assertEqual(vd.verify(out, posts), [])
        self.assertEqual(out["claims"][0]["id"], "c1")
        self.assertEqual(out["claims"][0]["quote"], "Format A keeps the registry small")
        self.assertEqual(out["claims"][0]["target"], "format-a")
        self.assertEqual(out["claims"][0]["status"], "answered")
        self.assertEqual(out["claims"][0]["answered_by"], "c2")
        self.assertEqual(out["claims"][1]["id"], "c2")
        self.assertEqual(out["claims"][1]["quote"], "Nested groups are worth the extra code")
        self.assertEqual(out["claims"][1]["target"], "format-b")
        self.assertNotIn("answered_by", out["claims"][1])
        self.assertEqual(out["positions"][0]["id"], "p1")
        self.assertEqual(out["positions"][0]["prefers"], ["format-b"])
        self.assertEqual(out["positions"][0]["basis"], "stated")
        self.assertEqual(out["positions"][1]["id"], "p2")
        self.assertEqual(out["positions"][1]["prefers"], ["format-a"])
        self.assertEqual(out["positions"][1]["basis"], "inferred")
        self.assertEqual(out["positions"][1]["supersedes"], "p1")
        self.assertEqual(out["discussion"]["sources"][0]["cursor"], 4)


    def test_single_proposal_fixture_targets_a_drafted_question(self) -> None:
        posts = vd.load_posts(SINGLE / "posts.jsonl")
        state = empty_from(ds.load(SINGLE / "state.json"))
        draft = ScriptedDraft(
            {
                1: {
                    "claims": [
                        {
                            "quote": "The proposal needs a fallback for clients that cannot verify the new field",
                            "text": "Need a fallback",
                        }
                    ],
                    "questions": [{"text": "What happens when the new field is absent?"}],
                }
            }
        )
        judge = ScriptedJudge({"polarity": lambda payload, labels: ("blocker", 0.8)})
        out, _log = run_extract(state, posts, draft, judge)
        self.assertEqual(ds.validate(out), [])
        self.assertEqual(vd.verify(out, posts), [])
        self.assertEqual(len(out["questions"]), 1)
        self.assertEqual(out["questions"][0]["id"], "q1")
        self.assertEqual(out["claims"][0]["id"], "c1")
        self.assertEqual(out["claims"][0]["target"], "q1")
        self.assertEqual(out["claims"][0]["post_url"], posts[0]["url"])

    def test_hallucinated_quote_is_dropped_with_reason(self) -> None:
        posts = vd.load_posts(OPTIONS / "posts.jsonl")
        state = empty_from(ds.load(OPTIONS / "state.json"))
        draft = ScriptedDraft(
            {
                1: {
                    "claims": [
                        {
                            "quote": "this text is not in the body at all",
                            "text": "hallucinated",
                        },
                        {
                            "quote": "Format A keeps the registry small",
                            "text": "real",
                        },
                    ],
                    "questions": [],
                }
            }
        )
        judge = ScriptedJudge(
            {
                "target": lambda payload, labels: ("format-a", 0.9),
                "polarity": lambda payload, labels: ("benefit", 0.8),
            }
        )
        out, log = run_extract(state, posts, draft, judge)
        self.assertEqual(log["dropped"]["verify"], 1)
        self.assertIn("quote is not a verbatim substring", log["drop_reasons"][0])
        self.assertEqual(len(out["claims"]), 1)
        self.assertEqual(out["claims"][0]["quote"], "Format A keeps the registry small")
        self.assertEqual(ds.validate(out), [])
        self.assertEqual(vd.verify(out, posts), [])

    def test_second_run_over_the_same_posts_appends_nothing(self) -> None:
        posts = vd.load_posts(OPTIONS / "posts.jsonl")
        state = empty_from(ds.load(OPTIONS / "state.json"))
        drafts = {
            1: {
                "claims": [{"quote": "Format A keeps the registry small", "text": "small"}],
                "questions": [],
            },
            3: {
                "claims": [{"quote": "Nested groups are worth the extra code", "text": "nested"}],
                "questions": [],
            },
        }
        judge_plan = {
            "target": lambda payload, labels: (
                ("format-b", 0.9) if "Nested groups" in payload["quote"] else ("format-a", 0.9)
            ),
            "polarity": lambda payload, labels: ("benefit", 0.8),
        }
        out1, _log1 = run_extract(state, posts, ScriptedDraft(drafts), ScriptedJudge(judge_plan))
        out2, log2 = run_extract(
            out1, posts, ScriptedDraft(drafts), ScriptedJudge(judge_plan), cursor=0
        )
        self.assertEqual(out2["claims"], out1["claims"])
        self.assertEqual(out2["positions"], out1["positions"])
        self.assertEqual(out2["questions"], out1["questions"])
        self.assertEqual(log2["dropped"]["duplicate_hash"], len(out1["claims"]))

    def test_duplicate_of_verdict_drops_the_new_claim(self) -> None:
        posts = deepcopy(vd.load_posts(OPTIONS / "posts.jsonl"))
        posts[1]["body_text"] = "Format A keeps the registry small for clients, but nested groups still matter."
        posts[1]["content_hash"] = ds.post_content_hash(posts[1]["body_text"])
        state = empty_from(ds.load(OPTIONS / "state.json"))
        drafts = {
            1: {
                "claims": [{"quote": "Format A keeps the registry small", "text": "first"}],
                "questions": [],
            },
            2: {
                "claims": [
                    {
                        "quote": "Format A keeps the registry small for clients",
                        "text": "near duplicate",
                    }
                ],
                "questions": [],
            },
        }

        def duplicate_of(payload, labels):
            if "c1" in labels:
                return "c1", 0.9
            return dx.NONE_LABEL, 0.5

        judge = ScriptedJudge(
            {
                "target": lambda payload, labels: ("format-a", 0.9),
                "polarity": lambda payload, labels: ("benefit", 0.8),
                "duplicate_of": duplicate_of,
            }
        )
        out, log = run_extract(state, posts, ScriptedDraft(drafts), judge)
        self.assertEqual(log["dropped"]["duplicate_of"], 1)
        self.assertEqual(len(out["claims"]), 1)
        self.assertEqual(out["claims"][0]["id"], "c1")
        self.assertEqual(out["claims"][0]["quote"], "Format A keeps the registry small")

    def test_all_open_author_claims_are_probed_for_concession(self) -> None:
        posts = [
            {
                "post_number": n,
                "author": "ada",
                "created_at": f"2026-03-0{n}T09:00:00Z",
                "url": f"https://forum.example/t/101/{n}",
                "body_text": body,
                "content_hash": ds.post_content_hash(body),
                "reply_to_post_number": None if n == 1 else n - 1,
            }
            for n, body in (
                (1, "first argument here"),
                (2, "second argument here"),
                (3, "third argument here"),
                (4, "I no longer hold the second argument."),
            )
        ]
        state = {
            "schema_version": ds.SCHEMA_VERSION,
            "discussion": {
                "id": "mini-concede",
                "title": "Concede probe",
                "sources": [
                    {
                        "platform": "discourse",
                        "url": "https://forum.example/t/101",
                        "cursor": 3,
                    }
                ],
            },
            "options": [{"id": "format-a", "name": "Format A", "kind": "option"}],
            "claims": [],
            "positions": [],
            "questions": [],
        }
        for n, quote in ((1, "first argument here"), (2, "second argument here"), (3, "third argument here")):
            url = f"https://forum.example/t/101/{n}"
            state["claims"].append(
                {
                    "id": f"c{n}",
                    "hash": ds.claim_hash("ada", url, quote),
                    "polarity": "benefit",
                    "target": "format-a",
                    "participant": "ada",
                    "post_url": url,
                    "quote": quote,
                    "date": f"2026-03-0{n}T09:00:00Z",
                    "status": "open",
                }
            )

        def concedes(payload, labels):
            if "second" in payload["claim_quote"]:
                return 0.9
            return 0.2

        judge = ScriptedJudge({"concedes": concedes})
        seen = {
            "schema_version": dx.SEEN_VERSION,
            "posts": {
                post["url"]: {"content_hash": post["content_hash"], "post_number": post["post_number"]}
                for post in posts
            },
        }
        out, log = run_extract(state, posts, ScriptedDraft({}), judge, seen=seen)
        concede_calls = [call for call in judge.calls if call[0] == "concedes"]
        self.assertEqual(len(concede_calls), 3)
        self.assertEqual(log["concede_calls"], 3)
        by_id = {claim["id"]: claim for claim in out["claims"]}
        self.assertEqual(by_id["c1"]["status"], "open")
        self.assertEqual(by_id["c2"]["status"], "conceded")
        self.assertEqual(by_id["c3"]["status"], "open")

    def test_candidates_are_batched_not_truncated(self) -> None:
        base = "Format A keeps the registry small for constrained clients and that is why we should pick it now"
        posts = [
            {
                "post_number": 1,
                "author": "ada",
                "created_at": "2026-03-01T09:00:00Z",
                "url": "https://forum.example/t/101/1",
                "body_text": "seed",
                "content_hash": ds.post_content_hash("seed"),
                "reply_to_post_number": None,
            },
            {
                "post_number": 2,
                "author": "brin",
                "created_at": "2026-03-02T09:00:00Z",
                "url": "https://forum.example/t/101/2",
                "body_text": base,
                "content_hash": ds.post_content_hash(base),
                "reply_to_post_number": 1,
            },
        ]
        state = {
            "schema_version": ds.SCHEMA_VERSION,
            "discussion": {
                "id": "mini-batch",
                "title": "Batch probe",
                "sources": [
                    {
                        "platform": "discourse",
                        "url": "https://forum.example/t/101",
                        "cursor": 1,
                    }
                ],
            },
            "options": [{"id": "format-a", "name": "Format A", "kind": "option"}],
            "claims": [],
            "positions": [],
            "questions": [],
        }
        url = posts[0]["url"]
        ids = []
        for i in range(12):
            quote = f"{base} extra{i:02d}"
            cid = f"c{i + 1}"
            ids.append(cid)
            state["claims"].append(
                {
                    "id": cid,
                    "hash": ds.claim_hash("ada", url, quote),
                    "polarity": "benefit",
                    "target": "format-a",
                    "participant": "ada",
                    "post_url": url,
                    "quote": quote,
                    "date": "2026-03-01T09:00:00Z",
                    "status": "open",
                }
            )
        draft = ScriptedDraft({2: {"claims": [{"quote": base, "text": "new"}], "questions": []}})
        judge = ScriptedJudge(
            {
                "target": lambda payload, labels: ("format-a", 0.9),
                "polarity": lambda payload, labels: ("benefit", 0.8),
            }
        )
        seen = {
            "schema_version": dx.SEEN_VERSION,
            "posts": {
                post["url"]: {"content_hash": post["content_hash"], "post_number": post["post_number"]}
                for post in posts
            },
        }
        _out, _log = run_extract(state, posts, draft, judge, seen=seen)
        dup_calls = [labels for qid, labels in judge.calls if qid == "duplicate_of"]
        self.assertGreater(len(dup_calls), 1)
        union = set()
        for labels in dup_calls:
            union.update(labels)
        self.assertEqual(union - {dx.NONE_LABEL}, set(ids))

    def test_responds_to_marks_the_earlier_claim_answered(self) -> None:
        posts = vd.load_posts(OPTIONS / "posts.jsonl")
        state = empty_from(ds.load(OPTIONS / "state.json"))
        drafts = {
            1: {
                "claims": [{"quote": "Format A keeps the registry small", "text": "small"}],
                "questions": [],
            },
            2: {
                "claims": [{"quote": "Format A cannot express nested groups", "text": "blocks"}],
                "questions": [],
            },
        }

        def responds_to(payload, labels):
            if "c1" in labels:
                return "c1", 0.9
            return dx.NONE_LABEL, 0.5

        def target(payload, labels):
            if "cannot express" in payload["quote"]:
                return "format-a", 0.9
            return "format-a", 0.9

        judge = ScriptedJudge(
            {
                "target": target,
                "polarity": lambda payload, labels: (
                    ("blocker", 0.8) if "cannot express" in payload["quote"] else ("benefit", 0.8)
                ),
                "responds_to": responds_to,
            }
        )
        out, log = run_extract(state, posts, ScriptedDraft(drafts), judge)
        self.assertEqual(out["claims"][0]["status"], "answered")
        self.assertEqual(out["claims"][0]["answered_by"], "c2")
        self.assertNotIn("answered_by", out["claims"][1])
        self.assertEqual(out["claims"][1]["status"], "open")
        self.assertEqual(log["answered_links"], 1)
        self.assertEqual(ds.validate(out), [])
        self.assertEqual(vd.verify(out, posts), [])

    def test_preference_shift_chains_by_supersedes(self) -> None:
        posts = vd.load_posts(OPTIONS / "posts.jsonl")
        posts = deepcopy(posts)
        extra = deepcopy(posts[3])
        extra["post_number"] = 5
        extra["url"] = extra["url"] + "-again"
        extra["created_at"] = "2026-03-06T10:00:00Z"
        extra["content_hash"] = ds.post_content_hash(extra["body_text"])
        posts.append(extra)
        state = empty_from(ds.load(OPTIONS / "state.json"))

        def explicit_preference(payload, labels):
            excerpt = payload.get("post_excerpt", "")
            if "Changing my mind" in excerpt or "now prefer Format A" in excerpt:
                return "format-a", 0.5
            if "prefer Format B" in excerpt:
                return "format-b", 0.9
            return dx.NONE_LABEL, 0.5

        judge = ScriptedJudge({"explicit_preference": explicit_preference})
        out, log = run_extract(state, posts, ScriptedDraft({}), judge)
        self.assertEqual(log["positions_added"], 2)
        self.assertEqual(len(out["positions"]), 2)
        first, second = out["positions"]
        self.assertEqual(first["id"], "p1")
        self.assertEqual(first["prefers"], ["format-b"])
        self.assertEqual(first["basis"], "stated")
        self.assertEqual(second["id"], "p2")
        self.assertEqual(second["prefers"], ["format-a"])
        self.assertEqual(second["basis"], "inferred")
        self.assertEqual(second["supersedes"], "p1")
        self.assertEqual(ds.validate(out), [])


if __name__ == "__main__":
    unittest.main()
