#!/usr/bin/env python3
"""Tests for shared serving-mode parsing and public-artifact verification."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    mod = cast(Any, importlib.util.module_from_spec(spec))
    assert spec.loader is not None
    spec.loader.exec_module(cast(ModuleType, mod))
    return mod


sync_hugo = _load("sync_hugo_content", ROOT / "scripts" / "sync_hugo_content.py")
promote = _load("promote_to_service", ROOT / "scripts" / "promote_to_service.py")
verify = _load("verify_public_artifacts", ROOT / "scripts" / "verify_public_artifacts.py")
watch_config = _load("watch_config", ROOT / "scripts" / "watch_config.py")


VALID = {
    "feed.json": json.dumps({"schema_version": "source-watch.feed.v0", "items": []}),
    "projects.json": json.dumps({"schema_version": "source-watch.projects.v0", "projects": []}),
    "sources.json": json.dumps({"schema_version": "source-watch.sources.v0", "sources": []}),
    "watch.json": json.dumps({"name": "Example Watch"}),
    "feed.xml": '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>',
    "items.jsonl": "",
}


class HugoServingModeTests(unittest.TestCase):
    def test_write_hugo_toml_normalizes_service_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = sync_hugo.SITE
            sync_hugo.SITE = Path(tmp)
            try:
                sync_hugo.write_hugo_toml({
                    "name": "X",
                    "base_url": "https://x/",
                    "serving": {"mode": "Service ", "service_url": "https://x/"},
                })
                text = (Path(tmp) / "hugo.toml").read_text()
            finally:
                sync_hugo.SITE = old
        self.assertIn('serving_mode = "service"', text)

    def test_service_mode_without_url_is_allowed_for_predeploy_tools(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write("serving:\n  mode: service\n  service_url: ''\n")
            path = Path(fh.name)
        try:
            serving = watch_config.load_serving(path, require_service_url=False)
        finally:
            path.unlink()
        self.assertEqual(serving, {"mode": "service", "service_url": ""})

    def test_sync_and_promote_accept_predeploy_service_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            watch_path = root / "watch.yaml"
            watch_path.write_text(
                "name: Predeploy\nserving:\n  mode: service\n  service_url: ''\n"
            )
            old_sync = (sync_hugo.SITE, sync_hugo.CONTENT, sync_hugo.WATCH_CONFIG)
            old_promote_watch = promote.WATCH
            sync_hugo.SITE = root / "site"
            sync_hugo.CONTENT = sync_hugo.SITE / "content"
            sync_hugo.WATCH_CONFIG = watch_path
            promote.WATCH = watch_path
            try:
                self.assertEqual(sync_hugo.main(), 0)
                self.assertEqual(promote.load_mode(), "service")
            finally:
                sync_hugo.SITE, sync_hugo.CONTENT, sync_hugo.WATCH_CONFIG = old_sync
                promote.WATCH = old_promote_watch
            self.assertTrue((root / "site" / "content" / "weeks" / "live" / "_index.md").exists())

    def test_service_mode_without_url_remains_strict_for_network_tools(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write("serving:\n  mode: service\n  service_url: ''\n")
            path = Path(fh.name)
        try:
            with self.assertRaises(SystemExit):
                watch_config.load_serving(path)
        finally:
            path.unlink()

    def test_structurally_invalid_watch_yaml_exits(self) -> None:
        for text in ("serving: service\n", "- not-a-mapping\n"):
            with self.subTest(text=text), tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
                fh.write(text)
                path = Path(fh.name)
            try:
                with self.assertRaises(SystemExit):
                    watch_config.load_serving(path, require_service_url=False)
            finally:
                path.unlink()

    def test_service_url_must_be_an_origin(self) -> None:
        for url in (
            "https:///path",
            "https://user@example.com/",
            "https://example.com/path",
            "https://example.com/?query=1",
            "https://example.com/#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(SystemExit):
                watch_config.normalize_serving({"mode": "service", "service_url": url})


class VerifyContractTests(unittest.TestCase):
    def test_check_all_accepts_six_valid_artifacts(self) -> None:
        self.assertEqual(verify.check_all(VALID.__getitem__), 0)

    def test_check_all_omitting_sources_exits(self) -> None:
        def read(name: str) -> str:
            if name == "sources.json":
                raise SystemExit("missing sources.json")
            return VALID[name]

        with self.assertRaises(SystemExit) as raised:
            verify.check_all(read)
        self.assertIn("sources.json", str(raised.exception))

    def test_check_all_rejects_invalid_jsonl_and_xml(self) -> None:
        invalid_jsonl = dict(VALID, **{"items.jsonl": "not json\n"})
        with self.assertRaises(SystemExit):
            verify.check_all(invalid_jsonl.__getitem__)
        invalid_xml = dict(VALID, **{"feed.xml": "<?xml nope <rss"})
        with self.assertRaises(SystemExit):
            verify.check_all(invalid_xml.__getitem__)

    def test_check_all_rejects_cross_artifact_mismatch(self) -> None:
        item = {
            "id": "item-1",
            "title": "Item",
            "source_url": "https://example.com/item",
            "source_type": "docs_page",
            "event_type": "source_seeded",
            "observed_at": "2026-01-01T00:00:00Z",
            "evidence": [{"url": "https://example.com/item"}],
            "project": "Missing project",
        }
        artifacts = dict(VALID)
        artifacts["feed.json"] = json.dumps({"schema_version": "source-watch.feed.v0", "items": [item]})
        artifacts["items.jsonl"] = json.dumps(item) + "\n"
        artifacts["feed.xml"] = (
            "<?xml version='1.0'?><rss><channel><item>"
            "<guid>item-1</guid></item></channel></rss>"
        )
        with self.assertRaises(SystemExit) as raised:
            verify.check_all(artifacts.__getitem__)
        self.assertIn("known project", str(raised.exception))

    def test_unparseable_watch_yaml_exits(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write("serving: [unterminated")
            path = Path(fh.name)
        old = verify.WATCH
        verify.WATCH = path
        try:
            with self.assertRaises(SystemExit) as raised:
                verify.main()
            self.assertNotIn("missing public artifacts", str(raised.exception))
        finally:
            verify.WATCH = old
            path.unlink()


if __name__ == "__main__":
    unittest.main()
