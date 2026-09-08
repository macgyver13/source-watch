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



if __name__ == "__main__":
    unittest.main()
