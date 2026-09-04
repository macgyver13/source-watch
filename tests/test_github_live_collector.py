#!/usr/bin/env python3
"""Tests for GitHub search-backed live collection and config-driven relevance."""
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


ATLAS_QUEST = {
    "full_name": "example/atlas-quest",
    "html_url": "https://github.com/example/atlas-quest",
    "description": "A small game with an atlas theme",
    "updated_at": "2026-08-29T12:00:00Z",
    "topics": ["game", "rpg"],
}
ATLAS_SPEC = {
    "full_name": "example/atlas-spec",
    "html_url": "https://github.com/example/atlas-spec",
    "description": "Specification and docs for the atlas protocol",
    "updated_at": "2026-08-29T13:00:00Z",
    "topics": ["docs", "specification"],
}
QUICKSTART = {
    "full_name": "example/quickstart-notes",
    "html_url": "https://github.com/example/quickstart-notes",
    "description": "Notes on quickstart without mentioning the required protocol name.",
    "updated_at": "2026-08-29T14:00:00Z",
    "topics": ["notes"],
}

ATLAS_WATCH = {
    "relevance": {
        "always_match": ["quickstart"],
        "required_any": ["atlas"],
        "context_any": [
            "docs",
            "specification",
            "guide",
        ],
    }
}

EMPTY_WATCH = {"relevance": {"always_match": [], "required_any": [], "context_any": []}}


def _empty_artifacts(out: Path) -> None:
    (out / "feed.json").write_text(json.dumps({"items": []}))
    (out / "projects.json").write_text(json.dumps({"projects": []}))
    (out / "sources.json").write_text(json.dumps({"sources": []}))


def _collector_cfg(query: str = "atlas archived:false", tags=None) -> dict:
    return {
        "seeded_sources": {},
        "live_collectors": {
            "github_repository_searches": [
                {
                    "id": "repo-discovery",
                    "query": query,
                    "tags": tags or ["candidate"],
                }
            ]
        },
    }


