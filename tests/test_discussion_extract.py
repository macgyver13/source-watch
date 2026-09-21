#!/usr/bin/env python3
"""Offline tests for judged discussion claim extraction."""
from __future__ import annotations

import json
import sys
import tempfile
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


def _changed_keys(old: dict, new: dict) -> set[str]:
    keys = set(old) | set(new)
    return {key for key in keys if old.get(key) != new.get(key)}



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

    def test_only_contested_author_claims_are_probed_for_concession(self) -> None:
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
                (3, "counterpoint here"),
                (4, "I no longer hold the second argument."),
            )
        ]
        posts[2]["author"] = "bob"
        posts[2]["author_name"] = "Bob"
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
        # ada argues for format-a twice; bob argues against it in post 3, which is
        # what makes ada's earlier claims contested and a concession possible.
        for n, quote, who, polarity in (
            (1, "first argument here", "ada", "benefit"),
            (2, "second argument here", "ada", "benefit"),
            (3, "counterpoint here", "bob", "blocker"),
        ):
            url = f"https://forum.example/t/101/{n}"
            state["claims"].append(
                {
                    "id": f"c{n}",
                    "hash": ds.claim_hash(who, url, quote),
                    "polarity": polarity,
                    "target": "format-a",
                    "participant": who,
                    "post_url": url,
                    "quote": quote,
                    "date": f"2026-03-0{n}T09:00:00Z",
                    "status": "open",
                }
            )

        probed: list[str] = []

        def concedes(payload, labels):
            probed.append(payload["claim_quote"])
            if "second" in payload["claim_quote"]:
                return 0.95
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
        self.assertEqual(len(concede_calls), 2)
        self.assertEqual(log["concede_calls"], 2)
        self.assertNotIn("counterpoint here", probed)
        self.assertEqual(sorted(probed), ["first argument here", "second argument here"])
        by_id = {claim["id"]: claim for claim in out["claims"]}
        self.assertEqual(by_id["c1"]["status"], "open")
        self.assertEqual(by_id["c2"]["status"], "conceded")
        self.assertEqual(by_id["c2"]["concede_confidence"], 0.95)
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

    def test_incremental_split_is_byte_identical(self) -> None:
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
            3: {
                "claims": [{"quote": "Nested groups are worth the extra code", "text": "nested"}],
                "questions": [],
            },
            4: {
                "claims": [{"quote": "Format A plus the grouping shim is enough", "text": "shim"}],
                "questions": [],
            },
        }

        def target(payload, labels):
            quote = payload["quote"]
            if "Nested groups" in quote:
                return "format-b", 0.9
            if "shim" in quote:
                return "grouping-shim", 0.9
            return "format-a", 0.9

        def polarity(payload, labels):
            if "cannot express" in payload["quote"]:
                return "blocker", 0.8
            return "benefit", 0.8

        plan = {"target": target, "polarity": polarity}
        out1, log1 = run_extract(
            state, posts, ScriptedDraft(drafts), ScriptedJudge(plan), limit=2
        )
        self.assertEqual(log1["cursor_out"], 2)
        out2, log2 = run_extract(
            out1, posts, ScriptedDraft(drafts), ScriptedJudge(plan)
        )
        self.assertEqual(log2["cursor_in"], 2)
        self.assertEqual(log2["cursor_out"], 4)
        run1_ids = {claim["id"] for claim in out1["claims"]}
        run1_by_id = {claim["id"]: claim for claim in out1["claims"]}
        run2_by_id = {claim["id"]: claim for claim in out2["claims"]}
        self.assertTrue(run1_ids <= set(run2_by_id))
        for cid in run1_ids:
            self.assertEqual(
                json.dumps(run1_by_id[cid], sort_keys=True),
                json.dumps(run2_by_id[cid], sort_keys=True),
            )
        new_ids = [claim["id"] for claim in out2["claims"] if claim["id"] not in run1_ids]
        max_old = max(int(cid[1:]) for cid in run1_ids)
        for cid in new_ids:
            self.assertGreater(int(cid[1:]), max_old)

    def test_status_changes_are_the_only_permitted_mutation(self) -> None:
        posts = deepcopy(vd.load_posts(OPTIONS / "posts.jsonl"))
        posts[2]["reply_to_post_number"] = 1
        posts[3]["author"] = "brin"
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
            3: {
                "claims": [{"quote": "Nested groups are worth the extra code", "text": "nested"}],
                "questions": [],
            },
        }

        def target(payload, labels):
            if "Nested groups" in payload["quote"]:
                return "format-b", 0.9
            return "format-a", 0.9

        def polarity(payload, labels):
            if "cannot express" in payload["quote"]:
                return "blocker", 0.8
            return "benefit", 0.8

        def responds_to(payload, labels):
            if "Nested groups" in payload["quote"] and "c1" in labels:
                return "c1", 0.9
            return dx.NONE_LABEL, 0.5

        def concedes(payload, labels):
            if payload.get("claim_quote") == "Format A cannot express nested groups":
                return 0.9
            return 0.0

        plan = {
            "target": target,
            "polarity": polarity,
            "responds_to": responds_to,
            "concedes": concedes,
        }
        out1, _log1 = run_extract(
            state, posts, ScriptedDraft(drafts), ScriptedJudge(plan), limit=2
        )
        out2, _log2 = run_extract(
            out1, posts, ScriptedDraft(drafts), ScriptedJudge(plan)
        )
        run1 = {claim["id"]: claim for claim in out1["claims"]}
        run2 = {claim["id"]: claim for claim in out2["claims"]}
        self.assertEqual(_changed_keys(run1["c1"], run2["c1"]), {"status", "answered_by"})
        self.assertEqual(
            _changed_keys(run1["c2"], run2["c2"]), {"status", "concede_confidence"}
        )
        self.assertEqual(run2["c1"]["status"], "answered")
        self.assertEqual(run2["c1"]["answered_by"], "c3")
        self.assertEqual(run2["c2"]["status"], "conceded")
        self.assertEqual(ds.validate(out2), [])

    def test_supersedes_chain_survives_the_split(self) -> None:
        posts = vd.load_posts(OPTIONS / "posts.jsonl")
        state = empty_from(ds.load(OPTIONS / "state.json"))

        def explicit_preference(payload, labels):
            excerpt = payload.get("post_excerpt", "")
            if "Changing my mind" in excerpt or "now prefer Format A" in excerpt:
                return "format-a", 0.5
            if "prefer Format B" in excerpt:
                return "format-b", 0.9
            return dx.NONE_LABEL, 0.5

        plan = {"explicit_preference": explicit_preference}
        out1, log1 = run_extract(
            state, posts, ScriptedDraft({}), ScriptedJudge(plan), limit=3
        )
        self.assertEqual(log1["cursor_out"], 3)
        self.assertEqual(len(out1["positions"]), 1)
        out2, _log2 = run_extract(out1, posts, ScriptedDraft({}), ScriptedJudge(plan))
        self.assertEqual(len(out2["positions"]), 2)
        first, second = out2["positions"]
        self.assertEqual(second["supersedes"], first["id"])
        seen = set()
        current = second["id"]
        by_id = {row["id"]: row for row in out2["positions"]}
        while current:
            self.assertNotIn(current, seen)
            seen.add(current)
            current = by_id[current].get("supersedes")
        self.assertEqual(ds.validate(out2), [])

    def test_edited_post_hides_dependent_claims(self) -> None:
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
        plan = {
            "target": lambda payload, labels: ("format-a", 0.9),
            "polarity": lambda payload, labels: (
                ("blocker", 0.8) if "cannot express" in payload["quote"] else ("benefit", 0.8)
            ),
        }
        with tempfile.TemporaryDirectory() as tmp:
            seen_path = Path(tmp) / "seen.json"
            out1, log1 = run_extract(
                state, posts, ScriptedDraft(drafts), ScriptedJudge(plan)
            )
            dx.save_seen(seen_path, posts)
            edited = deepcopy(posts)
            old_hash = edited[1]["content_hash"]
            edited[1]["body_text"] = (
                edited[1]["body_text"] + " Extra commentary that does not touch the quote."
            )
            edited[1]["content_hash"] = ds.post_content_hash(edited[1]["body_text"])
            new_hash = edited[1]["content_hash"]
            seen = dx.load_seen(seen_path)
            out2, log2 = run_extract(
                out1,
                edited,
                ScriptedDraft(drafts),
                ScriptedJudge(plan),
                seen=seen,
                cursor=log1["cursor_out"],
            )
            post2_url = posts[1]["url"]
            run1_by_id = {claim["id"]: claim for claim in out1["claims"]}
            dependent = [claim for claim in out2["claims"] if claim["post_url"] == post2_url]
            others = [claim for claim in out2["claims"] if claim["post_url"] != post2_url]
            self.assertTrue(dependent)
            for claim in others:
                self.assertEqual(
                    json.dumps(run1_by_id[claim["id"]], sort_keys=True),
                    json.dumps(claim, sort_keys=True),
                )
            for claim in dependent:
                old = run1_by_id[claim["id"]]
                stripped = dict(claim)
                hidden = stripped.pop("hidden")
                self.assertEqual(
                    json.dumps(old, sort_keys=True),
                    json.dumps(stripped, sort_keys=True),
                )
                self.assertEqual(hidden["reason"], "source_edited")
                self.assertIn(old_hash[:12], hidden["note"])
                self.assertIn(new_hash[:12], hidden["note"])
            self.assertEqual(log2["hidden"]["source_edited"], len(dependent))
            self.assertEqual(ds.validate(out2), [])

    def test_edited_post_without_sidecar_falls_back_to_the_verifier(self) -> None:
        posts = vd.load_posts(OPTIONS / "posts.jsonl")
        state = empty_from(ds.load(OPTIONS / "state.json"))
        drafts = {
            2: {
                "claims": [{"quote": "Format A cannot express nested groups", "text": "blocks"}],
                "questions": [],
            }
        }
        plan = {
            "target": lambda payload, labels: ("format-a", 0.9),
            "polarity": lambda payload, labels: ("blocker", 0.8),
        }
        with tempfile.TemporaryDirectory() as tmp:
            seen_path = Path(tmp) / "seen.json"
            out1, log1 = run_extract(
                state, posts, ScriptedDraft(drafts), ScriptedJudge(plan)
            )
            dx.save_seen(seen_path, posts)
            seen_path.unlink()
            edited = deepcopy(posts)
            edited[1]["body_text"] = "The quoted sentence is gone now."
            edited[1]["content_hash"] = ds.post_content_hash(edited[1]["body_text"])
            seen = dx.load_seen(seen_path)
            out2, log2 = run_extract(
                out1,
                edited,
                ScriptedDraft(drafts),
                ScriptedJudge(plan),
                seen=seen,
                cursor=log1["cursor_out"],
            )
            dependent = [
                claim for claim in out2["claims"] if claim["post_url"] == posts[1]["url"]
            ]
            self.assertEqual(len(dependent), 1)
            self.assertEqual(dependent[0]["hidden"]["reason"], "source_edited")
            self.assertIn("quote is not a verbatim substring", dependent[0]["hidden"]["note"])
            self.assertEqual(log2["hidden"]["source_edited"], 1)
            self.assertEqual(ds.validate(out2), [])

    def test_deleted_post_hides_dependent_claims(self) -> None:
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
        plan = {
            "target": lambda payload, labels: ("format-a", 0.9),
            "polarity": lambda payload, labels: (
                ("blocker", 0.8) if "cannot express" in payload["quote"] else ("benefit", 0.8)
            ),
        }
        out1, log1 = run_extract(state, posts, ScriptedDraft(drafts), ScriptedJudge(plan))
        remaining = [post for post in posts if post["post_number"] != 2]
        out2, log2 = run_extract(
            out1,
            remaining,
            ScriptedDraft(drafts),
            ScriptedJudge(plan),
            cursor=log1["cursor_out"],
        )
        dependent = [claim for claim in out2["claims"] if claim["post_url"] == posts[1]["url"]]
        self.assertEqual(len(dependent), 1)
        self.assertEqual(dependent[0]["id"], "c2")
        self.assertEqual(dependent[0]["hidden"]["reason"], "source_deleted")
        self.assertEqual(log2["hidden"]["source_deleted"], 1)
        self.assertEqual(ds.validate(out2), [])

    def test_snapshot_records_cursor_and_counts(self) -> None:
        posts = vd.load_posts(OPTIONS / "posts.jsonl")
        state = empty_from(ds.load(OPTIONS / "state.json"))
        drafts = {
            1: {
                "claims": [{"quote": "Format A keeps the registry small", "text": "small"}],
                "questions": [],
            }
        }
        plan = {
            "target": lambda payload, labels: ("format-a", 0.9),
            "polarity": lambda payload, labels: ("benefit", 0.8),
        }
        out, _log = run_extract(state, posts, ScriptedDraft(drafts), ScriptedJudge(plan))
        self.assertTrue(out["snapshots"])
        snap = out["snapshots"][-1]
        self.assertEqual(snap["at"], "2026-03-05T14:20:00Z")
        self.assertEqual(snap["cursors"], {"https://forum.example/t/101": 4})
        hidden = 0
        visible = {"claims": 0, "positions": 0, "questions": 0}
        for key in visible:
            for row in out.get(key) or []:
                if "hidden" in row:
                    hidden += 1
                else:
                    visible[key] += 1
        self.assertEqual(
            snap["counts"],
            {
                "claims": visible["claims"],
                "positions": visible["positions"],
                "questions": visible["questions"],
                "hidden": hidden,
            },
        )

    def test_seeded_single_option_state_validates(self) -> None:
        seed = ds.load(FIXTURES / "bips-2212" / "state-seed.json")
        self.assertEqual(ds.validate(seed), [])
        labels, _options_text, _questions_text = dx.target_catalog(seed)
        self.assertEqual(labels, ["bip-460"])




if __name__ == "__main__":
    unittest.main()
