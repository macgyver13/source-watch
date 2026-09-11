ALTER TABLE seed_additions ADD COLUMN entry_id TEXT;
UPDATE seed_additions SET entry_id = lower(trim(json_extract(entry, '$.id')));
DELETE FROM seed_additions
WHERE entry_id IS NOT NULL
  AND id NOT IN (
    SELECT MIN(id) FROM seed_additions
    WHERE entry_id IS NOT NULL
    GROUP BY entry_id COLLATE NOCASE
  );
CREATE UNIQUE INDEX seed_additions_entry_id ON seed_additions (entry_id COLLATE NOCASE);
CREATE TABLE seed_locators (
  kind TEXT NOT NULL, locator TEXT NOT NULL, entry_id TEXT NOT NULL,
  PRIMARY KEY (kind, locator)
);
DELETE FROM exclusions WHERE id NOT IN (SELECT MIN(id) FROM exclusions GROUP BY kind, lower(value));
DELETE FROM include_terms WHERE id NOT IN (SELECT MIN(id) FROM include_terms GROUP BY bucket, lower(term));
CREATE UNIQUE INDEX exclusions_kind_value_nocase ON exclusions (kind, value COLLATE NOCASE);
CREATE UNIQUE INDEX include_terms_bucket_term_nocase ON include_terms (bucket, term COLLATE NOCASE);
