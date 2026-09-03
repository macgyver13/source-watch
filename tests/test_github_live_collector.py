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


FROSTED_POOP = {
    "full_name": "sethabout3653-sketch/frosted-poop",
    "html_url": "https://github.com/sethabout3653-sketch/frosted-poop",
    "description": "A small game project with a frosty theme",
    "updated_at": "2026-08-29T12:00:00Z",
    "topics": ["game", "winter"],
}
KONCLAVE = {
    "full_name": "deegalabs/konclave",
    "html_url": "https://github.com/deegalabs/konclave",
    "description": "A local-first collective Zcash treasury using FROST threshold signatures.",
    "updated_at": "2026-08-29T13:00:00Z",
    "topics": ["frost", "zcash", "threshold-signatures", "dkg"],
}
CHILLDKG = {
    "full_name": "example/chilldkg-notes",
    "html_url": "https://github.com/example/chilldkg-notes",
    "description": "Notes on chilldkg without mentioning the required protocol name.",
    "updated_at": "2026-08-29T14:00:00Z",
    "topics": ["dkg"],
}

FROST_WATCH = {
    "relevance": {
        "always_match": ["chilldkg"],
        "required_any": ["frost"],
        "context_any": [
            "threshold",
            "threshold-signatures",
            "dkg",
            "zcash",
            "taproot",
            "multisig",
            "cryptograph",
            "mpc",
            "secp256k1",
            "schnorr",
            "tss",
        ],
    }
}

EMPTY_WATCH = {"relevance": {"always_match": [], "required_any": [], "context_any": []}}


def _empty_artifacts(out: Path) -> None:
    (out / "feed.json").write_text(json.dumps({"items": []}))
    (out / "projects.json").write_text(json.dumps({"projects": []}))
    (out / "sources.json").write_text(json.dumps({"sources": []}))


