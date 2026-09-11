"""Regression tests for upgrading service-mode D1 schemas."""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def test_admin_integrity_migration_deduplicates_existing_seed_ids(self) -> None:
        db = sqlite3.connect(":memory:")
        db.executescript((ROOT / "migrations/0001_init.sql").read_text())
        db.executemany(
            "INSERT INTO seed_additions (kind, entry, created_at) VALUES (?, ?, ?)",
            [
                ("docs_pages", '{"id":"Foo","url":"https://example.com/a"}', "2026-01-01T00:00:00Z"),
                ("docs_pages", '{"id":"foo","url":"https://example.com/b"}', "2026-01-02T00:00:00Z"),
            ],
        )

        db.executescript((ROOT / "migrations/0002_admin_integrity.sql").read_text())
        db.executescript((ROOT / "migrations/0003_mutation_revisions.sql").read_text())

        rows = db.execute("SELECT id, entry_id FROM seed_additions ORDER BY id").fetchall()
        self.assertEqual(rows, [(1, "foo")])
        self.assertIn("revision", {row[1] for row in db.execute("PRAGMA table_info(overrides)")})
        self.assertIn("revision", {row[1] for row in db.execute("PRAGMA table_info(exclusions)")})


if __name__ == "__main__":
    unittest.main()
