#!/usr/bin/env python3
"""Tests for serving.mode: service collector config, exclusions, and ingest gates."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

from typing import Any, cast
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_seed_feed.py"

spec = importlib.util.spec_from_file_location("build_seed_feed", SCRIPT)
assert spec is not None
build_seed_feed = cast(Any, importlib.util.module_from_spec(spec))
assert spec.loader is not None
spec.loader.exec_module(cast(ModuleType, build_seed_feed))

NOISE_REPO = {
    "full_name": "acme/noise",
    "html_url": "https://github.com/acme/noise",
    "description": "A noisy matching repo",
    "updated_at": "2026-08-29T12:00:00Z",
    "topics": ["atlas"],
}
KEEP_REPO = {
    "full_name": "acme/atlas-spec",
    "html_url": "https://github.com/acme/atlas-spec",
    "description": "Specification and docs for the atlas protocol",
    "updated_at": "2026-08-29T13:00:00Z",
    "topics": ["atlas", "docs"],
}


def _empty_artifacts(out: Path) -> None:
    (out / "feed.json").write_text(json.dumps({"items": []}))
    (out / "projects.json").write_text(json.dumps({"projects": []}))
    (out / "sources.json").write_text(json.dumps({"sources": []}))


class ServingConfigTests(unittest.TestCase):
    def test_bogus_mode_exits(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            build_seed_feed.normalize_serving({"mode": "bogus"})
        self.assertIn("serving.mode", str(raised.exception))

    def test_service_without_url_exits(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            build_seed_feed.normalize_serving({"mode": "service"})
        self.assertIn("service_url", str(raised.exception))

    def test_service_url_gains_trailing_slash(self) -> None:
        serving = build_seed_feed.normalize_serving({
            "mode": "service",
            "service_url": "https://watch.example.workers.dev",
        })
        self.assertEqual(serving["mode"], "service")
        self.assertEqual(serving["service_url"], "https://watch.example.workers.dev/")


class ServiceConfigMergeTests(unittest.TestCase):
    def test_apply_service_config_appends_terms_and_overrides_floor(self) -> None:
        watch = build_seed_feed.normalize_watch({
            "relevance": {
                "always_match": [],
                "required_any": ["Lattice"],
                "context_any": [],
            },
            "discovered_after": "2024-01-01T00:00:00Z",
        })
        remote = {
            "include_terms": [
                {"bucket": "required_any", "term": "lattice"},
                {"bucket": "required_any", "term": "lattice"},
                {"bucket": "required_any", "term": "isogeny"},
                {"bucket": "nope", "term": "ignored"},
            ],
            "settings": {"discovered_after": "2026-02-01T00:00:00Z"},
        }
        merged = build_seed_feed.apply_service_config(watch, remote)
        self.assertEqual(merged["relevance"]["required_any"], ["Lattice", "isogeny"])
        self.assertEqual(merged["discovered_after"], "2026-02-01T00:00:00Z")
        self.assertEqual(watch["relevance"]["required_any"], ["Lattice"])
        self.assertEqual(watch["discovered_after"], "2024-01-01T00:00:00Z")

    def test_merge_seed_additions_appends_new_and_skips_existing_url(self) -> None:
        cfg = {
            "seeded_sources": {
                "github_repositories": [{
                    "id": "existing",
                    "url": "https://github.com/acme/keep",
                    "repo": "acme/keep",
                }]
            }
        }
        merged = build_seed_feed.merge_seed_additions(cfg, [
            {"kind": "github_repositories", "entry": {
                "id": "new-repo",
                "url": "https://github.com/acme/new",
                "repo": "acme/new",
            }},
            {"kind": "github_repositories", "entry": {
                "id": "duplicate-url",
                "url": "https://github.com/acme/keep",
                "repo": "acme/keep",
            }},
        ])
        repos = merged["seeded_sources"]["github_repositories"]
        self.assertEqual(len(repos), 2)
        self.assertEqual(repos[1]["id"], "new-repo")
        self.assertEqual(len(cfg["seeded_sources"]["github_repositories"]), 1)

    def test_merge_seed_additions_skips_same_repo_or_crate_name(self) -> None:
        cfg = {
            "seeded_sources": {
                "github_repositories": [{"id": "keep", "repo": "acme/keep"}],
                "crates": [{"id": "old-crate", "name": "foo-bar"}],
            }
        }
        merged = build_seed_feed.merge_seed_additions(cfg, [
            {"kind": "github_repositories", "entry": {
                "id": "other-id",
                "url": "https://github.com/acme/keep",
            }},
            {"kind": "crates", "entry": {"id": "new-crate", "name": "foo-bar"}},
            {"kind": "crates", "entry": {"id": "fresh", "name": "baz"}},
        ])
        self.assertEqual(len(merged["seeded_sources"]["github_repositories"]), 1)
        crates = merged["seeded_sources"]["crates"]
        self.assertEqual([c["id"] for c in crates], ["old-crate", "fresh"])



class ServiceExclusionTests(unittest.TestCase):
    def test_live_repo_exclusion_drops_noise_keeps_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)

                def fake_fetch(_query: str) -> list[dict[str, object]]:
                    return [NOISE_REPO, KEEP_REPO]

                items, _projects, sources = build_seed_feed.build_items(
                    {
                        "seeded_sources": {},
                        "live_collectors": {
                            "github_repository_searches": [{
                                "id": "repo-discovery",
                                "query": "atlas archived:false",
                                "tags": ["candidate"],
                            }]
                        },
                    },
                    github_repo_fetcher=fake_fetch,
                    watch={},
                    exclusions=[{"kind": "repo", "value": "acme/noise"}],
                )
                urls = {item["source_url"] for item in items}
                self.assertEqual(urls, {KEEP_REPO["html_url"]})
                self.assertNotIn("acme/noise", " ".join(sources))
            finally:
                build_seed_feed.OUT = old_out

    def test_seeded_repo_is_not_dropped_by_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                _empty_artifacts(out)
                items, _projects, _sources = build_seed_feed.build_items(
                    {
                        "seeded_sources": {
                            "github_repositories": [{
                                "id": "acme-noise",
                                "repo": "acme/noise",
                                "project": "Noise",
                                "tags": ["atlas"],
                            }]
                        },
                        "live_collectors": {
                            "github_repository_searches": [{
                                "id": "repo-discovery",
                                "query": "atlas archived:false",
                                "tags": ["candidate"],
                            }]
                        },
                    },
                    github_repo_fetcher=lambda _q: [],
                    watch={},
                    exclusions=[{"kind": "repo", "value": "acme/noise"}],
                )
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["id"], "seed:acme-noise")
            finally:
                build_seed_feed.OUT = old_out

    def test_existing_loader_replaces_file_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                items, _projects, _sources = build_seed_feed.build_items(
                    {"seeded_sources": {"docs_pages": [{
                        "id": "x",
                        "name": "Example docs",
                        "url": "https://docs.example.com/x",
                        "project": "Example",
                        "tags": ["docs"],
                    }]}},
                    existing_loader=lambda: (
                        {"seed:x": {
                            "id": "seed:x",
                            "discovered_at": "2025-01-02T00:00:00Z",
                            "event_time": "2025-01-02T00:00:00Z",
                        }},
                        {},
                        {},
                    ),
                )
                self.assertEqual(items[0]["discovered_at"], "2025-01-02T00:00:00Z")
            finally:
                build_seed_feed.OUT = old_out

    def test_repo_exclusion_does_not_match_name_prefix(self) -> None:
        self.assertTrue(build_seed_feed.repo_rule_matches(
            "https://github.com/acme/foo", "acme/foo",
        ))
        self.assertTrue(build_seed_feed.repo_rule_matches(
            "https://github.com/acme/foo/pull/1", "acme/foo",
        ))
        self.assertFalse(build_seed_feed.repo_rule_matches(
            "https://github.com/acme/foobar", "acme/foo",
        ))
        self.assertFalse(build_seed_feed.excluded_by_service(
            [{"kind": "repo", "value": "acme/foo"}],
            haystack="acme/foobar atlas",
            url="https://github.com/acme/foobar",
            project="Foobar",
            source_type="github_repository",
        ))



class ServiceMainGateTests(unittest.TestCase):
    def test_seed_only_without_allow_partial_ingest_exits(self) -> None:
        watch = build_seed_feed.normalize_watch({
            "serving": {"mode": "service", "service_url": "http://localhost:8787/"},
        })
        old_argv = sys.argv
        try:
            sys.argv = ["build_seed_feed.py", "--seed-only"]
            with mock.patch.object(build_seed_feed, "load_watch", return_value=watch):
                with mock.patch.object(build_seed_feed, "ServiceClient") as client_cls:
                    with self.assertRaises(SystemExit) as raised:
                        build_seed_feed.main()
            self.assertIn("--allow-partial-ingest", str(raised.exception))
            client_cls.assert_not_called()
        finally:
            sys.argv = old_argv


if __name__ == "__main__":
    unittest.main()