def _collector_cfg(query: str = "FROST archived:false", tags=None) -> dict:
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
                    return [FROSTED_POOP, KONCLAVE]

                items, _projects, _sources = build_seed_feed.build_items(
                    _collector_cfg(),
                    github_repo_fetcher=fake_fetch,
                    watch={},
                )
                urls = {item["source_url"] for item in items}
                self.assertEqual(len(items), 2)
                self.assertEqual(urls, {FROSTED_POOP["html_url"], KONCLAVE["html_url"]})
            finally:
                build_seed_feed.OUT = old_out

    def test_required_any_and_context_keep_konclave_drop_game(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)

                def fake_fetch(_query: str) -> list[dict[str, object]]:
                    return [FROSTED_POOP, KONCLAVE]

                items, projects, sources = build_seed_feed.build_items(
                    _collector_cfg(),
                    github_repo_fetcher=fake_fetch,
                    watch=FROST_WATCH,
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["source_url"], KONCLAVE["html_url"])
                self.assertNotIn("gh-search:repo-discovery:sethabout3653-sketch-frosted-poop", sources)
                self.assertIn("deegalabs-konclave", projects)
            finally:
                build_seed_feed.OUT = old_out

    def test_always_match_chilldkg_short_circuits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)

                def fake_fetch(_query: str) -> list[dict[str, object]]:
                    return [FROSTED_POOP, CHILLDKG]

                items, _projects, _sources = build_seed_feed.build_items(
                    _collector_cfg(),
                    github_repo_fetcher=fake_fetch,
                    watch=FROST_WATCH,
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["source_url"], CHILLDKG["html_url"])
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
                    query='FROST "threshold signature" dkg archived:false -ethereum',
                    tags=["frost", "candidate"],
                )

                def fake_fetch(_query: str) -> list[dict[str, object]]:
                    return [
                        {
                            "full_name": "example/frost-mpc-wallet",
                            "html_url": "https://github.com/example/frost-mpc-wallet",
                            "description": "FROST threshold-signature wallet for Ethereum and Bitcoin",
                            "updated_at": "2026-08-29T12:00:00Z",
                            "topics": ["frost", "ethereum", "wallet"],
                        },
                        {
                            "full_name": "example/frost-dkg",
                            "html_url": "https://github.com/example/frost-dkg",
                            "description": "Threshold signing with FROST DKG",
                            "updated_at": "2026-08-29T13:00:00Z",
                            "topics": ["frost", "dkg", "threshold-signatures"],
                        },
                    ]

                items, projects, sources = build_seed_feed.build_items(
                    cfg,
                    github_repo_fetcher=fake_fetch,
                    watch=FROST_WATCH,
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["source_url"], "https://github.com/example/frost-dkg")
                self.assertNotIn("gh-search:repo-discovery:example-frost-mpc-wallet", sources)
                self.assertNotIn("example-frost-mpc-wallet", projects)
            finally:
                build_seed_feed.OUT = old_out

    def test_github_repository_search_results_become_feed_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                cfg = _collector_cfg(query="frost dkg", tags=["frost", "candidate"])

                def fake_fetch(_query: str) -> list[dict[str, object]]:
                    return [
                        {
                            "full_name": "example/frost-dkg",
                            "html_url": "https://github.com/example/frost-dkg",
                            "description": "Threshold signing with FROST DKG",
                            "updated_at": "2026-08-29T12:00:00Z",
                            "created_at": "2026-08-20T12:00:00Z",
                            "topics": ["frost", "dkg", "threshold-signatures"],
                        }
                    ]

                items, projects, sources = build_seed_feed.build_items(
                    cfg,
                    github_repo_fetcher=fake_fetch,
                    watch=FROST_WATCH,
                )
                self.assertEqual(len(items), 1)
                item = items[0]
                self.assertEqual(item["event_type"], "source_discovered")
                self.assertEqual(item["source_type"], "github_repository")
                self.assertEqual(item["source_url"], "https://github.com/example/frost-dkg")
                self.assertEqual(item["project"], "example/frost-dkg")
                self.assertEqual(item["confidence"], "github_search")
                self.assertEqual(item["status"], "candidate")
                self.assertEqual(item["discovered_at"], "2026-08-20T12:00:00Z")
                self.assertEqual(item["activity_at"], "2026-08-29T12:00:00Z")
                self.assertIn("candidate", item["tags"])
                self.assertIn("threshold-signatures", item["tags"])
                self.assertTrue(item["id"].startswith("gh-search:repo-discovery:example-frost-dkg"))
                self.assertEqual(sources["gh-search:repo-discovery:example-frost-dkg"]["confidence"], "github_search")
                self.assertIn("example-frost-dkg", projects)
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
                        "id": "gh-search:repo-discovery:example-frost-dkg",
                        "discovered_at": "2026-08-25T00:00:00Z",
                        "event_time": "2026-08-25T00:00:00Z",
                    }]
                }))
                (out / "projects.json").write_text(json.dumps({"projects": []}))
                (out / "sources.json").write_text(json.dumps({
                    "sources": [{
                        "id": "gh-search:repo-discovery:example-frost-dkg",
                        "discovered_at": "2026-08-25T00:00:00Z",
                        "first_seen": "2026-08-25T00:00:00Z",
                    }]
                }))
                cfg = _collector_cfg(query="frost dkg", tags=["frost", "candidate"])

                def fake_fetch(_query: str) -> list[dict[str, object]]:
                    return [
                        {
                            "full_name": "example/frost-dkg",
                            "html_url": "https://github.com/example/frost-dkg",
                            "description": "Threshold signing with FROST DKG",
                            "updated_at": "2026-08-29T12:00:00Z",
                            "created_at": "2026-08-20T12:00:00Z",
                            "topics": ["frost", "dkg"],
                        }
                    ]

                items, _projects, sources = build_seed_feed.build_items(
                    cfg,
                    github_repo_fetcher=fake_fetch,
                    watch=FROST_WATCH,
                )
                self.assertEqual(items[0]["discovered_at"], "2026-08-20T12:00:00Z")
                self.assertEqual(items[0]["event_time"], "2026-08-20T12:00:00Z")
                self.assertEqual(
                    sources["gh-search:repo-discovery:example-frost-dkg"]["discovered_at"],
                    "2026-08-20T12:00:00Z",
                )
            finally:
                build_seed_feed.OUT = old_out

    def test_repo_matches_relevance_rules_direct(self) -> None:
        self.assertTrue(build_seed_feed.repo_matches_relevance_rules(FROSTED_POOP, {}))
        self.assertTrue(build_seed_feed.repo_matches_relevance_rules(FROSTED_POOP, EMPTY_WATCH))
        self.assertTrue(build_seed_feed.repo_matches_relevance_rules(
            {"full_name": "foo/frosted-poop", "description": "", "topics": []},
            {},
        ))
        self.assertFalse(build_seed_feed.repo_matches_relevance_rules(FROSTED_POOP, FROST_WATCH))
        self.assertTrue(build_seed_feed.repo_matches_relevance_rules(KONCLAVE, FROST_WATCH))
        self.assertTrue(build_seed_feed.repo_matches_relevance_rules(CHILLDKG, FROST_WATCH))
        self.assertFalse(build_seed_feed.repo_matches_relevance_rules(
            {"description": "", "topics": []},
            {"relevance": {"required_any": ["frost"]}},
        ))
        self.assertTrue(build_seed_feed.repo_matches_relevance_rules(
            {"description": "", "topics": []},
            {"relevance": {"required_any": []}},
        ))


if __name__ == "__main__":
    unittest.main()
