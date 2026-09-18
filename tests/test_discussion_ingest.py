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


if __name__ == "__main__":
    unittest.main()