class GitHubLiveCollectorTests(unittest.TestCase):
    def test_empty_relevance_accepts_both_fake_repos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)

                def fake_fetch(_query: str) -> list[dict[str, object]]:
                    return [ATLAS_QUEST, ATLAS_SPEC]

                items, _projects, _sources = build_seed_feed.build_items(
                    _collector_cfg(),
                    github_repo_fetcher=fake_fetch,
                    watch={},
                )
                urls = {item["source_url"] for item in items}
                self.assertEqual(len(items), 2)
                self.assertEqual(urls, {ATLAS_QUEST["html_url"], ATLAS_SPEC["html_url"]})
            finally:
                build_seed_feed.OUT = old_out

    def test_required_any_and_context_keep_spec_drop_game(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)

                def fake_fetch(_query: str) -> list[dict[str, object]]:
                    return [ATLAS_QUEST, ATLAS_SPEC]

                items, projects, sources = build_seed_feed.build_items(
                    _collector_cfg(),
                    github_repo_fetcher=fake_fetch,
                    watch=ATLAS_WATCH,
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["source_url"], ATLAS_SPEC["html_url"])
                self.assertNotIn("gh-search:repo-discovery:example-atlas-quest", sources)
                self.assertIn("example-atlas-spec", projects)
            finally:
                build_seed_feed.OUT = old_out

    def test_always_match_quickstart_short_circuits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)

                def fake_fetch(_query: str) -> list[dict[str, object]]:
                    return [ATLAS_QUEST, QUICKSTART]

                items, _projects, _sources = build_seed_feed.build_items(
                    _collector_cfg(),
                    github_repo_fetcher=fake_fetch,
                    watch=ATLAS_WATCH,
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["source_url"], QUICKSTART["html_url"])
            finally:
                build_seed_feed.OUT = old_out

    def test_github_repository_search_excludes_ethereum_matches_after_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                cfg = _collector_cfg(
                    query='atlas specification archived:false -ethereum',
                    tags=["docs", "candidate"],
                )

                def fake_fetch(_query: str) -> list[dict[str, object]]:
                    return [
                        {
                            "full_name": "example/atlas-eth-wallet",
                            "html_url": "https://github.com/example/atlas-eth-wallet",
                            "description": "Atlas wallet for Ethereum",
                            "updated_at": "2026-08-29T12:00:00Z",
                            "topics": ["atlas", "ethereum", "wallet"],
                        },
                        {
                            "full_name": "example/atlas-docs",
                            "html_url": "https://github.com/example/atlas-docs",
                            "description": "Atlas specification and docs",
                            "updated_at": "2026-08-29T13:00:00Z",
                            "topics": ["atlas", "docs", "specification"],
                        },
                    ]

                items, projects, sources = build_seed_feed.build_items(
                    cfg,
                    github_repo_fetcher=fake_fetch,
                    watch=ATLAS_WATCH,
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["source_url"], "https://github.com/example/atlas-docs")
                self.assertNotIn("gh-search:repo-discovery:example-atlas-eth-wallet", sources)
                self.assertNotIn("example-atlas-eth-wallet", projects)
            finally:
                build_seed_feed.OUT = old_out

    def test_github_repository_search_results_become_feed_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                cfg = _collector_cfg(query="atlas docs", tags=["docs", "candidate"])

                def fake_fetch(_query: str) -> list[dict[str, object]]:
                    return [
                        {
                            "full_name": "example/atlas-spec",
                            "html_url": "https://github.com/example/atlas-spec",
                            "description": "Specification and docs for the atlas protocol",
                            "updated_at": "2026-08-29T12:00:00Z",
                            "created_at": "2026-08-20T12:00:00Z",
                            "topics": ["docs", "specification"],
                        }
                    ]

                items, projects, sources = build_seed_feed.build_items(
                    cfg,
                    github_repo_fetcher=fake_fetch,
                    watch=ATLAS_WATCH,
                )
                self.assertEqual(len(items), 1)
                item = items[0]
                self.assertEqual(item["event_type"], "source_discovered")
                self.assertEqual(item["source_type"], "github_repository")
                self.assertEqual(item["source_url"], "https://github.com/example/atlas-spec")
                self.assertEqual(item["project"], "example/atlas-spec")
                self.assertEqual(item["confidence"], "github_search")
                self.assertEqual(item["status"], "candidate")
                self.assertEqual(item["discovered_at"], "2026-08-20T12:00:00Z")
                self.assertEqual(item["activity_at"], "2026-08-29T12:00:00Z")
                self.assertIn("candidate", item["tags"])
                self.assertIn("specification", item["tags"])
                self.assertTrue(item["id"].startswith("gh-search:repo-discovery:example-atlas-spec"))
                self.assertEqual(sources["gh-search:repo-discovery:example-atlas-spec"]["confidence"], "github_search")
                self.assertIn("example-atlas-spec", projects)
            finally:
                build_seed_feed.OUT = old_out

    def test_github_created_at_is_the_discovery_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                (out / "feed.json").write_text(json.dumps({
                    "items": [{
                        "id": "gh-search:repo-discovery:example-atlas-spec",
                        "discovered_at": "2026-08-25T00:00:00Z",
                        "event_time": "2026-08-25T00:00:00Z",
                    }]
                }))
                (out / "projects.json").write_text(json.dumps({"projects": []}))
                (out / "sources.json").write_text(json.dumps({
                    "sources": [{
                        "id": "gh-search:repo-discovery:example-atlas-spec",
                        "discovered_at": "2026-08-25T00:00:00Z",
                        "first_seen": "2026-08-25T00:00:00Z",
                    }]
                }))
                cfg = _collector_cfg(query="atlas docs", tags=["docs", "candidate"])

                def fake_fetch(_query: str) -> list[dict[str, object]]:
                    return [
                        {
                            "full_name": "example/atlas-spec",
                            "html_url": "https://github.com/example/atlas-spec",
                            "description": "Specification and docs for the atlas protocol",
                            "updated_at": "2026-08-29T12:00:00Z",
                            "created_at": "2026-08-20T12:00:00Z",
                            "topics": ["docs"],
                        }
                    ]

                items, _projects, sources = build_seed_feed.build_items(
                    cfg,
                    github_repo_fetcher=fake_fetch,
                    watch=ATLAS_WATCH,
                )
                self.assertEqual(items[0]["discovered_at"], "2026-08-20T12:00:00Z")
                self.assertEqual(items[0]["event_time"], "2026-08-20T12:00:00Z")
                self.assertEqual(
                    sources["gh-search:repo-discovery:example-atlas-spec"]["discovered_at"],
                    "2026-08-20T12:00:00Z",
                )
            finally:
                build_seed_feed.OUT = old_out

    def test_repo_matches_relevance_rules_direct(self) -> None:
        self.assertTrue(build_seed_feed.repo_matches_relevance_rules(ATLAS_QUEST, {}))
        self.assertTrue(build_seed_feed.repo_matches_relevance_rules(ATLAS_QUEST, EMPTY_WATCH))
        self.assertTrue(build_seed_feed.repo_matches_relevance_rules(
            {"full_name": "foo/atlas-quest", "description": "", "topics": []},
            {},
        ))
        self.assertFalse(build_seed_feed.repo_matches_relevance_rules(ATLAS_QUEST, ATLAS_WATCH))
        self.assertTrue(build_seed_feed.repo_matches_relevance_rules(ATLAS_SPEC, ATLAS_WATCH))
        self.assertTrue(build_seed_feed.repo_matches_relevance_rules(QUICKSTART, ATLAS_WATCH))
        self.assertFalse(build_seed_feed.repo_matches_relevance_rules(
            {"description": "", "topics": []},
            {"relevance": {"required_any": ["atlas"]}},
        ))
        self.assertTrue(build_seed_feed.repo_matches_relevance_rules(
            {"description": "", "topics": []},
            {"relevance": {"required_any": []}},
        ))


if __name__ == "__main__":
    unittest.main()
