#!/usr/bin/env python3
"""Tests for Delving Bitcoin topic search and category live collection."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_seed_feed.py"

spec = importlib.util.spec_from_file_location("build_seed_feed", SCRIPT)
assert spec is not None
build_seed_feed = cast(Any, importlib.util.module_from_spec(spec))
assert spec.loader is not None
spec.loader.exec_module(cast(ModuleType, build_seed_feed))

FROST = {
    "id": 99,
    "title": "FROST for threshold signatures",
    "slug": "frost-for-threshold-signatures",
    "created_at": "2026-01-10T12:00:00.000Z",
    "last_posted_at": "2026-08-01T15:00:00.000Z",
    "excerpt": "Using FROST to build threshold Schnorr signing.",
    "tags": [{"id": 7, "name": "musig2", "slug": "musig2"}],
}

GAME = {
    "id": 100,
    "title": "FROST quest minigame",
    "slug": "frost-quest-minigame",
    "created_at": "2026-02-01T12:00:00Z",
    "last_posted_at": "2026-02-02T12:00:00Z",
    "excerpt": "A video game about frost wizards.",
    "tags": [],
}

ABOUT = {
    "id": 876,
    "title": "About the wg-silent-payments category",
    "slug": "about-the-wg-silent-payments-category",
    "created_at": "2024-05-17T10:54:15.019Z",
    "last_posted_at": "2024-05-17T10:54:15.024Z",
    "excerpt": "Category for discussing BIP352 wallet support.",
    "pinned": True,
    "tags": [],
}

SILENT = {
    "id": 2203,
    "title": "Silent Payments notifications via Nostr",
    "slug": "silent-payments-notifications-via-nostr",
    "created_at": "2026-01-15T23:00:09.398Z",
    "last_posted_at": "2026-03-18T06:17:17.971Z",
    "excerpt": "Sending notifications for incoming Silent Payments via Nostr.",
    "tags": [],
}

FROST_WATCH = {
    "relevance": {
        "always_match": [],
        "required_any": ["frost"],
        "context_any": ["threshold", "schnorr", "signature"],
    }
}

EMPTY_WATCH = {"relevance": {"always_match": [], "required_any": [], "context_any": []}}


def _empty_artifacts(out: Path) -> None:
    (out / "feed.json").write_text(json.dumps({"items": []}))
    (out / "projects.json").write_text(json.dumps({"projects": []}))
    (out / "sources.json").write_text(json.dumps({"sources": []}))


def _search_cfg(query: str = "frost in:title", tags=None) -> dict:
    return {
        "seeded_sources": {},
        "live_collectors": {
            "delving_topic_searches": [
                {
                    "id": "delving-frost",
                    "query": query,
                    "tags": tags or ["delving", "candidate", "topic-discovery"],
                }
            ]
        },
    }


def _category_cfg(category: str = "12", tags=None) -> dict:
    return {
        "seeded_sources": {},
        "live_collectors": {
            "delving_category_listings": [
                {
                    "id": "delving-wg",
                    "category": category,
                    "tags": tags or ["delving", "candidate", "topic-discovery"],
                }
            ]
        },
    }


class DelvingLiveCollectorTests(unittest.TestCase):
    def test_empty_relevance_accepts_search_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                items, _projects, _sources = build_seed_feed.build_items(
                    _search_cfg(),
                    delving_search_fetcher=lambda _q: [FROST, GAME],
                    watch={},
                )
                urls = {item["source_url"] for item in items}
                self.assertEqual(len(items), 2)
                self.assertIn("https://delvingbitcoin.org/t/frost-for-threshold-signatures/99", urls)
                self.assertIn("https://delvingbitcoin.org/t/frost-quest-minigame/100", urls)
            finally:
                build_seed_feed.OUT = old_out

    def test_relevance_keeps_protocol_drops_game(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                items, projects, sources = build_seed_feed.build_items(
                    _search_cfg(),
                    delving_search_fetcher=lambda _q: [FROST, GAME],
                    watch=FROST_WATCH,
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["title"], FROST["title"])
                self.assertNotIn("delving-search:delving-frost:100", sources)
                self.assertIn("frost-for-threshold-signatures", projects)
            finally:
                build_seed_feed.OUT = old_out

    def test_search_hit_becomes_candidate_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                items, projects, sources = build_seed_feed.build_items(
                    _search_cfg(tags=["delving", "candidate"]),
                    delving_search_fetcher=lambda _q: [FROST],
                    watch=EMPTY_WATCH,
                )
                self.assertEqual(len(items), 1)
                item = items[0]
                self.assertEqual(item["event_type"], "source_discovered")
                self.assertEqual(item["source_type"], "delving_topic")
                self.assertEqual(item["confidence"], "delving_search")
                self.assertEqual(item["status"], "candidate")
                self.assertEqual(item["discovered_at"], "2026-01-10T12:00:00.000Z")
                self.assertEqual(item["activity_at"], "2026-08-01T15:00:00.000Z")
                self.assertEqual(item["evidence"][0]["query"], "frost in:title")
                self.assertIn("musig2", item["tags"])
                self.assertEqual(item["id"], "delving-search:delving-frost:99")
                self.assertEqual(sources[item["id"]]["confidence"], "delving_search")
                self.assertIn("frost-for-threshold-signatures", projects)
            finally:
                build_seed_feed.OUT = old_out

    def test_about_category_topic_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                items, _projects, _sources = build_seed_feed.build_items(
                    _category_cfg(),
                    delving_category_fetcher=lambda _c: [ABOUT, SILENT],
                    watch={},
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["id"], "delving-search:delving-wg:2203")
                self.assertEqual(items[0]["confidence"], "delving_category")
                self.assertEqual(items[0]["evidence"][0]["category"], "12")
            finally:
                build_seed_feed.OUT = old_out

    def test_negative_query_term_excludes_ethereum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                eth = {
                    **FROST,
                    "id": 101,
                    "title": "FROST on Ethereum",
                    "slug": "frost-on-ethereum",
                    "excerpt": "Threshold signatures for Ethereum.",
                }
                items, _projects, _sources = build_seed_feed.build_items(
                    _search_cfg(query="frost in:title -ethereum"),
                    delving_search_fetcher=lambda _q: [FROST, eth],
                    watch={},
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["id"], "delving-search:delving-frost:99")
            finally:
                build_seed_feed.OUT = old_out

    def test_seeded_delving_url_is_not_rediscovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                cfg = {
                    "seeded_sources": {
                        "docs_pages": [{
                            "id": "seeded-frost",
                            "name": "FROST for threshold signatures",
                            "url": "https://delvingbitcoin.org/t/frost-for-threshold-signatures/99",
                            "project": "FROST",
                            "tags": ["docs"],
                        }]
                    },
                    "live_collectors": {
                        "delving_topic_searches": [{
                            "id": "delving-frost",
                            "query": "frost in:title",
                            "tags": ["candidate"],
                        }]
                    },
                }
                items, _projects, sources = build_seed_feed.build_items(
                    cfg,
                    delving_search_fetcher=lambda _q: [FROST],
                    watch={},
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["event_type"], "source_seeded")
                self.assertNotIn("delving-search:delving-frost:99", sources)
            finally:
                build_seed_feed.OUT = old_out

    def test_skip_searches_does_not_call_delving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)

                def boom(_arg: str) -> list:
                    raise AssertionError("live Delving fetcher should not run")

                items, _projects, _sources = build_seed_feed.build_items(
                    _search_cfg(),
                    delving_search_fetcher=boom,
                    delving_category_fetcher=boom,
                    skip_searches=True,
                    watch={},
                )
                self.assertEqual(items, [])
            finally:
                build_seed_feed.OUT = old_out

    def test_search_groups_topics_and_fills_excerpt_from_blurb(self) -> None:
        payload = {
            "posts": [
                {"id": 1, "topic_id": 99, "post_number": 1, "blurb": "First post blurb about FROST."},
                {"id": 2, "topic_id": 99, "post_number": 2, "blurb": "A later reply."},
            ],
            "topics": [
                {"id": 99, "title": "FROST", "slug": "frost", "created_at": "2026-01-01T00:00:00Z"},
                {"id": 99, "title": "FROST", "slug": "frost", "created_at": "2026-01-01T00:00:00Z"},
            ],
        }
        old = build_seed_feed.delving_get_json

        def fake_get(url: str) -> dict:
            self.assertIn("/search.json?", url)
            return payload

        build_seed_feed.delving_get_json = fake_get
        try:
            topics = build_seed_feed.search_delving_topics("frost", max_results=10)
        finally:
            build_seed_feed.delving_get_json = old
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["id"], 99)
        self.assertEqual(topics[0]["excerpt"], "First post blurb about FROST.")

    def test_category_listing_uses_topic_list(self) -> None:
        old = build_seed_feed.delving_get_json

        def fake_get(url: str) -> dict:
            self.assertEqual(url, "https://delvingbitcoin.org/c/12/l/latest.json")
            return {"topic_list": {"topics": [ABOUT, SILENT, FROST]}}

        build_seed_feed.delving_get_json = fake_get
        try:
            topics = build_seed_feed.list_delving_category("12", max_results=2)
        finally:
            build_seed_feed.delving_get_json = old
        self.assertEqual([t["id"] for t in topics], [876, 2203])


if __name__ == "__main__":
    unittest.main()
