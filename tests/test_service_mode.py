#!/usr/bin/env python3
"""Tests for serving.mode: service collector config, exclusions, and ingest gates."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from io import BytesIO
from urllib.error import HTTPError
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

    def test_http_non_loopback_exits(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            build_seed_feed.normalize_serving({
                "mode": "service",
                "service_url": "http://watch.example.com/",
            })
        self.assertIn("https", str(raised.exception))

    def test_http_loopback_and_https_accepted(self) -> None:
        local = build_seed_feed.normalize_serving({
            "mode": "service",
            "service_url": "http://localhost:8787/",
        })
        self.assertEqual(local["mode"], "service")
        remote = build_seed_feed.normalize_serving({
            "mode": "Service ",
            "service_url": "https://x/",
        })
        self.assertEqual(remote["mode"], "service")
        self.assertEqual(remote["service_url"], "https://x/")


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

    def test_apply_service_config_empty_floor_clears_yaml(self) -> None:
        watch = build_seed_feed.normalize_watch({
            "discovered_after": "2024-01-01T00:00:00Z",
        })
        merged = build_seed_feed.apply_service_config(watch, {"settings": {"discovered_after": ""}})
        self.assertEqual(merged["discovered_after"], "")
        unchanged = build_seed_feed.apply_service_config(watch, {"settings": {}})
        self.assertEqual(unchanged["discovered_after"], "2024-01-01T00:00:00Z")


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

    def test_merge_seed_additions_skips_id_from_another_kind(self) -> None:
        cfg = {
            "seeded_sources": {
                "github_repositories": [{"id": "shared", "repo": "acme/keep"}],
            }
        }
        merged = build_seed_feed.merge_seed_additions(cfg, [
            {"kind": "docs_pages", "entry": {
                "id": "shared",
                "url": "https://example.com/docs",
            }},
        ])
        self.assertNotIn("docs_pages", merged["seeded_sources"])
        self.assertEqual(len(merged["seeded_sources"]["github_repositories"]), 1)




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

    def test_empty_remote_state_falls_back_to_local_artifacts(self) -> None:
        local = ({"seed:x": {"id": "seed:x", "discovered_at": "2025-01-02T00:00:00Z"}}, {}, {})
        empty = build_seed_feed.existing_state_loader(lambda: ({}, {}, {}), lambda: local)
        self.assertEqual(empty()[0]["seed:x"]["discovered_at"], "2025-01-02T00:00:00Z")
        remote = ({"seed:y": {"id": "seed:y"}}, {}, {})
        live = build_seed_feed.existing_state_loader(lambda: remote, lambda: local)
        self.assertEqual(live()[0], remote[0])


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
        self.assertFalse(build_seed_feed.repo_rule_matches(
            "https://notgithub.com/acme/foo", "acme/foo",
        ))
        self.assertFalse(build_seed_feed.repo_rule_matches(
            "https://evil.example/github.com/acme/foo", "acme/foo",
        ))

        self.assertFalse(build_seed_feed.excluded_by_service(
            [{"kind": "repo", "value": "acme/foo"}],
            haystack="acme/foobar atlas",
            url="https://github.com/acme/foobar",
            project="Foobar",
            source_type="github_repository",
        ))

    def test_url_prefix_does_not_match_longer_pull_number(self) -> None:
        rules = [{"kind": "url_prefix", "value": "https://github.com/acme/lib/pull/12"}]
        self.assertTrue(build_seed_feed.url_prefix_matches(
            "https://github.com/acme/lib/pull/12", rules[0]["value"],
        ))
        self.assertFalse(build_seed_feed.url_prefix_matches(
            "https://github.com/acme/lib/pull/120", rules[0]["value"],
        ))
        self.assertFalse(build_seed_feed.excluded_by_service(
            rules,
            haystack="pr",
            url="https://github.com/acme/lib/pull/120",
            project="Lib",
            source_type="github_pull_request",
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


def _http_error(url: str, code: int, msg: str) -> HTTPError:
    err = HTTPError(url, code, msg, hdrs=None, fp=BytesIO())
    err.close()
    return err

class SeedGitHubSoftFailTests(unittest.TestCase):
    def tearDown(self) -> None:
        build_seed_feed.COLLECTOR_FAILURES.clear()

    def test_github_get_json_404_does_not_note_collector_failure(self) -> None:
        err = _http_error("https://api.github.com/repos/missing/gone", 404, "Not Found")
        with mock.patch.object(build_seed_feed, "urlopen", side_effect=err):
            payload = build_seed_feed.github_get_json("/repos/missing/gone")
        self.assertIsNone(payload)
        self.assertEqual(build_seed_feed.COLLECTOR_FAILURES, [])

    def test_github_get_json_timeout_does_not_note_collector_failure(self) -> None:
        with mock.patch.object(build_seed_feed, "urlopen", side_effect=TimeoutError("timed out")):
            payload = build_seed_feed.github_get_json("/repos/acme/keep")
        self.assertIsNone(payload)
        self.assertEqual(build_seed_feed.COLLECTOR_FAILURES, [])

    def test_github_repo_search_failure_notes_collector_failure(self) -> None:
        err = _http_error("https://api.github.com/search/repositories", 502, "Bad Gateway")
        with mock.patch.object(build_seed_feed, "urlopen", side_effect=err):
            self.assertEqual(build_seed_feed.search_github_repositories("atlas"), [])
        self.assertEqual(len(build_seed_feed.COLLECTOR_FAILURES), 1)
        self.assertIn("GitHub repository search failed", build_seed_feed.COLLECTOR_FAILURES[0])

    def test_github_pr_search_failure_notes_collector_failure(self) -> None:
        err = _http_error("https://api.github.com/search/issues", 502, "Bad Gateway")
        with mock.patch.object(build_seed_feed, "urlopen", side_effect=err):
            self.assertEqual(build_seed_feed.search_github_pull_requests("atlas is:pr"), [])
        self.assertEqual(len(build_seed_feed.COLLECTOR_FAILURES), 1)
        self.assertIn("GitHub pull request search failed", build_seed_feed.COLLECTOR_FAILURES[0])

    def test_delving_get_json_failure_notes_collector_failure(self) -> None:
        err = _http_error("https://delvingbitcoin.org/search.json", 502, "Bad Gateway")
        with mock.patch.object(build_seed_feed, "urlopen", side_effect=err):
            self.assertIsNone(build_seed_feed.delving_get_json("https://delvingbitcoin.org/search.json"))
        self.assertEqual(len(build_seed_feed.COLLECTOR_FAILURES), 1)
        self.assertIn("Delving GET", build_seed_feed.COLLECTOR_FAILURES[0])

    def test_seed_github_404_does_not_abort_service_ingest(self) -> None:
        watch = build_seed_feed.normalize_watch({
            "name": "Example Watch",
            "serving": {"mode": "service", "service_url": "http://localhost:8787/"},
        })
        cfg = {
            "seeded_sources": {
                "github_repositories": [{
                    "id": "gone-repo",
                    "repo": "missing/gone",
                    "discovered_at": "2025-08-26T17:54:03Z",
                    "activity_at": "2026-05-29T18:55:54Z",
                }]
            },
            "live_collectors": {
                "github_repository_searches": [{"id": "repo-discovery", "query": "atlas"}],
            },
        }
        client = mock.Mock()
        client.collector_config.return_value = {}
        client.collector_state.return_value = ({}, {}, {})
        client.ingest.return_value = {"ingest_id": "ing_1"}
        err = _http_error("https://api.github.com/repos/missing/gone", 404, "Not Found")
        old_argv = sys.argv
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            _empty_artifacts(out)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                sys.argv = ["build_seed_feed.py"]
                with mock.patch.object(build_seed_feed, "load_watch", return_value=watch), \
                        mock.patch.object(build_seed_feed, "parse_yaml", return_value=cfg), \
                        mock.patch.object(build_seed_feed, "require_ingest_token", return_value="tok"), \
                        mock.patch.object(build_seed_feed, "ServiceClient", return_value=client), \
                        mock.patch.object(build_seed_feed, "skip_live_collectors", return_value=False), \
                        mock.patch.object(build_seed_feed, "urlopen", side_effect=err), \
                        mock.patch.object(build_seed_feed, "search_github_repositories", return_value=[]), \
                        mock.patch.object(build_seed_feed, "search_github_pull_requests", return_value=[]), \
                        mock.patch.object(build_seed_feed, "search_delving_topics", return_value=[]), \
                        mock.patch.object(build_seed_feed, "list_delving_category", return_value=[]):
                    rc = build_seed_feed.main()
            finally:
                build_seed_feed.OUT = old_out
                sys.argv = old_argv
        self.assertEqual(rc, 0)
        client.ingest.assert_called_once()
        payload = client.ingest.call_args.args[0]
        self.assertEqual(payload["items"][0]["id"], "seed:gone-repo")
        self.assertEqual(payload["items"][0]["discovered_at"], "2025-08-26T17:54:03Z")
        self.assertEqual(payload["items"][0]["activity_at"], "2026-05-29T18:55:54Z")

    def test_live_search_failure_aborts_service_ingest(self) -> None:
        watch = build_seed_feed.normalize_watch({
            "name": "Example Watch",
            "serving": {"mode": "service", "service_url": "http://localhost:8787/"},
        })
        cfg = {
            "seeded_sources": {},
            "live_collectors": {
                "github_repository_searches": [{"id": "repo-discovery", "query": "atlas"}],
            },
        }
        client = mock.Mock()
        client.collector_config.return_value = {}
        client.collector_state.return_value = ({}, {}, {})

        def fail_search(query, max_results=10):
            build_seed_feed.note_collector_failure(
                f"GitHub repository search failed for query {query!r}: boom"
            )
            return []

        old_argv = sys.argv
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            _empty_artifacts(out)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                sys.argv = ["build_seed_feed.py"]
                with mock.patch.object(build_seed_feed, "load_watch", return_value=watch), \
                        mock.patch.object(build_seed_feed, "parse_yaml", return_value=cfg), \
                        mock.patch.object(build_seed_feed, "require_ingest_token", return_value="tok"), \
                        mock.patch.object(build_seed_feed, "ServiceClient", return_value=client), \
                        mock.patch.object(build_seed_feed, "skip_live_collectors", return_value=False), \
                        mock.patch.object(build_seed_feed, "github_get_json", return_value=None), \
                        mock.patch.object(build_seed_feed, "search_github_repositories", side_effect=fail_search), \
                        mock.patch.object(build_seed_feed, "search_github_pull_requests", return_value=[]), \
                        mock.patch.object(build_seed_feed, "search_delving_topics", return_value=[]), \
                        mock.patch.object(build_seed_feed, "list_delving_category", return_value=[]):
                    with self.assertRaises(SystemExit) as raised:
                        build_seed_feed.main()
            finally:
                build_seed_feed.OUT = old_out
                sys.argv = old_argv
        self.assertIn("live collector HTTP failed", str(raised.exception))
        self.assertIn("GitHub repository search failed", str(raised.exception))
        client.ingest.assert_not_called()



class FakeHttpBody:
    def __init__(self, payload) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _service_main_ctx(watch, cfg, urlopen=None, extra_patches=None):
    client = mock.Mock()
    client.collector_config.return_value = {}
    client.collector_state.return_value = ({}, {}, {})
    patches = [
        mock.patch.object(build_seed_feed, "load_watch", return_value=watch),
        mock.patch.object(build_seed_feed, "parse_yaml", return_value=cfg),
        mock.patch.object(build_seed_feed, "require_ingest_token", return_value="tok"),
        mock.patch.object(build_seed_feed, "ServiceClient", return_value=client),
        mock.patch.object(build_seed_feed, "skip_live_collectors", return_value=False),
        mock.patch.object(build_seed_feed, "github_get_json", return_value=None),
        mock.patch.object(build_seed_feed, "search_github_pull_requests", return_value=[]),
        mock.patch.object(build_seed_feed, "search_delving_topics", return_value=[]),
        mock.patch.object(build_seed_feed, "list_delving_category", return_value=[]),
    ]
    if urlopen is not None:
        patches.append(mock.patch.object(build_seed_feed, "urlopen", urlopen))
    for patch in extra_patches or []:
        patches.append(patch)
    return client, patches


class PartialCollectorResponseTests(unittest.TestCase):
    def tearDown(self) -> None:
        build_seed_feed.COLLECTOR_FAILURES.clear()

    def test_incomplete_results_notes_failure(self) -> None:
        build_seed_feed.COLLECTOR_FAILURES.clear()
        with mock.patch.object(build_seed_feed, "urlopen", lambda *a, **k: FakeHttpBody({"incomplete_results": True, "items": []})):
            self.assertEqual(build_seed_feed.search_github_repositories("atlas"), [])
            self.assertEqual(build_seed_feed.search_github_pull_requests("atlas"), [])
        self.assertTrue(any("incomplete" in msg for msg in build_seed_feed.COLLECTOR_FAILURES))

    def test_invalid_items_array_notes_failure(self) -> None:
        build_seed_feed.COLLECTOR_FAILURES.clear()
        with mock.patch.object(build_seed_feed, "urlopen", lambda *a, **k: FakeHttpBody({"items": "nope"})):
            self.assertEqual(build_seed_feed.search_github_repositories("atlas"), [])
        self.assertTrue(any("invalid items" in msg for msg in build_seed_feed.COLLECTOR_FAILURES))

    def test_repository_missing_required_field_notes_failure(self) -> None:
        build_seed_feed.COLLECTOR_FAILURES.clear()
        with mock.patch.object(
            build_seed_feed,
            "urlopen",
            lambda *a, **k: FakeHttpBody({"items": [{}]}),
        ):
            self.assertEqual(build_seed_feed.search_github_repositories("atlas"), [])
        self.assertTrue(any("without full_name or html_url" in msg for msg in build_seed_feed.COLLECTOR_FAILURES))

    def test_non_object_delving_response_notes_failure(self) -> None:
        build_seed_feed.COLLECTOR_FAILURES.clear()
        with mock.patch.object(build_seed_feed, "urlopen", lambda *a, **k: FakeHttpBody([])):
            self.assertEqual(build_seed_feed.search_delving_topics("frost"), [])
        self.assertTrue(any("expected object" in msg for msg in build_seed_feed.COLLECTOR_FAILURES))

    def test_delving_missing_topics_notes_failure(self) -> None:
        build_seed_feed.COLLECTOR_FAILURES.clear()
        with mock.patch.object(build_seed_feed, "delving_get_json", return_value={"posts": []}):
            self.assertEqual(build_seed_feed.search_delving_topics("frost"), [])
        self.assertTrue(any("no topics array" in msg for msg in build_seed_feed.COLLECTOR_FAILURES))

    def test_delving_topic_missing_required_field_notes_failure(self) -> None:
        build_seed_feed.COLLECTOR_FAILURES.clear()
        with mock.patch.object(
            build_seed_feed,
            "delving_get_json",
            return_value={"topics": [{"id": 1}], "posts": []},
        ):
            self.assertEqual(build_seed_feed.search_delving_topics("frost"), [])
        self.assertTrue(any("without id or title" in msg for msg in build_seed_feed.COLLECTOR_FAILURES))

    def test_delving_category_invalid_topic_notes_failure(self) -> None:
        build_seed_feed.COLLECTOR_FAILURES.clear()
        with mock.patch.object(
            build_seed_feed,
            "delving_get_json",
            return_value={"topic_list": {"topics": [{"title": "Missing id"}]}},
        ):
            self.assertEqual(build_seed_feed.list_delving_category("protocol"), [])
        self.assertTrue(any("invalid topic" in msg for msg in build_seed_feed.COLLECTOR_FAILURES))

    def test_incomplete_results_aborts_service_ingest(self) -> None:
        watch = build_seed_feed.normalize_watch({
            "name": "Example Watch",
            "serving": {"mode": "service", "service_url": "http://localhost:8787/"},
        })
        cfg = {
            "seeded_sources": {},
            "live_collectors": {
                "github_repository_searches": [{"id": "repo-discovery", "query": "atlas"}],
            },
        }
        client, patches = _service_main_ctx(
            watch,
            cfg,
            extra_patches=[
                mock.patch.object(
                    build_seed_feed,
                    "search_github_repositories",
                    side_effect=lambda query, max_results=10: (
                        build_seed_feed.note_collector_failure(f"GitHub repository search incomplete for query {query!r}")
                        or []
                    ),
                ),
            ],
        )
        old_argv = sys.argv
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            _empty_artifacts(out)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                sys.argv = ["build_seed_feed.py"]
                with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9]:
                    with self.assertRaises(SystemExit) as raised:
                        build_seed_feed.main()
            finally:
                build_seed_feed.OUT = old_out
                sys.argv = old_argv
        self.assertIn("live collector HTTP failed", str(raised.exception))
        self.assertIn("incomplete", str(raised.exception))
        client.ingest.assert_not_called()


class SeedCatalogUniquenessTests(unittest.TestCase):
    def test_duplicate_ids_across_kinds_exit(self) -> None:
        cfg = {
            "seeded_sources": {
                "github_repositories": [
                    {"id": "acme-lib", "repo": "acme/keep"},
                    {"id": "other", "repo": "acme/other"},
                ],
                "crates": [{"id": "acme-lib", "name": "acme-lib"}],
            }
        }
        with self.assertRaises(SystemExit) as raised:
            build_seed_feed.validate_seed_catalog(cfg)
        msg = str(raised.exception)
        self.assertIn("acme-lib", msg)
        self.assertIn("github_repositories[0]", msg)
        self.assertIn("crates[0]", msg)


class ServiceClientRedirectTests(unittest.TestCase):
    def test_redirect_is_refused_without_forwarding_token(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        import service_client

        seen = []

        class Handler:
            pass

        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading

        class RedirectOnce(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:9/steal")
                self.end_headers()

            def log_message(self, *_args):
                return

        server = HTTPServer(("127.0.0.1", 0), RedirectOnce)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        port = server.server_address[1]
        client = service_client.ServiceClient(f"http://127.0.0.1:{port}/", "secret-token")
        with self.assertRaises(service_client.ServiceError) as raised:
            client.collector_config()
        thread.join(timeout=2)
        server.server_close()
        self.assertIn("refusing redirect", str(raised.exception))
        self.assertNotIn("secret-token", str(raised.exception))
        self.assertEqual(seen, ["Bearer secret-token"])


if __name__ == "__main__":
    unittest.main()
