#!/usr/bin/env python3
"""Regression tests for stable Source Watch discovery dates."""
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


class DiscoveryDateTests(unittest.TestCase):
    def test_existing_item_discovered_at_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                (out / "feed.json").write_text(json.dumps({
                    "items": [{
                        "id": "seed:example-docs-get-started",
                        "title": "Old title",
                        "source_url": "https://docs.github.com/en/get-started",
                        "event_time": "2026-08-28T00:00:00Z",
                        "observed_at": "2026-08-29T00:00:00Z",
                    }]
                }))
                (out / "projects.json").write_text(json.dumps({"projects": []}))
                (out / "sources.json").write_text(json.dumps({"sources": []}))

                cfg = {"seeded_sources": {"docs_pages": [{
                    "id": "example-docs-get-started",
                    "name": "GitHub Docs · Get started",
                    "url": "https://docs.github.com/en/get-started",
                    "project": "GitHub Docs",
                    "tags": ["docs"],
                }]}}
                items, _projects, _sources = build_seed_feed.build_items(cfg)
                self.assertEqual(items[0]["discovered_at"], "2026-08-28T00:00:00Z")
                self.assertEqual(items[0]["event_time"], "2026-08-28T00:00:00Z")
                self.assertNotEqual(items[0]["last_seen_at"], "2026-08-28T00:00:00Z")
            finally:
                build_seed_feed.OUT = old_out

    def test_existing_project_first_seen_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                (out / "feed.json").write_text(json.dumps({"items": []}))
                (out / "projects.json").write_text(json.dumps({
                    "projects": [{
                        "id": "github-docs",
                        "name": "GitHub Docs",
                        "first_seen": "2026-08-27T00:00:00Z",
                    }]
                }))
                (out / "sources.json").write_text(json.dumps({"sources": []}))

                cfg = {"seeded_sources": {"docs_pages": [{
                    "id": "example-docs-get-started",
                    "name": "GitHub Docs · Get started",
                    "url": "https://docs.github.com/en/get-started",
                    "project": "GitHub Docs",
                    "tags": ["docs"],
                }]}}
                _items, projects, _sources = build_seed_feed.build_items(cfg)
                self.assertEqual(projects["github-docs"]["discovered_at"], "2026-08-27T00:00:00Z")
                self.assertEqual(projects["github-docs"]["first_seen"], "2026-08-27T00:00:00Z")
            finally:
                build_seed_feed.OUT = old_out

    def test_project_latest_discovered_at_tracks_newest_child_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                (out / "feed.json").write_text(json.dumps({
                    "items": [{
                        "id": "seed:old-source",
                        "discovered_at": "2026-08-27T00:00:00Z",
                    }]
                }))
                (out / "projects.json").write_text(json.dumps({"projects": []}))
                (out / "sources.json").write_text(json.dumps({"sources": []}))

                cfg = {"seeded_sources": {"docs_pages": [
                    {
                        "id": "old-source",
                        "name": "Old source",
                        "url": "https://example.com/old",
                        "project": "Shared project",
                        "tags": ["docs"],
                    },
                    {
                        "id": "new-source",
                        "name": "New source",
                        "url": "https://example.com/new",
                        "project": "Shared project",
                        "tags": ["docs"],
                    },
                ]}}
                _items, projects, _sources = build_seed_feed.build_items(cfg)
                latest = projects["shared-project"]["latest_discovered_at"]
                self.assertGreater(latest, "2026-08-27T00:00:00Z")
            finally:
                build_seed_feed.OUT = old_out

    def test_seed_summary_is_used_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                (out / "feed.json").write_text(json.dumps({"items": []}))
                (out / "projects.json").write_text(json.dumps({"projects": []}))
                (out / "sources.json").write_text(json.dumps({"sources": []}))
                cfg = {"seeded_sources": {"docs_pages": [{
                    "id": "example-docs-get-started",
                    "name": "GitHub Docs · Get started",
                    "url": "https://docs.github.com/en/get-started",
                    "project": "GitHub Docs",
                    "tags": ["docs"],
                    "summary": "Placeholder docs page. Replace with a documentation URL your watch should track.",
                }]}}
                items, _projects, _sources = build_seed_feed.build_items(cfg)
                self.assertEqual(
                    items[0]["summary"],
                    "Placeholder docs page. Replace with a documentation URL your watch should track.",
                )
                self.assertFalse(items[0]["summary"].startswith("Seeded monitored source"))
            finally:
                build_seed_feed.OUT = old_out

    def test_default_repository_summary_is_generic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                (out / "feed.json").write_text(json.dumps({"items": []}))
                (out / "projects.json").write_text(json.dumps({"projects": []}))
                (out / "sources.json").write_text(json.dumps({"sources": []}))
                cfg = {"seeded_sources": {"github_repositories": [{
                    "id": "example-repo",
                    "repo": "example/docs",
                    "project": "Example",
                    "tags": ["docs"],
                }]}}
                items, _projects, _sources = build_seed_feed.build_items(cfg)
                self.assertEqual(items[0]["summary"], "Public repository: example/docs.")
            finally:
                build_seed_feed.OUT = old_out

    def test_default_tag_injected_only_when_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                (out / "feed.json").write_text(json.dumps({"items": []}))
                (out / "projects.json").write_text(json.dumps({"projects": []}))
                (out / "sources.json").write_text(json.dumps({"sources": []}))
                cfg = {"seeded_sources": {"docs_pages": [{
                    "id": "plain",
                    "name": "Plain",
                    "url": "https://example.com/plain",
                    "project": "Plain",
                    "tags": ["docs"],
                }]}}
                items, _, _ = build_seed_feed.build_items(cfg)
                self.assertEqual(items[0]["tags"], ["docs"])

                items2, _, _ = build_seed_feed.build_items({**cfg, "default_tag": "example"})
                self.assertEqual(items2[0]["tags"], ["example", "docs"])
            finally:
                build_seed_feed.OUT = old_out

    def test_seed_discovered_at_overrides_existing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                (out / "feed.json").write_text(json.dumps({
                    "items": [{
                        "id": "seed:example-docs-get-started",
                        "discovered_at": "2026-09-02T00:00:00Z",
                        "event_time": "2026-09-02T00:00:00Z",
                        "activity_at": "2026-09-02T00:00:00Z",
                    }]
                }))
                (out / "projects.json").write_text(json.dumps({
                    "projects": [{
                        "id": "github-docs",
                        "discovered_at": "2026-09-02T00:00:00Z",
                        "first_seen": "2026-09-02T00:00:00Z",
                        "activity_at": "2026-09-02T00:00:00Z",
                        "latest_discovered_at": "2026-09-02T00:00:00Z",
                    }]
                }))
                (out / "sources.json").write_text(json.dumps({
                    "sources": [{
                        "id": "example-docs-get-started",
                        "discovered_at": "2026-09-02T00:00:00Z",
                        "first_seen": "2026-09-02T00:00:00Z",
                    }]
                }))
                cfg = {"seeded_sources": {"docs_pages": [{
                    "id": "example-docs-get-started",
                    "name": "GitHub Docs · Get started",
                    "url": "https://docs.github.com/en/get-started",
                    "project": "GitHub Docs",
                    "tags": ["docs"],
                    "discovered_at": "2025-08-26T18:20:48Z",
                    "activity_at": "2026-04-20T13:45:21Z",
                }]}}
                items, projects, sources = build_seed_feed.build_items(cfg)
                self.assertEqual(items[0]["discovered_at"], "2025-08-26T18:20:48Z")
                self.assertEqual(items[0]["event_time"], "2025-08-26T18:20:48Z")
                self.assertEqual(items[0]["activity_at"], "2026-04-20T13:45:21Z")
                self.assertNotEqual(items[0]["observed_at"], "2025-08-26T18:20:48Z")
                self.assertEqual(sources["example-docs-get-started"]["discovered_at"], "2025-08-26T18:20:48Z")
                self.assertEqual(projects["github-docs"]["discovered_at"], "2025-08-26T18:20:48Z")
                self.assertEqual(projects["github-docs"]["activity_at"], "2026-04-20T13:45:21Z")
                self.assertEqual(projects["github-docs"]["latest_discovered_at"], "2025-08-26T18:20:48Z")
            finally:
                build_seed_feed.OUT = old_out

    def test_project_activity_at_is_not_refresh_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old_out = build_seed_feed.OUT
            build_seed_feed.OUT = out
            try:
                (out / "feed.json").write_text(json.dumps({"items": []}))
                (out / "projects.json").write_text(json.dumps({"projects": []}))
                (out / "sources.json").write_text(json.dumps({"sources": []}))
                cfg = {"seeded_sources": {"github_repositories": [{
                    "id": "example-repo",
                    "repo": "example/docs",
                    "project": "Example",
                    "tags": ["docs"],
                    "discovered_at": "2025-08-25T21:28:19Z",
                    "activity_at": "2025-08-29T20:20:27Z",
                }]}}
                items, projects, _sources = build_seed_feed.build_items(cfg)
                self.assertEqual(items[0]["discovered_at"], "2025-08-25T21:28:19Z")
                self.assertEqual(items[0]["activity_at"], "2025-08-29T20:20:27Z")
                self.assertEqual(projects["example"]["activity_at"], "2025-08-29T20:20:27Z")
                self.assertGreater(projects["example"]["last_observed_activity"], items[0]["activity_at"])
            finally:
                build_seed_feed.OUT = old_out


if __name__ == "__main__":
    unittest.main()
