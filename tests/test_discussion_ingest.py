#!/usr/bin/env python3
"""Unit tests for discussion ingest adapters."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import discussion_ingest as di

# Recorded fixture data for Discourse
FIXTURE_TOPIC = {
    "id": 100,
    "title": "A Discussion on Proposed Improvements",
    "slug": "proposed-improvements",
    "post_stream": {
        "posts": [
            {
                "id": 1001,
                "post_number": 1,
                "username": "sipa",
                "name": "Pieter Wuille",
                "created_at": "2026-01-01T12:00:00.000Z",
                "updated_at": "2026-01-01T12:00:00.000Z",
                "cooked": "<p>This is the first post outlining proposal A.</p>",
                "reply_to_post_number": None,
            },
            {
                "id": 1002,
                "post_number": 2,
                "username": "conduition",
                "name": "Conduition",
                "created_at": "2026-01-02T10:00:00.000Z",
                "updated_at": "2026-01-02T10:00:00.000Z",
                "cooked": "<blockquote><p>proposal A</p></blockquote><p>I prefer proposal B &amp; C instead.</p>",
                "reply_to_post_number": 1,
            },
        ],
        "stream": [1001, 1002, 1004],  # Note post_number 3 / id 1003 is missing (gap)
    },
}

FIXTURE_POST_1004 = {
    "id": 1004,
    "post_number": 4,
    "username": "fjahr",
    "name": "Fabian Jahr",
    "created_at": "2026-01-03T15:00:00.000Z",
    "updated_at": "2026-01-03T15:00:00.000Z",
    "cooked": "<p>Here is analysis of proposal B.</p>",
    "reply_to_post_number": 2,
}


class TestDiscussionIngestDiscourse(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_html_text_normalization(self) -> None:
        raw_html = (
            "<p>Paragraph 1 with <b>bold</b> text.</p>"
            "<blockquote><p>Quote from someone &amp; friends</p></blockquote>"
            "<p>Paragraph 2<br>with newline and &lt;brackets&gt;.</p>"
        )
        text = di.normalize_html_to_text(raw_html)
        self.assertIn("Paragraph 1 with bold text.", text)
        self.assertIn("Quote from someone & friends", text)
        self.assertIn("Paragraph 2\nwith newline and <brackets>.", text)
        self.assertNotIn("<p>", text)
        self.assertNotIn("<b>", text)
        self.assertNotIn("&amp;", text)

    def test_find_stream_gaps(self) -> None:
        self.assertEqual(di.find_stream_gaps({1, 2, 3}), [])
        self.assertEqual(di.find_stream_gaps({1, 2, 4}), [3])
        self.assertEqual(di.find_stream_gaps({1, 3, 5}), [2, 4])
        self.assertEqual(di.find_stream_gaps(set()), [])

    @mock.patch("discussion_ingest.delving_get_json")
    def test_discourse_ingest_fixture(self, mock_get_json: mock.MagicMock) -> None:
        def side_effect(url: str) -> dict | None:
            if url.endswith("/t/100.json"):
                return copy.deepcopy(FIXTURE_TOPIC)
            if "posts.json" in url or "1004.json" in url:
                return {"post_stream": {"posts": [copy.deepcopy(FIXTURE_POST_1004)]}}
            return None

        mock_get_json.side_effect = side_effect

        output_file = self.out_dir / "posts.jsonl"
        gaps_file = self.out_dir / "gaps.json"

        posts, gaps = di.ingest_discourse("https://delvingbitcoin.org/t/proposed-improvements/100")
        self.assertEqual(len(posts), 3)
        self.assertEqual(gaps, [3])

        final_posts = di.merge_posts_idempotent([], posts)
        di.write_jsonl(output_file, final_posts)
        di.write_gaps_file(gaps_file, gaps)

        # Check output files
        self.assertTrue(output_file.exists())
        self.assertTrue(gaps_file.exists())

        loaded_posts = di.load_existing_jsonl(output_file)
        self.assertEqual(len(loaded_posts), 3)
        self.assertEqual([p["post_number"] for p in loaded_posts], [1, 2, 4])
        self.assertEqual(loaded_posts[0]["author"], "sipa")
        self.assertEqual(loaded_posts[1]["author"], "conduition")
        self.assertEqual(loaded_posts[1]["reply_to_post_number"], 1)
        self.assertEqual(loaded_posts[2]["author"], "fjahr")
        self.assertEqual(loaded_posts[2]["reply_to_post_number"], 2)

        with open(gaps_file, "r", encoding="utf-8") as f:
            gaps_data = json.load(f)
        self.assertEqual(gaps_data, {"gaps": [3]})

    @mock.patch("discussion_ingest.delving_get_json")
    def test_idempotent_rerun_and_hash_rewrite(self, mock_get_json: mock.MagicMock) -> None:
        topic_state = copy.deepcopy(FIXTURE_TOPIC)
        post_1004_state = copy.deepcopy(FIXTURE_POST_1004)

        def side_effect(url: str) -> dict | None:
            if url.endswith("/t/100.json"):
                return copy.deepcopy(topic_state)
            if "posts.json" in url or "1004.json" in url:
                return {"post_stream": {"posts": [copy.deepcopy(post_1004_state)]}}
            return None

        mock_get_json.side_effect = side_effect

        output_file = self.out_dir / "posts.jsonl"
        gaps_file = self.out_dir / "gaps.json"

        # First run
        posts, gaps = di.ingest_discourse("https://delvingbitcoin.org/t/100")
        final_posts = di.merge_posts_idempotent([], posts)
        di.write_jsonl(output_file, final_posts)
        di.write_gaps_file(gaps_file, gaps)

        first_posts = di.load_existing_jsonl(output_file)
        original_hash_2 = first_posts[1]["content_hash"]
        self.assertNotIn("previous_hash", first_posts[1])

        # Second run with no changes
        posts_run2, gaps_run2 = di.ingest_discourse("https://delvingbitcoin.org/t/100")
        merged_run2 = di.merge_posts_idempotent(first_posts, posts_run2)
        di.write_jsonl(output_file, merged_run2)

        second_posts = di.load_existing_jsonl(output_file)
        self.assertEqual(second_posts, first_posts)

        # Third run: simulate post 2 edited
        topic_state["post_stream"]["posts"][1]["cooked"] = (
            "<p>I changed my mind completely: proposal A is best.</p>"
        )
        posts_run3, _ = di.ingest_discourse("https://delvingbitcoin.org/t/100")
        merged_run3 = di.merge_posts_idempotent(second_posts, posts_run3)
        di.write_jsonl(output_file, merged_run3)

        third_posts = di.load_existing_jsonl(output_file)
        self.assertEqual(len(third_posts), 3)
        self.assertNotEqual(third_posts[1]["content_hash"], original_hash_2)
        self.assertEqual(third_posts[1]["previous_hash"], original_hash_2)
        self.assertEqual(third_posts[0]["content_hash"], first_posts[0]["content_hash"])
        self.assertEqual(third_posts[2]["content_hash"], first_posts[2]["content_hash"])

    @mock.patch("discussion_ingest.delving_get_json")
    def test_cli_invocation(self, mock_get_json: mock.MagicMock) -> None:
        def side_effect(url: str) -> dict | None:
            if url.endswith("/t/100.json"):
                return copy.deepcopy(FIXTURE_TOPIC)
            if "posts.json" in url or "1004.json" in url:
                return {"post_stream": {"posts": [copy.deepcopy(FIXTURE_POST_1004)]}}
            return None

        mock_get_json.side_effect = side_effect

        output_file = self.out_dir / "cli_posts.jsonl"
        gaps_file = self.out_dir / "cli_gaps.json"

        with mock.patch(
            "sys.argv",
            [
                "discussion_ingest.py",
                "https://delvingbitcoin.org/t/100",
                "--output",
                str(output_file),
                "--gaps",
                str(gaps_file),
            ],
        ):
            di.main()

        self.assertTrue(output_file.exists())
        self.assertTrue(gaps_file.exists())
        posts = di.load_existing_jsonl(output_file)
        self.assertEqual(len(posts), 3)
        with open(gaps_file, "r", encoding="utf-8") as f:
            gaps = json.load(f)
        self.assertEqual(gaps, {"gaps": [3]})


FIXTURE_GITHUB_ISSUE = {
    "id": 5001,
    "html_url": "https://github.com/bitcoin/bips/pull/2212",
    "created_at": "2026-07-21T20:00:16Z",
    "updated_at": "2026-07-21T20:00:16Z",
    "pull_request": {},
    "user": {"login": "fjahr", "name": "Fabian Jahr"},
    "body": "BIP 460: Cross-input signature aggregation proposal.",
}

FIXTURE_GITHUB_COMMENT = {
    "id": 6001,
    "html_url": "https://github.com/bitcoin/bips/pull/2212#issuecomment-1",
    "created_at": "2026-07-22T10:00:00Z",
    "updated_at": "2026-07-22T10:00:00Z",
    "user": {"login": "sipa", "name": "Pieter Wuille"},
    "body": "General comment on CISA efficiency.",
}

FIXTURE_GITHUB_REVIEW_COMMENT_1 = {
    "id": 7001,
    "html_url": "https://github.com/bitcoin/bips/pull/2212#discussion_r1",
    "created_at": "2026-07-23T11:00:00Z",
    "updated_at": "2026-07-23T11:00:00Z",
    "user": {"login": "ariard", "name": "Antoine Riard"},
    "body": "Line review: check opcode interaction.",
    "in_reply_to_id": None,
}

FIXTURE_GITHUB_REVIEW_COMMENT_2 = {
    "id": 7002,
    "html_url": "https://github.com/bitcoin/bips/pull/2212#discussion_r2",
    "created_at": "2026-07-23T12:00:00Z",
    "updated_at": "2026-07-23T12:00:00Z",
    "user": {"login": "fjahr", "name": "Fabian Jahr"},
    "body": "Good point, opcode interaction addressed in <code>bip-0460.mediawiki</code>.",
    "in_reply_to_id": 7001,
}


class TestDiscussionIngestGitHub(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_parse_github_target(self) -> None:
        self.assertEqual(
            di.parse_github_target("bitcoin/bips#2212"), ("bitcoin", "bips", 2212)
        )
        self.assertEqual(
            di.parse_github_target("https://github.com/bitcoin/bips/pull/2212"),
            ("bitcoin", "bips", 2212),
        )
        self.assertEqual(
            di.parse_github_target("https://github.com/bitcoin/bips/issues/2212"),
            ("bitcoin", "bips", 2212),
        )

    @mock.patch("discussion_ingest.github_get_json")
    def test_github_ingest_fixture(self, mock_get_json: mock.MagicMock) -> None:
        def side_effect(url: str) -> Any:
            if url.endswith("/issues/2212"):
                return copy.deepcopy(FIXTURE_GITHUB_ISSUE)
            if "/issues/2212/comments" in url:
                if "page=1" in url:
                    return [copy.deepcopy(FIXTURE_GITHUB_COMMENT)]
                return []
            if "/pulls/2212/comments" in url:
                if "page=1" in url:
                    return [
                        copy.deepcopy(FIXTURE_GITHUB_REVIEW_COMMENT_1),
                        copy.deepcopy(FIXTURE_GITHUB_REVIEW_COMMENT_2),
                    ]
                return []
            return None

        mock_get_json.side_effect = side_effect

        posts, gaps = di.ingest_github("bitcoin/bips#2212")
        self.assertEqual(len(posts), 4)  # 1 issue + 1 comment + 2 review comments
        self.assertEqual(gaps, [])

        output_file = self.out_dir / "posts.jsonl"
        gaps_file = self.out_dir / "gaps.json"
        di.write_jsonl(output_file, posts)
        di.write_gaps_file(gaps_file, gaps)

        loaded = di.load_existing_jsonl(output_file)
        self.assertEqual(len(loaded), 4)
        self.assertEqual([p["post_number"] for p in loaded], [1, 2, 3, 4])
        self.assertEqual(loaded[0]["author"], "fjahr")
        self.assertIsNone(loaded[0]["reply_to_post_number"])

        self.assertEqual(loaded[1]["author"], "sipa")
        self.assertIsNone(loaded[1]["reply_to_post_number"])

        self.assertEqual(loaded[2]["author"], "ariard")
        self.assertIsNone(loaded[2]["reply_to_post_number"])

        # Review comment 2 replied to review comment 1 (which became post_number 3)
        self.assertEqual(loaded[3]["author"], "fjahr")
        self.assertEqual(loaded[3]["reply_to_post_number"], 3)

        with open(gaps_file, "r", encoding="utf-8") as f:
            gaps_data = json.load(f)
        self.assertEqual(gaps_data, {"gaps": []})

    @mock.patch("discussion_ingest.github_get_json")
    def test_github_idempotency_and_hash_rewrite(self, mock_get_json: mock.MagicMock) -> None:
        issue = copy.deepcopy(FIXTURE_GITHUB_ISSUE)
        comment = copy.deepcopy(FIXTURE_GITHUB_COMMENT)

        def side_effect(url: str) -> Any:
            if url.endswith("/issues/2212"):
                return copy.deepcopy(issue)
            if "/issues/2212/comments" in url:
                if "page=1" in url:
                    return [copy.deepcopy(comment)]
                return []
            if "/pulls/2212/comments" in url:
                return []
            return None

        mock_get_json.side_effect = side_effect

        output_file = self.out_dir / "posts.jsonl"
        posts, gaps = di.ingest_github("bitcoin/bips#2212")
        di.write_jsonl(output_file, posts)

        first_posts = di.load_existing_jsonl(output_file)
        original_hash_1 = first_posts[1]["content_hash"]

        # Simulate comment edited
        comment["body"] = "Updated comment text with new insights."
        posts_run2, _ = di.ingest_github("bitcoin/bips#2212")
        merged = di.merge_posts_idempotent(first_posts, posts_run2)
        di.write_jsonl(output_file, merged)

        second_posts = di.load_existing_jsonl(output_file)
        self.assertEqual(len(second_posts), 2)
        self.assertEqual(second_posts[1]["previous_hash"], original_hash_1)
        self.assertNotEqual(second_posts[1]["content_hash"], original_hash_1)


if __name__ == "__main__":
    unittest.main()
