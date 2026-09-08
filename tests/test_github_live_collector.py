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


SP_WATCH = {
    "relevance": {
        "always_match": ["silent-payments", "silent payments", "silentpayments", "bip352"],
        "required_any": ["silent-payments", "silent payments", "silentpayments", "bip352"],
        "context_any": [],
    }
}

SEEDSIGNER_PR = {
    "html_url": "https://github.com/SeedSigner/seedsigner/pull/949",
    "title": "[New Feature] Silent Payment",
    "body": "This PR adds Silent Payments support to SeedSigner.",
    "number": 949,
    "created_at": "2026-07-13T19:18:57Z",
    "updated_at": "2026-08-24T13:26:56Z",
    "pull_request": {"html_url": "https://github.com/SeedSigner/seedsigner/pull/949"},
    "repository_url": "https://api.github.com/repos/SeedSigner/seedsigner",
}

UNRELATED_PR = {
    "html_url": "https://github.com/example/wallet/pull/1",
    "title": "Fix README typo",
    "body": "Docs only.",
    "number": 1,
    "created_at": "2026-07-01T00:00:00Z",
    "updated_at": "2026-07-02T00:00:00Z",
    "pull_request": {"html_url": "https://github.com/example/wallet/pull/1"},
}


def _pr_collector_cfg(query: str = '"silent payments"', tags=None, seeded=None) -> dict:
    return {
        "seeded_sources": seeded or {},
        "live_collectors": {
            "github_pull_request_searches": [
                {
                    "id": "pr-discovery",
                    "query": query,
                    "tags": tags or ["candidate", "pull-request-discovery"],
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

    def test_pr_search_appends_is_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                queries: list[str] = []

                def fake_fetch(query: str) -> list[dict[str, object]]:
                    queries.append(query)
                    return []

                build_seed_feed.build_items(
                    _pr_collector_cfg(query='"silent payments"'),
                    github_pr_fetcher=fake_fetch,
                    watch=SP_WATCH,
                )
                self.assertEqual(queries, ['"silent payments" is:pr'])
            finally:
                build_seed_feed.OUT = old_out

    def test_pr_search_hit_becomes_candidate_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                items, projects, sources = build_seed_feed.build_items(
                    _pr_collector_cfg(),
                    github_pr_fetcher=lambda _q: [SEEDSIGNER_PR],
                    watch=SP_WATCH,
                )
                self.assertEqual(len(items), 1)
                item = items[0]
                self.assertEqual(item["event_type"], "source_discovered")
                self.assertEqual(item["source_type"], "github_pull_request")
                self.assertEqual(item["status"], "candidate")
                self.assertEqual(item["confidence"], "github_pr_search")
                self.assertEqual(item["title"], "SeedSigner/seedsigner #949")
                self.assertEqual(item["source_url"], SEEDSIGNER_PR["html_url"])
                self.assertEqual(item["discovered_at"], "2026-07-13T19:18:57Z")
                self.assertEqual(item["activity_at"], "2026-08-24T13:26:56Z")
                self.assertEqual(item["summary"], "[New Feature] Silent Payment")
                self.assertEqual(item["id"], "gh-pr-search:pr-discovery:seedsigner-seedsigner-949")
                self.assertEqual(sources[item["id"]]["source_type"], "github_pull_request")
                self.assertIn("seedsigner-seedsigner", projects)
            finally:
                build_seed_feed.OUT = old_out

    def test_seeded_repo_does_not_hide_matching_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                cfg = _pr_collector_cfg(seeded={
                    "github_repositories": [{
                        "id": "seedsigner-seedsigner",
                        "repo": "SeedSigner/seedsigner",
                        "project": "SeedSigner",
                        "tags": ["silent-payments"],
                    }]
                })
                items, projects, sources = build_seed_feed.build_items(
                    cfg,
                    github_pr_fetcher=lambda _q: [SEEDSIGNER_PR],
                    watch=SP_WATCH,
                )
                urls = {item["source_url"] for item in items}
                self.assertIn("https://github.com/SeedSigner/seedsigner", urls)
                self.assertIn(SEEDSIGNER_PR["html_url"], urls)
                pr = next(item for item in items if item["source_type"] == "github_pull_request")
                self.assertEqual(pr["project"], "SeedSigner")
                self.assertEqual(pr["status"], "candidate")
                self.assertIn("seedsigner-seedsigner", projects["seedsigner"]["sources"])
                self.assertIn(pr["id"], projects["seedsigner"]["sources"])
                self.assertIn(pr["id"], sources)
            finally:
                build_seed_feed.OUT = old_out

    def test_seeded_pr_url_is_not_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                cfg = _pr_collector_cfg(seeded={
                    "github_pull_requests": [{
                        "id": "seedsigner-pr-949",
                        "name": "SeedSigner/seedsigner #949",
                        "url": "https://github.com/SeedSigner/seedsigner/pull/949",
                        "project": "SeedSigner",
                        "tags": ["silent-payments", "pull-request"],
                    }]
                })
                items, _projects, sources = build_seed_feed.build_items(
                    cfg,
                    github_pr_fetcher=lambda _q: [SEEDSIGNER_PR],
                    watch=SP_WATCH,
                )
                prs = [item for item in items if item["source_type"] == "github_pull_request"]
                self.assertEqual(len(prs), 1)
                self.assertEqual(prs[0]["id"], "seed:seedsigner-pr-949")
                self.assertEqual(prs[0]["status"], "seeded")
                self.assertNotIn("gh-pr-search:pr-discovery:seedsigner-seedsigner-949", sources)
            finally:
                build_seed_feed.OUT = old_out

    def test_pr_relevance_drops_unrelated_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                items, _projects, _sources = build_seed_feed.build_items(
                    _pr_collector_cfg(),
                    github_pr_fetcher=lambda _q: [SEEDSIGNER_PR, UNRELATED_PR],
                    watch=SP_WATCH,
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["source_url"], SEEDSIGNER_PR["html_url"])
            finally:
                build_seed_feed.OUT = old_out

    def test_pr_search_excludes_negative_query_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                eth_pr = {
                    **SEEDSIGNER_PR,
                    "html_url": "https://github.com/example/sp-eth/pull/3",
                    "title": "Silent Payments on Ethereum",
                    "body": "Port Silent Payments to Ethereum.",
                    "number": 3,
                    "pull_request": {"html_url": "https://github.com/example/sp-eth/pull/3"},
                }
                items, _projects, _sources = build_seed_feed.build_items(
                    _pr_collector_cfg(query='"silent payments" -ethereum'),
                    github_pr_fetcher=lambda _q: [SEEDSIGNER_PR, eth_pr],
                    watch=SP_WATCH,
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["source_url"], SEEDSIGNER_PR["html_url"])
            finally:
                build_seed_feed.OUT = old_out

    def test_ensure_pr_search_query_is_idempotent(self) -> None:
        self.assertEqual(build_seed_feed.ensure_pr_search_query("is:pr"), "is:pr")
        self.assertEqual(
            build_seed_feed.ensure_pr_search_query('"silent payments" is:pr'),
            '"silent payments" is:pr',
        )
        self.assertEqual(
            build_seed_feed.ensure_pr_search_query('"silent payments"'),
            '"silent payments" is:pr',
        )

    def test_repo_search_before_discovered_after_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                old_lib = {
                    "full_name": "21-DOT-DEV/swift-secp256k1",
                    "html_url": "https://github.com/21-DOT-DEV/swift-secp256k1",
                    "description": "secp256k1 bindings with BIP352 helpers",
                    "created_at": "2020-07-05T07:26:05Z",
                    "updated_at": "2026-08-29T12:00:00Z",
                    "topics": ["bip352", "bitcoin"],
                }
                new_lib = {
                    "full_name": "cygnet3/rust-silentpayments",
                    "html_url": "https://github.com/cygnet3/rust-silentpayments",
                    "description": "Silent Payments library",
                    "created_at": "2023-07-11T19:41:51Z",
                    "updated_at": "2026-08-29T13:00:00Z",
                    "topics": ["silent-payments"],
                }

                def fake_fetch(_query: str) -> list[dict[str, object]]:
                    return [old_lib, new_lib]

                items, _projects, _sources = build_seed_feed.build_items(
                    _collector_cfg(query="bip352 archived:false"),
                    github_repo_fetcher=fake_fetch,
                    watch={**SP_WATCH, "discovered_after": "2022-03-13"},
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["source_url"], new_lib["html_url"])
                self.assertEqual(items[0]["discovered_at"], "2023-07-11T19:41:51Z")
            finally:
                build_seed_feed.OUT = old_out

    def test_pr_search_before_discovered_after_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                old_pr = {
                    **SEEDSIGNER_PR,
                    "html_url": "https://github.com/example/wallet/pull/9",
                    "title": "Silent Payments prototype",
                    "body": "Early silent payments experiment.",
                    "number": 9,
                    "created_at": "2021-04-01T00:00:00Z",
                    "updated_at": "2021-04-02T00:00:00Z",
                    "pull_request": {"html_url": "https://github.com/example/wallet/pull/9"},
                }
                items, _projects, _sources = build_seed_feed.build_items(
                    _pr_collector_cfg(),
                    github_pr_fetcher=lambda _q: [old_pr, SEEDSIGNER_PR],
                    watch={**SP_WATCH, "discovered_after": "2022-03-13"},
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["source_url"], SEEDSIGNER_PR["html_url"])
            finally:
                build_seed_feed.OUT = old_out




if __name__ == "__main__":
    unittest.main()
