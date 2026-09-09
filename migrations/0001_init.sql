CREATE TABLE raw_items (
  ingest_id TEXT NOT NULL, id TEXT NOT NULL, json TEXT NOT NULL,
  PRIMARY KEY (ingest_id, id)
);
CREATE TABLE raw_projects (
  ingest_id TEXT NOT NULL, id TEXT NOT NULL, json TEXT NOT NULL,
  PRIMARY KEY (ingest_id, id)
);
CREATE TABLE raw_sources (
  ingest_id TEXT NOT NULL, id TEXT NOT NULL, json TEXT NOT NULL,
  PRIMARY KEY (ingest_id, id)
);
CREATE TABLE ingests (
  ingest_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, committed_at TEXT,
  generated_at TEXT, watch_json TEXT, feed_title TEXT, feed_description TEXT
);
CREATE TABLE overrides (
  kind TEXT NOT NULL, target_id TEXT NOT NULL, patch TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (kind, target_id)
);
CREATE TABLE exclusions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, value TEXT NOT NULL,
  note TEXT, created_at TEXT NOT NULL, UNIQUE (kind, value)
);
CREATE TABLE include_terms (
  id INTEGER PRIMARY KEY AUTOINCREMENT, bucket TEXT NOT NULL, term TEXT NOT NULL,
  note TEXT, created_at TEXT NOT NULL, UNIQUE (bucket, term)
);
CREATE TABLE seed_additions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, entry TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE rendered (
  name TEXT NOT NULL, seq INTEGER NOT NULL, body TEXT NOT NULL,
  PRIMARY KEY (name, seq)
);
CREATE TABLE rendered_meta (
  name TEXT PRIMARY KEY, etag TEXT NOT NULL, content_type TEXT NOT NULL,
  bytes INTEGER NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, action TEXT NOT NULL,
  target TEXT, detail TEXT
);
